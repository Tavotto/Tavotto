"""Bounded, static SVG import boundary shared by the writer and artifact inspector.

Only UTF-8 XML, static SVG and inert Matplotlib RDF metadata are accepted. This is
validation, not a lossy sanitizer: rejected input never becomes a partial figure.
The sole normalization helper removes the exact inert Matplotlib DTD declaration;
callers must use it only at the trusted worker-generation boundary, before hashing.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from xml.etree import ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_METADATA_NS = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://purl.org/dc/elements/1.1/",
    "http://creativecommons.org/ns#",
}
_STATIC_ELEMENTS = {
    "svg",
    "g",
    "defs",
    "symbol",
    "use",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "clipPath",
    "mask",
    "pattern",
    "marker",
    "linearGradient",
    "radialGradient",
    "stop",
    "image",
    "text",
    "tspan",
    "textPath",
    "title",
    "desc",
    "metadata",
    "style",
}
_CSS_PROPERTIES = {
    "fill",
    "fill-opacity",
    "fill-rule",
    "stroke",
    "stroke-opacity",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "opacity",
    "clip-path",
    "clip-rule",
    "mask",
    "marker-start",
    "marker-mid",
    "marker-end",
    "color",
    "color-interpolation",
    "color-rendering",
    "shape-rendering",
    "image-rendering",
    "text-rendering",
    "vector-effect",
    "paint-order",
    "display",
    "visibility",
    "font",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "font-variant",
    "font-stretch",
    "font-size-adjust",
    "text-anchor",
    "dominant-baseline",
    "alignment-baseline",
    "baseline-shift",
    "letter-spacing",
    "word-spacing",
    "text-decoration",
    "white-space",
    "stop-color",
    "stop-opacity",
}
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*\Z")
_URL = re.compile(r"url\(\s*(['\"]?)(#[A-Za-z_][A-Za-z0-9_.:-]*)\1\s*\)", re.I)
_SELECTOR = re.compile(r"[A-Za-z0-9_.*#\s,>\-]+\Z")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECLARATION = re.compile(r"\s*([a-z][a-z-]*)\s*:\s*(.*)\Z", re.S | re.I)
_MATPLOTLIB_DOCTYPE = (
    b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"\n'
    b'  "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
)


@dataclass(frozen=True)
class SVGBudget:
    max_bytes: int = 32 * 1024 * 1024
    max_depth: int = 128
    max_elements: int = 200_000
    max_attributes: int = 64
    max_attribute_chars: int = 24 * 1024 * 1024
    max_text_chars: int = 4 * 1024 * 1024
    max_image_bytes: int = 16 * 1024 * 1024
    max_image_pixels: int = 100_000_000
    max_expanded_elements: int = 1_000_000


class SVGError(ValueError):
    """Stable reason code, without reflecting document contents or paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def strip_matplotlib_doctype(data: bytes) -> bytes:
    """Remove only Matplotlib's exact stock declaration, never a custom DTD/subset."""
    return data.replace(_MATPLOTLIB_DOCTYPE, b"", 1)


class _BoundedBuilder(ET.TreeBuilder):
    def __init__(self, budget: SVGBudget):
        super().__init__()
        self.budget = budget
        self.depth = self.elements = self.attribute_chars = self.text_chars = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.elements += 1
        self.attribute_chars += sum(len(k) + len(v) for k, v in attrs.items())
        if (
            self.depth > self.budget.max_depth
            or self.elements > self.budget.max_elements
            or len(attrs) > self.budget.max_attributes
            or self.attribute_chars > self.budget.max_attribute_chars
        ):
            raise SVGError("svg_resource_limit")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def data(self, data):
        self.text_chars += len(data)
        if self.text_chars > self.budget.max_text_chars:
            raise SVGError("svg_resource_limit")
        return super().data(data)

    def doctype(self, name, pubid, system):
        raise SVGError("svg_dtd_forbidden")

    def pi(self, target, data):
        raise SVGError("svg_processing_instruction_forbidden")


def _name(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        return tuple(tag[1:].split("}", 1))
    return "", tag


def _css_value(value: str) -> list[str]:
    # Reject escapes/comments rather than attempting a second, incomplete CSS lexer.
    if any(token in value for token in ("\\", "/*", "*/", "@", "{", "}", "<", ">")):
        raise SVGError("svg_unsafe_css")
    refs = [m.group(2)[1:] for m in _URL.finditer(value)]
    rest = _URL.sub("", value)
    if re.search(r"url\s*\(|expression\s*\(|var\s*\(|javascript\s*:|vbscript\s*:", rest, re.I):
        raise SVGError("svg_external_resource")
    # Color functions are the only functions needed by static figure presentation.
    if any(
        f.lower() not in ("rgb", "rgba", "hsl", "hsla")
        for f in re.findall(r"(?<![\w-])([\w-]+)\s*\(", rest)
    ):
        raise SVGError("svg_unsafe_css")
    return refs


def _declarations(css: str) -> list[str]:
    refs: list[str] = []
    for part in css.split(";"):
        if not part.strip():
            continue
        match = _DECLARATION.fullmatch(part)
        if not match or match.group(1).lower() not in _CSS_PROPERTIES:
            raise SVGError("svg_unsafe_css")
        refs.extend(_css_value(match.group(2)))
    return refs


def _stylesheet(css: str) -> list[str]:
    refs: list[str] = []
    pos = 0
    while pos < len(css):
        if not css[pos:].strip():
            break
        match = _RULE.match(css, pos)
        if not match or not _SELECTOR.fullmatch(match.group(1).strip()):
            raise SVGError("svg_unsafe_css")
        if any(not selector.strip() for selector in match.group(1).split(",")):
            raise SVGError("svg_unsafe_css")
        refs.extend(_declarations(match.group(2)))
        pos = match.end()
    return refs


def _image_data(value: str, budget: SVGBudget) -> tuple[int, int]:
    match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=\s]+)", value)
    if not match:
        raise SVGError("svg_external_resource")
    encoded = re.sub(r"\s", "", match.group(2))
    if len(encoded) > (budget.max_image_bytes + 2) // 3 * 4:
        raise SVGError("svg_resource_limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SVGError("svg_invalid_image") from exc
    if len(raw) > budget.max_image_bytes:
        raise SVGError("svg_resource_limit")
    width = height = 0
    if match.group(1) == "png":
        if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[8:16] != b"\0\0\0\rIHDR":
            raise SVGError("svg_invalid_image")
        width, height = struct.unpack(">II", raw[16:24])
    else:
        if raw[:2] != b"\xff\xd8":
            raise SVGError("svg_invalid_image")
        pos = 2
        while pos + 4 <= len(raw):
            if raw[pos] != 0xFF:
                break
            marker = raw[pos + 1]
            if marker == 0xFF:
                pos += 1
                continue
            if marker in (0xD9, 0xDA):
                break
            length = int.from_bytes(raw[pos + 2 : pos + 4], "big")
            if length < 2 or pos + 2 + length > len(raw):
                break
            if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
                height, width = struct.unpack(">HH", raw[pos + 5 : pos + 9])
                break
            pos += 2 + length
    if width <= 0 or height <= 0:
        raise SVGError("svg_invalid_image")
    if width * height > budget.max_image_pixels:
        raise SVGError("svg_resource_limit")
    return len(raw), width * height


def _references(nodes, edges, refs, ids, budget):
    for index, names in refs.items():
        for name in names:
            if name not in ids:
                raise SVGError("svg_missing_reference")
            edges[index].append(ids[name])
    # Child and resource edges together catch use -> ancestor recursion as well as
    # indirect gradient/pattern cycles. Memoized expansion also bounds exponential use DAGs.
    done: dict[int, int] = {}
    active: set[int] = set()
    for root in range(len(nodes)):
        stack = [(root, False)]
        while stack:
            node, finish = stack.pop()
            if node in done:
                continue
            if finish:
                count = 1 + sum(done[child] for child in edges[node])
                if count > budget.max_expanded_elements:
                    raise SVGError("svg_resource_limit")
                done[node] = count
                active.remove(node)
                continue
            if node in active:
                raise SVGError("svg_reference_cycle")
            active.add(node)
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(edges[node]))


def parse_svg(data: bytes, *, budget: SVGBudget | None = None) -> ET.Element:
    """Parse a bounded safe static SVG, validating every local resource reference."""
    budget = budget or SVGBudget()
    if len(data) > budget.max_bytes:
        raise SVGError("svg_resource_limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SVGError("svg_encoding_unsupported") from exc
    if "\x00" in text:
        raise SVGError("svg_encoding_unsupported")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise SVGError("svg_dtd_forbidden")
    encoding = re.match(r"\s*<\?xml\b[^?]*\bencoding\s*=\s*(['\"])(.*?)\1", text, re.I)
    if encoding and encoding.group(2).lower().replace("-", "") != "utf8":
        raise SVGError("svg_encoding_unsupported")
    try:
        root = ET.fromstring(text, parser=ET.XMLParser(target=_BoundedBuilder(budget)))
    except (ET.ParseError, LookupError) as exc:
        raise SVGError("svg_unparseable") from exc
    if _name(root.tag) not in ((SVG_NS, "svg"), ("", "svg")):
        raise SVGError("svg_invalid_root")
    nodes = list(root.iter())
    indices = {id(node): i for i, node in enumerate(nodes)}
    edges = [[indices[id(child)] for child in node] for node in nodes]
    ids: dict[str, int] = {}
    refs: dict[int, list[str]] = {}
    metadata: set[int] = set()
    image_bytes = image_pixels = 0
    for index, node in enumerate(nodes):
        ns, tag = _name(node.tag)
        in_metadata = index in metadata or (ns in ("", SVG_NS) and tag == "metadata")
        if in_metadata:
            metadata.update(edges[index])
        if ns not in ("", SVG_NS):
            if not in_metadata or ns not in _METADATA_NS:
                raise SVGError("svg_foreign_namespace")
        elif tag not in _STATIC_ELEMENTS:
            raise SVGError("svg_unsafe_element")
        if tag == "style":
            if node.get("type", "text/css") != "text/css" or len(node):
                raise SVGError("svg_unsafe_css")
            refs[index] = _stylesheet(node.text or "")
        for key, value in node.attrib.items():
            ans, attr = _name(key)
            if attr.lower().startswith("on") or (ans == _XML_NS and attr == "base"):
                raise SVGError("svg_unsafe_attribute")
            if ans not in ("", _XML_NS, XLINK_NS) and not (in_metadata and ans in _METADATA_NS):
                raise SVGError("svg_foreign_namespace")
            if ans == _XML_NS and attr not in ("lang", "space"):
                raise SVGError("svg_unsafe_attribute")
            if attr == "id":
                if ans or not _ID.fullmatch(value) or value in ids:
                    raise SVGError("svg_invalid_id")
                ids[value] = index
            elif attr == "href":
                if value.startswith("#") and _ID.fullmatch(value[1:]):
                    refs.setdefault(index, []).append(value[1:])
                elif tag == "image" and ns in ("", SVG_NS):
                    size, pixels = _image_data(value, budget)
                    image_bytes += size
                    image_pixels += pixels
                    if (
                        image_bytes > budget.max_image_bytes
                        or image_pixels > budget.max_image_pixels
                    ):
                        raise SVGError("svg_resource_limit")
                else:
                    raise SVGError("svg_external_resource")
            elif attr == "style":
                refs.setdefault(index, []).extend(_declarations(value))
            elif attr.lower() in ("src", "base"):
                raise SVGError("svg_external_resource")
            elif attr in _CSS_PROPERTIES or (
                not in_metadata and re.search(r"url\s*\(|\\", value, re.I)
            ):
                refs.setdefault(index, []).extend(_css_value(value))
        if tag in ("image", "use", "textPath") and not any(
            _name(k)[1] == "href" for k in node.attrib
        ):
            raise SVGError("svg_missing_reference")
    _references(nodes, edges, refs, ids, budget)
    return root
