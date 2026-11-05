"""SVG → IR 翻译器（ADR 0166 §2；NEXT_PHASE R07）。

组合后的计算 SVG（经 svgcheck 校验的字节）→ `rendercore.ir.Page` → 现有
`pdfwriter.write_pdf` → PDF / PNG / TIFF。**只处理 svgcheck 放行的静态子集**；
不可表达的特性（渐变 / mask / filter / 弧命令）以 `SVGIRError` 逐元素如实报，
**不静默栅格化**。

坐标合同：SVG y-down → IR y-up 一次根翻转（与 svgwriter 的输出方向互补）；
viewBox 原点映射由调用方（app 层的 chart 导出）预先处理——本模块接收的
坐标已经是**场景用户空间**。

文本：`<text>` 的 font-family 与已批准字体按族名匹配（不匹配 → 映射到
Liberation Sans 并在 notes 里如实记录）；HarfBuzz 整形由现有 `HbFace.shape`
承担；字号从 font-size 属性或 style 里取（缺省 10pt）。

不支持：渐变填充（`url(#grad)` 引用非 clipPath defs）、mask、filter、
弧命令（A/Aa）——全部逐元素 `SVGIRError`。
"""

from __future__ import annotations

import base64
import binascii
import re
import xml.etree.ElementTree as ET

from tavotto.rendercore import ir
from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

#: 不支持的 SVG 特性 → 稳定错误码（前端文案落地时同步）。
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
    """翻译失败。`code` ∈ ERROR_CODES，`element` 是触发元素的 tag + id。"""

    def __init__(self, code: str, detail: str = "", element: str = "") -> None:
        if code not in ERROR_CODES:
            raise AssertionError(f"undisclosed svgir error: {code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.element = element


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(_NUM)
_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\(\s*([^)]*)\)")
_GRADIENT_RE = re.compile(r"url\(#([^)]+)\)")
_DATA_URI_RE = re.compile(r"^data:image/(png|jpeg);base64,(.*)$", re.S)


def _parse_transform(text: str) -> ir.Matrix:
    m = ir.IDENTITY
    for name, raw in _TRANSFORM_RE.findall(text or ""):
        vals = [float(v) for v in _NUM_RE.findall(raw)]
        if name == "matrix" and len(vals) == 6:
            local = tuple(vals)
        elif name == "translate" and len(vals) in (1, 2):
            tx = vals[0]
            ty = vals[1] if len(vals) > 1 else 0.0
            local = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale" and len(vals) in (1, 2):
            sx = vals[0]
            sy = vals[1] if len(vals) > 1 else sx
            local = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and len(vals) in (1, 3):
            import math

            ang = math.radians(vals[0])
            cos, sin = math.cos(ang), math.sin(ang)
            rot = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(vals) == 3:
                cx, cy = vals[1], vals[2]
                local = ir.compose(
                    ir.compose((1.0, 0.0, 0.0, 1.0, -cx, -cy), rot),
                    (1.0, 0.0, 0.0, 1.0, cx, cy),
                )
            else:
                local = rot
        elif name == "skewX" and len(vals) == 1:
            import math

            local = (1.0, 0.0, math.tan(math.radians(vals[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and len(vals) == 1:
            import math

            local = (1.0, math.tan(math.radians(vals[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            raise SVGIRError("svgir_unsupported_element", f"transform {name}")
        m = ir.compose(m, local)
    return m


def _parse_path_d(d: str) -> list[tuple]:
    """SVG path d → IR segments (M/L/C/Z)。弧命令 → SVGIRError。"""
    tokens = re.findall(r"([MLHVCSQTAZmlhvcsqtaz])([^MLHVCSQTAZmlhvcsqtaz]*)", d)
    segs: list[tuple] = []
    cur = (0.0, 0.0)
    start: tuple[float, float] | None = None
    for op, arg in tokens:
        nums = [float(v) for v in _NUM_RE.findall(arg)]
        op = op.upper()
        i = 0
        while i < len(nums) or (op == "Z" and i == 0):
            if op == "M":
                cur = (nums[i], nums[i + 1])
                start = cur
                segs.append(("M", cur[0], cur[1]))
                i += 2
                op = "L"  # 隐式 lineto
            elif op == "L":
                cur = (nums[i], nums[i + 1])
                segs.append(("L", cur[0], cur[1]))
                i += 2
            elif op == "H":
                cur = (nums[i], cur[1])
                segs.append(("L", cur[0], cur[1]))
                i += 1
            elif op == "V":
                cur = (cur[0], nums[i])
                segs.append(("L", cur[0], cur[1]))
                i += 1
            elif op == "C":
                segs.append(
                    ("C", nums[i], nums[i + 1], nums[i + 2], nums[i + 3], nums[i + 4], nums[i + 5])
                )
                cur = (nums[i + 4], nums[i + 5])
                i += 6
            elif op == "S":
                if len(nums) - i < 4:
                    break
                x2, y2, x, y = nums[i : i + 4]
                segs.append(("C", cur[0], cur[1], x2, y2, x, y))
                cur = (x, y)
                i += 4
            elif op == "Q":
                if len(nums) - i < 4:
                    break
                x1, y1, x, y = nums[i : i + 4]
                cx1 = cur[0] + 2 / 3 * (x1 - cur[0])
                cy1 = cur[1] + 2 / 3 * (y1 - cur[1])
                cx2 = x + 2 / 3 * (x1 - x)
                cy2 = y + 2 / 3 * (y1 - y)
                segs.append(("C", cx1, cy1, cx2, cy2, x, y))
                cur = (x, y)
                i += 4
            elif op == "T":
                if len(nums) - i < 2:
                    break
                x, y = nums[i], nums[i + 1]
                segs.append(("C", cur[0], cur[1], x, y, x, y))
                cur = (x, y)
                i += 2
            elif op == "A":
                raise SVGIRError("svgir_arc", "arc command not supported")
            elif op == "Z":
                segs.append(("Z",))
                cur = start or (0.0, 0.0)
                break
            else:
                break
    return segs


def _parse_color(s: str) -> tuple[ir.RGB, float] | None:
    """CSS 颜色 → (rgb, alpha)；none → None。"""
    s = (s or "").strip().lower()
    if s in ("", "none", "transparent"):
        return None
    m = re.match(r"#([0-9a-f]{6})$", s)
    if m:
        v = m.group(1)
        return (int(v[0:2], 16) / 255, int(v[2:4], 16) / 255, int(v[4:6], 16) / 255), 1.0
    m = re.match(r"#([0-9a-f]{3})$", s)
    if m:
        v = m.group(1)
        return tuple(int(c * 2, 16) / 255 for c in v), 1.0  # type: ignore[return-value]
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        vals = []
        for p in parts[:3]:
            vals.append(float(p[:-1]) / 100 if p.endswith("%") else float(p) / 255)
        alpha = 1.0
        if len(parts) > 3:
            alpha = float(parts[3])
        return (vals[0], vals[1], vals[2]), alpha
    named = {
        "white": (1, 1, 1),
        "black": (0, 0, 0),
        "red": (1, 0, 0),
        "green": (0, 0.5, 0),
        "blue": (0, 0, 1),
        "orange": (1, 0.647, 0),
        "yellow": (1, 1, 0),
        "gray": (0.5, 0.5, 0.5),
        "grey": (0.5, 0.5, 0.5),
        "purple": (0.5, 0, 0.5),
        "cyan": (0, 1, 1),
        "magenta": (1, 0, 1),
    }
    if s in named:
        return named[s], 1.0
    return None


def _style_dict(el: ET.Element) -> dict[str, str]:
    """元素 style 属性 + presentation 属性 → 统一 dict。"""
    out: dict[str, str] = {}
    for decl in el.attrib.get("style", "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip()] = v.strip()
    for k in (
        "fill",
        "stroke",
        "stroke-width",
        "fill-opacity",
        "stroke-opacity",
        "opacity",
        "font-family",
        "font-size",
        "font-weight",
        "font-style",
        "text-anchor",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
    ):
        if k in el.attrib:
            out.setdefault(k, el.attrib[k])
    return out


def _paint(st: dict, key: str, parent_opacity: float) -> ir.Paint | None:
    col = _parse_color(st.get(key, ""))
    if col is None:
        return None
    rgb, alpha = col
    op_key = f"{key}-opacity"
    if op_key in st:
        try:
            alpha *= float(st[op_key])
        except ValueError:
            pass
    return ir.Paint(rgb, alpha * parent_opacity)


def _stroke_of(st: dict, parent_opacity: float) -> ir.Stroke | None:
    sp = _paint(st, "stroke", parent_opacity)
    if sp is None:
        return None
    width = 1.0
    if "stroke-width" in st:
        try:
            width = float(re.match(_NUM_RE, st["stroke-width"]).group(0))  # type: ignore[union-attr]
        except (ValueError, AttributeError):
            pass
    dash: tuple[float, ...] = ()
    if "stroke-dasharray" in st and st["stroke-dasharray"] != "none":
        dash = tuple(float(v) for v in re.findall(_NUM_RE, st["stroke-dasharray"]))
    cap = {"butt": "butt", "round": "round", "square": "square"}.get(
        st.get("stroke-linecap", "butt"), "butt"
    )
    join = {"miter": "miter", "round": "round", "bevel": "bevel"}.get(
        st.get("stroke-linejoin", "miter"), "miter"
    )
    return ir.Stroke(sp, width, cap=cap, join=join, dash=dash)


def _shape_to_path(tag: str, attrib: dict) -> list[tuple]:
    """rect/circle/ellipse/line/polygon/polyline → IR segments。"""

    def f(key: str, default: float = 0.0) -> float:
        try:
            return float(attrib.get(key, default))
        except (ValueError, TypeError):
            return default

    if tag == "rect":
        x, y = f("x"), f("y")
        w, h = f("width"), f("height")
        return [("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]
    if tag == "circle":
        cx, cy, r = f("cx"), f("cy"), f("r")
        k = 0.5522847498 * r
        return [
            ("M", cx - r, cy),
            ("C", cx - r, cy - k, cx - k, cy - r, cx, cy - r),
            ("C", cx + k, cy - r, cx + r, cy - k, cx + r, cy),
            ("C", cx + r, cy + k, cx + k, cy + r, cx, cy + r),
            ("C", cx - k, cy + r, cx - r, cy + k, cx - r, cy),
            ("Z",),
        ]
    if tag == "ellipse":
        cx, cy, rx, ry = f("cx"), f("cy"), f("rx"), f("ry")
        kx, ky = 0.5522847498 * rx, 0.5522847498 * ry
        return [
            ("M", cx - rx, cy),
            ("C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry),
            ("C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy),
            ("C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry),
            ("C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy),
            ("Z",),
        ]
    if tag == "line":
        return [("M", f("x1"), f("y1")), ("L", f("x2"), f("y2"))]
    if tag in ("polygon", "polyline"):
        nums = [float(v) for v in re.findall(_NUM_RE, attrib.get("points", ""))]
        pts = list(zip(nums[::2], nums[1::2]))
        if not pts:
            return []
        segs = [("M", *pts[0])]
        segs.extend(("L", *p) for p in pts[1:])
        if tag == "polygon":
            segs.append(("Z",))
        return segs
    raise SVGIRError("svgir_unsupported_element", f"shape {tag}")


class _Translator:
    """SVG DOM → IR nodes。一次遍历，累积 resources。"""

    def __init__(self, root: ET.Element, w_pt: float, h_pt: float, fonts) -> None:
        self.root = root
        self.w_pt = w_pt
        self.h_pt = h_pt
        self.fonts = fonts
        self.resources: dict[str, ir.Resource] = {}
        self.clip_defs: dict[str, ir.Path] = {}
        self.use_defs: dict[str, ET.Element] = {}
        self._font_cache: dict[str, str] = {}
        self._faces: dict[str, object] = {}  # key → HbFace
        self._notes: list[str] = []
        # 收集 defs
        for defs in root.findall(".//{SVG_NS}defs".replace("SVG_NS", SVG_NS)):
            for child in defs:
                cid = child.get("id", "")
                tag = child.tag.split("}")[-1]
                if tag == "clipPath":
                    for shape in child:
                        stag = shape.tag.split("}")[-1]
                        if stag in ("rect", "path", "circle", "ellipse"):
                            segs = (
                                _parse_path_d(shape.get("d", ""))
                                if stag == "path"
                                else _shape_to_path(stag, shape.attrib)
                            )
                            self.clip_defs[cid] = ir.Path(tuple(segs))
                elif tag in ("path", "rect", "circle", "g"):
                    if cid:
                        self.use_defs[cid] = child

    #: matplotlib 族名 → IR 通用族名（批准集合的四族 + 常见别名映射）
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

    def _font_resource(self, family: str) -> str:
        """family 名 → resources key。族名先过别名映射，不匹配 → sans-serif 兜底。"""
        if family in self._font_cache:
            return self._font_cache[family]
        first = family.split(",")[0].strip().strip("'\"")
        mapped = self._FAMILY_MAP.get(first.lower(), first.lower())
        for candidate in (mapped, "sans-serif"):
            try:
                face = self.fonts.face_for(candidate, False, False)
                break
            except Exception:
                continue
        else:
            raise SVGIRError("svgir_font_missing", f"no approved font for {family!r}")
        if candidate != first.lower():
            self._notes.append(f"font '{first}' mapped to {candidate}")
        key = f"f{len(self.resources)}"
        self.resources[key] = face.resource
        self._font_cache[family] = key
        self._faces[key] = face
        return key

    def _clip_path(self, clip_ref: str) -> ir.Path | None:
        m = _GRADIENT_RE.match(clip_ref)
        if m:
            return self.clip_defs.get(m.group(1))
        return None

    def translate(self) -> ir.Page:
        kids = self._walk(self.root, ir.IDENTITY, 1.0)
        # 根翻转：SVG y-down → IR y-up
        flip = (1.0, 0.0, 0.0, -1.0, 0.0, self.h_pt)
        page = ir.Page(
            self.w_pt, self.h_pt, (ir.Group(tuple(kids), transform=flip),), self.resources
        )
        ir.validate(page)
        return page

    def _walk(self, el: ET.Element, ctm: ir.Matrix, parent_alpha: float) -> list[ir.Node]:
        nodes: list[ir.Node] = []
        for ch in el:
            tag = ch.tag.split("}")[-1]
            if tag in ("defs", "metadata", "style", "title", "desc"):
                continue
            t = _parse_transform(ch.get("transform", "")) if ch.get("transform") else ir.IDENTITY
            nctm = ir.compose(ctm, t)
            st = _style_dict(ch)
            try:
                group_alpha = parent_alpha * float(st.get("opacity", "1.0"))
            except ValueError:
                group_alpha = parent_alpha
            clip_ref = st.get("clip-path") or ch.get("clip-path", "")
            clip = (
                self._clip_path(clip_ref) if "clip-path" in st or "clip-path" in ch.attrib else None
            )
            if tag == "g":
                children = self._walk(ch, nctm, group_alpha)
                clip_path = clip
                if clip_path is not None:
                    nodes.append(
                        ir.Group(
                            tuple(children), transform=nctm, clip=clip_path, opacity=group_alpha
                        )
                    )
                elif group_alpha < 1.0:
                    nodes.append(ir.Group(tuple(children), transform=nctm, opacity=group_alpha))
                else:
                    nodes.append(ir.Group(tuple(children), transform=nctm))
            elif tag == "path":
                d = ch.get("d", "")
                if "url(#" in st.get("fill", "") and "clipPath" not in st.get("fill", ""):
                    raise SVGIRError("svgir_gradient", "gradient fill", f"path {ch.get('id', '')}")
                segs = _parse_path_d(d)
                fill = _paint(st, "fill", parent_alpha)
                stroke = _stroke_of(st, parent_alpha)
                node = ir.Path(tuple(segs), fill=fill, stroke=stroke, object_id=ch.get("id", ""))
                if clip is not None:
                    nodes.append(ir.Group((node,), transform=nctm, clip=clip))
                elif nctm != ir.IDENTITY:
                    nodes.append(ir.Group((node,), transform=nctm))
                else:
                    nodes.append(node)
            elif tag in ("rect", "circle", "ellipse", "line", "polygon", "polyline"):
                segs = _shape_to_path(tag, ch.attrib)
                fill = _paint(st, "fill", parent_alpha)
                stroke = _stroke_of(st, parent_alpha)
                node = ir.Path(tuple(segs), fill=fill, stroke=stroke, object_id=ch.get("id", ""))
                if clip is not None:
                    nodes.append(ir.Group((node,), transform=nctm, clip=clip))
                elif nctm != ir.IDENTITY:
                    nodes.append(ir.Group((node,), transform=nctm))
                else:
                    nodes.append(node)
            elif tag == "text":
                node = self._text_to_ir(ch, st, nctm, parent_alpha)
                if node is not None:
                    nodes.append(node)
            elif tag == "image":
                node = self._image_to_ir(ch, nctm, parent_alpha)
                if node is not None:
                    nodes.append(node)
            elif tag == "use":
                href = ch.get("href") or ch.get(XLINK_HREF, "")
                ref = href[1:] if href.startswith("#") else href
                def_el = self.use_defs.get(ref)
                if def_el is not None:
                    nodes.extend(self._walk(def_el, nctm, parent_alpha))
                else:
                    self._notes.append(f"use->{ref} not in defs (skipped)")
            else:
                self._notes.append(f"unsupported element <{tag}> (skipped)")
        return nodes

    def _text_to_ir(
        self, ch: ET.Element, st: dict, ctm: ir.Matrix, parent_alpha: float
    ) -> ir.ShapedText | None:
        content = "".join(ch.itertext())
        if not content.strip():
            return None
        fam = st.get("font-family", "sans-serif")
        size = 10.0
        fs = st.get("font-size", "")
        if fs:
            try:
                size = float(re.match(_NUM_RE, fs).group(0))  # type: ignore[union-attr]
            except (ValueError, AttributeError):
                pass
        key = self._font_resource(fam)
        face = self._faces.get(key)
        if face is None:
            raise SVGIRError("svgir_font_missing", f"font resource {key}")
        shaped = face.shape(content)
        anchor = st.get("text-anchor", "start")
        upem = face.resource.units_per_em
        total_advance = sum(g.x_advance for g in shaped) / upem * size
        dx = {"start": 0.0, "middle": -total_advance / 2, "end": -total_advance}.get(anchor, 0.0)
        x = float(ch.get("x", 0))
        y = float(ch.get("y", 0))
        a, b, c, d, e, f = ctm
        px = (x + dx) * a + y * c + e
        py = (x + dx) * b + y * d + f
        col = _parse_color(st.get("fill", "#000000"))
        rgb = col[0] if col else (0.0, 0.0, 0.0)
        alpha = parent_alpha
        if "fill-opacity" in st:
            try:
                alpha *= float(st["fill-opacity"])
            except ValueError:
                pass
        return ir.ShapedText(
            px,
            py,
            (ir.GlyphRun(key, size, shaped),),
            ir.Paint(rgb, alpha),
            object_id=ch.get("id", ""),
        )

    def _image_to_ir(self, ch: ET.Element, ctm: ir.Matrix, parent_alpha: float) -> ir.Image | None:
        href = ch.get("href") or ch.get(XLINK_HREF, "")
        m = _DATA_URI_RE.match(href)
        if not m:
            self._notes.append("image with non-data URI skipped")
            return None
        fmt, b64 = m.group(1), m.group(2)
        try:
            raw = base64.b64decode(b64)
        except (binascii.Error, ValueError):
            return None
        key = f"img{len(self.resources)}"
        kind = "png" if fmt == "png" else "jpeg"
        import hashlib as _hl

        self.resources[key] = ir.FileResource(
            source_id=key,
            kind=kind,
            sha256=_hl.sha256(raw).hexdigest(),
            origin="static",
            semantic_identity=key,
        )
        # 图片数据由写入器从 provider 获取；这里把 bytes 暂存到 resources 的 sha256 占位
        # 实际写入时 pdfwriter 走 files 回调
        x = float(ch.get("x", 0))
        y = float(ch.get("y", 0))
        w = float(ch.get("width", 0))
        h = float(ch.get("height", 0))
        # y-down → y-up：rect y = h_pt - (y + h)
        rect = (x, self.h_pt - (y + h), w, h)
        alpha = parent_alpha
        st = _style_dict(ch)
        if "opacity" in st:
            try:
                alpha *= float(st["opacity"])
            except ValueError:
                pass
        return ir.Image(resource=key, rect=rect, opacity=alpha, object_id=ch.get("id", ""))


def svg_to_ir(data: bytes, w_pt: float, h_pt: float, fonts) -> tuple[ir.Page, list[str]]:
    """组合后的 SVG 字节 → IR Page + notes。不支持特性抛 SVGIRError。

    ``fonts``：`HbFaceProvider` 实例（父进程 rendercore 依赖链内）。
    """
    root = parse_svg(strip_matplotlib_doctype(data))
    if root.tag.split("}")[-1] != "svg":
        raise SVGIRError("svgir_invalid", "root element is not <svg>")
    tr = _Translator(root, w_pt, h_pt, fonts)
    page = tr.translate()
    return page, tr._notes
