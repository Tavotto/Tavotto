"""Render IR → self-contained SVG, with shaped text represented by glyph outlines (X03).

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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from xml.etree import ElementTree as ET

from . import ir, placement, raster, rasterio
from .hbshaper import HbFaceProvider, require
from .svgcheck import SVGError, parse_svg

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)

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
    text_policy: str = "outlines"
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


_URL_REF = re.compile(r"url\(\s*(['\"]?)#([^\s)'\"]+)\1\s*\)", re.I)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_ID = re.compile(r"#([A-Za-z_][A-Za-z0-9_-]*)")


def _isolate(root: ET.Element, prefix: str, scope: str) -> None:
    """Rewrite only validated local references; CSS selectors are scoped per instance.

    svgcheck admits flat rules and a deliberately small selector grammar, so this
    does not attempt to parse arbitrary CSS. In particular colors in declarations
    are never mistaken for ID selectors.
    """
    ids = {node.get("id"): prefix + node.get("id") for node in root.iter() if node.get("id")}

    def urls(value: str) -> str:
        return _URL_REF.sub(lambda m: f"url(#{ids[m.group(2)]})", value)

    for node in root.iter():
        for key, value in list(node.attrib.items()):
            if key == "id":
                node.set(key, ids[value])
            elif key in ("href", f"{{{XLINK}}}href") and value.startswith("#"):
                node.set(key, "#" + ids[value[1:]])
            else:
                node.set(key, urls(value))
        if node.tag == f"{{{SVG}}}style" and node.text:
            rules = []
            for match in _CSS_RULE.finditer(node.text):
                selectors = _CSS_ID.sub(
                    lambda m: "#" + ids.get(m.group(1), prefix + m.group(1)), match.group(1)
                )
                scoped = ",".join(f"#{scope} {s.strip()}" for s in selectors.split(","))
                rules.append(scoped + "{" + urls(match.group(2)) + "}")
            node.text = "\n".join(rules)


class SvgWriter:
    def __init__(
        self,
        page: ir.Page,
        provider: HbFaceProvider,
        files: Mapping[str, bytes] | None = None,
        *,
        imported_svgs: Mapping[str, bytes] | None = None,
    ) -> None:
        self.page = ir.validate(page)
        self.provider = provider
        self.files = files or {}
        self.imported_svgs = imported_svgs or {}
        self.facts = WriteFacts(page=(page.width_pt, page.height_pt))
        self._counter = 0
        self._faces: dict = {}
        self._glyph_sets: dict = {}
        self._glyphs: dict[tuple[str, int], str] = {}
        self._images: dict[str, tuple[str, dict]] = {}
        self._sources: dict[str, ET.Element] = {}
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

    def _check(self, operation: str, object_id: str) -> None:
        cap = ir.CAPABILITIES["svg"][operation]
        if cap.level == ir.CAP_UNSUPPORTED:
            raise UnsupportedCapability(operation, cap.reason, object_id)

    def _clip(self, parent: ET.Element, segments: tuple, rule: str = "nonzero") -> None:
        name = self._name("clip")
        clip = _element(self.defs, "clipPath", id=name, clipPathUnits="userSpaceOnUse")
        _element(clip, "path", d=_path(segments), **{"clip-rule": rule})
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
        x, y, w, h = pl.clip
        self._clip(
            group, (("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",))
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

    def _emit(self, node: ir.Node, parent: ET.Element) -> None:
        for operation in ir.operations_of(node):
            if operation == "imported_page":
                # This operation is data-dependent: opaque PDF remains unsupported;
                # _imported checks the explicitly frozen SVG companion identity.
                continue
            self._check(operation, node.object_id)
        if isinstance(node, ir.Path):
            self._paint_path(node, parent)
        elif isinstance(node, ir.Group):
            self.facts.groups += 1
            self.facts.transparency_groups += int(node.opacity < 1)
            group = _element(
                parent, "g", transform=_matrix(node.transform), opacity=_num(node.opacity)
            )
            if node.clip is not None:
                self._clip(group, node.clip.segments, node.clip.fill_rule)
            for child in node.children:
                self._emit(child, group)
        elif isinstance(node, ir.ShapedText):
            self._text(node, parent)
        elif isinstance(node, ir.Image):
            self._image(node, parent)
        elif isinstance(node, ir.ImportedPage):
            self._imported(node, parent)

    def _paint_path(self, node: ir.Path, parent: ET.Element) -> None:
        attrs = {
            "d": _path(node.segments),
            "fill": "none",
            "stroke": "none",
            "fill-rule": node.fill_rule,
        }
        if node.fill is not None:
            attrs.update(fill=_color(node.fill.rgb), **{"fill-opacity": _num(node.fill.alpha)})
        if node.stroke is not None:
            stroke = node.stroke
            if stroke.width == 0:
                raise UnsupportedCapability(
                    "path_stroke", "svg_hairline_unsupported", node.object_id
                )
            attrs.update(
                stroke=_color(stroke.paint.rgb),
                **{
                    "stroke-opacity": _num(stroke.paint.alpha),
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
                    "outlined": True,
                    "embedded": False,
                }
            )
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
        name = self._name("glyph")
        _element(self.defs, "path", id=name, d=commands)
        self._glyphs[(key, gid)] = name
        return name

    def _text(self, node: ir.ShapedText, parent: ET.Element) -> None:
        group = _element(
            parent, "g", fill=_color(node.paint.rgb), **{"fill-opacity": _num(node.paint.alpha)}
        )
        pen_x = node.x
        for run in node.runs:
            face = self._face(run.font)
            size = run.size / face.upem
            for glyph in run.glyphs:
                name = self._glyph(run.font, glyph.gid)
                matrix = (
                    size,
                    0,
                    0,
                    size,
                    pen_x + glyph.x_offset * size,
                    node.y + run.rise + glyph.y_offset * size,
                )
                _element(
                    group, "use", **{f"{{{XLINK}}}href": "#" + name, "transform": _matrix(matrix)}
                )
                pen_x += glyph.x_advance * size
                self.facts.outlined_glyphs += 1
                self.facts.notdef_codes += int(glyph.gid == 0)
        self.facts.text_objects += 1

    def _image(self, node: ir.Image, parent: ET.Element) -> None:
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
                if jpeg is not None:
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

    def _imported(self, node: ir.ImportedPage, parent: ET.Element) -> None:
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
                root = parse_svg(data)
                for element in root.iter():
                    if not element.tag.startswith("{"):
                        element.tag = f"{{{SVG}}}{element.tag}"
                if any(n.tag == f"{{{SVG}}}text" for n in root.iter()):
                    raise UnsupportedCapability(
                        "text", "svg_source_text_not_outlined", node.object_id
                    )
                values = [float(v) for v in root.get("viewBox", "").replace(",", " ").split()]
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
        _, _, width, height = (float(v) for v in root.get("viewBox").replace(",", " ").split())
        group, pl = self._placed(parent, node, (0, 0, width, height))
        scope = self._name("source")
        wrapper = _element(group, "g", id=scope, transform=f"matrix(1 0 0 -1 0 {_num(height)})")
        _isolate(root, scope + "-", scope)
        root.set("x", "0")
        root.set("y", "0")
        root.set("width", _num(width))
        root.set("height", _num(height))
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
            parse_svg(data)
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
) -> WriteFacts:
    """Write a validated, self-contained vector SVG. No PDF → raster fallback exists."""
    return SvgWriter(page, provider, files, imported_svgs=imported_svgs).write(out)
