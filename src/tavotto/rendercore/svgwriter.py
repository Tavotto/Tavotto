"""Render IR → self-contained SVG; shaped text is live ``<text>`` by default, glyph outlines on request (X03).

``text_policy="text"`` (the default, ADR 0144) writes one ``<text>`` per shaped run with the
approved face's real family name, per-character ``x`` positions taken from the IR advances, and
the real Unicode from the cluster mapping. A run that cannot be restored to reliable Unicode
(missing cluster text, ``.notdef``, private-use or control characters, a character the face does
not cover) falls back to glyph outlines for that run only and is counted in the facts.
``text_policy="outlines"`` keeps the previous behaviour (pure glyph paths, no font dependence).

The page uses physical pt dimensions and one outer y-up → y-down transform. All IR
coordinates and group matrices otherwise remain unchanged. Images and same-snapshot
SVG companions use the existing placement contract; opaque PDF is never rasterized.

``imported_svgs`` contains validated same-execution SVG companions keyed by the PDF
resource key. Provenance is established by the source resolver, not inferred from a
filename. The writer rechecks canonical source hashes, validates every SVG, isolates
each imported instance's IDs and CSS, and validates the assembled document as well.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from xml.etree import ElementTree as ET

from . import ir, placement, raster, rasterio
from .hbshaper import HbFaceProvider, require
from .svgcheck import SVGError, name_char, name_start, parse_svg
from .svgfonts import family_list

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)

TEXT_POLICY_TEXT = "text"
TEXT_POLICY_OUTLINES = "outlines"
TEXT_POLICIES = (TEXT_POLICY_TEXT, TEXT_POLICY_OUTLINES)

WRITER_ERROR_CODES = (
    "unsupported_capability",
    "font_identity",
    "font_format",
    "source_bytes_missing",
    "source_identity",
    "source_unreadable",
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
            f"SVG writer cannot represent {operation}: {reason}",
            {"operation": operation, "reason": reason, "object_id": object_id},
        )
        self.operation = operation


@dataclass
class WriteFacts:
    page: tuple[float, float]
    fonts: list[dict] = field(default_factory=list)
    text_objects: int = 0
    outlined_glyphs: int = 0
    text_policy: str = TEXT_POLICY_TEXT
    #: runs written as live ``<text>`` / runs that had to fall back to outlines under the text policy
    text_runs: int = 0
    text_fallback_runs: int = 0
    #: ``<text>`` elements carried by validated imported SVG companions (kept as-is under the text policy)
    imported_text_elements: int = 0
    paths: int = 0
    groups: int = 0
    transparency_groups: int = 0
    notdef_codes: int = 0
    imported_pages: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    sha256: str = ""
    bytes: int = 0


def _num(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError(f"Non-finite SVG coordinate: {value!r}")
    result = f"{value:.9f}".rstrip("0").rstrip(".")
    return result if result not in ("", "-0") else "0"


def _matrix(value: ir.Matrix) -> str:
    return "matrix(" + " ".join(_num(v) for v in value) + ")"


def _color(value: ir.RGB) -> str:
    return "rgb(" + ",".join(_num(v * 100) + "%" for v in value) + ")"


def _path(segments: tuple) -> str:
    return " ".join(seg[0] + " ".join(_num(v) for v in seg[1:]) for seg in segments)


def _element(parent: ET.Element, tag: str, **attrs: str) -> ET.Element:
    return ET.SubElement(parent, f"{{{SVG}}}{tag}", attrs)


_XML = "http://www.w3.org/XML/1998/namespace"


def _text_safe(cp: int) -> bool:
    """A code point that survives XML and has a stable, editable meaning in Office."""
    if cp in (0x20, 0xA0):
        return True
    if cp < 0x20 or 0x7F <= cp <= 0x9F or 0xD800 <= cp <= 0xDFFF or cp in (0xFFFE, 0xFFFF):
        return False
    return not (0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0x10FFFF or 0xFDD0 <= cp <= 0xFDEF)


def _isolate(root: ET.Element, prefix: str, scope: str) -> None:
    """Rewrite only validated local references; CSS selectors are scoped per instance.

    svgcheck admits flat rules and a deliberately small selector grammar, so this
    does not attempt to parse arbitrary CSS. In particular colors in declarations
    are never mistaken for ID selectors.
    """
    from tavotto.svgisolation import isolate_svg

    isolate_svg(root, prefix=prefix, scope=scope)


def xml_id(label: str) -> str | None:
    """对象 / 组的名字 → SVG 的 `id`（ADR 0208 §七之三 第 2 条）；没有名字 = None。

    合法的 XML 名字字符原样保留（中文可以）；空格换 `_`（Illustrator 显示回空格）；其余不能进 `id` 的字符
    写成 `_x<十六进制码点>_`（Illustrator 自己导出 SVG 用的写法，读回来是原字符）；以数字 / `.` / `-` 开头的前面
    加 `_`；`tavotto-` 开头的也加 `_`，不与写入器自己生成的 `tavotto-clip-3` 之类撞车。唯一性由写入器加序号。"""
    out: list[str] = []
    for ch in label.strip():
        if ch == " ":
            out.append("_")
        elif name_char(ch):
            out.append(ch)
        else:
            out.append(f"_x{ord(ch):X}_")
    name = "".join(out)
    if not name:
        return None
    if not name_start(name[0]) or name.startswith("tavotto-"):
        name = "_" + name
    return name


class SvgWriter:
    def __init__(
        self,
        page: ir.Page,
        provider: HbFaceProvider,
        files: Mapping[str, bytes] | None = None,
        *,
        imported_svgs: Mapping[str, bytes] | None = None,
        imported_text_svgs: Mapping[str, bytes] | None = None,
        text_policy: str = TEXT_POLICY_TEXT,
    ) -> None:
        if text_policy not in TEXT_POLICIES:
            raise ValueError(f"Unknown SVG text policy: {text_policy!r}")
        self.text_policy = text_policy
        self.page = ir.validate(page)
        self.provider = provider
        self.files = files or {}
        self.imported_svgs = imported_svgs or {}
        # 同一次执行的「保留文字」成品（ADR 0144）：身份仍由 path 基准 `imported_svgs` 的 hash 钉住，
        # 这份只在 text 策略下替换写入的字节；解析失败就回退 path 版。
        self.imported_text_svgs = imported_text_svgs or {}
        self.facts = WriteFacts(page=(page.width_pt, page.height_pt), text_policy=text_policy)
        self._counter = 0
        self._ids: set[str] = set()
        self._group_clips: dict[tuple, str] = {}
        self._faces: dict = {}
        self._font_facts: dict[str, dict] = {}
        self._glyph_sets: dict = {}
        self._glyphs: dict[tuple[str, int], str] = {}
        self._images: dict[str, tuple[str, dict]] = {}
        self._sources: dict[str, ET.Element] = {}
        self._static_svg_sizes: dict[str, tuple[float, float]] = {}
        self._raster_pixels = 0
        self.root = ET.Element(
            f"{{{SVG}}}svg",
            {
                "version": "1.1",
                "width": _num(page.width_pt) + "pt",
                "height": _num(page.height_pt) + "pt",
                "viewBox": f"0 0 {_num(page.width_pt)} {_num(page.height_pt)}",
            },
        )
        self.defs = _element(self.root, "defs")

    def _name(self, prefix: str) -> str:
        self._counter += 1
        return f"tavotto-{prefix}-{self._counter}"

    def _id(self, label: str) -> dict[str, str]:
        """名字 → `{"id": …}`（唯一；重名加序号）；没有名字 = 空字典。"""
        base = xml_id(label) if label else None
        if base is None:
            return {}
        name, n = base, 1
        while name in self._ids:
            n += 1
            name = f"{base}_{n}"
        self._ids.add(name)
        return {"id": name}

    def _check(self, operation: str, object_id: str) -> None:
        cap = ir.CAPABILITIES["svg"][operation]
        if cap.level == ir.CAP_UNSUPPORTED:
            raise UnsupportedCapability(operation, cap.reason, object_id)

    def _gradient(self, g) -> str:
        """渐变（ADR 0186）→ `<defs>` 里的 `<linearGradient>` / `<radialGradient>`，`userSpaceOnUse`：坐标就是引用它的
        路径所在的框空间（随外层 `<g transform>` 走）。色标的 alpha 走 `stop-opacity`。回 id。"""
        name = self._name("grad")
        if isinstance(g, ir.LinearGradient):
            el = _element(
                self.defs,
                "linearGradient",
                id=name,
                gradientUnits="userSpaceOnUse",
                x1=_num(g.p0[0]),
                y1=_num(g.p0[1]),
                x2=_num(g.p1[0]),
                y2=_num(g.p1[1]),
            )
        else:
            el = _element(
                self.defs,
                "radialGradient",
                id=name,
                gradientUnits="userSpaceOnUse",
                cx=_num(g.center[0]),
                cy=_num(g.center[1]),
                r=_num(g.radius),
            )
        for st in g.stops:
            _element(
                el,
                "stop",
                offset=_num(st.offset),
                **{"stop-color": _color(st.rgb), "stop-opacity": _num(st.alpha)},
            )
        return name

    def _clip(self, parent: ET.Element, segments: tuple, rule: str = "nonzero") -> None:
        name = self._name("clip")
        clip = _element(self.defs, "clipPath", id=name, clipPathUnits="userSpaceOnUse")
        _element(clip, "path", d=_path(segments), **{"clip-rule": rule})
        parent.set("clip-path", f"url(#{name})")

    def _shared_clip(self, parent: ET.Element, clip: ir.Path) -> None:
        """文档里的剪切组（ADR 0208 §七之三 第 3 条）：同一个轮廓只写一个 `<clipPath>`，组的 `<g>` 引用它。"""
        key = (clip.segments, clip.fill_rule)
        name = self._group_clips.get(key)
        if name is None:
            self._clip(parent, clip.segments, clip.fill_rule)
            self._group_clips[key] = parent.get("clip-path", "")[5:-1]
        else:
            parent.set("clip-path", f"url(#{name})")

    def _placed(self, parent: ET.Element, node, source: placement.Rect):
        pl = placement.place(
            source,
            node.rect,
            crop=node.crop,
            rotate_cw_deg=node.rotate_cw_deg,
            flip_h=node.flip_h,
            flip_v=node.flip_v,
        )
        group = _element(parent, "g", transform=_matrix(pl.matrix), opacity=_num(node.opacity))
        # The image itself supplies this exact boundary. A coincident clip
        # would apply antialias coverage twice; retain only a genuine crop.
        # ImportedPage clipping remains independent of this image-only rule.
        if not isinstance(node, ir.Image) or node.crop not in (None, (0, 0, 1, 1)):
            x, y, w, h = pl.clip
            self._clip(
                group,
                (("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)),
            )
        return group, pl

    def _source_bytes(self, node) -> tuple[ir.FileResource, bytes]:
        res = self.page.resources[node.resource]
        assert isinstance(res, ir.FileResource)
        data = self.files.get(node.resource)
        params = {"resource": node.resource, "object_id": node.object_id, "figure": res.source_id}
        if data is None:
            raise WriterError("source_bytes_missing", "Source bytes were not supplied", params)
        if hashlib.sha256(data).hexdigest() != res.sha256:
            raise WriterError(
                "source_identity", "Source bytes do not match the frozen identity", params
            )
        return res, data

    def _emit(self, node: ir.Node, parent: ET.Element, label: str = "") -> None:
        for operation in ir.operations_of(node):
            if operation == "imported_page":
                # This operation is data-dependent: opaque PDF remains unsupported;
                # _imported checks the explicitly frozen SVG companion identity.
                continue
            self._check(operation, node.object_id)
        if isinstance(node, ir.Path):
            self._paint_path(node, parent, label)
        elif isinstance(node, ir.Group):
            self._group(node, parent, label)
        elif isinstance(node, ir.ShapedText):
            self._text(node, parent, label)
        elif isinstance(node, ir.Image):
            self._image(node, parent, label)
        elif isinstance(node, ir.ImportedPage):
            self._imported(node, parent, label)

    def _group(self, node: ir.Group, parent: ET.Element, label: str) -> None:
        self.facts.groups += 1
        self.facts.transparency_groups += int(node.opacity < 1)
        name = node.label or label
        if node.semantic:
            # 文档里的组：只归类，不带变换 / 不透明度；剪切组的蒙版挂在自己身上（只写一次）
            group = _element(parent, "g", **self._id(name))
            if node.clip is not None:
                self._shared_clip(group, node.clip)
            for child in node.children:
                self._emit(child, group)
            return
        plain = node.transform == ir.IDENTITY and node.opacity == 1.0 and node.clip is None
        if plain and len(node.children) == 1:
            # 没有作用的包装：不写 `<g>`；有名字时名字交给唯一的那个孩子。孩子不止一个时一个对象仍是一个组
            # （无名的两行文字、带底框 / 下划线 / 公式的文字、路径文字拆开了就不再是一个东西）
            for child in node.children:
                self._emit(child, parent, name)
            return
        if (
            name
            and len(node.children) == 1
            and isinstance(node.children[0], ir.Path)
            and node.clip is None
        ):
            # 一个对象 = 一个路径：名字、变换、不透明度都落在这条路径自己身上（不套一层无意义的组）
            for operation in ir.operations_of(node.children[0]):
                self._check(operation, node.children[0].object_id)
            self._paint_path(
                node.children[0],
                parent,
                name,
                transform=_matrix(node.transform),
                opacity=_num(node.opacity),
            )
            return
        if (
            len(node.children) == 1
            and isinstance(node.children[0], ir.ShapedText)
            and node.clip is None
            and node.opacity == 1.0
        ):
            # 一个文字对象 = 一个 `<text>`（旋转并进它自己的变换）；几段（上下标 / 字体回退）= 一个以对象名命名的组，
            # 变换与填充都落在这个组上（不再多套一层）
            text = node.children[0]
            for operation in ir.operations_of(text):
                self._check(operation, text.object_id)
            if self._single_text(text) is not None:
                self._text(text, parent, name, post=node.transform)
            else:
                self._text(text, parent, name, group_attrs={"transform": _matrix(node.transform)})
            return
        group = _element(
            parent,
            "g",
            **self._id(name),
            transform=_matrix(node.transform),
            opacity=_num(node.opacity),
        )
        if node.clip is not None:
            if isinstance(node.clip, ir.ClipUnion):
                clip_name = self._name("clip")
                clip = _element(self.defs, "clipPath", id=clip_name, clipPathUnits="userSpaceOnUse")
                for path in node.clip.paths:
                    _element(clip, "path", d=_path(path.segments), **{"clip-rule": path.fill_rule})
                group.set("clip-path", f"url(#{clip_name})")
            else:
                self._clip(group, node.clip.segments, node.clip.fill_rule)
        for child in node.children:
            self._emit(child, group)

    def _paint_path(
        self,
        node: ir.Path,
        parent: ET.Element,
        label: str = "",
        *,
        transform: str | None = None,
        opacity: str | None = None,
    ) -> None:
        attrs = {
            **self._id(label),
            **({"transform": transform} if transform is not None else {}),
            **({"opacity": opacity} if opacity is not None else {}),
            "d": _path(node.segments),
            "fill": "none",
            "stroke": "none",
            "fill-rule": node.fill_rule,
        }
        if node.fill is not None:
            if ir.is_gradient(node.fill):
                attrs["fill"] = f"url(#{self._gradient(node.fill)})"
            else:
                attrs.update(fill=_color(node.fill.rgb), **{"fill-opacity": _num(node.fill.alpha)})
        if node.stroke is not None:
            stroke = node.stroke
            if stroke.width == 0:
                raise UnsupportedCapability(
                    "path_stroke", "svg_hairline_unsupported", node.object_id
                )
            grad = ir.is_gradient(stroke.paint)
            attrs.update(
                stroke=f"url(#{self._gradient(stroke.paint)})"
                if grad
                else _color(stroke.paint.rgb),
                **({} if grad else {"stroke-opacity": _num(stroke.paint.alpha)}),
                **{
                    "stroke-width": _num(stroke.width),
                    "stroke-linecap": stroke.cap,
                    "stroke-linejoin": stroke.join,
                    "stroke-miterlimit": _num(stroke.miter_limit),
                },
            )
            if stroke.dash:
                attrs["stroke-dasharray"] = " ".join(_num(v) for v in stroke.dash)
                attrs["stroke-dashoffset"] = _num(stroke.dash_phase)
        _element(parent, "path", **attrs)
        self.facts.paths += 1

    def _face(self, key: str):
        if key not in self._faces:
            resource = self.page.resources[key]
            try:
                face = self.provider.face_by_resource(resource)
            except Exception as exc:
                raise WriterError("font_identity", str(exc), {"font": key}) from exc
            if face.resource != resource:
                raise WriterError(
                    "font_identity", "Font resource identity does not match", {"font": key}
                )
            if face.kind not in ir.FONT_KINDS:
                raise WriterError("font_format", f"Unsupported font format: {face.kind}")
            self._faces[key] = face
            self.facts.fonts.append(
                {
                    "face_id": resource.face_id,
                    "sha256": resource.sha256,
                    "outlined": False,
                    "as_text": False,
                    "embedded": False,
                }
            )
            self._font_facts[key] = self.facts.fonts[-1]
        return self._faces[key]

    def _glyph(self, key: str, gid: int) -> str:
        cached = self._glyphs.get((key, gid))
        if cached is not None:
            return cached
        require("fontTools")
        from fontTools.pens.svgPathPen import SVGPathPen

        face = self._face(key)
        try:
            if key not in self._glyph_sets:
                self._glyph_sets[key] = face._tt.getGlyphSet()
            glyphs = self._glyph_sets[key]
            pen = SVGPathPen(glyphs)
            glyphs[face.glyph_name(gid)].draw(pen)
            commands = pen.getCommands()
        except (IndexError, KeyError, ValueError) as exc:
            raise WriterError(
                "font_format", "Glyph outline is unavailable", {"font": key, "gid": gid}
            ) from exc
        self._font_facts[key]["outlined"] = True
        name = self._name("glyph")
        _element(self.defs, "path", id=name, d=commands)
        self._glyphs[(key, gid)] = name
        return name

    def _single_text(self, node: ir.ShapedText) -> tuple[ir.GlyphRun, tuple] | None:
        """一段只有一个可写成 `<text>` 的 run：就是一个 `<text>`（自带填充与名字），不再套 `<g fill>`
        ——Illustrator 里是一个文字框，不是「组里一个文字框」（ADR 0208 §七之三 第 5 条）。"""
        if len(node.runs) != 1 or node.runs[0].invisible or self.text_policy != TEXT_POLICY_TEXT:
            return None
        layout = self._text_layout(node.runs[0])
        return None if layout is None else (node.runs[0], layout)

    def _text(
        self,
        node: ir.ShapedText,
        parent: ET.Element,
        label: str = "",
        group_attrs: Mapping[str, str] | None = None,
        post: ir.Matrix | None = None,
    ) -> None:
        paint = {
            "fill": _color(node.paint.rgb),
            "fill-opacity": _num(node.paint.alpha),
        }
        single = self._single_text(node)
        if single is not None and not group_attrs:
            run, layout = single
            self._text_element(
                parent, node, run, node.x, layout, {**self._id(label), **paint}, post
            )
            self.facts.text_runs += 1
            self.facts.text_objects += 1
            return
        group = _element(parent, "g", **self._id(label), **(group_attrs or {}), **paint)
        pen_x = node.x
        for run in node.runs:
            layout = (
                self._text_layout(run)
                if self.text_policy == TEXT_POLICY_TEXT and not run.invisible
                else None
            )
            if run.invisible:
                # 只进 PDF 文本层的段（公式源码，ADR 0152 §七）：SVG 里什么都不画；笔照常前进，后面的字落位不变
                pass
            elif layout is not None:
                self._text_element(group, node, run, pen_x, layout)
                self.facts.text_runs += 1
            else:
                if self.text_policy == TEXT_POLICY_TEXT:
                    self.facts.text_fallback_runs += 1
                self._outline_run(group, node, run, pen_x)
            pen_x += self._run_advance(run)
        self.facts.text_objects += 1

    def _run_advance(self, run: ir.GlyphRun) -> float:
        size = run.size / self._face(run.font).upem
        return sum(g.x_advance for g in run.glyphs) * size

    def _outline_run(self, group: ET.Element, node: ir.ShapedText, run: ir.GlyphRun, pen_x: float):
        face = self._face(run.font)
        size = run.size / face.upem
        for glyph in run.glyphs:
            name = self._glyph(run.font, glyph.gid)
            matrix = (
                size,
                0,
                size * ir.SYNTH_ITALIC_SKEW if run.synth_italic else 0,
                size,
                pen_x + glyph.x_offset * size,
                node.y + run.rise + glyph.y_offset * size,
            )
            attrs = {f"{{{XLINK}}}href": "#" + name, "transform": _matrix(matrix)}
            if run.synth_bold:
                # 模拟粗体（ADR 0169 修订）：描边在字形坐标里，宽度除以缩放
                attrs |= {
                    "stroke": _color(node.paint.rgb),
                    "stroke-opacity": _num(node.paint.alpha),
                    "stroke-width": _num(ir.synth_bold_width(run.size) / size),
                    "stroke-linejoin": "round",
                }
            _element(group, "use", **attrs)
            pen_x += glyph.x_advance * size
            self.facts.outlined_glyphs += 1
            self.facts.notdef_codes += int(glyph.gid == 0)

    def _text_layout(self, run: ir.GlyphRun) -> tuple[str, float, list[float] | None] | None:
        """Unicode string + the run's single start x (pt, relative to the run origin) + optional
        per-character y, or None.

        One x per run, no per-glyph x list: Word's "Convert to Shape" cuts a new text segment at every
        explicit x, which scatters the letters ("T i t l e") and adds a paragraph mark after each one
        (user report, 2026-10-21). Letter spacing is left to the renderer; on a machine with the same
        approved face it is the face's own advance, i.e. what the IR was shaped with. ``y`` is only
        written when a glyph truly carries a vertical offset.

        None means this run cannot be restored to reliable Unicode and must stay outlines:
        no cluster text on the first glyph, ``.notdef``, a character the approved face does not
        cover, private-use / control / non-character code points, or no glyphs at all.
        """
        if not run.glyphs or run.glyphs[0].text == "":
            return None
        face = self._face(run.font)
        size = run.size / face.upem
        chars: list[str] = []
        ys: list[float] = []
        y = 0.0
        for glyph in run.glyphs:
            if glyph.gid == 0:
                return None
            if glyph.text:
                y = -glyph.y_offset * size
            for ch in glyph.text:
                cp = ord(ch)
                if not _text_safe(cp) or not face.covers(cp):
                    return None
                chars.append(ch)
                ys.append(y)
        return "".join(chars), run.glyphs[0].x_offset * size, (ys if any(ys) else None)

    def _text_element(
        self,
        group: ET.Element,
        node: ir.ShapedText,
        run: ir.GlyphRun,
        pen_x: float,
        layout: tuple[str, float, list[float] | None],
        extra: Mapping[str, str] | None = None,
        post: ir.Matrix | None = None,
    ) -> None:
        content, x0, ys = layout
        face = self._face(run.font)
        resource = face.resource
        self._font_facts[run.font]["as_text"] = True
        family = face.record.info.family_name or resource.postscript_name
        attrs = {
            **(extra or {}),
            # y is flipped once more so the glyph-up text frame sits inside the page's y-down flip.
            "transform": _matrix(
                ir.compose((1, 0, 0, -1, pen_x, node.y + run.rise), post)
                if post is not None and post != ir.IDENTITY
                else (1, 0, 0, -1, pen_x, node.y + run.rise)
            ),
            "x": _num(x0),
            "font-family": family_list(family, resource.family),
            "font-size": _num(run.size),
            "text-rendering": "geometricPrecision",
            f"{{{_XML}}}space": "preserve",
        }
        if ys is not None:
            attrs["y"] = " ".join(_num(v) for v in ys)
        # 模拟的粗斜也这样写：可编辑文字由打开它的软件合成，字体在那边有真粗体时还更好（ADR 0169 修订）
        if resource.bold or run.synth_bold:
            attrs["font-weight"] = "700"
        if resource.italic or run.synth_italic:
            attrs["font-style"] = "italic"
        element = _element(group, "text", **attrs)
        element.text = content

    def _image(self, node: ir.Image, parent: ET.Element, label: str = "") -> None:
        res, data = self._source_bytes(node)
        if node.resource not in self._images:
            try:
                width, height = rasterio.header_size(data, res.kind)
                pixels = width * height
                if pixels > raster.SOURCE_MAX_PIXELS:
                    raise rasterio.RasterDecodeError(
                        "raster_too_large", "Source exceeds pixel budget"
                    )
                if self._raster_pixels + pixels > raster.DOCUMENT_MAX_PIXELS:
                    raise WriterError(
                        "source_unreadable",
                        "Document exceeds pixel budget",
                        {"why": "raster_budget_exceeded"},
                    )
                self._raster_pixels += pixels
                jpeg = rasterio.jpeg_passthrough(
                    data, res.kind, max_pixels=raster.SOURCE_MAX_PIXELS
                )
                # 带方向标记的 JPEG 不直通：各家 SVG 阅读器对 `<image>` 里 Exif 方向照不照办不一致，解码成摆正后的 PNG
                # 才在哪儿都一样（`width` / `height` 已是摆正后的，与解码结果一致）
                if jpeg is not None and jpeg["orientation"] == 1:
                    mime, encoded, channels = "image/jpeg", data, jpeg["components"]
                else:
                    buf = rasterio.decode(data, res.kind, max_pixels=raster.SOURCE_MAX_PIXELS)
                    mime, encoded, channels = "image/png", raster.encode_png(buf), buf.channels
            except rasterio.RasterDecodeError as exc:
                raise WriterError(
                    "source_unreadable",
                    str(exc),
                    {"figure": res.source_id, "object_id": node.object_id, "why": exc.code},
                ) from exc
            name = self._name("image")
            _element(
                self.defs,
                "image",
                id=name,
                x="0",
                y="0",
                width="1",
                height="1",
                preserveAspectRatio="none",
                **{
                    f"{{{XLINK}}}href": f"data:{mime};base64,"
                    + base64.b64encode(encoded).decode("ascii")
                },
            )
            self._images[node.resource] = (
                name,
                {"width": width, "height": height, "channels": channels, "encoding": mime},
            )
        name, facts = self._images[node.resource]
        group, pl = self._placed(parent, node, (0, 0, 1, 1))
        group.attrib.update(self._id(label))
        _element(group, "use", transform="matrix(1 0 0 -1 0 1)", **{f"{{{XLINK}}}href": "#" + name})
        self.facts.images.append(
            {
                **facts,
                "resource": node.resource,
                "object_id": node.object_id,
                "matrix": list(pl.matrix),
                "clip": list(pl.clip),
            }
        )

    def _imported(self, node: ir.ImportedPage, parent: ET.Element, label: str = "") -> None:
        res, _ = self._source_bytes(node)
        data = self.imported_svgs.get(node.resource)
        if data is None or res.svg_sha256 is None or node.page_index != 0:
            raise UnsupportedCapability("imported_page", "svg_source_unavailable", node.object_id)
        if hashlib.sha256(data).hexdigest() != res.svg_sha256:
            raise WriterError(
                "source_identity",
                "SVG bytes do not match the frozen companion identity",
                {"resource": node.resource, "object_id": node.object_id},
            )
        if node.resource not in self._sources:
            try:
                root = None
                text_data = self.imported_text_svgs.get(node.resource)
                if (
                    text_data is not None
                    and self.text_policy == TEXT_POLICY_TEXT
                    and res.kind != "svg"
                ):
                    try:
                        root = parse_svg(text_data)
                    except (SVGError, ValueError):
                        root = None
                if root is None:
                    if res.kind == "svg" and res.origin == "static":
                        from .. import svgasset

                        data, size = svgasset.inspect_bytes(data)
                        self._static_svg_sizes[node.resource] = (size["w_pt"], size["h_pt"])
                    root = parse_svg(data, unicode_ids=True)
                for element in root.iter():
                    if not element.tag.startswith("{"):
                        element.tag = f"{{{SVG}}}{element.tag}"
                texts = sum(1 for n in root.iter() if n.tag == f"{{{SVG}}}text")
                if texts and self.text_policy != TEXT_POLICY_TEXT:
                    raise UnsupportedCapability(
                        "text", "svg_source_text_not_outlined", node.object_id
                    )
                self.facts.imported_text_elements += texts
                values = [float(v) for v in root.get("viewBox", "").replace(",", " ").split()]
                if ir.is_native_svg(res.kind, res.origin):
                    # Native SVG may establish its user coordinate system from
                    # CSS-pixel intrinsic lengths without an explicit viewBox.
                    from ..svgscene import _physical_size

                    if res.origin == "static":
                        w_pt, h_pt = self._static_svg_sizes[node.resource]
                        view_box = root.get("viewBox")
                    else:
                        w_pt, h_pt, view_box = _physical_size(root)
                    if view_box is None:
                        root.set("viewBox", f"0 0 {_num(w_pt * 96 / 72)} {_num(h_pt * 96 / 72)}")
                        values = [0, 0, w_pt * 96 / 72, h_pt * 96 / 72]
                if (
                    len(values) != 4
                    or not all(math.isfinite(v) for v in values)
                    or min(values[2:]) <= 0
                ):
                    raise ValueError("A finite, positive SVG viewBox is required")
                self._sources[node.resource] = root
            except (SVGError, ValueError) as exc:
                raise WriterError(
                    "source_unreadable",
                    str(exc),
                    {
                        "resource": node.resource,
                        "object_id": node.object_id,
                        "why": getattr(exc, "code", "svg_viewbox"),
                    },
                ) from exc
        root = copy.deepcopy(self._sources[node.resource])
        native_svg = ir.is_native_svg(res.kind, res.origin)
        if native_svg:
            from ..svgscene import _physical_size

            if res.origin == "static":
                width, height = self._static_svg_sizes[node.resource]
            else:
                width, height, _ = _physical_size(root)
        else:
            _, _, width, height = (float(v) for v in root.get("viewBox").replace(",", " ").split())
        if native_svg and res.origin == "declarative" and node.crop in (None, (0, 0, 1, 1)):
            # The nested SVG viewport already clips its physical source frame.
            # A second coincident Path clip multiplies edge coverage (visible
            # at fractional CSS pixels), although no crop was requested.
            pl = placement.place(
                (0, 0, width, height),
                node.rect,
                rotate_cw_deg=node.rotate_cw_deg,
                flip_h=node.flip_h,
                flip_v=node.flip_v,
            )
            group = _element(parent, "g", transform=_matrix(pl.matrix), opacity=_num(node.opacity))
            root.set("overflow", "hidden")
        else:
            group, pl = self._placed(parent, node, (0, 0, width, height))
        group.attrib.update(self._id(label))
        scope = self._name("source")
        wrapper = _element(group, "g", id=scope, transform=f"matrix(1 0 0 -1 0 {_num(height)})")
        _isolate(root, scope + "-", scope)
        root.set("x", "0")
        root.set("y", "0")
        root.set("width", _num(width))
        root.set("height", _num(height))
        if not native_svg:
            root.set("preserveAspectRatio", "none")
        wrapper.append(root)
        self.facts.transparency_groups += int(node.opacity < 1)
        self.facts.imported_pages.append(
            {
                "resource": node.resource,
                "object_id": node.object_id,
                "matrix": list(pl.matrix),
                "clip": list(pl.clip),
                "opacity": node.opacity,
            }
        )

    def write(self, out: Path) -> WriteFacts:
        if self.page.background is not None:
            self._check("page_background", "")
            _element(
                self.root,
                "rect",
                x="0",
                y="0",
                width=_num(self.page.width_pt),
                height=_num(self.page.height_pt),
                fill=_color(self.page.background),
            )
        canvas = _element(
            self.root, "g", transform=f"matrix(1 0 0 -1 0 {_num(self.page.height_pt)})"
        )
        for node in self.page.children:
            self._emit(node, canvas)
        data = ET.tostring(self.root, encoding="utf-8", xml_declaration=True)
        try:
            parse_svg(data, unicode_ids=True)
        except SVGError as exc:
            raise WriterError("source_unreadable", str(exc), {"why": exc.code}) from exc
        Path(out).write_bytes(data)
        self.facts.sha256 = hashlib.sha256(data).hexdigest()
        self.facts.bytes = len(data)
        return self.facts


def write_svg(
    page: ir.Page,
    out: Path,
    provider: HbFaceProvider,
    files: Mapping[str, bytes] | None = None,
    *,
    imported_svgs: Mapping[str, bytes] | None = None,
    imported_text_svgs: Mapping[str, bytes] | None = None,
    text_policy: str = TEXT_POLICY_TEXT,
) -> WriteFacts:
    """Write a validated, self-contained vector SVG. No PDF → raster fallback exists."""
    return SvgWriter(
        page,
        provider,
        files,
        imported_svgs=imported_svgs,
        imported_text_svgs=imported_text_svgs,
        text_policy=text_policy,
    ).write(out)
