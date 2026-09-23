"""RenderCore 面的 pdfbackend 契约实现——19 个导出项 + Canvas 面，同签名同返回结构（统一实施包 U08，ADR 0067）。

`pdfbackend/__init__.py` 是契约层：它按 `TAVOTTO_RENDER_BACKEND` 在 `pymupdf_backend`（默认）与本模块之间
**选一条**，选定之后不换、不在失败时试另一个（06 §1）。本模块只做「用新核心兑现同一份契约」，一个字都不
import 旧后端（D03；`tests/test_rendercore_model.py` 钉着）。

| 契约项 | 这里的实现 | 与旧后端的有意差异（记进 U08 交接表） |
|---|---|---|
| `BACKEND_NAME` / `BACKEND_VERSION` | `rendercore` / `rendercore.BACKEND_VERSION` | 名字不同 → `/api/render` 旧缓存键天然失效 |
| `CANVAS_TEXT_FAMILIES` / `COVERAGE_MAX_CP` / `mm2pt` / `hex2rgb` | `typography` / `ir` 同名 | 无 |
| `text_width` / `text_plan` / `missing_glyphs` / `coverage_ranges` | `typography` 同名函数，脸来自批准字体注册表 | `fallback` 层恒空（ADR 0060 §1：没有隐式回退脸） |
| `probe_asset(kind="pdf")` | `RenderHost.size()`（PDFium 可见框 + /Rotate，**乘 /UserUnit**；不加载页，按文件指纹复用，ADR 0077） | 无（实测 PyMuPDF 1.28.2 的 `page.rect` 同样乘 /UserUnit；忽略它的只是 PDFium 的 `get_size()`，child 已补上那一次） |
| `probe_asset(kind="raster")` | `rasterio.header_info()`（不解码） | 无（`alpha` 含调色板 tRNS） |
| `pdf_fonts` | pikepdf 读首页 /Font，递归进 Form XObject（有预算） | 无 |
| `render_preview_png` | `RenderHost.render(width_px=…)` + `raster.encode_png` | 栅格器换成 PDFium（像素按平台分基线） |
| `compare_png` | `rasterio.decode` → RGBA → `pixelmetrics.rgba_metrics` | 尺子同一份，只换解码器 |
| `compose()` → `Canvas` | `plan.compile_page` → `pdfwriter`（Canonical PDF）→ child 栅格一次 → PNG / TIFF 同一 buffer | opacity < 1 / flip 保矢量；文字基线按批准度量（ADR 0060 §4） |
| `original_pdf` | 矢量：pikepdf 整页搬第一页（copy_foreign，不重画）；位图：一页 Image XObject 铺满 `page_pt` | 无 |
| `original_png` | PNG 源逐字节 `copyfile`；JPEG 转码不重采样；矢量源 child 按 ppi 栅格 | 矢量源的 PNG 写真实 ppi 的 pHYs（旧后端写 96） |
| `original_tiff` | 同上，容器换 `raster.write_tiff`；位图源只写 `dpi_meta` | 无 |
| `annotate_asset` | 标注编成一页 IR → 覆盖到原 PDF 首页（pikepdf `add_overlay`，原内容流不动）；PNG 由注好的 PDF 栅格 | 旧后端 incremental save，这里整份重写（内容不变） |

产品导出路（`app._export_produce`）在候选后端下**不经 `compose()`**，直接走 `job.produce` +
`sources.ExecutionSourceResolver`（回执随源产物进 RenderPlan）；`Canvas` 面是给 RC-002 合同与老调用形态的
适配器，同一套编译 / 写入 / 栅格函数，不是第二套实现。

进程级共享：一份字体注册表（`provider()`）、一个 render child（`host()` = `renderhost.shared()`）、一个预览
缓存（`preview_cache()`）。候选包 / 字体缺席时在**第一次用到**那一刻抛 `CandidatePackagesMissing` /
`FontsUnavailable`——不静默退回旧后端（无静默回退政策，ADR 0067）。
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .. import pixelmetrics
from . import BACKEND_NAME, BACKEND_VERSION, ir, plan, raster, rasterio, typography
from .hbshaper import HbFaceProvider
from .ir import hex2rgb, mm2pt
from .preview import PreviewCache
from .renderhost import RenderHost
from .sources import FingerprintMemo, FrozenSource, SourceError
from .typography import CANVAS_TEXT_FAMILIES, COVERAGE_MAX_CP

__all__ = [
    "BACKEND_NAME",
    "BACKEND_VERSION",
    "CANVAS_TEXT_FAMILIES",
    "COVERAGE_MAX_CP",
    "Canvas",
    "annotate_asset",
    "compare_png",
    "compose",
    "coverage_ranges",
    "hex2rgb",
    "host",
    "missing_glyphs",
    "mm2pt",
    "original_pdf",
    "original_png",
    "original_tiff",
    "pdf_fonts",
    "prewarm",
    "preview_cache",
    "probe_asset",
    "provider",
    "render_preview_png",
    "reset_for_tests",
    "text_plan",
    "text_width",
]

#: `pdf_fonts` 递归进 Form XObject 的预算（深度 / 访问过的 form 数）：恶意自引用的资源树不许让它无限走。
FONT_SCAN_MAX_DEPTH = 8
FONT_SCAN_MAX_FORMS = 256

# ---------------------------------------------------------------------------
# 进程级共享服务（懒建、一个进程一份）
# ---------------------------------------------------------------------------
_PROVIDER: HbFaceProvider | None = None
_PREVIEW: PreviewCache | None = None
_LOCK = threading.Lock()
#: `probe_asset` 的派生值表：素材库每次 `/api/panels` 都把每个素材探一遍（`app.scan_panels`），文件没动就不再
#: 问 child——判「没动」的只有 `sources.file_fingerprint`，身份仍是字节 hash（这里不产身份）。
_PROBES = FingerprintMemo()


def provider() -> HbFaceProvider:
    """批准字体注册表 → HarfBuzz 脸（一个进程一份）。缺候选包 / 缺字体在这里抛，不退回别的脸。"""
    global _PROVIDER
    with _LOCK:
        if _PROVIDER is None:
            from .fonts import FontRegistry, FontsUnavailable
            from .hbshaper import require

            require("pikepdf", "fontTools", "uharfbuzz")
            reg = FontRegistry.discover()
            if reg.missing:
                raise FontsUnavailable(
                    "face_missing",
                    f"批准字体不全：缺 {reg.missing}（先跑 scripts/fetch_fonts.py）",
                )
            _PROVIDER = HbFaceProvider(reg)
        return _PROVIDER


def host() -> RenderHost:
    """进程级共享的 render child（ADR 0066）。"""
    from .renderhost import shared

    return shared()


def preview_cache(cache_dir: Path | None = None, *, max_bytes: int | None = None) -> PreviewCache:
    """`/api/render` 的预览缓存：一个进程一份，**跟着 `cache_dir` 走**——目录变了（测试逐用例换
    临时目录、用户换数据目录）就重建，不让第一次的目录把后面的请求锁死。"""
    global _PREVIEW
    with _LOCK:
        if _PREVIEW is None or (cache_dir is not None and _PREVIEW.cache_dir != Path(cache_dir)):
            if cache_dir is None:
                raise ValueError("第一次建预览缓存必须给 cache_dir")
            kwargs = {}
            if max_bytes is not None:
                kwargs["max_bytes"] = int(max_bytes)
            _PREVIEW = PreviewCache(Path(cache_dir), host(), **kwargs)
        return _PREVIEW


def prewarm() -> threading.Thread:
    """冷启动三件（起 render child ~70 ms、字体注册表 ~90 ms、第一次排版载 CJK 脸 ~60 ms）放到后台线程里先做掉，
    用户的第一次预览 / 导出不再替它们付账。由 `pdfbackend.warm()` 在服务启动时调。失败只记日志：同一个错误
    会在第一次真用到时原样抛出（无静默回退——这里不替谁选别的后端，也不吞掉那一次）。"""

    def run() -> None:
        try:
            host().ping()
            typography.text_width("图 Aa", 10.0, _faces("serif", False, False))
        except Exception as exc:  # noqa: BLE001 —— 预热不是权威路径，真用到时同一个错误会再抛
            import logging

            logging.getLogger("tavotto").warning("RenderCore 预热未完成：%s", exc)

    t = threading.Thread(target=run, name="rendercore-prewarm", daemon=True)
    t.start()
    return t


def reset_for_tests() -> None:
    """丢掉进程级共享实例（字体注册表 / 预览缓存）；render child 由 `renderhost.shutdown_shared()` 收。"""
    global _PROVIDER, _PREVIEW
    with _LOCK:
        _PROVIDER = None
        _PREVIEW = None
    _PROBES.clear()


# ---------------------------------------------------------------------------
# 文字度量（同签名）
# ---------------------------------------------------------------------------
def _faces(family: object, bold: bool, italic: bool) -> typography.FaceSet:
    return typography.faces_for(provider(), family, bold, italic)


def text_width(
    s: str,
    size_pt: float,
    bold: bool = False,
    italic: bool = False,
    family: object = "serif",
) -> float:
    return typography.text_width(s, size_pt, _faces(family, bold, italic))


def text_plan(
    s: str, family: object = "serif", bold: bool = False, italic: bool = False
) -> list[tuple[str, str]]:
    return typography.text_plan(s, _faces(family, bold, italic))


def missing_glyphs(
    s: str, family: object = "serif", bold: bool = False, italic: bool = False
) -> list[str]:
    return typography.missing_glyphs(s, _faces(family, bold, italic))


def coverage_ranges() -> dict:
    """`rendercore/canvas_coverage.json`（候选表）的唯一产生者。

    `primary` 是**12 张 Liberation 脸 cmap 的交集**（3 族 × 常规 / 粗 / 斜 / 粗斜）：前端读的是一张与族、
    字重无关的表，它说「画得出」就得任何一张脸都画得出——旧后端的 base-14 各脸同一字符集，这条不用算；
    Liberation 各脸差 16 个码位（U06 evidence 实测），交集才是那句能力承诺。`cjk` 只有一张脸；
    `fallback` 恒空是能力边界不是漏写（ADR 0060 §1）。
    """
    from .. import glyphplan

    prov = provider()
    faces = [
        prov.face_for(family, bold, italic)
        for family in CANVAS_TEXT_FAMILIES
        for bold in (False, True)
        for italic in (False, True)
    ]
    serif = _faces("serif", False, False)
    lo, hi = 0x20, COVERAGE_MAX_CP
    return {
        "primary": glyphplan.ranges_of(lambda cp: all(f.covers(cp) for f in faces), lo, hi),
        "cjk": glyphplan.ranges_of(typography.coverage(serif).cjk, lo, hi),
        "fallback": [],
    }


# ---------------------------------------------------------------------------
# 探测 / 预览 / 比较
# ---------------------------------------------------------------------------
def _kind_of(path: Path) -> str:
    kind = Path(path).suffix.lstrip(".").lower()
    return "jpg" if kind == "jpeg" else kind


def probe_asset(path: Path, kind: str) -> dict:
    """同旧后端的返回结构。PDF 的尺寸经 render child 的 `size`（PDFium 可见框，含 /Rotate，**乘 /UserUnit**；
    不加载页）。文件没动（`sources.file_fingerprint` 相同）就复用上一次的结果，每次回一份新 dict。"""
    path = Path(path)
    if kind == "pdf":

        def measure() -> dict:
            r = host().size(path)
            return {"kind": "pdf", "w_pt": float(r["width_pt"]), "h_pt": float(r["height_pt"])}

    else:

        def measure() -> dict:
            info = rasterio.header_info(path.read_bytes(), _kind_of(path))
            return {
                "kind": "raster",
                "px_w": int(info["width"]),
                "px_h": int(info["height"]),
                "alpha": bool(info["alpha"]),
            }

    return dict(_PROBES.get_or_compute(path, measure, key=kind))


def _strip_subset(base: str) -> str:
    if "+" in base and len(base.split("+", 1)[0]) == 6:
        return base.split("+", 1)[1]
    return base


def pdf_fonts(path: Path) -> list[str]:
    """首页真正引用的字体名（去子集前缀、去重、保序）：页面 /Resources/Font，再按资源顺序递归进
    Form XObject（有深度 / 数量预算）。读不出就抛——与旧后端同一政策，由调用方决定跳过。"""
    import pikepdf

    names: list[str] = []
    seen_forms: set[int] = set()

    def visit(resources, depth: int) -> None:
        if resources is None or depth > FONT_SCAN_MAX_DEPTH:
            return
        fonts = resources.get("/Font")
        if fonts is not None:
            for _key, font in fonts.items():
                base = str(font.get("/BaseFont", "")).lstrip("/")
                base = _strip_subset(base)
                if base and base not in names:
                    names.append(base)
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return
        for _key, xobj in xobjects.items():
            if len(seen_forms) >= FONT_SCAN_MAX_FORMS:
                return
            if str(xobj.get("/Subtype", "")) != "/Form":
                continue
            ident = xobj.objgen if xobj.is_indirect else id(xobj)
            if ident in seen_forms:
                continue
            seen_forms.add(ident)
            visit(xobj.get("/Resources"), depth + 1)

    with pikepdf.open(str(path)) as pdf:
        if len(pdf.pages) == 0:
            return []
        visit(pdf.pages[0].obj.get("/Resources"), 0)
    return names


def render_preview_png(path: Path, width_px: int, out: Path) -> None:
    """矢量面板首页 → 指定像素宽度的 PNG（白底）。缓存归 `preview_cache()`；这里只是那一次栅格。"""
    buf = host().render(Path(path), width_px=int(width_px), transparent=False)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raster.encode_png(buf))


def _rgba_of(path: Path) -> tuple[bytes, tuple[int, int]]:
    buf = rasterio.decode(Path(path).read_bytes(), _kind_of(path))
    if buf.channels == 4:
        return buf.packed(), (buf.width, buf.height)
    rgb = buf.packed()
    out = bytearray(buf.width * buf.height * 4)
    out[0::4] = rgb[0::3]
    out[1::4] = rgb[1::3]
    out[2::4] = rgb[2::3]
    out[3::4] = b"\xff" * (buf.width * buf.height)
    return bytes(out), (buf.width, buf.height)


def compare_png(baseline: Path, candidate: Path) -> dict:
    """同旧后端：逐 RGBA 通道、底噪 3、三指标——尺子在 `pixelmetrics`，这里只换解码器。"""
    a, size_a = _rgba_of(baseline)
    b, size_b = _rgba_of(candidate)
    return pixelmetrics.rgba_metrics(a, b, size_a, size_b)


# ---------------------------------------------------------------------------
# Canvas 面（RC-002）
# ---------------------------------------------------------------------------
class _PathResolver:
    """`Canvas.place()` 的 `resolve_panel` 回调 → 冻结源：路径是「去哪读」，身份是字节 hash。"""

    def __init__(self) -> None:
        self.paths: dict[str, Path] = {}

    def resolve(self, obj: dict) -> FrozenSource:
        from ..engine import figcapture

        oid = str(obj.get("id", ""))
        path = self.paths.get(oid)
        if path is None:
            raise SourceError("source_missing", f"{oid} 没有经 place() 解析过", {"figure": oid})
        if not Path(path).is_file():
            raise SourceError("source_missing", f"{oid}: {path} 不存在", {"figure": oid})
        kind = _kind_of(path)
        if kind not in ir.FILE_KINDS:
            raise SourceError(
                "source_kind_unsupported", f"{oid}: {kind!r} 不是可放置的源", {"figure": oid}
            )
        art = figcapture.source_artifact_from_file(
            path, source_id=oid, origin=figcapture.ORIGIN_STATIC
        )
        return FrozenSource(artifact=art, path=Path(path))


class Canvas:
    """一页合成画布（旧 facade 同一用法）：`place()` 逐个记对象，`save_pdf()` 编译一次写 Canonical PDF，
    `save_png()` / `save_tiff()` 把**同一份** PDF 交给 render child 按 dpi 栅格一次、两个容器同一 buffer
    （RC-002 must_fail：save_png 不重新解析另一份来源）。`close()` 幂等，删自己的临时目录。"""

    def __init__(self, page_w_mm: float, page_h_mm: float, transparent: bool = False):
        self._w_mm, self._h_mm = float(page_w_mm), float(page_h_mm)
        self._transparent = bool(transparent)
        self._objects: list[dict] = []
        self._resolver = _PathResolver()
        self._compiled: plan.CompiledPage | None = None
        self._files: dict[str, bytes] | None = None
        self._tmp: Path | None = None
        self._pdf: Path | None = None
        self._buffers: dict[tuple[int, bool], raster.RasterBuffer] = {}
        self._closed = False
        self.problems: list[dict] = []

    # -- 记对象 -----------------------------------------------------------
    def place(self, o: dict, dpi: int, resolve_panel: Callable[[dict, int], Path]) -> None:
        if self._closed:
            raise RuntimeError("Canvas 已关闭")
        if o.get("hidden"):
            return
        kind = o.get("type")
        if kind not in ("panel", "text", "arrow", "shape"):
            return
        if kind == "panel":
            self._resolver.paths[str(o.get("id", ""))] = Path(resolve_panel(o, dpi))
        self._objects.append(o)
        # 页面变了：编译结果、Canonical PDF、栅格缓冲一起作废——只丢编译结果会让下一次 save 复用
        # 上一次的 PDF 文件，新放的对象静默丢失（Codex #476 P2）
        self._compiled = None
        self._files = None
        self._pdf = None
        self._buffers.clear()

    # -- 编译 / 写 --------------------------------------------------------
    def _compile(self) -> plan.CompiledPage:
        from .sources import read_frozen

        if self._compiled is None:
            compiled = plan.compile_page(
                self._w_mm,
                self._h_mm,
                self._objects,
                sources=self._resolver,
                faces=provider(),
                background=None if self._transparent else (1.0, 1.0, 1.0),
            )
            self._files = {key: read_frozen(fs) for key, fs in compiled.sources.items()}
            self._compiled = compiled
            self.problems = list(compiled.problems)
            self._buffers.clear()
        return self._compiled

    def _tmp_dir(self) -> Path:
        if self._tmp is None:
            self._tmp = Path(tempfile.mkdtemp(prefix="tavotto-canvas-"))
        return self._tmp

    def _canonical_pdf(self) -> Path:
        from . import pdfwriter

        compiled = self._compile()
        if self._pdf is None:
            out = self._tmp_dir() / "canonical.pdf"
            pdfwriter.write_pdf(compiled.page, out, provider(), self._files or {})
            self._pdf = out
        return self._pdf

    def save_pdf(self, path: Path) -> None:
        shutil.copyfile(self._canonical_pdf(), Path(path))

    def _raster(self, dpi: int) -> raster.RasterBuffer:
        key = (int(dpi), self._transparent)
        buf = self._buffers.get(key)
        if buf is None:
            pdf = self._canonical_pdf()
            page = self._compile().page
            buf = host().render(
                pdf,
                dpi=float(dpi),
                transparent=self._transparent,
                page_size_pt=(page.width_pt, page.height_pt),
            )
            self._buffers[key] = buf
        return buf

    def save_png(self, path: Path, dpi: int) -> None:
        Path(path).write_bytes(raster.encode_png(self._raster(dpi)))

    def save_tiff(self, path: Path, dpi: int) -> dict:
        return raster.write_tiff(self._raster(dpi), Path(path))

    @property
    def size_pt(self) -> tuple[float, float]:
        return (mm2pt(self._w_mm), mm2pt(self._h_mm))

    def close(self) -> None:
        self._closed = True
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
        self._pdf = None
        self._buffers.clear()

    def __enter__(self) -> Canvas:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def compose(page_w_mm: float, page_h_mm: float, transparent: bool = False) -> Canvas:
    return Canvas(page_w_mm, page_h_mm, transparent)


# ---------------------------------------------------------------------------
# 按原图导出（scope=original）
# ---------------------------------------------------------------------------
def _file_resource(path: Path, source_id: str) -> tuple[str, ir.FileResource, bytes]:
    from ..engine import figcapture

    data = Path(path).read_bytes()
    art = figcapture.source_artifact_from_file(
        path, source_id=source_id, origin=figcapture.ORIGIN_STATIC
    )
    key = f"file:{art.bytes_sha256[:16]}:{art.semantic_identity()[7:23]}"
    res = ir.FileResource(
        source_id=art.source_id,
        kind=art.kind,
        sha256=art.bytes_sha256,
        origin=art.origin,
        semantic_identity=art.semantic_identity(),
    )
    return key, res, data


def original_pdf(src: Path, out: Path, page_pt: tuple[float, float] | None = None) -> dict:
    """矢量源：**整页搬运第一页**（pikepdf copy_foreign，不重画，页面尺寸 / 字体 / 路径一个字节不动）；
    位图源：新建一页 `page_pt` 大小、整页铺一个 Image XObject（不声称矢量）。回同旧后端的事实。"""
    import pikepdf

    from . import pdfwriter

    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        probe = host().size(src)
        with pikepdf.open(str(src)) as doc:
            pages = len(doc.pages)
            dest = pikepdf.new()
            # 只搬第一页：一张「图」在本产品里恒等于一页（画布上看到的也是第一页）
            dest.pages.append(doc.pages[0])
            dest.save(
                str(out),
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.disable,
                deterministic_id=True,
            )
            dest.close()
        return {
            "w_pt": float(probe["width_pt"]),
            "h_pt": float(probe["height_pt"]),
            "px_w": None,
            "px_h": None,
            "vector": True,
            "pages": pages,
        }

    if page_pt is None or page_pt[0] <= 0 or page_pt[1] <= 0:
        raise ValueError("位图源装进 PDF 必须给页面尺寸（见 engine/originalspec）")
    w_pt, h_pt = float(page_pt[0]), float(page_pt[1])
    key, res, data = _file_resource(src, src.name)
    info = rasterio.header_info(data, _kind_of(src))
    page = ir.validate(
        ir.Page(
            w_pt,
            h_pt,
            (ir.Image(resource=key, rect=(0.0, 0.0, w_pt, h_pt), object_id=src.stem),),
            {key: res},
            background=None,
        )
    )
    pdfwriter.write_pdf(page, out, provider(), {key: data})
    return {
        "w_pt": w_pt,
        "h_pt": h_pt,
        "px_w": int(info["width"]),
        "px_h": int(info["height"]),
        "vector": False,
        "pages": 1,
    }


def original_png(src: Path, out: Path, ppi: int | None, transparent: bool = False) -> dict:
    """矢量源按 `ppi` 栅格（child）；PNG 源**逐字节复制**；其它位图转码不重采样（像素网格不变）。"""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        if not ppi:
            raise ValueError("矢量源栅格化必须给 ppi")
        buf = host().render(src, dpi=float(ppi), transparent=bool(transparent))
        out.write_bytes(raster.encode_png(buf))
        return {"px_w": buf.width, "px_h": buf.height, "resampled": False, "transcoded": True}
    data = src.read_bytes()
    kind = _kind_of(src)
    if kind == "png":
        info = rasterio.header_info(data, kind)  # 先核它真是 PNG，再逐字节复制
        shutil.copyfile(src, out)
        return {
            "px_w": int(info["width"]),
            "px_h": int(info["height"]),
            "resampled": False,
            "transcoded": False,
        }
    buf = rasterio.decode(data, kind)
    out.write_bytes(raster.encode_png(buf))
    return {"px_w": buf.width, "px_h": buf.height, "resampled": False, "transcoded": True}


def original_tiff(
    src: Path,
    out: Path,
    ppi: int | None,
    transparent: bool = False,
    *,
    dpi_meta: float | None = None,
) -> dict:
    """同 `original_png` 一套规则，容器换 TIFF：矢量源分辨率标签 = ppi；位图源只写 `dpi_meta`
    （源文件自己声明过的），没给就写「没有绝对单位」。"""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        if not ppi:
            raise ValueError("矢量源栅格化必须给 ppi")
        buf = host().render(src, dpi=float(ppi), transparent=bool(transparent))
        raster.write_tiff(buf, out)
        return {"px_w": buf.width, "px_h": buf.height, "resampled": False, "transcoded": True}
    buf = rasterio.decode(src.read_bytes(), _kind_of(src))
    buf = dataclasses.replace(buf, dpi=float(dpi_meta) if dpi_meta else None)
    raster.write_tiff(buf, out)
    return {"px_w": buf.width, "px_h": buf.height, "resampled": False, "transcoded": True}


# ---------------------------------------------------------------------------
# 写回携带标注
# ---------------------------------------------------------------------------
class _NoPanels:
    """标注只允许 text / arrow / shape：面板在这条路上是调用方的错，不是可解析的源。"""

    def resolve(self, obj: dict) -> FrozenSource:
        raise SourceError(
            "source_kind_unsupported",
            f"写回标注不接受面板对象（{obj.get('id', '')}）",
            {"figure": str(obj.get("id", ""))},
        )


def annotate_asset(
    pdf_path: Path, png_path: Path | None, objects: list[dict], dpi: int = 600
) -> None:
    """把画布标注（坐标为**该图自身的 mm**）画进单图文件：标注编成一页 IR、写成 Canonical 覆盖页，经
    pikepdf `add_overlay` 叠到原 PDF 首页之上（原内容流与资源不动，仍是矢量、文字层在）；PNG 由注好的
    PDF 重新栅格化——两种载体逐像素同源。"""
    import pikepdf

    from . import pdfwriter

    pdf_path = Path(pdf_path)
    probe = host().size(pdf_path)
    w_pt, h_pt = float(probe["width_pt"]), float(probe["height_pt"])
    # 面板 / 不认识的类型不许静默跳过：面板由 `_NoPanels` 拒（source_kind_unsupported），别的类型由
    # `compile_page` 拒（bad_object）——两条都在动原件之前抛出，原件零改动
    compiled = plan.compile_page(
        w_pt * 25.4 / 72.0,
        h_pt * 25.4 / 72.0,
        list(objects),
        sources=_NoPanels(),
        faces=provider(),
        background=None,
    )
    tmp_dir = Path(tempfile.mkdtemp(prefix="tavotto-annotate-"))
    try:
        overlay = tmp_dir / "overlay.pdf"
        pdfwriter.write_pdf(compiled.page, overlay, provider(), {})
        staged = tmp_dir / "annotated.pdf"
        with pikepdf.open(str(pdf_path)) as base, pikepdf.open(str(overlay)) as ov:
            page = base.pages[0]
            page.add_overlay(ov.pages[0], pikepdf.Rectangle(page.trimbox))
            base.save(
                str(staged),
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.disable,
                deterministic_id=True,
            )
        shutil.copyfile(staged, pdf_path)
        if png_path is not None:
            buf = host().render(pdf_path, dpi=float(dpi), transparent=False)
            Path(png_path).write_bytes(raster.encode_png(buf))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
