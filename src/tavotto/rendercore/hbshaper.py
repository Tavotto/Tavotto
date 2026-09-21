"""HarfBuzz / fontTools 适配：把注册表里的一张脸变成可排版的 `typography.Face`（统一实施包 U06，ADR 0060）。

这是 native 适配层：`uharfbuzz`（shaping：glyph / cluster / advance / offset）与 `fontTools`
（hmtx / glyph order / CFF 的 CID 判定 / 子集）**只在这里 import，且在类里按需 import**——
`tavotto[rendercore]` 没装的机器上 `import tavotto.rendercore.hbshaper` 仍成功，只有构造 `HbFace`
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

from dataclasses import dataclass, field
from pathlib import Path

from . import fonts
from .ir import FontResource, Glyph


class CandidatePackagesMissing(RuntimeError):
    """`tavotto[rendercore]` 的候选包没装（uharfbuzz / fontTools / pikepdf）。"""


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
            f"缺候选包 {missing}：pip install 'tavotto[rendercore]'（或 requirements-rendercore.txt）"
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

    def __post_init__(self) -> None:
        require("uharfbuzz", "fontTools")
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont

        self.resource = self.record.resource
        self.ascender = self.record.ascender
        self.descender = self.record.descender
        self.path: Path = self.record.path
        self._tt = TTFont(str(self.path), lazy=True)
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
        blob = hb.Blob.from_file_path(str(self.path))
        self._hb_font = hb.Font(hb.Face(blob))
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
        fresh = TTFont(str(self.path), recalcTimestamp=False)
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

    def _load(self, record: fonts.FaceRecord) -> HbFace:
        face = self._faces.get(record.identity)
        if face is None:
            face = HbFace(record)
            self._faces[record.identity] = face
        return face

    def face_for(self, family: str, bold: bool, italic: bool) -> HbFace:
        return self._load(self.registry.record_for(family, bold, italic))

    def cjk_face(self) -> HbFace | None:
        rec = self.registry.cjk_record()
        return self._load(rec) if rec is not None else None

    def face_by_resource(self, resource: FontResource) -> HbFace:
        """IR 里的 `FontResource` → 脸。**按 sha256 找**，找到的脸身份必须与资源逐字相同——同名不同
        字体在这里被拒（RC-023）。"""
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
