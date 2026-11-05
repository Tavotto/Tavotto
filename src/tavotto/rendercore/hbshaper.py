"""HarfBuzz / fontTools 适配：把注册表里的一张脸变成可排版的 `typography.Face`（统一实施包 U06，ADR 0060）。

这是 native 适配层：`uharfbuzz`（shaping：glyph / cluster / advance / offset）与 `fontTools`
（hmtx / glyph order / CFF 的 CID 判定 / 子集）**只在这里 import，且在类里按需 import**——
RenderCore 的运行时依赖没装的机器上 `import tavotto.rendercore.hbshaper` 仍成功，只有构造 `HbFace`
时才报 `CandidatePackagesMissing`。纯模型（`typography` / `plan`）只认协议，不认这个模块。

## 三条纪律

* **shaping 结果就是 IR 里的字形**：`shape()` 回 `ir.Glyph(gid, cluster 原文, x_advance, x_offset,
  y_offset)`，字体单位；一个 cluster 出多个字形时只有第一个带原文（写入器据此包 ActualText），
  一个字形覆盖多个码位（连字 / 合成序列）时原文就是那几个码位。
* **字形缓存的键含身份**：缓存挂在 `HbFace` 实例上，而实例按 `(sha256, face_index)` 建
  （`HbFaceProvider`），同名不同字体绝不共用缓存（RC-023）。
* **格式边界显式**：`fonts.font_kind()` 粗分之后，CFF 还要读 top dict 的 ROS 确认 CID-keyed；
  不是 → `FontsUnavailable("font_file_invalid")`，不静默换脸（RC-037）。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import fonts
from .ir import FontResource, Glyph, VGlyph


class CandidatePackagesMissing(RuntimeError):
    """RenderCore 的运行时依赖没装（uharfbuzz / fontTools / pikepdf）。名字沿用 U06 的（那时它们是候选 extra），
    U10 起它们是 `dependencies`（ADR 0072）：抛出来意味着安装闭包不完整，不是「候选未启用」。"""


def require(*names: str) -> None:
    """按需 import 候选包；缺哪个就把哪个说出来（一次说全）。"""
    import importlib

    missing = []
    for name in names:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise CandidatePackagesMissing(
            f"缺 RenderCore 的运行时依赖 {missing}：pip install -r requirements.txt（U10 起是运行时依赖，不是 extra；"
            "闭包不完整的安装物不该发出去——ADR 0072）"
        )


def versions() -> dict[str, str]:
    """候选包版本（进证据 / 缓存键那一维）。没装的记 None。"""
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str | None] = {}
    for dist in ("uharfbuzz", "fonttools", "pikepdf", "pypdfium2"):
        try:
            out[dist] = version(dist)
        except PackageNotFoundError:
            out[dist] = None
    return out


@dataclass
class HbFace:
    """一张已加载的脸：注册表记录 + HarfBuzz 字体 + fontTools 表。"""

    record: fonts.FaceRecord
    resource: FontResource = field(init=False)
    ascender: float = field(init=False)
    descender: float = field(init=False)
    _shape_cache: dict[str, tuple[Glyph, ...]] = field(default_factory=dict, repr=False)
    _vshape_cache: dict[str, tuple[VGlyph, ...]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        require("uharfbuzz", "fontTools")
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont

        self.resource = self.record.resource
        self.ascender = self.record.ascender
        self.descender = self.record.descender
        self.path: Path = self.record.path
        # 用户字体（record.data 非空，ADR 0164）按字节加载：不 lazy、不 mmap，进程不持有指向用户文件的
        # 句柄 / 映射。批准字体随包分发，维持按路径加载
        self._data = self.record.data
        if self._data is not None:
            import io

            self._tt = TTFont(io.BytesIO(self._data), fontNumber=self.record.face_index)
        else:
            self._tt = TTFont(str(self.path), lazy=True, fontNumber=self.record.face_index)
        tables = set(self._tt.keys())
        kind = fonts.font_kind(frozenset(tables))
        if kind == "cff-cid":
            top = self._tt["CFF "].cff.topDictIndex[0]
            if not hasattr(top, "ROS"):
                raise fonts.FontsUnavailable(
                    "font_file_invalid",
                    f"{self.path.name}: 非 CID-keyed 的 CFF 本轮不支持（RC-037）",
                )
        if kind != self.resource.kind:
            raise fonts.FontsUnavailable(
                "font_file_invalid",
                f"{self.path.name}: 表里是 {kind}，allowlist 说 {self.resource.kind}",
            )
        self.kind = kind
        self.upem: int = self._tt["head"].unitsPerEm
        self._hmtx = self._tt["hmtx"].metrics
        self._order: list[str] = list(self._tt.getGlyphOrder())
        blob = (
            hb.Blob(self._data)
            if self._data is not None
            else hb.Blob.from_file_path(str(self.path))
        )
        self._hb_font = hb.Font(hb.Face(blob, self.record.face_index))
        self._hb_font.scale = (self.upem, self.upem)

    # -- typography.Face ----------------------------------------------------
    def covers(self, cp: int) -> bool:
        return self.record.covers(cp)

    def advance(self, gid: int) -> int:
        return int(self._hmtx[self._order[gid]][0])

    def shape(self, text: str) -> tuple[Glyph, ...]:
        cached = self._shape_cache.get(text)
        if cached is not None:
            return cached
        import uharfbuzz as hb

        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self._hb_font, buf, {})
        infos, positions = buf.glyph_infos, buf.glyph_positions
        clusters = sorted({i.cluster for i in infos})
        bounds = {
            c: (c, clusters[k + 1] if k + 1 < len(clusters) else len(text))
            for k, c in enumerate(clusters)
        }
        seen: set[int] = set()
        out: list[Glyph] = []
        for info, pos in zip(infos, positions):
            start, end = bounds[info.cluster]
            piece = text[start:end] if info.cluster not in seen else ""
            seen.add(info.cluster)
            out.append(
                Glyph(
                    gid=int(info.codepoint),
                    text=piece,
                    x_advance=int(pos.x_advance),
                    x_offset=int(pos.x_offset),
                    y_offset=int(pos.y_offset),
                )
            )
        result = tuple(out)
        if len(self._shape_cache) >= 4096:
            self._shape_cache.clear()
        self._shape_cache[text] = result
        return result

    def shape_vertical(self, text: str) -> tuple[VGlyph, ...]:
        """竖排（TTB）塑形——直立排的字（中日韩、假名、全角标点）走这里（ADR 0163）。

        HarfBuzz 在 TTB 下自己做三件事：套 `vert` / `vrt2`（全角标点换竖排字形）、读 `vmtx` / `vhea` /
        `VORG` 给竖排进退与竖排原点；字体没有这些表（Liberation、多数用户字体）时用**合成**的竖排度量
        （进退 = 上升 + 下降，原点在字宽中线 / 上升处）。`x_offset` / `y_offset` 已经把横排原点折到
        竖排原点，调用方只管把笔位加上它们。"""
        cached = self._vshape_cache.get(text)
        if cached is not None:
            return cached
        import uharfbuzz as hb

        buf = hb.Buffer()
        buf.add_str(text)
        buf.direction = "ttb"
        buf.guess_segment_properties()
        hb.shape(self._hb_font, buf, {"vert": True, "vrt2": True})
        infos, positions = buf.glyph_infos, buf.glyph_positions
        clusters = sorted({i.cluster for i in infos})
        bounds = {
            c: (c, clusters[k + 1] if k + 1 < len(clusters) else len(text))
            for k, c in enumerate(clusters)
        }
        seen: set[int] = set()
        out: list[VGlyph] = []
        for info, pos in zip(infos, positions):
            start, end = bounds[info.cluster]
            piece = text[start:end] if info.cluster not in seen else ""
            seen.add(info.cluster)
            out.append(
                VGlyph(
                    gid=int(info.codepoint),
                    text=piece,
                    y_advance=-int(pos.y_advance),
                    x_offset=int(pos.x_offset),
                    y_offset=int(pos.y_offset),
                )
            )
        result = tuple(out)
        if len(self._vshape_cache) >= 4096:
            self._vshape_cache.clear()
        self._vshape_cache[text] = result
        return result

    # -- 写入器要的 ----------------------------------------------------------
    def glyph_name(self, gid: int) -> str:
        return self._order[gid]

    def cid_of(self, gid: int) -> int:
        """CFF CID-keyed：charset 给出 GID → CID；.notdef 是 0。"""
        charset = self._tt["CFF "].cff.topDictIndex[0].charset
        name = charset[gid]
        if name == ".notdef":
            return 0
        assert name.startswith("cid"), name
        return int(name[3:])

    def reverse_cmap(self) -> dict[int, int]:
        """GID → 码位（cmap 里第一个映到它的）。给 ToUnicode 兜底用。"""
        out: dict[int, int] = {}
        for cp, gid in sorted(self.record.info.cmap.items()):
            out.setdefault(gid, cp)
        return out

    def subset(self, gids: set[int]) -> tuple[bytes, dict[int, int], int]:
        """子集字体程序：返回 (字节, 原 GID → 子集 GID, 子集 glyph 数)。TrueType 回完整 sfnt
        （FontFile2），CFF 回裸 CFF 表（FontFile3 / CIDFontType0C）。同一输入两次结果字节相同
        （`recalcTimestamp=False`）。"""
        from fontTools import subset as ftsubset
        from fontTools.ttLib import TTFont

        want = sorted(set(gids) | {0})
        opts = ftsubset.Options()
        opts.retain_gids = False
        opts.notdef_outline = True
        opts.notdef_glyph = True
        opts.glyph_names = False
        opts.layout_features = []
        opts.hinting = False
        opts.desubroutinize = True
        opts.recalc_bounds = False
        opts.recalc_timestamp = False
        opts.name_IDs = [0, 1, 2, 3, 4, 5, 6]
        opts.drop_tables += [
            "GSUB",
            "GPOS",
            "GDEF",
            "BASE",
            "JSTF",
            "MATH",
            "DSIG",
            "kern",
            "vhea",
            "vmtx",
            "VORG",
            "hdmx",
            "LTSH",
            "VDMX",
            "gasp",
            "STAT",
            "meta",
            "FFTM",  # FontForge 时间戳表：fontTools 不会子集它，留着只会打一行 warning
        ]
        # fontTools 的子集器每张表打一行 INFO（"glyf pruned" / "name pruned" …）：一次导出十几行，
        # 全进 app.log。它不是产品的日志（产品的进度在 job.trace），压到 WARNING——真出问题的
        # 那些（缺表、坏 cmap）仍是 warning / error，留得住。只动 fontTools.subset 这个 logger。
        logging.getLogger("fontTools.subset").setLevel(logging.WARNING)
        if self._data is not None:
            import io as _io

            src = _io.BytesIO(self._data)
        else:
            src = str(self.path)
        fresh = TTFont(src, recalcTimestamp=False, fontNumber=self.record.face_index)
        s = ftsubset.Subsetter(opts)
        s.populate(gids=want)
        s.subset(fresh)
        new_order = list(fresh.getGlyphOrder())
        index = {name: i for i, name in enumerate(new_order)}
        remap = {gid: index[self._order[gid]] for gid in want}
        if self.kind == "truetype":
            import io

            buf = io.BytesIO()
            fresh.save(buf)
            data = buf.getvalue()
        else:
            data = fresh["CFF "].compile(fresh)
        return data, remap, len(new_order)

    def descriptor_metrics(self) -> dict:
        """FontDescriptor 用的度量（千分之一 em）。"""
        k = 1000.0 / self.upem
        head, os2, post = self._tt["head"], self._tt["OS/2"], self._tt["post"]
        flags = 4  # Symbolic：CID 字体一律 Identity 编码
        if "Serif" in (self.record.info.family_name or ""):
            flags |= 2
        if post.italicAngle:
            flags |= 64
        return {
            "flags": flags,
            "bbox": [
                round(head.xMin * k),
                round(head.yMin * k),
                round(head.xMax * k),
                round(head.yMax * k),
            ],
            "italic_angle": float(post.italicAngle),
            "ascent": round(os2.sTypoAscender * k),
            "descent": round(os2.sTypoDescender * k),
            "cap_height": round(getattr(os2, "sCapHeight", 0) * k) or round(os2.sTypoAscender * k),
            "stem_v": 80,
        }


class HbFaceProvider:
    """`typography.FaceProvider` 的真实实现：注册表 → 按身份缓存的 `HbFace`。"""

    def __init__(self, registry: fonts.FontRegistry | None = None) -> None:
        self.registry = registry if registry is not None else fonts.FontRegistry.discover()
        self._faces: dict[tuple[str, int], HbFace] = {}
        #: 用户本机字体（ADR 0164）：身份 (sha256, face_index) → 已核过的记录。**不进 `registry.faces`**
        #: ——批准集合是闭集（ADR 0060），用户脸只在被某个对象的 `source_font` 点名时才登记进来
        self._user_records: dict[tuple[str, int], fonts.FaceRecord] = {}
        self._user_stat: dict[tuple[str, int], tuple[int, int]] = {}
        #: 进程级单例（`facade.provider()`），Flask 多线程下预览与导出会并发进来：这把锁只护**字典操作**
        #: （查 / 插 / 移到最新 / 淘汰 / 计字节）。读文件、算 sha256、解析 sfnt、建 `HbFace` 都在锁外
        self._lock = threading.RLock()

    def _load(self, record: fonts.FaceRecord) -> HbFace:
        with self._lock:
            face = self._faces.get(record.identity)
        if face is not None:
            return face
        built = HbFace(record)  # 慢（解析字体）：锁外建
        with self._lock:
            # 别的线程可能已经插了同一身份：用已有的那份，丢掉自己的
            return self._faces.setdefault(record.identity, built)

    def face_for(self, family: str, bold: bool, italic: bool) -> HbFace:
        return self._load(self.registry.record_for(family, bold, italic))

    def cjk_face(self, family: str, bold: bool = False) -> HbFace | None:
        rec = self.registry.cjk_record(family, bold)
        return self._load(rec) if rec is not None else None

    def user_face(self, ref) -> "HbFace":
        """`userfonts.FaceRef`（sha256 + face 序号）→ 脸。由 `userfonts.admit` 核过安全 / 许可 / 格式后
        登记；任何一环不过抛 `userfonts.UserFontUnavailable(reason)`，**调用方决定回退**（本方法
        不替谁换脸）。"""
        from . import userfonts

        key = (ref.sha256, ref.face)
        with self._lock:
            rec = self._user_records.get(key)
            expect = self._user_stat.get(key)
        if rec is not None:
            # 进程里活得比文件久：登记之后文件被删 / 被换，下一次问就重新核（不拿旧记录嵌一张已经不是它的脸）
            try:
                st = rec.path.stat()
                fresh = expect is not None and (st.st_size, st.st_mtime_ns) == expect
            except OSError:
                fresh = False
            if not fresh:
                with self._lock:
                    if self._user_records.get(key) is rec:
                        self._user_records.pop(key, None)
                        self._user_stat.pop(key, None)
                        self._faces.pop(rec.identity, None)
                rec = None
        if rec is None:
            new_rec = userfonts.admit(ref)  # 读文件 + sha256 + 解析：锁外
            st = new_rec.path.stat()
            with self._lock:
                rec = self._user_records.setdefault(key, new_rec)  # 别的线程先插了就用它的
                self._user_stat.setdefault(key, (st.st_size, st.st_mtime_ns))
                # 插入与淘汰必须在同一段锁里：分两段时，别的线程会在两段之间看到超过上限的缓存
                # （test_user_face_cache_holds_its_bounds_under_concurrent_requests 偶发 'count 3'）
                self._touch_and_evict_user(key)
        else:
            with self._lock:
                self._touch_and_evict_user(key)
        try:
            return self._load(rec)
        except fonts.FontsUnavailable as exc:
            self._forget_user(key)
            raise userfonts.UserFontUnavailable("unsupported_format", str(exc)) from exc
        except CandidatePackagesMissing:
            raise
        except Exception as exc:  # noqa: BLE001 —— fontTools / HarfBuzz 对坏字体各有各的异常类型
            self._forget_user(key)
            raise userfonts.UserFontUnavailable(
                "unreadable", f"{type(exc).__name__}: {exc}"
            ) from exc

    #: 内存里的用户字体字节总量上限（含已建 face 的表副本不计；按原文件字节算）。超出从最久没用的开始丢，
    #: 当前这张永远保留；被丢的下次 `user_face` 重新入场（再读一遍文件）
    USER_FACE_BYTES_CAP = 384 * 1024 * 1024
    USER_FACE_COUNT_CAP = 32

    def _touch_and_evict_user(self, key: tuple[str, int]) -> None:
        """调用方持锁。LRU：把 key 挪到队尾，再按上限淘汰（当前这张保留）。"""
        if key in self._user_records:
            self._user_records[key] = self._user_records.pop(key)  # LRU：最近用的挪到队尾
        self._evict_user_faces(keep=key)

    def _forget_user(self, key: tuple[str, int]) -> None:
        with self._lock:
            self._user_records.pop(key, None)
            self._user_stat.pop(key, None)

    def _evict_user_faces(self, keep: tuple[str, int]) -> None:
        """调用方持锁。只删缓存条目，不关闭 / 释放任何东西：别的线程手里还拿着的 face 靠引用计数活到用完。"""

        def total() -> int:
            return sum(len(r.data or b"") for r in self._user_records.values())

        while len(self._user_records) > 1 and (
            len(self._user_records) > self.USER_FACE_COUNT_CAP or total() > self.USER_FACE_BYTES_CAP
        ):
            victim = next((k for k in self._user_records if k != keep), None)
            if victim is None:
                break
            rec = self._user_records.pop(victim)
            self._user_stat.pop(victim, None)
            self._faces.pop(rec.identity, None)

    def face_by_resource(self, resource: FontResource) -> HbFace:
        """IR 里的 `FontResource` → 脸。**按 sha256 找**，找到的脸身份必须与资源逐字相同——同名不同
        字体在这里被拒（RC-023）。用户脸（ADR 0164）同一条规则：只认已经 `user_face` 登记过的身份。"""
        with self._lock:
            user = self._user_records.get((resource.sha256, resource.face_index))
        if user is not None:
            face = self._load(user)
            if face.resource != resource:
                raise fonts.FontsUnavailable(
                    "face_missing",
                    f"资源 {resource.face_id} 的身份与已登记的用户脸不一致（{face.resource} ≠ {resource}）",
                )
            return face
        for rec in self.registry.faces.values():
            if rec.identity == (resource.sha256, resource.face_index):
                face = self._load(rec)
                if face.resource != resource:
                    raise fonts.FontsUnavailable(
                        "face_missing",
                        f"资源 {resource.face_id} 的身份与注册表里的脸不一致（{face.resource} ≠ {resource}）",
                    )
                return face
        raise fonts.FontsUnavailable(
            "face_missing", f"注册表里没有 sha256={resource.sha256[:12]} 的脸（{resource.face_id}）"
        )
