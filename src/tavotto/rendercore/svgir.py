"""Safe static SVG → existing RenderCore IR (no second rendering pipeline).

Coordinates stay local until one root viewBox/physical-point mapping. Each DOM
transform and opacity appears exactly once. Text/images get one local reflection
because their IR payloads are y-up. Unsupported layout or paint features fail
explicitly instead of quietly disappearing from exported figures.
"""

from __future__ import annotations

import base64
import colorsys
import hashlib
import math
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from tavotto import svgcoord
from tavotto.rendercore import ir, typography
from tavotto.rendercore.fonts import FontsUnavailable
from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
ERROR_CODES = (
    "svgir_gradient",
    "svgir_mask",
    "svgir_filter",
    "svgir_arc",
    "svgir_unsupported_element",
    "svgir_font_missing",
    "svgir_invalid",
)


class SVGIRError(ValueError):
    def __init__(self, code: str, detail: str = "", element: str = "") -> None:
        if code not in ERROR_CODES:
            raise AssertionError(f"undisclosed svgir error: {code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.element = code, detail, element


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(_NUM)
_TOKEN_RE = re.compile(rf"[A-Za-z]|{_NUM}")
_REF_RE = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)")
_DATA_URI_RE = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=\s]+)$")


def _tag(el):
    return el.tag.split("}")[-1]


def _number(value):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise SVGIRError("svgir_invalid", "invalid number") from exc
    if not math.isfinite(result):
        raise SVGIRError("svgir_invalid", "non-finite number")
    return result


def _numbers(value):
    if _NUM_RE.sub("", value).strip(" ,\t\n\r"):
        raise SVGIRError("svgir_invalid", "invalid number list")
    return [_number(v) for v in _NUM_RE.findall(value)]


def _length(value, *, percent=1, em=16):
    """Local SVG user units; absolute units use CSS px (96/in)."""
    m = re.fullmatch(rf"\s*({_NUM})(px|pt|pc|in|cm|mm|q|em|ex|%)?\s*", str(value))
    if not m:
        raise SVGIRError("svgir_unsupported_element", "unsupported length")
    factors = {
        None: 1,
        "px": 1,
        "pt": 96 / 72,
        "pc": 16,
        "in": 96,
        "cm": 96 / 2.54,
        "mm": 96 / 25.4,
        "q": 96 / 101.6,
        "em": em,
        "ex": em / 2,
        "%": percent / 100,
    }
    return _number(m[1]) * factors[m[2]]


def _parse_transform(value):
    try:
        return svgcoord.parse_transform(value)
    except ValueError as exc:
        raise SVGIRError("svgir_invalid", "invalid transform") from exc


def _arc(cur, rx, ry, rotation, large, sweep, end):
    """Endpoint arc → <=90° cubic pieces (SVG 1.1 implementation notes)."""
    if large not in (0, 1) or sweep not in (0, 1):
        raise SVGIRError("svgir_invalid", "arc flags must be 0 or 1")
    if cur == end:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [("L", *end)]
    phi = math.radians(rotation % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (cur[0] - end[0]) / 2, (cur[1] - end[1]) / 2
    xp, yp = cp * dx + sp * dy, -sp * dx + cp * dy
    size = (xp / rx) ** 2 + (yp / ry) ** 2
    if size > 1:
        rx, ry = rx * math.sqrt(size), ry * math.sqrt(size)
    den = (rx * yp) ** 2 + (ry * xp) ** 2
    coef = math.sqrt(max(0, ((rx * ry) ** 2 - den) / den)) if den else 0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * yp / ry, -coef * ry * xp / rx
    cx = cp * cxp - sp * cyp + (cur[0] + end[0]) / 2
    cy = sp * cxp + cp * cyp + (cur[1] + end[1]) / 2
    start = math.atan2((yp - cyp) / ry, (xp - cxp) / rx)
    finish = math.atan2((-yp - cyp) / ry, (-xp - cxp) / rx)
    delta = (finish - start) % (2 * math.pi)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    count = max(1, math.ceil(abs(delta) / (math.pi / 2)))

    def point(t):
        return cx + cp * rx * math.cos(t) - sp * ry * math.sin(t), cy + sp * rx * math.cos(
            t
        ) + cp * ry * math.sin(t)

    def tangent(t):
        return -cp * rx * math.sin(t) - sp * ry * math.cos(t), -sp * rx * math.sin(
            t
        ) + cp * ry * math.cos(t)

    result = []
    for i in range(count):
        a, b = start + delta * i / count, start + delta * (i + 1) / count
        k = 4 / 3 * math.tan((b - a) / 4)
        p, q, u, v = point(a), point(b), tangent(a), tangent(b)
        result.append(("C", p[0] + k * u[0], p[1] + k * u[1], q[0] - k * v[0], q[1] - k * v[1], *q))
    result[-1] = (*result[-1][:-2], *end)
    return result


def _parse_path_d(data):
    """Normalize relative/absolute M/L/H/V/C/S/Q/T/A/Z to absolute M/L/C/Z."""
    if _TOKEN_RE.sub("", data).strip(" ,\t\n\r"):
        raise SVGIRError("svgir_invalid", "invalid path syntax")
    tokens, result = _TOKEN_RE.findall(data), []
    cur = start = (0.0, 0.0)
    cubic = quadratic = None
    op, previous, i = "", "", 0
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}
    while i < len(tokens):
        explicit = tokens[i].isalpha()
        if explicit:
            op, i = tokens[i], i + 1
        upper = op.upper()
        if upper == "Z":
            if not explicit or not result:
                raise SVGIRError("svgir_invalid", "invalid closepath")
            result.append(("Z",))
            cur, cubic, quadratic, previous, op = start, None, None, "Z", ""
            continue
        n = arity.get(upper)
        if n is None or i + n > len(tokens) or any(t.isalpha() for t in tokens[i : i + n]):
            raise SVGIRError("svgir_invalid", "incomplete path command")
        if not result and upper != "M":
            raise SVGIRError("svgir_invalid", "path must start with moveto")
        vals, i, rel = [_number(v) for v in tokens[i : i + n]], i + n, op.islower()

        def xy(a, b):
            return (a + cur[0], b + cur[1]) if rel else (a, b)

        next_cubic = next_quad = None
        if upper in ("M", "L", "T"):
            end = xy(*vals)
            if upper == "T":
                control = (
                    (2 * cur[0] - quadratic[0], 2 * cur[1] - quadratic[1])
                    if previous in ("Q", "T") and quadratic
                    else cur
                )
                q1 = (
                    cur[0] + 2 / 3 * (control[0] - cur[0]),
                    cur[1] + 2 / 3 * (control[1] - cur[1]),
                )
                q2 = (
                    end[0] + 2 / 3 * (control[0] - end[0]),
                    end[1] + 2 / 3 * (control[1] - end[1]),
                )
                result.append(("C", *q1, *q2, *end))
                next_quad = control
            else:
                result.append((upper, *end))
            if upper == "M":
                start, op = end, "l" if rel else "L"
        elif upper in ("H", "V"):
            end = (
                ((cur[0] + vals[0] if rel else vals[0]), cur[1])
                if upper == "H"
                else (cur[0], (cur[1] + vals[0] if rel else vals[0]))
            )
            result.append(("L", *end))
        elif upper in ("C", "S"):
            if upper == "C":
                c1, c2, end = xy(*vals[:2]), xy(*vals[2:4]), xy(*vals[4:])
            else:
                c1 = (
                    (2 * cur[0] - cubic[0], 2 * cur[1] - cubic[1])
                    if previous in ("C", "S") and cubic
                    else cur
                )
                c2, end = xy(*vals[:2]), xy(*vals[2:])
            result.append(("C", *c1, *c2, *end))
            next_cubic = c2
        elif upper == "Q":
            control, end = xy(*vals[:2]), xy(*vals[2:])
            c1 = (cur[0] + 2 / 3 * (control[0] - cur[0]), cur[1] + 2 / 3 * (control[1] - cur[1]))
            c2 = (end[0] + 2 / 3 * (control[0] - end[0]), end[1] + 2 / 3 * (control[1] - end[1]))
            result.append(("C", *c1, *c2, *end))
            next_quad = control
        else:
            end = xy(*vals[-2:])
            result.extend(_arc(cur, *vals[:5], end))
        cur, cubic, quadratic, previous = end, next_cubic, next_quad, upper
    return result


_NAMED = {
    "white": (1, 1, 1),
    "black": (0, 0, 0),
    "red": (1, 0, 0),
    "green": (0, 128 / 255, 0),
    "blue": (0, 0, 1),
    "orange": (1, 165 / 255, 0),
    "yellow": (1, 1, 0),
    "gray": (128 / 255,) * 3,
    "grey": (128 / 255,) * 3,
    "silver": (192 / 255,) * 3,
    "purple": (128 / 255, 0, 128 / 255),
    "cyan": (0, 1, 1),
    "aqua": (0, 1, 1),
    "magenta": (1, 0, 1),
    "fuchsia": (1, 0, 1),
    "lime": (0, 1, 0),
    "navy": (0, 0, 128 / 255),
    "teal": (0, 128 / 255, 128 / 255),
    "maroon": (128 / 255, 0, 0),
    "olive": (128 / 255, 128 / 255, 0),
    "rebeccapurple": (102 / 255, 51 / 255, 153 / 255),
}


def _parse_color(value, current="black"):
    value = value.strip().lower()
    if value in ("none", "transparent"):
        return None
    if value == "currentcolor":
        return _parse_color(current if current.lower() != "currentcolor" else "black")
    if _REF_RE.fullmatch(value):
        raise SVGIRError("svgir_gradient", "paint server is unsupported")
    if value in _NAMED:
        return _NAMED[value], 1
    if re.fullmatch(r"#[\da-f]{3,4}|#[\da-f]{6}(?:[\da-f]{2})?", value):
        h = value[1:]
        if len(h) < 5:
            h = "".join(c * 2 for c in h)
        channels = [int(h[i : i + 2], 16) / 255 for i in range(0, len(h), 2)]
        return tuple(channels[:3]), channels[3] if len(channels) == 4 else 1
    match = re.fullmatch(r"(rgb|rgba|hsl|hsla)\((.*)\)", value)
    if match:
        p = re.split(r"\s*[,/]\s*|\s+", match[2].strip())
        if len(p) not in (3, 4):
            raise SVGIRError("svgir_invalid", "invalid color function")
        alpha = (
            _number(p[3][:-1]) / 100
            if len(p) == 4 and p[3].endswith("%")
            else _number(p[3])
            if len(p) == 4
            else 1
        )
        if match[1].startswith("rgb"):
            rgb = tuple(
                min(1, max(0, _number(c[:-1]) / 100 if c.endswith("%") else _number(c) / 255))
                for c in p[:3]
            )
        else:
            hue = _number(p[0].removesuffix("deg")) % 360 / 360
            sat, light = (_number(c.removesuffix("%")) / 100 for c in p[1:3])
            rgb = colorsys.hls_to_rgb(hue, min(1, max(0, light)), min(1, max(0, sat)))
        return rgb, min(1, max(0, alpha))
    raise SVGIRError("svgir_unsupported_element", "unsupported color")


_INHERITED = frozenset(
    (
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-opacity",
        "stroke-width",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "color",
        "visibility",
        "clip-rule",
        "dominant-baseline",
        "alignment-baseline",
        XML_SPACE,
    )
)
_PRESENTATION = _INHERITED | {
    "opacity",
    "overflow",
    "clip-path",
    "display",
    "mask",
    "filter",
    "font",
    "marker-start",
    "marker-mid",
    "marker-end",
    "vector-effect",
    "paint-order",
    "letter-spacing",
    "word-spacing",
    "baseline-shift",
    "text-decoration",
}
_DEFAULT = {
    "fill": "black",
    "stroke": "none",
    "color": "black",
    "font-family": "sans-serif",
    "font-size": "16",
    "font-weight": "normal",
    "font-style": "normal",
    "overflow": "visible",
}


def _declarations(value):
    return [
        (k.strip().lower(), v.strip())
        for d in value.split(";")
        if ":" in d
        for k, v in [d.split(":", 1)]
    ]


def _font_declarations(value):
    keys = ("font-size", "font-family", "font-style", "font-weight")
    if value in ("inherit", "initial"):
        return dict.fromkeys(keys, value)
    m = re.fullmatch(rf"(.*?)({_NUM}(?:px|pt|em|%))(?:\s*/\s*[^ ]+)?\s+(.+)", value)
    if not m:
        raise SVGIRError("svgir_unsupported_element", "unsupported font shorthand")
    tokens = m[1].split()
    weight = next(
        (token for token in tokens if token in ("bold", "bolder", "lighter") or token.isdigit()),
        "normal",
    )
    return {
        "font-size": m[2],
        "font-family": m[3],
        "font-style": "italic"
        if "italic" in tokens
        else "oblique"
        if "oblique" in tokens
        else "normal",
        "font-weight": weight,
    }


def _simple_selector(el, value):
    tag = re.match(r"^[\w*-]+", value)
    if tag and tag[0] != "*" and tag[0] != _tag(el):
        return False
    for kind, name in re.findall(r"([.#])([\w-]+)", value):
        if kind == "#" and el.get("id") != name:
            return False
        if kind == "." and name not in el.get("class", "").split():
            return False
    return True


def _transformed_segments(segments, matrix):
    return tuple(
        (s[0], *(v for i in range(1, len(s), 2) for v in ir.apply(matrix, s[i], s[i + 1])))
        for s in segments
    )


@dataclass(slots=True)
class _TextCharacter:
    char: str
    style: dict
    owners: tuple[ET.Element, ...]
    angle: float
    preserve: bool


@dataclass(slots=True)
class _TextGlyph:
    glyph: ir.Glyph
    font: str
    face: typography.Face
    size: float
    character: _TextCharacter
    index: int
    x: float = 0
    y: float = 0

    @property
    def advance(self):
        return self.glyph.x_advance * self.size / self.face.resource.units_per_em


class _Translator:
    _FAMILY_MAP = {
        "dejavu sans": "sans-serif",
        "dejavu sans mono": "monospace",
        "dejavu serif": "serif",
        "arial": "sans-serif",
        "helvetica": "sans-serif",
        "times new roman": "serif",
        "courier new": "monospace",
        "liberation sans": "sans-serif",
        "liberation serif": "serif",
        "liberation mono": "monospace",
        "noto sans sc": "cjk",
    }

    def __init__(self, root, w_pt, h_pt, fonts, files):
        self.root, self.w_pt, self.h_pt, self.fonts, self.files = root, w_pt, h_pt, fonts, files
        self.resources, self.faces, self.font_cache, self.notes = {}, {}, {}, []
        self.ids = {e.get("id"): e for e in root.iter() if e.get("id")}
        self.parents = {c: p for p in root.iter() for c in p}
        self.rules = []
        for element in root.iter():
            if _tag(element) == "style":
                for selectors, declarations in re.findall(
                    r"([^{}]+)\{([^{}]*)\}", element.text or ""
                ):
                    for selector in selectors.split(","):
                        spec = (
                            selector.count("#"),
                            selector.count("."),
                            len(re.findall(r"(?:^|[\s>])[A-Za-z][\w-]*", selector)),
                        )
                        self.rules.append(
                            (spec, len(self.rules), selector.strip(), _declarations(declarations))
                        )
        self.rules.sort(key=lambda rule: rule[:2])
        vb = _numbers(root.get("viewBox", ""))
        if vb:
            if len(vb) != 4 or min(vb[2:]) <= 0:
                raise SVGIRError("svgir_invalid", "invalid viewBox")
            self.viewport = vb[2], vb[3]
        else:
            self.viewport = (
                _length(root.get("width", str(w_pt * 96 / 72))),
                _length(root.get("height", str(h_pt * 96 / 72))),
            )
            vb = [0, 0, *self.viewport]
        try:
            self.root_map = svgcoord.viewbox_to_pt(vb, w_pt, h_pt, root.get("preserveAspectRatio"))
        except ValueError as exc:
            raise SVGIRError("svgir_invalid", "invalid viewport mapping") from exc

        self.active_viewports = {root: self.viewport}

    def _matches(self, el, selector):
        parts = re.findall(r"[^\s>]+|>", selector)
        simple = r"(?:[A-Za-z_][\w-]*|\*)?(?:[.#][\w-]+)*"
        if not parts or parts[0] == ">" or parts[-1] == ">":
            return False
        if any(p != ">" and not re.fullmatch(simple, p) for p in parts):
            return False
        if any(a == b == ">" for a, b in zip(parts, parts[1:])):
            return False

        def match(node, index):
            if node is None or not _simple_selector(node, parts[index]):
                return False
            if index == 0:
                return True
            parent = self.parents.get(node)
            if parts[index - 1] == ">":
                return match(parent, index - 2)
            while parent is not None:
                if match(parent, index - 1):
                    return True
                parent = self.parents.get(parent)
            return False

        return bool(parts) and match(el, len(parts) - 1)

    def _style(self, el, parent):
        result = {k: v for k, v in parent.items() if k in _INHERITED}
        # SVG's UA rule clips non-root svg/symbol viewports. Overflow is not
        # inherited, but explicit inherit must see the parent's computed value.
        default_overflow = (
            "hidden"
            if _tag(el) == "symbol" or (_tag(el) == "svg" and el is not self.root)
            else "visible"
        )
        result["overflow"] = default_overflow
        own, priorities = {}, {}

        def apply(declarations, specificity, order):
            for key, value in declarations:
                important = re.search(r"\s*!important\s*$", value, re.I)
                if important:
                    value = value[: important.start()].strip()
                priority = (bool(important), *specificity, order)
                expanded = _font_declarations(value) if key == "font" else {key: value}
                for name, val in expanded.items():
                    if priority >= priorities.get(name, (False, -1, -1, -1, -1, -1)):
                        priorities[name], own[name] = priority, val

        apply(((k, v) for k, v in el.attrib.items() if k in _PRESENTATION), (0, 0, 0, 0), -1)
        for spec, order, selector, declarations in self.rules:
            if self._matches(el, selector):
                apply(declarations, (0, *spec), order)
        apply(_declarations(el.get("style", "")), (1, 0, 0, 0), 0)
        for k, v in own.items():
            if k == "overflow" and v in ("unset", "revert", "revert-layer"):
                result[k] = "visible" if v == "unset" else default_overflow
                continue
            if v == "inherit":
                if k in parent:
                    result[k] = parent[k]
            elif v == "initial":
                if k in _DEFAULT:
                    result[k] = _DEFAULT[k]
                else:
                    result.pop(k, None)
            else:
                result[k] = v
        parent_size = _number(parent.get("font-size", "16"))
        if "font-size" in own and own["font-size"] not in ("inherit", "initial"):
            result["font-size"] = str(
                _length(own["font-size"], percent=parent_size, em=parent_size)
            )
        return result

    def _viewport_mapping(self, el, width, height, *, allow_zero=False):
        """One viewBox authority for symbol instances and nested SVGs."""
        vb = _numbers(el.get("viewBox", ""))
        if not vb:
            return ir.IDENTITY, (width, height)
        if len(vb) != 4 or min(vb[2:]) < 0:
            raise SVGIRError("svgir_invalid", "invalid viewport viewBox")
        if min(vb[2:]) == 0:
            if allow_zero:
                return None, (vb[2], vb[3])
            raise SVGIRError("svgir_invalid", "zero viewport viewBox")
        try:
            mapping = svgcoord.viewbox_to_pt(vb, width, height, el.get("preserveAspectRatio"))
        except ValueError as exc:
            raise SVGIRError("svgir_invalid", "invalid viewport mapping") from exc
        return mapping, (vb[2], vb[3])

    @staticmethod
    def _viewport_clip(width, height):
        return ir.Path(
            (("M", 0, 0), ("L", width, 0), ("L", width, height), ("L", 0, height), ("Z",))
        )

    def _viewport_size(self, el, st, viewport_size=None):
        size = viewport_size or (None, None)
        lengths = [
            override if override is not None else el.get(key, "100%")
            for key, override in zip(("width", "height"), size)
        ]
        width, height = (
            self._len("100%" if value == "auto" else value, axis, st)
            for axis, value in enumerate(lengths)
        )
        if min(width, height) < 0:
            raise SVGIRError("svgir_invalid", "negative SVG viewport")
        return width, height

    def _nested_svg(self, el, st, local, viewport_size):
        # Element x/y/size resolve in the parent user space, before changing the
        # percentage basis for descendants.
        width, height = self._viewport_size(el, st, viewport_size)
        if min(width, height) == 0:
            return []
        offset = ir.translate(
            self._len(el.get("x", "0"), st=st), self._len(el.get("y", "0"), 1, st)
        )
        mapping, viewport = self._viewport_mapping(el, width, height, allow_zero=True)
        if mapping is None:
            return []
        saved_viewport = self.viewport
        active = self.active_viewports.get(el)
        try:
            self.viewport = viewport
            self.active_viewports[el] = viewport
            children = [node for child in el for node in self._node(child, st)]
            if not children:
                return []
            clip_ref = st.get("clip-path", "none")
            user_clip = self._clip(clip_ref) if clip_ref != "none" else None
            mapped = ir.Group(
                tuple(children),
                transform=mapping,
                clip=user_clip,
                opacity=min(1, max(0, _length(st.get("opacity", "1"), percent=1))),
            )
        finally:
            self.viewport = saved_viewport
            if active is None:
                self.active_viewports.pop(el, None)
            else:
                self.active_viewports[el] = active
        # SVG overflow's implicit clip is replaced by an explicit clip-path on
        # this same element (SVG 1.1 §14.3.3). Ancestor clips still intersect.
        # Its rectangle belongs outside the content's viewBox transformation.
        clip = (
            self._viewport_clip(width, height)
            if user_clip is None and st["overflow"] in ("hidden", "scroll")
            else None
        )
        return [
            ir.Group(
                (mapped,),
                transform=ir.compose(offset, local),
                clip=clip,
                object_id=el.get("id", ""),
            )
        ]

    def _len(self, value, axis=0, st=None):
        size = _number((st or {}).get("font-size", "16"))
        ref = self.viewport[axis] if axis in (0, 1) else math.hypot(*self.viewport) / math.sqrt(2)
        return _length(value, percent=ref, em=size)

    def _shape(self, el, st=None):
        tag = _tag(el)

        def f(key, default=0, axis=0):
            return self._len(el.get(key, str(default)), axis, st)

        if tag == "path":
            return _parse_path_d(el.get("d", ""))
        if tag == "line":
            return [("M", f("x1"), f("y1", axis=1)), ("L", f("x2"), f("y2", axis=1))]
        if tag in ("polygon", "polyline"):
            p = _numbers(el.get("points", ""))
            if len(p) % 2:
                raise SVGIRError("svgir_invalid", "odd polygon coordinates")
            return [("M" if i == 0 else "L", p[i], p[i + 1]) for i in range(0, len(p), 2)] + (
                [("Z",)] if p and tag == "polygon" else []
            )
        if tag == "rect":
            x, y, w, h = f("x"), f("y", axis=1), f("width"), f("height", axis=1)
            if min(w, h) < 0:
                raise SVGIRError("svgir_invalid", "negative rectangle")
            if min(w, h) == 0:
                return []
            rx = f("rx", f("ry", axis=1))
            ry = f("ry", rx, axis=1)
            rx, ry = min(rx, w / 2), min(ry, h / 2)
            if rx <= 0 or ry <= 0:
                return [("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]
            return _parse_path_d(
                f"M{x + rx} {y} H{x + w - rx} A{rx} {ry} 0 0 1 {x + w} {y + ry} V{y + h - ry} A{rx} {ry} 0 0 1 {x + w - rx} {y + h} H{x + rx} A{rx} {ry} 0 0 1 {x} {y + h - ry} V{y + ry} A{rx} {ry} 0 0 1 {x + rx} {y} Z"
            )
        if tag in ("circle", "ellipse"):
            cx, cy = f("cx"), f("cy", axis=1)
            rx, ry = (f("r", axis=2),) * 2 if tag == "circle" else (f("rx"), f("ry", axis=1))
            if min(rx, ry) < 0:
                raise SVGIRError("svgir_invalid", "negative radius")
            if min(rx, ry) == 0:
                return []
            return [
                ("M", cx + rx, cy),
                *_arc((cx + rx, cy), rx, ry, 0, 1, 1, (cx - rx, cy)),
                *_arc((cx - rx, cy), rx, ry, 0, 1, 1, (cx + rx, cy)),
                ("Z",),
            ]
        raise SVGIRError("svgir_unsupported_element", f"unsupported geometry {tag}")

    def _paint(self, st, key):
        color = _parse_color(st.get(key, "none"), st.get("color", "black"))
        if color is None:
            return None
        rgb, alpha = color
        return ir.Paint(
            rgb, alpha * min(1, max(0, _length(st.get(f"{key}-opacity", "1"), percent=1)))
        )

    def _stroke(self, st):
        paint = self._paint(st, "stroke")
        if paint is None:
            return None
        width = self._len(st.get("stroke-width", "1"), 2, st)
        if width < 0:
            raise SVGIRError("svgir_invalid", "negative stroke width")
        if width == 0:
            return None  # PDF's zero width means a visible device hairline.
        dash = tuple(
            self._len(v, 2, st)
            for v in re.split(r"[\s,]+", st.get("stroke-dasharray", "none").strip())
            if v and v != "none"
        )
        if any(v < 0 for v in dash):
            raise SVGIRError("svgir_invalid", "negative dash")
        if dash and not any(dash):
            dash = ()
        return ir.Stroke(
            paint,
            width,
            cap=st.get("stroke-linecap", "butt"),
            join=st.get("stroke-linejoin", "miter"),
            dash=dash,
            dash_phase=self._len(st.get("stroke-dashoffset", "0"), 2, st),
            miter_limit=_number(st.get("stroke-miterlimit", "4")),
        )

    def _font(self, st):
        if self.fonts is None:
            raise SVGIRError("svgir_font_missing", "approved font provider required")
        family, weight = st.get("font-family", "sans-serif"), st.get("font-weight", "normal")
        bold = weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600)
        italic = st.get("font-style", "normal") in ("italic", "oblique")
        cache = family, bold, italic
        if cache in self.font_cache:
            key = self.font_cache[cache]
            return key, self.faces[key]
        candidates = [p.strip().strip("'\"").lower() for p in family.split(",")]
        mapped = [self._FAMILY_MAP.get(p, p) for p in candidates]
        for candidate in dict.fromkeys([*mapped, "sans-serif"]):
            try:
                face = self.fonts.face_for(candidate, bold, italic)
                break
            except (KeyError, ValueError, FontsUnavailable):
                continue
        else:
            raise SVGIRError("svgir_font_missing", "no approved font")
        if candidate not in candidates:
            self.notes.append(f"font '{candidates[0]}' mapped to {candidate}")
        key = f"font:{face.resource.face_id}"
        self.resources[key], self.faces[key], self.font_cache[cache] = face.resource, face, key
        return key, face

    def _text(self, el, st):
        """Resolve SVG positioning around the existing font shaping authority.

        DOM/style fragments are not text chunks. Only an absolute x/y starts a
        new anchored chunk; relative dx/dy, fill, font and tspan boundaries do
        not. Whitespace and per-element first-addressable-character positions
        are resolved before shaping/placement, then one anchor shift applies to
        every glyph in that chunk. Glyph rotation does not rotate the baseline.
        """
        raw, styles = [], {}
        # Use computed inheritance, including the instantiated <use> parent,
        # rather than walking a referenced text definition's original parents.
        preserve = st.get(XML_SPACE) == "preserve"

        def collect(ch, style, owners=(), angle=0.0, keep=preserve):
            if style.get("display") == "none":
                return
            styles[ch] = style
            owners = (*owners, ch)
            space = ch.get(XML_SPACE)
            if space is not None:
                keep = space == "preserve"
            rotation = _numbers(ch.get("rotate", str(angle)))
            if len(rotation) != 1:
                raise SVGIRError("svgir_unsupported_element", "per-character text rotation")
            angle = rotation[0]

            def append(text):
                raw.extend(_TextCharacter(c, style, owners, angle, keep) for c in text or "")

            append(ch.text)
            for child in ch:
                if _tag(child) != "tspan":
                    raise SVGIRError("svgir_unsupported_element", "textPath is unsupported")
                child_style = self._style(child, style)
                self._check_style(child_style)
                collect(child, child_style, owners, angle, keep)
                append(child.tail)

        collect(el, st)
        chars = []
        for char in raw:
            if char.char in "\t\r\n":
                char.char = " "
            if (
                char.char == " "
                and not char.preserve
                and (not chars or (chars[-1].char == " " and not chars[-1].preserve))
            ):
                continue
            chars.append(char)
        while chars and chars[-1].char == " " and not chars[-1].preserve:
            chars.pop()
        if not chars:
            return []

        # An empty/collapsed element does not move a following sibling's text.
        # Nested attributes assigned to the same addressable character override
        # ancestor attributes; they are not added twice (notably dx/dy).
        first, events = {}, {}
        for i, char in enumerate(chars):
            for owner in char.owners:
                if owner in first:
                    continue
                first[owner] = i
                for axis, key in enumerate(("x", "y", "dx", "dy")):
                    if key not in owner.attrib:
                        continue
                    value = re.split(r"[\s,]+", owner.get(key, "0").strip())
                    if len(value) != 1:
                        raise SVGIRError(
                            "svgir_unsupported_element", "per-character text positioning"
                        )
                    events.setdefault(i, {})[key] = self._len(value[0], axis % 2, styles[owner])
        boundaries = [
            0,
            *(i for i, event in events.items() if i and ("x" in event or "y" in event)),
            len(chars),
        ]
        boundaries = sorted(set(boundaries))
        glyphs, cursor = [], [0.0, 0.0]
        cjk = self.fonts.cjk_face() if self.fonts is not None else None

        for begin, end in zip(boundaries, boundaries[1:]):
            chunk = []
            index = begin
            while index < end:
                _, face = self._font(chars[index].style)
                size = _number(chars[index].style.get("font-size", "16"))
                stop = index + 1
                while stop < end:
                    _, next_face = self._font(chars[stop].style)
                    if (
                        next_face.resource != face.resource
                        or _number(chars[stop].style.get("font-size", "16")) != size
                    ):
                        break
                    stop += 1
                text = "".join(char.char for char in chars[index:stop])
                pieces = typography.shape_segment(text, typography.FaceSet(face, cjk), size)
                missing = typography.missing_glyphs(text, typography.FaceSet(face, cjk))
                if missing:
                    self.notes.append("glyph_missing: " + "".join(missing))
                # Kerning survives a paint-only DOM boundary (A<tspan>V), but
                # a single glyph cannot straddle independently painted/scoped
                # text nodes. Ask the same shaper again only at those cluster
                # boundaries; never synthesize glyph IDs or font advances.
                cuts, logical = set(), index
                for piece in pieces:
                    for glyph in piece.glyphs:
                        if glyph.text:
                            for boundary in range(logical + 1, logical + len(glyph.text)):
                                if (
                                    chars[boundary].owners != chars[logical].owners
                                    or boundary in events
                                ):
                                    cuts.add(boundary)
                            logical += len(glyph.text)
                ranges = [index, *sorted(cuts), stop]
                for part, part_end in zip(ranges, ranges[1:]):
                    shaped = (
                        pieces
                        if not cuts
                        else typography.shape_segment(
                            "".join(char.char for char in chars[part:part_end]),
                            typography.FaceSet(face, cjk),
                            size,
                        )
                    )
                    logical = part
                    for piece in shaped:
                        font = f"font:{piece.face.resource.face_id}"
                        self.resources[font], self.faces[font] = piece.face.resource, piece.face
                        cluster = logical
                        for glyph in piece.glyphs:
                            if glyph.text:
                                cluster = logical
                                logical += len(glyph.text)
                            char = chars[cluster]
                            chunk.append(_TextGlyph(glyph, font, piece.face, size, char, cluster))
                index = stop

            applied = set()
            for item in chunk:
                if item.index not in applied:
                    applied.add(item.index)
                    event = events.get(item.index, {})
                    for axis, key in enumerate(("x", "y")):
                        if key in event:
                            cursor[axis] = event[key]
                        cursor[axis] += event.get("d" + key, 0)
                item.x, item.y = cursor
                cursor[0] += item.advance
            if chunk:
                left = min(min(g.x, g.x + g.advance) for g in chunk)
                right = max(max(g.x, g.x + g.advance) for g in chunk)
                anchor = chunk[0].character.style.get("text-anchor", "start")
                target = {"start": left, "middle": (left + right) / 2, "end": right}.get(anchor)
                if target is None:
                    raise SVGIRError("svgir_invalid", "invalid text anchor")
                shift = chunk[0].x - target
                for item in chunk:
                    item.x += shift
                glyphs.extend(chunk)

        # Keep SVG styling groups and paint order, but join adjacent unrotated
        # glyphs into the existing ShapedText/GlyphRun representation. Rotated
        # glyphs are placed individually so their advances stay on the baseline.
        placements = []
        pending, pending_key = [], None

        def emit():
            if not pending:
                return
            first_glyph = pending[0]
            style = first_glyph.character.style
            paint = self._paint(style, "fill")
            if style.get("visibility") in ("hidden", "collapse") or first_glyph.size <= 0:
                paint = None
            if self._paint(style, "stroke") is not None:
                raise SVGIRError("svgir_unsupported_element", "stroked text requires outlines")
            if paint is not None:
                run_data = []
                for item in pending:
                    if run_data and run_data[-1][:2] == (item.font, item.size):
                        run_data[-1][2].append(item.glyph)
                    else:
                        run_data.append((item.font, item.size, [item.glyph]))
                runs = tuple(
                    ir.GlyphRun(font, size, tuple(glyphs)) for font, size, glyphs in run_data
                )
                text = ir.ShapedText(0, 0, runs, paint, object_id=el.get("id", ""))
                matrix = ir.compose(ir.scale(1, -1), ir.rotate_ccw(first_glyph.character.angle))
                matrix = ir.compose(matrix, ir.translate(first_glyph.x, baseline(first_glyph)))
                placements.append(
                    (first_glyph.character.owners[1:], ir.Group((text,), transform=matrix))
                )
            pending.clear()

        def baseline(item):
            value = item.character.style.get(
                "dominant-baseline", item.character.style.get("alignment-baseline", "auto")
            )
            y, face, size = item.y, item.face, item.size
            if value in ("middle", "central"):
                return y + (face.ascender + face.descender) / 2 * size
            if value in ("hanging", "text-before-edge", "before-edge"):
                return y + face.ascender * size
            if value in ("text-after-edge", "after-edge"):
                return y + face.descender * size
            if value not in ("auto", "alphabetic", "baseline"):
                raise SVGIRError("svgir_unsupported_element", "unsupported text baseline")
            return y

        for item in glyphs:
            char = item.character
            key = (
                char.owners,
                self._paint(char.style, "fill"),
                char.style.get("visibility"),
                baseline(item),
            )
            contiguous = pending and abs(item.x - (pending[-1].x + pending[-1].advance)) < 1e-9
            if pending and (
                key != pending_key
                or char.angle != 0
                or pending[-1].character.angle != 0
                or not contiguous
            ):
                emit()
            pending.append(item)
            pending_key = key
        emit()

        def groups(items, depth=0):
            result, i = [], 0
            while i < len(items):
                owners, node = items[i]
                if len(owners) == depth:
                    result.append(node)
                    i += 1
                    continue
                owner = owners[depth]
                stop = i + 1
                while (
                    stop < len(items)
                    and len(items[stop][0]) > depth
                    and items[stop][0][depth] is owner
                ):
                    stop += 1
                children = groups(items[i:stop], depth + 1)
                style = styles[owner]
                opacity = min(1, max(0, _length(style.get("opacity", "1"), percent=1)))
                matrix = _parse_transform(owner.get("transform", ""))
                clip_ref = style.get("clip-path", "none")
                clip = self._clip(clip_ref) if clip_ref != "none" else None
                if opacity != 1 or matrix != ir.IDENTITY or clip:
                    result.append(
                        ir.Group(tuple(children), transform=matrix, opacity=opacity, clip=clip)
                    )
                else:
                    result.extend(children)
                i = stop
            return result

        return groups(placements)

    def _image(self, el, st):
        match = _DATA_URI_RE.fullmatch(el.get("href") or el.get(XLINK_HREF, ""))
        if not match or self.files is None:
            raise SVGIRError("svgir_invalid", "embedded image requires a files receiver")
        raw = base64.b64decode(re.sub(r"\s", "", match[2]), validate=True)
        key = f"img{len(self.resources)}"
        self.files[key] = raw
        sha = hashlib.sha256(raw).hexdigest()
        self.resources[key] = ir.FileResource(key, match[1], sha, "static", sha)
        x, y = self._len(el.get("x", "0"), st=st), self._len(el.get("y", "0"), 1, st)
        w, h = self._len(el.get("width", "0"), st=st), self._len(el.get("height", "0"), 1, st)
        if w <= 0 or h <= 0:
            return []
        if match[1] == "png":
            iw, ih = struct.unpack(">II", raw[16:24])
        else:
            pos, iw, ih = 2, 0, 0
            while pos + 4 <= len(raw):
                marker = raw[pos + 1]
                if marker == 255:
                    pos += 1
                    continue
                length = int.from_bytes(raw[pos + 2 : pos + 4], "big")
                if marker in (192, 193, 194):
                    ih, iw = struct.unpack(">HH", raw[pos + 5 : pos + 9])
                    break
                pos += 2 + length
        try:
            matrix = svgcoord.viewbox_to_pt([0, 0, iw, ih], w, h, el.get("preserveAspectRatio"))
        except ValueError as exc:
            raise SVGIRError("svgir_invalid", "invalid image viewport") from exc
        image = ir.Image(
            key,
            (matrix[4] + x, matrix[5] + y, iw * matrix[0], ih * matrix[3]),
            flip_v=True,
            object_id=el.get("id", ""),
        )
        ix, iy, image_w, image_h = image.rect
        # The image already owns its painted boundary. Only slice/overflowing
        # placement needs this viewport clip; repeating a coincident boundary
        # changes antialias coverage. Explicit/ancestor clips are applied by
        # _node independently and are not removed here.
        if x <= ix and y <= iy and ix + image_w <= x + w and iy + image_h <= y + h:
            return [image]
        clip = ir.Path((("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)))
        return [ir.Group((image,), clip=clip)]

    def _check_style(self, st):
        for name, code in (("mask", "svgir_mask"), ("filter", "svgir_filter")):
            if st.get(name, "none") != "none":
                raise SVGIRError(code, f"unsupported {name}")
        for key in ("marker-start", "marker-mid", "marker-end"):
            if st.get(key, "none") != "none":
                raise SVGIRError("svgir_unsupported_element", f"unsupported {key}")
        for key, allowed in (
            ("vector-effect", ("none",)),
            ("paint-order", ("normal", "fill stroke markers")),
            ("letter-spacing", ("normal", "0", "0px")),
            ("word-spacing", ("normal", "0", "0px")),
            ("baseline-shift", ("baseline", "0", "0px")),
            ("text-decoration", ("none",)),
            ("overflow", ("visible", "hidden", "scroll", "auto")),
        ):
            if key in st and st[key] not in allowed:
                raise SVGIRError("svgir_unsupported_element", f"unsupported {key}")

    def _clip(self, value):
        match = _REF_RE.fullmatch(value)
        definition = self.ids.get(match[1]) if match else None
        if definition is None or _tag(definition) != "clipPath":
            raise SVGIRError("svgir_invalid", "invalid clip reference")
        if definition.get("clipPathUnits", "userSpaceOnUse") != "userSpaceOnUse":
            raise SVGIRError("svgir_unsupported_element", "objectBoundingBox clip")
        paths = []

        chain, parent = [], self.parents.get(definition)
        while parent is not None:
            chain.append(parent)
            parent = self.parents.get(parent)
        inherited, definition_viewport = self._clip_context(reversed(chain))

        def gather(el, parent, parent_style):
            local = ir.compose(_parse_transform(el.get("transform", "")), parent)
            style = self._style(el, parent_style)
            if style.get("display") == "none":
                return
            if _tag(el) in ("clipPath", "g"):
                for child in el:
                    gather(child, local, style)
            elif _tag(el) == "use":
                ref = el.get("href") or el.get(XLINK_HREF, "")
                target = self.ids.get(ref[1:])
                if target is None:
                    raise SVGIRError("svgir_invalid", "invalid clip use")
                offset = ir.translate(
                    self._len(el.get("x", "0"), st=style), self._len(el.get("y", "0"), 1, style)
                )
                gather(target, ir.compose(offset, local), style)
            else:
                if style.get("visibility") in ("hidden", "collapse"):
                    return
                segments = _transformed_segments(self._shape(el, style), local)
                if segments:
                    paths.append(ir.Path(segments, fill_rule=style.get("clip-rule", "nonzero")))

        saved_viewport = self.viewport
        try:
            self.viewport = definition_viewport
            gather(definition, ir.IDENTITY, inherited)
        finally:
            self.viewport = saved_viewport
        if not paths:
            return ir.Path((("M", 0, 0), ("Z",)))
        if len(paths) == 1:
            return paths[0]
        # Filled clip children are a geometric union, not a compound path.
        return ir.ClipUnion(tuple(paths))

    def _clip_context(self, ancestors):
        # Referenced clip shapes resolve percentages in their definition's
        # viewport, then apply numeric geometry in the referencing user space.
        # Active instances supply overridden sizes; forward references may
        # point inside an as-yet unvisited nested SVG.
        saved_viewport = self.viewport
        inherited = _DEFAULT
        try:
            self.viewport = self.active_viewports[self.root]
            for ancestor in ancestors:
                inherited = self._style(ancestor, inherited)
                if ancestor in self.active_viewports:
                    self.viewport = self.active_viewports[ancestor]
                elif _tag(ancestor) == "svg":
                    width, height = self._viewport_size(ancestor, inherited)
                    if min(width, height) == 0:
                        raise SVGIRError("svgir_invalid", "zero clip definition viewport")
                    _, self.viewport = self._viewport_mapping(ancestor, width, height)
            return inherited, self.viewport
        finally:
            self.viewport = saved_viewport

    def _node(self, el, parent, *, instance=False, viewport_size=None):
        tag = _tag(el)
        if tag in (
            "defs",
            "metadata",
            "style",
            "title",
            "desc",
            "clipPath",
            "mask",
            "linearGradient",
            "radialGradient",
            "pattern",
            "marker",
            "stop",
        ):
            return []
        if tag == "symbol" and not instance:
            return []
        st = self._style(el, parent)
        if st.get("display") == "none":
            return []
        self._check_style(st)
        local = _parse_transform(el.get("transform", ""))
        if ir.determinant(local) == 0:
            return []
        if tag == "svg" and el is not self.root:
            return self._nested_svg(el, st, local, viewport_size)
        if tag in ("g", "svg", "symbol"):
            children = [n for child in el for n in self._node(child, st)]
        elif tag == "use":
            href = el.get("href") or el.get(XLINK_HREF, "")
            target = self.ids.get(href[1:]) if href.startswith("#") else None
            if target is None:
                raise SVGIRError("svgir_invalid", "unresolved use")
            offset = ir.translate(
                self._len(el.get("x", "0"), st=st), self._len(el.get("y", "0"), 1, st)
            )
            if _tag(target) == "symbol":
                w, h = (
                    self._len(el.get("width", str(self.viewport[0])), st=st),
                    self._len(el.get("height", str(self.viewport[1])), 1, st),
                )
                if min(w, h) <= 0:
                    return []
                symbol_map, viewport = self._viewport_mapping(target, w, h)
                saved_viewport = self.viewport
                active = self.active_viewports.get(target)
                try:
                    self.viewport = viewport
                    self.active_viewports[target] = viewport
                    content = self._node(target, st, instance=True)
                finally:
                    self.viewport = saved_viewport
                    if active is None:
                        self.active_viewports.pop(target, None)
                    else:
                        self.active_viewports[target] = active
                mapped = ir.Group(tuple(content), transform=symbol_map)
                target_style = self._style(target, st)
                clip = (
                    self._viewport_clip(w, h)
                    if target_style["overflow"] in ("hidden", "scroll")
                    else None
                )
                children = [ir.Group((mapped,), transform=offset, clip=clip)]
            else:
                local = ir.compose(offset, local)
                children = self._node(
                    target,
                    st,
                    instance=True,
                    viewport_size=(el.get("width"), el.get("height"))
                    if _tag(target) == "svg"
                    else None,
                )
        elif tag == "text":
            children = self._text(el, st)
        elif st.get("visibility") in ("hidden", "collapse"):
            children = []
        elif tag in ("path", "rect", "circle", "ellipse", "line", "polygon", "polyline"):
            segments, fill, stroke = self._shape(el, st), self._paint(st, "fill"), self._stroke(st)
            children = (
                [
                    ir.Path(
                        tuple(segments),
                        fill,
                        stroke,
                        st.get("fill-rule", "nonzero"),
                        el.get("id", ""),
                    )
                ]
                if segments and (fill or stroke)
                else []
            )
        elif tag == "image":
            children = self._image(el, st)
        else:
            raise SVGIRError("svgir_unsupported_element", f"unsupported element {tag}")
        if not children:
            return []
        opacity = min(1, max(0, _length(st.get("opacity", "1"), percent=1)))
        clip_ref = st.get("clip-path", "none")
        clip = self._clip(clip_ref) if clip_ref != "none" else None
        if (
            local != ir.IDENTITY
            or clip
            or opacity != 1
            or (el.get("id") and tag in ("g", "use", "svg", "symbol"))
        ):
            return [
                ir.Group(
                    tuple(children),
                    transform=local,
                    clip=clip,
                    opacity=opacity,
                    object_id=el.get("id", ""),
                )
            ]
        return children

    def translate(self):
        children = self._node(self.root, _DEFAULT)
        matrix = ir.compose(self.root_map, (1, 0, 0, -1, 0, self.h_pt))
        page = ir.Page(
            self.w_pt,
            self.h_pt,
            (ir.Group(tuple(children), transform=matrix),),
            self.resources,
            background=None,
        )
        ir.validate(page)
        return page


def svg_to_ir(
    data: bytes, w_pt: float, h_pt: float, fonts, *, files: dict[str, bytes] | None = None
) -> tuple[ir.Page, list[str]]:
    """Page/notes; pass resource-keyed ``files`` to ``pdfwriter.write_pdf``.

    Image-free callers keep their previous binary API. Embedded images require
    the optional receiver; bytes and SHA identities are handed off together.
    """
    root = parse_svg(strip_matplotlib_doctype(data))
    if _tag(root) != "svg":
        raise SVGIRError("svgir_invalid", "root element is not svg")
    try:
        pending_files = {} if files is not None else None
        tr = _Translator(root, w_pt, h_pt, fonts, pending_files)
        page = tr.translate()
        if files is not None:
            if any(key in files and files[key] != value for key, value in pending_files.items()):
                raise SVGIRError("svgir_invalid", "image resource key collision")
            files.update(pending_files)
        return page, tr.notes
    except (ir.IRError, OverflowError, ZeroDivisionError) as exc:
        raise SVGIRError("svgir_invalid", "invalid SVG geometry") from exc
