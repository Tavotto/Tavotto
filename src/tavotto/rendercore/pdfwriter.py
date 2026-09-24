"""PDF 写入器：Render IR → 可检索文字的真矢量 PDF（统一实施包 U06，ADR 0059 / 0060）。

U02 的受限 emitter 收编进产品：pikepdf（qpdf）做对象模型 / 序列化 / 外来页导入，fontTools 做子集，
**内容流的操作符自己写**——只会做 `ir.CAPABILITIES["pdf"]` 里声明 `native` 的那几件事（路径填充 /
描边 / 裁剪 / 透明组 / 常量 alpha / 可检索文字 / 页面底色 / 外来页 Form XObject / 位图 Image XObject /
镜像），**不是通用 PDF 写入器，更不是 parser**。声明 `unsupported` 的操作以 `UnsupportedCapability`
拒绝，不静默跳过、不整页位图冒充矢量（RC-011）。

## 面板（U07，ADR 0065）

* `ImportedPage`：源 PDF 的那一页经 qpdf `as_form_xobject(handle_transformations=True)` 变成 Form
  XObject，`copy_foreign` 搬进本文档——源页的 /Resources（含从 /Pages 继承的）随 form 走，各自一份，
  两个源里同名的 /F1 / /X1 互不相干（RC-043）；源页的 /Rotate 与 /UserUnit 由 qpdf 折进 form 的
  /Matrix，本模块只看 BBox 经 /Matrix 映射后的**可见框**，于是恰好应用一次（RC-039）；可见框由
  qpdf 定（TrimBox → CropBox → MediaBox），非零原点的页盒因此天然正确（RC-038）。crop / 翻转 /
  旋转 / 填满目标框的顺序只在 `placement.place()` 一处。注释、动作、附件、JavaScript **不进** form
  （只有内容流与资源会被 `as_form_xobject` 收进去），要密码才能打开的源以 `source_unreadable` 拒绝（RC-046；只有 owner 密码的按普通 PDF 导入）。
  `opacity < 1` 时整页包成透明组再画——组内 alpha 从 1 起算，源页内部重叠不被二次压暗
  （RC-041），**仍是矢量、文字层随 form 保留**（RC-040）。
* `Image`：位图经 `rasterio.decode()` 成 `RasterBuffer`，写成 8 bit DeviceRGB 的 Image XObject（Flate），
  alpha 单独成 /SMask（straight，与 PNG / TIFF 同义）；8 bit RGB / 灰度 JPEG 原字节直通 `/DCTDecode`。
  `opacity < 1` 是 ExtGState 常量 alpha（位图是一次填充）。同一份字节在一份文档里只嵌一次
  （按资源 key 去重，key 含字节 hash——按身份去重，不按名字）。
* 资源的字节由调用方经 `files` 交进来（`job` 里是 `sources.read_frozen()` 核过 hash 的那一份）；
  写入器再核一次 sha256（`source_identity`）——两次核对是有意的冗余（RC-014）。

## 坐标

IR 就是 PDF 用户空间（pt、左下原点、y 向上，ADR 0059）：本模块**原样**把 IR 的数写进内容流，
不换算、不翻转。`Group.transform` → `cm`，`Group.clip` → `W n`，children 按列表顺序。

## 文字层（RC-034 / RC-035 / RC-036）

* 字体程序两条路，其余格式显式拒绝：TrueType（glyf）→ `CIDFontType2` + `FontFile2`，code = 子集
  后的 GID，`/CIDToGIDMap /Identity`；CFF CID-keyed → `CIDFontType0` + `FontFile3/CIDFontType0C`，
  code = 原 CID（子集后 charset 保留 CID）。子集是真子集（`hbshaper.HbFace.subset`）。
* `/W` 数组按 code 记每个用到的字形的 advance（千分之一 em）；内容流里 `TJ` 的位移让笔的净位移
  等于 HarfBuzz 的 `x_advance`（先挪 `x_offset`，字形之后补回差值）；`y_offset` 用 `Ts` 抬。
* **ToUnicode 按 cluster 写**：一个 code 对应它**第一次**覆盖的原文（连字 `fi` 一个 code → 两个
  码位；合成序列 `e` + U+0301 一个 code → 两个码位）。三种情形另外包 **ActualText**
  （`/Span <</ActualText …>> BDC … EMC`）：① 一个 cluster 出多个字形（组合标记未合成）；② 同一
  code 在另一处覆盖了不同的原文（ToUnicode 只能记一个）；③ `GlyphRun.actual_text`（合成上下标：
  画的是 `5`，用户写的是 `⁵`）。多字形 cluster 的续字形在 ToUnicode 里映到**空串**（`<>`），
  不认 ActualText 的读取器抽回的仍是第一个字形带的整段原文、不重复吐字。
* **缺字不伪装**：gid 0（.notdef）照常写进内容流（画面上就是那个方框）、ToUnicode 照常回原字，
  `WriteFacts.notdef_codes` 记下有多少 code 落在 .notdef 上，调用方（`job`）把它连同编译期的
  `glyph_missing` 一起报出去；本模块不替它换脸。

同一输入两次运行字节相同（子集 `recalcTimestamp=False`、`deterministic_id=True`、不写时间戳）。

候选包只在函数里 import（`hbshaper.require`），没装 extra 时 import 本模块仍成功。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .. import deflate
from . import ir, placement, raster, rasterio
from .hbshaper import HbFace, HbFaceProvider, require

WRITER_ERROR_CODES = (
    "unsupported_capability",  # IR 用到了 CAPABILITIES["pdf"] 里声明 unsupported 的操作
    "font_identity",  # 资源里的脸与注册表里的脸身份不一致
    "font_format",  # 字体程序格式不在两条路里
    "source_bytes_missing",  # 节点引用的文件资源没有交进来字节（files 里没有这个 key）
    "source_identity",  # 交进来的字节 sha256 与资源身份不符
    "source_unreadable",  # 源 PDF 打不开 / 加密 / 没有这一页 / 位图解不开
)


class WriterError(RuntimeError):
    def __init__(self, code: str, message: str, params: dict | None = None) -> None:
        assert code in WRITER_ERROR_CODES, code
        super().__init__(message)
        self.code = code
        self.params = params or {}


class UnsupportedCapability(WriterError):
    def __init__(self, operation: str, reason: str, object_id: str = "") -> None:
        super().__init__(
            "unsupported_capability",
            f"PDF 写入器不支持 {operation}（{reason}）",
            {"operation": operation, "reason": reason, "object_id": object_id},
        )
        self.operation = operation


@dataclass
class WriteFacts:
    page: tuple[float, float]
    fonts: list[dict] = field(default_factory=list)
    text_objects: int = 0
    paths: int = 0
    groups: int = 0
    transparency_groups: int = 0
    actualtext_spans: int = 0
    notdef_codes: int = 0
    #: 每个 ImportedPage 一条：可见框 / 落位矩阵 / 裁剪 / 是否包了透明组（evidence 与测试读）。
    imported_pages: list[dict] = field(default_factory=list)
    #: 每个 Image 一条：像素尺寸 / 通道 / 编码（flate / dct）/ 落位矩阵。
    images: list[dict] = field(default_factory=list)
    sha256: str = ""
    bytes: int = 0
    versions: dict = field(default_factory=dict)


def _num(v: float) -> str:
    if not math.isfinite(v):  # validate() 已经挡过；这里是最后一道
        raise ValueError(f"非有限数进了内容流: {v!r}")
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _rgb(p: ir.RGB) -> str:
    return " ".join(_num(c) for c in p)


def _hex_utf16(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


# ---------------------------------------------------------------------------
# 字体收集
# ---------------------------------------------------------------------------
class _FontUse:
    """一张脸在这份文档里用到的字形与 ToUnicode 事实。"""

    def __init__(self, face: HbFace, name: str) -> None:
        self.face = face
        self.name = name  # 资源名 /F1
        self.used: dict[int, str] = {}  # gid → 第一次覆盖的原文（续字形为 ""）
        self.remap: dict[int, int] = {}
        self.obj: Any = None

    def note(self, gid: int, text: str) -> bool:
        """记一次使用；返回「这次的原文与 ToUnicode 里那条不一致」（要 ActualText）。"""
        if gid not in self.used:
            self.used[gid] = text
            return False
        if text and self.used[gid] != text:
            if not self.used[gid]:
                self.used[gid] = text
                return False
            return True
        return False

    def code(self, gid: int) -> int:
        return self.remap[gid] if self.face.kind == "truetype" else self.face.cid_of(gid)


@dataclass
class _Recorder:
    ops: list[Any] = field(default_factory=list)  # str | _TextOp
    xobjects: dict[str, Any] = field(default_factory=dict)
    extgstates: dict[str, Any] = field(default_factory=dict)
    fonts: set[str] = field(default_factory=set)


@dataclass
class _TextOp:
    node: ir.ShapedText
    #: 与 node.runs 对齐：每个 run 的 (_FontUse, [每个字形要不要 ActualText 的 cluster 标记])
    runs: list[tuple[_FontUse, list[bool]]]
    #: `paint.alpha < 1` 时的 ExtGState 资源名（`ca` = 文字的填充 alpha）；None = 不透明
    gs: str | None = None


def _subset_tag(used: dict[int, str]) -> str:
    h = hashlib.sha256(",".join(str(g) for g in sorted(used)).encode()).digest()
    return "".join(chr(ord("A") + b % 26) for b in h[:6])


# ---------------------------------------------------------------------------
# 写入器
# ---------------------------------------------------------------------------
class PdfWriter:
    def __init__(
        self,
        page: ir.Page,
        provider: HbFaceProvider,
        files: Mapping[str, bytes] | None = None,
    ) -> None:
        require("pikepdf", "fontTools", "uharfbuzz")
        import pikepdf

        self.page = ir.validate(page)
        self.provider = provider
        self.files: Mapping[str, bytes] = files or {}
        self.pdf = pikepdf.new()
        self.facts = WriteFacts(page=(page.width_pt, page.height_pt))
        self._fonts: dict[str, _FontUse] = {}  # 资源 key → use
        self._n = 0
        self._gs_cache: dict[tuple[float, float], Any] = {}
        self._table = ir.CAPABILITIES["pdf"]
        #: 透明组的 form 内容与它的 BBox，序列化时再变成 Form XObject
        self._forms_pending: list[tuple[_Recorder, tuple[float, float, float, float]]] = []
        #: (资源 key, page_index) → 已 copy_foreign 进来的 Form XObject（同一源两个实例共用一份对象）
        self._foreign_forms: dict[tuple[str, int], Any] = {}
        #: 外来 Pdf 要活到 save() 之后：qpdf 对流数据是按需读的
        self._foreign_docs: list[Any] = []
        #: 资源 key → 已写好的 Image XObject（+ 它的事实）
        self._image_objs: dict[str, tuple[Any, dict]] = {}
        #: 本文档已解码 / 已嵌入的位图像素总数（去重后按资源计），对 `raster.DOCUMENT_MAX_PIXELS` 判
        self._raster_pixels = 0

    # -- 资源 -------------------------------------------------------------
    def _name(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def _font_use(self, key: str) -> _FontUse:
        use = self._fonts.get(key)
        if use is None:
            res = self.page.resources[key]
            assert isinstance(res, ir.FontResource)
            try:
                face = self.provider.face_by_resource(res)
            except Exception as exc:  # noqa: BLE001 —— FontsUnavailable 之类要带 code 出去
                raise WriterError(
                    "font_identity",
                    str(exc),
                    {"face_id": res.face_id, "code": getattr(exc, "code", "")},
                ) from exc
            if face.kind not in ir.FONT_KINDS:
                raise WriterError("font_format", f"{res.face_id}: {face.kind}")
            use = _FontUse(face, f"F{len(self._fonts) + 1}")
            self._fonts[key] = use
        return use

    def _gs(self, rec: _Recorder, fill_alpha: float, stroke_alpha: float) -> str:
        import pikepdf

        key = (round(fill_alpha, 3), round(stroke_alpha, 3))
        name = f"GS{int(key[0] * 1000):04d}{int(key[1] * 1000):04d}"
        if name not in rec.extgstates:
            obj = self._gs_cache.get(key)
            if obj is None:
                obj = self.pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.ExtGState, ca=float(key[0]), CA=float(key[1])
                    )
                )
                self._gs_cache[key] = obj
            rec.extgstates[name] = obj
        return name

    def _check(self, op: str, object_id: str) -> None:
        cap = self._table[op]
        if cap.level == ir.CAP_UNSUPPORTED:
            raise UnsupportedCapability(op, cap.reason, object_id)

    # -- 节点 -------------------------------------------------------------
    def _emit(self, node: ir.Node, rec: _Recorder, ctm: ir.Matrix, depth: int) -> None:
        for op in ir.operations_of(node):
            self._check(op, getattr(node, "object_id", ""))
        if isinstance(node, ir.Path):
            rec.ops.append(self._path_ops(node, rec))
            self.facts.paths += 1
        elif isinstance(node, ir.ShapedText):
            rec.ops.append(self._text_op(node, rec))
            self.facts.text_objects += 1
        elif isinstance(node, ir.Group):
            self._group(node, rec, ctm, depth)
        elif isinstance(node, ir.ImportedPage):
            self._imported_page(node, rec, ctm)
        elif isinstance(node, ir.Image):
            self._image(node, rec)
        else:  # validate() 已经挡住不认识的节点；这里是最后一道
            raise UnsupportedCapability(type(node).__name__.lower(), "不认识的节点", "")

    # -- 面板 -------------------------------------------------------------
    def _source_bytes(self, node: ir.ImportedPage | ir.Image) -> tuple[ir.FileResource, bytes]:
        res = self.page.resources[node.resource]
        assert isinstance(res, ir.FileResource)
        data = self.files.get(node.resource)
        if data is None:
            raise WriterError(
                "source_bytes_missing",
                f"{node.resource}: 没有交进来字节（files 里没有这个 key）",
                {"resource": node.resource, "object_id": node.object_id},
            )
        sha = hashlib.sha256(data).hexdigest()
        if sha != res.sha256:
            raise WriterError(
                "source_identity",
                f"{res.source_id}: 字节 sha256 {sha[:12]} 与资源身份 {res.sha256[:12]} 不符",
                {"resource": node.resource, "object_id": node.object_id, "figure": res.source_id},
            )
        return res, data

    def _foreign_form(self, node: ir.ImportedPage, res: ir.FileResource, data: bytes):
        """源 PDF 的那一页 → 本文档里的 Form XObject（同一 (资源, 页) 只搬一次）。"""
        import io

        import pikepdf

        key = (node.resource, node.page_index)
        form = self._foreign_forms.get(key)
        if form is not None:
            return form
        try:
            src = pikepdf.open(io.BytesIO(data))
        except pikepdf.PasswordError as exc:
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: 源 PDF 加密，本轮不打开加密文件（RC-046）",
                {"figure": res.source_id, "object_id": node.object_id, "why": "encrypted"},
            ) from exc
        except (pikepdf.PdfError, ValueError, OSError) as exc:
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: 源 PDF 打不开: {exc}",
                {"figure": res.source_id, "object_id": node.object_id, "why": "broken"},
            ) from exc
        self._foreign_docs.append(src)
        # 只有 owner 密码（user 密码为空）的 PDF 走到这里就是打开了，按普通 PDF 导入（#516 裁决 B，ADR 0065 RC-046）：
        # 任何阅读器都无密码打开它，期刊 / 出版社发的 PDF 常见这种权限限制；qpdf 读时透明解密，搬进来的内容流与资源
        # 同未加密的原件相同，产物不加密、不带权限位。要 user 密码的在上面的 PasswordError 就拒了
        if node.page_index >= len(src.pages):
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: 只有 {len(src.pages)} 页，没有第 {node.page_index} 页",
                {"figure": res.source_id, "object_id": node.object_id, "why": "page_index"},
            )
        try:
            src_page = src.pages[node.page_index]
            src_form = src_page.as_form_xobject(handle_transformations=True)
            _keep_source_encoding(src_page, src_form)
            form = self.pdf.copy_foreign(src_form)
        except (pikepdf.PdfError, ValueError, RuntimeError) as exc:
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: 页面导入失败: {exc}",
                {"figure": res.source_id, "object_id": node.object_id, "why": "import"},
            ) from exc
        self._foreign_forms[key] = form
        return form

    def _imported_page(self, node: ir.ImportedPage, rec: _Recorder, ctm: ir.Matrix) -> None:
        res, data = self._source_bytes(node)
        form = self._foreign_form(node, res, data)
        bbox = tuple(float(v) for v in form.BBox)
        matrix = form.get("/Matrix")
        fm = tuple(float(v) for v in matrix) if matrix is not None else None
        visible = placement.visible_box(bbox, fm)
        if not (all(math.isfinite(v) for v in visible) and visible[2] > 0 and visible[3] > 0):
            # 语法上读得出、页盒却是零宽 / 零高（或 NaN）：这是源的问题，不许在 placement 里变成 ZeroDivisionError
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: 源页可见框退化 {visible}（页盒零宽 / 零高）",
                {"figure": res.source_id, "object_id": node.object_id, "why": "degenerate_box"},
            )
        pl = placement.place(
            visible,
            node.rect,
            crop=node.crop,
            rotate_cw_deg=node.rotate_cw_deg,
            flip_h=node.flip_h,
            flip_v=node.flip_v,
        )
        name = self._name("Fm")
        cx, cy, cw, ch = pl.clip
        inner = " ".join(
            [
                "q",
                " ".join(_num(v) for v in pl.matrix) + " cm",
                f"{_num(cx)} {_num(cy)} {_num(cw)} {_num(ch)} re W n",
                f"/{name} Do",
                "Q",
            ]
        )
        fact = {
            "object_id": node.object_id,
            "resource": node.resource,
            "page_index": node.page_index,
            "bbox": list(bbox),
            "form_matrix": list(fm) if fm else None,
            "visible": list(visible),
            "clip": list(pl.clip),
            "matrix": list(pl.matrix),
            "rect": list(node.rect),
            "crop": list(node.crop) if node.crop else None,
            "rotate_cw_deg": node.rotate_cw_deg,
            "flip_h": node.flip_h,
            "flip_v": node.flip_v,
            "opacity": node.opacity,
            "transparency_group": node.opacity < 1.0,
        }
        if node.opacity < 1.0:
            # 整页包成透明组：组内 alpha 从 1 起算，源页内部重叠不被二次压暗（RC-041）；仍是矢量
            wrap = _Recorder()
            wrap.xobjects[name] = form
            wrap.ops.append(inner)
            self._forms_pending.append((wrap, pl.bbox))
            wname = self._name("Grp")
            rec.xobjects[wname] = wrap
            rec.ops.append(f"q /{self._gs(rec, node.opacity, node.opacity)} gs /{wname} Do Q")
            self.facts.transparency_groups += 1
            fact["group"] = wname
        else:
            rec.xobjects[name] = form
            rec.ops.append(inner)
        self.facts.imported_pages.append(fact)

    def _charge_raster(self, node: ir.Image, res: ir.FileResource, width: int, height: int) -> None:
        """文档级像素预算：每个**不同**的位图资源在嵌入前记一次账，累计超过就拒——单张各判一次
        挡不住「很多张各自刚好在线下」的那种耗尽（Codex #463 第二轮 P2）。"""
        pixels = int(width) * int(height)
        if self._raster_pixels + pixels > raster.DOCUMENT_MAX_PIXELS:
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: 本文档位图像素累计 {self._raster_pixels + pixels} > 预算 "
                f"{raster.DOCUMENT_MAX_PIXELS}（已嵌 {len(self._image_objs)} 张）",
                {
                    "figure": res.source_id,
                    "object_id": node.object_id,
                    "why": "raster_budget_exceeded",
                    "pixels_used": self._raster_pixels,
                    "pixels_wanted": pixels,
                },
            )
        self._raster_pixels += pixels

    def _image_xobject(self, node: ir.Image, res: ir.FileResource, data: bytes):
        import pikepdf

        hit = self._image_objs.get(node.resource)
        if hit is not None:
            return hit
        try:
            # 两级预算都在**任何**解码之前按头里的尺寸记账（JPEG 读 SOF，其它 Pillow 懒打开）：单张
            # `SOURCE_MAX_PIXELS`、文档累计 `DOCUMENT_MAX_PIXELS`；超过就是结构化拒绝，一个像素不解——
            # JPEG 直通的「整张真解一遍」也排在记账之后（Codex #463 第三轮 P2）
            width, height = rasterio.header_size(data, res.kind)
            if width * height > raster.SOURCE_MAX_PIXELS:
                raise rasterio.RasterDecodeError(
                    "raster_too_large", f"{width}×{height} > 像素预算 {raster.SOURCE_MAX_PIXELS}"
                )
            self._charge_raster(node, res, width, height)
            jpeg = rasterio.jpeg_passthrough(data, res.kind, max_pixels=raster.SOURCE_MAX_PIXELS)
        except rasterio.RasterDecodeError as exc:
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: {exc}",
                {"figure": res.source_id, "object_id": node.object_id, "why": exc.code},
            ) from exc
        if jpeg is not None:
            obj = pikepdf.Stream(self.pdf, data)
            obj["/Type"] = pikepdf.Name.XObject
            obj["/Subtype"] = pikepdf.Name.Image
            obj["/Width"] = jpeg["width"]
            obj["/Height"] = jpeg["height"]
            obj["/ColorSpace"] = (
                pikepdf.Name.DeviceRGB if jpeg["components"] == 3 else pikepdf.Name.DeviceGray
            )
            obj["/BitsPerComponent"] = 8
            obj["/Filter"] = pikepdf.Name.DCTDecode
            fact = {
                "width": jpeg["width"],
                "height": jpeg["height"],
                "channels": jpeg["components"],
                "encoding": "dct",
                "smask": False,
            }
            hit = (self.pdf.make_indirect(obj), fact)
            self._image_objs[node.resource] = hit
            return hit
        try:
            buf = rasterio.decode(data, res.kind, max_pixels=raster.SOURCE_MAX_PIXELS)
        except rasterio.RasterDecodeError as exc:
            raise WriterError(
                "source_unreadable",
                f"{res.source_id}: {exc}",
                {"figure": res.source_id, "object_id": node.object_id, "why": exc.code},
            ) from exc
        rgb, alpha = buf.split_alpha()
        obj = pikepdf.Stream(self.pdf, rgb)
        obj["/Type"] = pikepdf.Name.XObject
        obj["/Subtype"] = pikepdf.Name.Image
        obj["/Width"] = buf.width
        obj["/Height"] = buf.height
        obj["/ColorSpace"] = pikepdf.Name.DeviceRGB
        obj["/BitsPerComponent"] = 8
        if alpha is not None:
            smask = pikepdf.Stream(self.pdf, alpha)
            smask["/Type"] = pikepdf.Name.XObject
            smask["/Subtype"] = pikepdf.Name.Image
            smask["/Width"] = buf.width
            smask["/Height"] = buf.height
            smask["/ColorSpace"] = pikepdf.Name.DeviceGray
            smask["/BitsPerComponent"] = 8
            obj["/SMask"] = self.pdf.make_indirect(smask)
        fact = {
            "width": buf.width,
            "height": buf.height,
            "channels": buf.channels,
            "encoding": "flate",
            "smask": alpha is not None,
            "dpi": buf.dpi,
        }
        hit = (self.pdf.make_indirect(obj), fact)
        self._image_objs[node.resource] = hit
        return hit

    def _image(self, node: ir.Image, rec: _Recorder) -> None:
        res, data = self._source_bytes(node)
        obj, fact = self._image_xobject(node, res, data)
        # Image XObject 画进单位正方形：可见框就是 (0, 0, 1, 1)
        pl = placement.place(
            (0.0, 0.0, 1.0, 1.0),
            node.rect,
            crop=node.crop,
            rotate_cw_deg=node.rotate_cw_deg,
            flip_h=node.flip_h,
            flip_v=node.flip_v,
        )
        name = self._name("Im")
        rec.xobjects[name] = obj
        parts = ["q"]
        if node.opacity < 1.0:
            parts.append(f"/{self._gs(rec, node.opacity, node.opacity)} gs")
        cx, cy, cw, ch = pl.clip
        parts += [
            " ".join(_num(v) for v in pl.matrix) + " cm",
            f"{_num(cx)} {_num(cy)} {_num(cw)} {_num(ch)} re W n",
            f"/{name} Do",
            "Q",
        ]
        rec.ops.append(" ".join(parts))
        self.facts.images.append(
            {
                **fact,
                "object_id": node.object_id,
                "resource": node.resource,
                "matrix": list(pl.matrix),
                "clip": list(pl.clip),
                "rect": list(node.rect),
                "opacity": node.opacity,
                "rotate_cw_deg": node.rotate_cw_deg,
                "flip_h": node.flip_h,
                "flip_v": node.flip_v,
            }
        )

    def _group(self, node: ir.Group, rec: _Recorder, ctm: ir.Matrix, depth: int) -> None:
        self.facts.groups += 1
        inner = ["q"]
        if node.transform != ir.IDENTITY:
            inner.append(" ".join(_num(v) for v in node.transform) + " cm")
        if node.clip is not None:
            inner.append(
                _path_segments(node.clip.segments)
                + (" W* n" if node.clip.fill_rule == "evenodd" else " W n")
            )
        local = ir.compose(node.transform, ctm)
        if node.opacity < 1.0:
            # 透明组：children 进一个带 /Group 的 Form XObject（组内 alpha 从 1 起算），整组以 opacity 合成。
            # form 的内容空间 = 父空间（cm 写在 form 里面），BBox 取页面矩形经父空间逆变换的包围盒。
            form_rec = _Recorder()
            form_rec.ops.append("\n".join(inner))
            for child in node.children:
                self._emit(child, form_rec, local, depth + 1)
            form_rec.ops.append("Q")
            bbox = _page_bbox_in(ctm, self.page)
            self._forms_pending.append((form_rec, bbox))
            name = self._name("Grp")
            rec.xobjects[name] = form_rec  # save() 时再序列化成 Form XObject
            rec.ops.append(f"q /{self._gs(rec, node.opacity, node.opacity)} gs /{name} Do Q")
            self.facts.transparency_groups += 1
            return
        rec.ops.append("\n".join(inner))
        for child in node.children:
            self._emit(child, rec, local, depth + 1)
        rec.ops.append("Q")

    def _path_ops(self, node: ir.Path, rec: _Recorder) -> str:
        parts = ["q"]
        fill_a = node.fill.alpha if node.fill is not None else 1.0
        stroke_a = node.stroke.paint.alpha if node.stroke is not None else 1.0
        if fill_a < 1.0 or stroke_a < 1.0:
            parts.append(f"/{self._gs(rec, fill_a, stroke_a)} gs")
        if node.fill is not None:
            parts.append(f"{_rgb(node.fill.rgb)} rg")
        if node.stroke is not None:
            s = node.stroke
            parts.append(f"{_rgb(s.paint.rgb)} RG")
            parts.append(f"{_num(s.width)} w")
            parts.append(
                f"{ir.LINE_CAPS.index(s.cap)} J {ir.LINE_JOINS.index(s.join)} j {_num(s.miter_limit)} M"
            )
            if s.dash:
                parts.append("[" + " ".join(_num(v) for v in s.dash) + f"] {_num(s.dash_phase)} d")
        parts.append(_path_segments(node.segments))
        star = "*" if node.fill_rule == "evenodd" else ""
        if node.fill is not None and node.stroke is not None:
            parts.append(f"B{star}")
        elif node.fill is not None:
            parts.append(f"f{star}")
        else:
            parts.append("S")
        parts.append("Q")
        return " ".join(parts)

    def _text_op(self, node: ir.ShapedText, rec: _Recorder) -> _TextOp:
        runs: list[tuple[_FontUse, list[bool]]] = []
        for run in node.runs:
            use = self._font_use(run.font)
            rec.fonts.add(run.font)
            flags: list[bool] = []
            glyphs = run.glyphs
            i = 0
            while i < len(glyphs):
                # 一个 cluster = 一个带原文的字形 + 紧随其后的续字形（原文为空）
                j = i + 1
                while j < len(glyphs) and glyphs[j].text == "":
                    j += 1
                cluster = glyphs[i:j]
                conflict = use.note(cluster[0].gid, cluster[0].text)
                for g in cluster[1:]:
                    use.note(g.gid, "")
                need = (
                    run.actual_text is None
                    and (len(cluster) > 1 or conflict)
                    and bool(cluster[0].text)
                )
                flags.extend([need] + [False] * (len(cluster) - 1))
                if any(g.gid == 0 for g in cluster):
                    self.facts.notdef_codes += 1
                i = j
            runs.append((use, flags))
        gs = self._gs(rec, node.paint.alpha, node.paint.alpha) if node.paint.alpha < 1.0 else None
        return _TextOp(node, runs, gs)

    # -- 序列化 -----------------------------------------------------------
    def _text_stream(self, op: _TextOp) -> str:
        """一个 ShapedText → `BT … ET`。

        **一个 ActualText 段里只放一个 `TJ`**：PDFium 对跨多个文字对象的 Span 会把 ActualText 与
        对象自己的 ToUnicode 各吐一遍（实测 pypdfium2 5.13 / PDFium 153：`x̃y x̃`），单个 `TJ` 的 Span
        三家读取器（PDFium / poppler / pdfminer）都对。代价：段内不能换 `Ts`，所以多字形 cluster
        里标记字形的 `y_offset` 不写（标记落在字形自己的默认高度）；段外的字形照常按 `y_offset` 抬。
        """
        node = op.node
        # 整个文字对象包在 q/Q 里：alpha 经 ExtGState（`ca`，文字用填充色）在 BT 之前设，
        # ET 之后随 Q 还原，不污染后面的对象
        out = ["q"]
        if op.gs is not None:
            out.append(f"/{op.gs} gs")
        out += ["BT", f"{_rgb(node.paint.rgb)} rg"]
        pen = 0.0
        for run, (use, flags) in zip(node.runs, op.runs):
            face = use.face
            k = 1000.0 / face.upem
            out.append(f"/{use.name} {_num(run.size)} Tf")
            out.append(f"1 0 0 1 {_num(node.x + pen)} {_num(node.y)} Tm")
            cur_rise: float | None = None
            tj: list[str] = []

            def set_rise(rise: float) -> None:
                nonlocal cur_rise
                if cur_rise is None or abs(rise - cur_rise) > 1e-6:
                    flush()
                    out.append(f"{_num(rise)} Ts")
                    cur_rise = rise

            def flush() -> None:
                if tj:
                    out.append("[" + " ".join(tj) + "] TJ")
                    tj.clear()

            def put(g: ir.Glyph) -> None:
                nonlocal pen
                if g.x_offset:
                    tj.append(_num(-g.x_offset * k))
                tj.append(f"<{use.code(g.gid):04X}>")
                adj = (face.advance(g.gid) + g.x_offset - g.x_advance) * k
                if abs(adj) > 0.01:
                    tj.append(_num(adj))
                pen += g.x_advance * run.size / face.upem

            glyphs = run.glyphs
            if run.actual_text is not None:
                # run 级 ActualText（合成上下标）：整段一个 TJ，基线 = run.rise
                set_rise(run.rise)
                flush()  # 段外已排好的字形先落，Span 里只剩这一段
                out.append(f"/Span <</ActualText <FEFF{_hex_utf16(run.actual_text)}>>> BDC")
                self.facts.actualtext_spans += 1
                for g in glyphs:
                    put(g)
                flush()
                out.append("EMC")
                continue
            i = 0
            while i < len(glyphs):
                j = i + 1
                while j < len(glyphs) and glyphs[j].text == "":
                    j += 1
                cluster = glyphs[i:j]
                if flags[i]:
                    # cluster 级 ActualText：一个 TJ，基线取 cluster 首字形的（标记的 y_offset 不写）
                    set_rise(run.rise + cluster[0].y_offset * run.size / face.upem)
                    flush()
                    out.append(f"/Span <</ActualText <FEFF{_hex_utf16(cluster[0].text)}>>> BDC")
                    self.facts.actualtext_spans += 1
                    for g in cluster:
                        put(g)
                    flush()
                    out.append("EMC")
                else:
                    for g in cluster:
                        set_rise(run.rise + g.y_offset * run.size / face.upem)
                        put(g)
                i = j
            flush()
        out += ["ET", "Q"]
        return "\n".join(out)

    def _build_fonts(self) -> None:
        import pikepdf

        for key in sorted(self._fonts):
            use = self._fonts[key]
            face = use.face
            data, remap, n_sub = face.subset(set(use.used))
            use.remap = remap
            tag = _subset_tag(use.used)
            base = f"{tag}+{face.resource.postscript_name}"
            m = face.descriptor_metrics()
            desc: dict = {
                "/Type": pikepdf.Name.FontDescriptor,
                "/FontName": pikepdf.Name("/" + base),
                "/Flags": m["flags"],
                "/FontBBox": pikepdf.Array(m["bbox"]),
                "/ItalicAngle": m["italic_angle"],
                "/Ascent": m["ascent"],
                "/Descent": m["descent"],
                "/CapHeight": m["cap_height"],
                "/StemV": m["stem_v"],
            }
            if face.kind == "truetype":
                desc["/FontFile2"] = self.pdf.make_indirect(
                    pikepdf.Stream(self.pdf, data, Length1=len(data))
                )
            else:
                desc["/FontFile3"] = self.pdf.make_indirect(
                    pikepdf.Stream(self.pdf, data, Subtype=pikepdf.Name.CIDFontType0C)
                )
            descriptor = self.pdf.make_indirect(pikepdf.Dictionary(desc))
            widths = {use.code(g): face.advance(g) * 1000.0 / face.upem for g in use.used}
            w_array: list = []
            for code in sorted(widths):
                w_array += [code, [round(widths[code], 3)]]
            cid: dict = {
                "/Type": pikepdf.Name.Font,
                "/BaseFont": pikepdf.Name("/" + base),
                "/CIDSystemInfo": pikepdf.Dictionary(
                    Registry=pikepdf.String("Adobe"),
                    Ordering=pikepdf.String("Identity"),
                    Supplement=0,
                ),
                "/FontDescriptor": descriptor,
                "/DW": 1000,
                "/W": pikepdf.Array(w_array),
            }
            if face.kind == "truetype":
                cid["/Subtype"] = pikepdf.Name.CIDFontType2
                cid["/CIDToGIDMap"] = pikepdf.Name.Identity
            else:
                cid["/Subtype"] = pikepdf.Name.CIDFontType0
            cid_font = self.pdf.make_indirect(pikepdf.Dictionary(cid))
            tounicode = self.pdf.make_indirect(
                pikepdf.Stream(self.pdf, _tounicode({use.code(g): t for g, t in use.used.items()}))
            )
            use.obj = self.pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type0,
                    BaseFont=pikepdf.Name("/" + base),
                    Encoding=pikepdf.Name("/Identity-H"),
                    DescendantFonts=pikepdf.Array([cid_font]),
                    ToUnicode=tounicode,
                )
            )
            self.facts.fonts.append(
                {
                    "resource": use.name,
                    "face_id": face.resource.face_id,
                    "sha256": face.resource.sha256,
                    "kind": face.kind,
                    "base_font": base,
                    "glyphs_in_source": len(face._order),
                    "glyphs_in_subset": n_sub,
                    "used_glyphs": len(use.used),
                    "font_program_bytes": len(data),
                    "tounicode_entries": sum(1 for t in use.used.values() if t),
                }
            )

    def _serialize(self, rec: _Recorder) -> bytes:
        lines = [self._text_stream(op) if isinstance(op, _TextOp) else op for op in rec.ops]
        return ("\n".join(lines) + "\n").encode("latin-1")

    def _resources(self, rec: _Recorder):
        import pikepdf

        res = pikepdf.Dictionary()
        if rec.xobjects:
            res["/XObject"] = pikepdf.Dictionary(
                {
                    "/" + k: (self._form(v) if isinstance(v, _Recorder) else v)
                    for k, v in rec.xobjects.items()
                }
            )
        if rec.extgstates:
            res["/ExtGState"] = pikepdf.Dictionary({"/" + k: v for k, v in rec.extgstates.items()})
        if rec.fonts:
            res["/Font"] = pikepdf.Dictionary(
                {"/" + self._fonts[k].name: self._fonts[k].obj for k in sorted(rec.fonts)}
            )
        return res

    def _form(self, form_rec: _Recorder):
        import pikepdf

        bbox = next(b for r, b in self._forms_pending if r is form_rec)
        stream = pikepdf.Stream(self.pdf, self._serialize(form_rec))
        stream["/Type"] = pikepdf.Name.XObject
        stream["/Subtype"] = pikepdf.Name.Form
        stream["/BBox"] = pikepdf.Array([float(v) for v in bbox])
        stream["/Resources"] = self._resources(form_rec)
        stream["/Group"] = pikepdf.Dictionary(
            S=pikepdf.Name.Transparency, CS=pikepdf.Name.DeviceRGB, I=True, K=False
        )
        return self.pdf.make_indirect(stream)

    def write(self, out: Path) -> WriteFacts:
        import pikepdf

        rec = _Recorder()
        if self.page.background is not None:
            self._check("page_background", "")
            w, h = self.page.width_pt, self.page.height_pt
            rec.ops.append(f"q {_rgb(self.page.background)} rg 0 0 {_num(w)} {_num(h)} re f Q")
        for child in self.page.children:
            self._emit(child, rec, ir.IDENTITY, 1)
        self._build_fonts()
        content = self._serialize(rec)
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=pikepdf.Array([0, 0, float(self.page.width_pt), float(self.page.height_pt)]),
            Resources=self._resources(rec),
            Contents=self.pdf.make_indirect(pikepdf.Stream(self.pdf, content)),
        )
        self.pdf.pages.append(pikepdf.Page(page))
        out = Path(out)
        _compress_unfiltered(self.pdf)
        # compress_streams=False：qpdf 的 compress_streams 会把源里带 predictor 的 Flate 图片流解码再重压
        # （与 stream_decode_level 无关，qpdf 12.3.2 实测：29 张图的海报 76 ms、体积不变）。要压的只有
        # 确实没过滤的流，上一行已经压过；其余流原样照搬源的编码。
        self.pdf.save(
            str(out),
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            compress_streams=False,
            deterministic_id=True,
            min_version="1.5",
        )
        for doc in self._foreign_docs:  # 流数据已经写进文件，外来文档可以放了
            doc.close()
        self._foreign_docs.clear()
        data = out.read_bytes()
        self.facts.sha256 = hashlib.sha256(data).hexdigest()
        self.facts.bytes = len(data)
        from .hbshaper import versions

        self.facts.versions = versions()
        return self.facts


#: 小于这个字节数的未过滤流不压（Flate 头 + adler32 就占掉收益）。
_COMPRESS_MIN_BYTES = 64


def _keep_source_encoding(src_page, form) -> None:
    """qpdf 的 `as_form_xobject` 把页的内容流**解码成明文**放进新 Form（海报 399 KB → 2.7 MB），save 时再整段重压。
    源页只有一段带过滤器的内容流、且解出来与 Form 的明文逐字节相同时，Form 直接用源的已编码字节 + 同一组
    /Filter /DecodeParms——解码后是同一份内容流，一个操作符都不变（渲染与文字层逐字节相同）。多段内容流
    （段界只保证落在词法边界上，压缩流不能首尾相接）、没有过滤器、或解不出来的，原样留给 `_compress_unfiltered`。
    改的是**源文档里**的这个 Form，`copy_foreign` 之前做，不牵扯跨文档对象。"""
    import pikepdf

    contents = src_page.obj.get("/Contents")
    if isinstance(contents, pikepdf.Array):
        if len(contents) != 1:
            return
        contents = contents[0]
    if not isinstance(contents, pikepdf.Stream) or contents.get("/Filter") is None:
        return
    try:
        if contents.read_bytes() != form.read_bytes():
            return
        form.write(
            contents.read_raw_bytes(),
            filter=contents.get("/Filter"),
            decode_parms=contents.get("/DecodeParms"),
        )
    except pikepdf.PdfError:
        return  # 过滤器 qpdf 解不开：留明文，照常由 `_compress_unfiltered` 压


def _compress_unfiltered(pdf) -> None:
    """文档里没有过滤器的流（本模块写的内容流 / 字体程序 / 外来页 Form 的明文）逐个 Flate 压，大流在
    `deflate.zlib_compress` 的线程池里分块并行；压了不变小的不动；XMP /Metadata 保持明文（阅读器与归档工具
    按明文读它）。已经有过滤器的流（源的图片、源的内容流）一个字节不碰——那正是不交给 qpdf 的
    `compress_streams` 的原因。按 `pdf.objects` 的顺序处理：同一输入同一份输出。"""
    import pikepdf

    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream) or obj.get("/Filter") is not None:
            continue
        if obj.get("/Type") == pikepdf.Name.Metadata:
            continue
        data = obj.read_raw_bytes()
        if len(data) < _COMPRESS_MIN_BYTES:
            continue
        packed = deflate.zlib_compress(data)
        if len(packed) < len(data):
            obj.write(packed, filter=pikepdf.Name.FlateDecode)


def _path_segments(segments: tuple) -> str:
    parts = []
    for seg in segments:
        op = seg[0]
        if op == "M":
            parts.append(f"{_num(seg[1])} {_num(seg[2])} m")
        elif op == "L":
            parts.append(f"{_num(seg[1])} {_num(seg[2])} l")
        elif op == "C":
            parts.append(" ".join(_num(v) for v in seg[1:]) + " c")
        elif op == "Z":
            parts.append("h")
    return " ".join(parts)


def _page_bbox_in(ctm: ir.Matrix, page: ir.Page) -> tuple[float, float, float, float]:
    """页面矩形经 `ctm` 的逆映射后的包围盒（透明组 form 的 BBox）。"""
    a, b, c, d, e, f = ctm
    det = a * d - b * c
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    ie, if_ = -(e * ia + f * ic), -(e * ib + f * id_)
    inv = (ia, ib, ic, id_, ie, if_)
    pts = [ir.apply(inv, x, y) for x in (0.0, page.width_pt) for y in (0.0, page.height_pt)]
    return (
        min(p[0] for p in pts),
        min(p[1] for p in pts),
        max(p[0] for p in pts),
        max(p[1] for p in pts),
    )


def _tounicode(mapping: dict[int, str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    entries = sorted(mapping.items())
    for i in range(0, len(entries), 100):
        chunk = entries[i : i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for code, text in chunk:
            lines.append(f"<{code:04X}> <{_hex_utf16(text)}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return ("\n".join(lines) + "\n").encode("ascii")


def write_pdf(
    page: ir.Page,
    out: Path,
    provider: HbFaceProvider,
    files: Mapping[str, bytes] | None = None,
) -> WriteFacts:
    """IR → PDF 文件。`provider` 解析 `FontResource` → 已加载的脸（按 sha256 身份）；`files` 是
    `FileResource` key → 冻结字节（`sources.read_frozen()` 核过 hash 的那份），面板要它。"""
    return PdfWriter(page, provider, files).write(out)
