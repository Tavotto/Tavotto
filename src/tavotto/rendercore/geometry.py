"""画布形状 / 箭头 → `ir.Path`（统一实施包 U06，ADR 0059）。

Arrow / Shape 不是 IR 里的节点：它们在编译期变成路径（`Path` 段 + 填充 / 描边），
写入器只认路径，不为每种图标建插件。

## 坐标

所有函数在**对象自己的框空间**里出几何：原点在框的左上角、**y 向下**、单位 pt、
框是 `(0, 0) – (w, h)`。这与旧 facade（PyMuPDF 时代 `pymupdf_backend.py` 的 `_draw_shape`
/ `_draw_arrow`）以及前端 `web/src/lib/shapeGeometry.ts` 是**同一套公式、同一个空间**
——`_polygon_points` / `_dash_pattern` 那一对严格同源对（`docs/rules/repo/same-origin-pairs.md`）
在这里只是换了个宿主，数字一个没动。把框空间放到页面（y 向上）上是 `plan.compile_page()`
的事：一个 `Group.transform` 完成平移 + y 翻转（+ 旋转），本模块不知道页面有多高。

描边语义与旧 facade 逐条相同：rect / ellipse 描边居中内缩半线宽（外沿贴合框），
polygon / triangle / diamond 同样内缩，line / brace 只描边、圆线帽；`dotted` 的
「点」全靠圆线帽画出来（段长 0.01 × 线宽）。

ADR 0174 加的属性（全部缺省 = 旧画法，旧文档逐字不变）：形状 `stroke: false`（无边框：不描边、填充铺满框）、
`stroke_opacity`、`dash` 加三种、`line_cap` / `line_join`；箭头加三种端型（dot / diamond / stealth）与
`head_size_start` / `head_size_end`。每一条与 `web/src/lib/shapeGeometry.ts` 同源，由共享向量
`tests/golden/shape_geometry_vectors.json` 接住。

ADR 0185 加的自由路径 `path`（`read_path` / `path_segments` / `path_paths`）与前端 `web/src/lib/pathGeom.ts`
同源，共享向量 `tests/golden/path_vectors.json`。

纯标准库。
"""

from __future__ import annotations

import dataclasses
import math
import re

from .ir import (
    GradientStop,
    Group,
    LinearGradient,
    Node,
    Paint,
    Path,
    RadialGradient,
    Segment,
    Stroke,
    hex2rgb,
)

#: 三次贝塞尔近似四分之一圆弧的控制点系数。
KAPPA = 0.5522847498


# ---------------------------------------------------------------------------
# 同源对：与 web/src/lib/shapeGeometry.ts 逐字相同的两条
# ---------------------------------------------------------------------------
def polygon_points(sides: int, w: float, h: float, inset: float) -> list[tuple[float, float]]:
    """正 N 边形顶点（内切包围盒，顶点朝上）；与前端 shapeGeometry 同一公式。"""
    n = max(3, min(12, int(round(sides))))
    rx, ry = max(w / 2 - inset, 0.001), max(h / 2 - inset, 0.001)
    return [
        (
            w / 2 + rx * math.cos(-math.pi / 2 + i * 2 * math.pi / n),
            h / 2 + ry * math.sin(-math.pi / 2 + i * 2 * math.pi / n),
        )
        for i in range(n)
    ]


#: 线型段长表（线宽的倍数，「画 / 空 / 画 / 空…」）；与前端 `shapeGeometry.DASH_RATIOS` 严格同源。
#: `dotted` 不在表里：它的「点」是 0.01×线宽 的段加圆线帽画出来的。
DASH_RATIOS: dict[str, tuple[float, ...]] = {
    "dashed": (4.0, 2.5),
    "longdash": (8.0, 3.0),
    "dashdot": (4.0, 2.5, 1.0, 2.5),
    "longdashdot": (8.0, 3.0, 1.0, 3.0),
}


def valid_dash_pattern(p: object) -> tuple[float, ...] | None:
    """自定义虚线（线段 / 间隙交替，pt 绝对值）读得成数就回它：1–12 个、每个有限且 ≥ 0、总长 > 0；否则 None
    （照预设线型画）。与前端 `shapeGeometry.validDashPattern` 同一条判据。"""
    if not isinstance(p, (list, tuple)) or not 1 <= len(p) <= 12:
        return None
    vals = []
    for v in p:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            return None
        vals.append(float(v))
    return tuple(vals) if sum(vals) > 0 else None


def miter_limit(v: object) -> float:
    """斜接上限：缺省 / 非有限数 / < 1 = 10（PDF 的缺省，也是画布的 `STROKE_MITER_LIMIT`）；与前端
    `shapeGeometry.miterLimitOf` 同一条。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 1:
        return 10.0
    return float(v)


def dash_pattern(dash: object, sw: float) -> tuple[float, ...]:
    """虚线间距按线宽比例换算；与前端 shapeGeometry.dashArray 同一比例。空元组 = 实线。"""
    if dash == "dotted":
        return (round(max(sw * 0.01, 0.01), 3), round(sw * 2, 3))
    ratios = DASH_RATIOS.get(dash) if isinstance(dash, str) else None
    return tuple(round(sw * r, 3) for r in ratios) if ratios else ()


#: 缺省线端：圆头（加线端 / 拐角属性之前就是这么画的）。
DEFAULT_LINE_CAP = "round"


def stroke_cap(cap: object, dash: object) -> str:
    """实际生效的线端；点线强制圆头（点靠圆线帽才有面积）。与前端 `shapeGeometry.strokeCap` 同源。"""
    if dash == "dotted":
        return "round"
    return cap if cap in ("butt", "round", "square") else DEFAULT_LINE_CAP  # type: ignore[return-value]


#: 只有描边、没有填充也没有描边开关的形状；与前端 `types/document.STROKE_ONLY_SHAPES` 同一张表。
STROKE_ONLY_SHAPES = ("line", "brace", "arc")

#: 星形的缺省角数与内径比；与前端 `types/document.DEFAULT_STAR_POINTS / DEFAULT_STAR_RATIO` 同源。
DEFAULT_STAR_POINTS = 5
DEFAULT_STAR_RATIO = 0.382


def default_line_join(shape: object) -> str:
    """缺省拐角：封闭形状 / 箭头圆角；只有描边的形状（直线 / 大括号 / 弧线）尖角。与前端 `shapeGeometry.defaultLineJoin` 同源。"""
    return "miter" if shape in STROKE_ONLY_SHAPES else "round"


def star_points(
    points: int, w: float, h: float, inset: float, ratio: float
) -> list[tuple[float, float]]:
    """星形顶点：外顶点内切包围盒，内顶点在外径 × 内径比上，从正上方起外 / 内交替；角数夹到 3–12、内径比夹到
    0.05–0.95。与前端 `shapeGeometry.starPoints` 同一公式（共享向量 `tests/golden/star_arc_vectors.json`）。"""
    n = max(3, min(12, int(round(points))))
    k = max(0.05, min(0.95, ratio))
    rx, ry = max(w / 2 - inset, 0.001), max(h / 2 - inset, 0.001)
    out = []
    for i in range(2 * n):
        a = -math.pi / 2 + i * math.pi / n
        f = k if i % 2 else 1.0
        out.append((w / 2 + rx * f * math.cos(a), h / 2 + ry * f * math.sin(a)))
    return out


def arc_segments(w: float, h: float, inset: float) -> tuple[Segment, ...]:
    """弧线：框的左下角到右上角、圆心在右下角的四分之一椭圆弧（一段三次贝塞尔，κ 近似）。与前端
    `shapeGeometry.arcPoints` 同一构造。"""
    cx, cy = w - inset, h - inset
    rx, ry = max(w - 2 * inset, 0.001), max(h - 2 * inset, 0.001)
    return (
        ("M", cx - rx, cy),
        ("C", cx - rx, cy - KAPPA * ry, cx - KAPPA * rx, cy - ry, cx, cy - ry),
    )


def _line_join(o: dict, default: str) -> str:
    j = o.get("line_join")
    return j if j in ("miter", "round", "bevel") else default


def _unit(v: object) -> float | None:
    """描边不透明度：缺省 / 非有限数 / ≥1 → None（不透明）；其余钳到 [0, 1)。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v >= 1:
        return None
    return max(0.0, float(v))


#: 端型大小三档的缩放系数；缺省 / 中 = 1。与前端 `shapeGeometry.HEAD_SIZE_FACTOR` 同源。
HEAD_SIZE_FACTOR = {"small": 0.7, "medium": 1.0, "large": 1.5}
#: 实心端型让线段回缩的距离（帽长的倍数）；dot 回缩半径、在 `head_geometry` 里单算。
HEAD_TRIM = {"triangle": 0.75, "stealth": 0.6, "diamond": 0.5}
#: 燕尾凹口离尖的距离（帽长的倍数）。
STEALTH_NOTCH = 0.7


# ---------------------------------------------------------------------------
# 段构造
# ---------------------------------------------------------------------------
def rect_segments(x: float, y: float, w: float, h: float) -> tuple[Segment, ...]:
    return (("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",))


def rounded_rect_segments(x: float, y: float, w: float, h: float, r: float) -> tuple[Segment, ...]:
    """圆角矩形：四条直边 + 四段四分之一圆弧（三次贝塞尔）。`r` 已裁到 ≤ min(w, h) / 2。"""
    k = KAPPA * r
    x1, y1 = x + w, y + h
    return (
        ("M", x + r, y),
        ("L", x1 - r, y),
        ("C", x1 - r + k, y, x1, y + r - k, x1, y + r),
        ("L", x1, y1 - r),
        ("C", x1, y1 - r + k, x1 - r + k, y1, x1 - r, y1),
        ("L", x + r, y1),
        ("C", x + r - k, y1, x, y1 - r + k, x, y1 - r),
        ("L", x, y + r),
        ("C", x, y + r - k, x + r - k, y, x + r, y),
        ("Z",),
    )


def ellipse_segments(x: float, y: float, w: float, h: float) -> tuple[Segment, ...]:
    """内切于 (x, y, w, h) 的椭圆，四段三次贝塞尔（从最右点起、顺时针——y 向下的空间里）。"""
    rx, ry = w / 2, h / 2
    cx, cy = x + rx, y + ry
    kx, ky = KAPPA * rx, KAPPA * ry
    return (
        ("M", cx + rx, cy),
        ("C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry),
        ("C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy),
        ("C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry),
        ("C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy),
        ("Z",),
    )


def polyline_segments(points: list[tuple[float, float]], close: bool) -> tuple[Segment, ...]:
    segs: list[Segment] = [("M", *points[0])]
    segs += [("L", px, py) for px, py in points[1:]]
    if close:
        segs.append(("Z",))
    return tuple(segs)


def curve_segment(
    p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]
) -> Segment:
    """「一个控制点」的曲线 → 三次贝塞尔段。控制点取法与旧 facade 的 `Shape.draw_curve`
    相同（`k1 = p1 + (p2 - p1)·κ`，`k2 = p3 + (p2 - p3)·κ`），所以大括号的形状与旧产物
    逐点一致，而不是前端 `Q` 二次曲线的另一种近似。"""
    k1 = (p1[0] + (p2[0] - p1[0]) * KAPPA, p1[1] + (p2[1] - p1[1]) * KAPPA)
    k2 = (p3[0] + (p2[0] - p3[0]) * KAPPA, p3[1] + (p2[1] - p3[1]) * KAPPA)
    return ("C", k1[0], k1[1], k2[0], k2[1], p3[0], p3[1])


# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------
def _alpha(o: dict) -> float:
    """描边不透明度；缺省 = 1。`0` 是合法取值（完全透明），不能被当成「没设」。"""
    u = _unit(o.get("stroke_opacity"))
    return 1.0 if u is None else u


def arrow_opacity(o: dict) -> float:
    """箭头的整体不透明度（杆 + 两端端型作为一个整体，`plan._shape_node` 把它放在 `Group.opacity` 上）。

    杆伸进实心端型里、开口端型与杆在尖上重叠：逐条路径各自带 alpha 的话，重叠处会叠深。
    所以箭头的路径本身一律不透明，透明度走透明组（组内 alpha 从 1 起算）；画布 `ArrowView` 同样用 `<g opacity>`。"""
    return _alpha(o)


_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _pair(v: object) -> tuple[float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    a, b = _num(v[0]), _num(v[1])
    return None if a is None or b is None else (a, b)


def read_gradient(g: object) -> dict | None:
    """渐变字段的统一读取（ADR 0186）；与前端 `lib/gradient.readGradient` 同一条规则（共享向量
    `tests/golden/gradient_vectors.json`）。读不成 = None（退回纯色，不报错）：

    - `kind` 只认 linear / radial；
    - 色标丢掉读不成的（offset 不是有限数、颜色不是 #rgb / #rrggbb），offset 钳到 [0, 1]、opacity 钳到 [0, 1]
      （缺省 1），按 offset 稳定排序；剩不到两个 = None；
    - 线性：from / to 都是有限数对、且不重合；径向：center 是有限数对、radius 有限且 > 0。"""
    if not isinstance(g, dict) or g.get("kind") not in ("linear", "radial"):
        return None
    stops = []
    for st in g.get("stops") or ():
        if not isinstance(st, dict):
            continue
        off = _num(st.get("offset"))
        color = st.get("color")
        if off is None or not isinstance(color, str) or not _HEX.match(color):
            continue
        op = st.get("opacity")
        opv = 1.0 if op is None else _num(op)
        if opv is None:
            continue
        stops.append(
            {"offset": min(max(off, 0.0), 1.0), "color": color, "opacity": min(max(opv, 0.0), 1.0)}
        )
    stops.sort(key=lambda st: st["offset"])
    if len(stops) < 2:
        return None
    if g["kind"] == "linear":
        a, b = _pair(g.get("from")), _pair(g.get("to"))
        if a is None or b is None or a == b:
            return None
        return {"kind": "linear", "stops": stops, "from": a, "to": b}
    c, r = _pair(g.get("center")), _num(g.get("radius"))
    if c is None or r is None or r <= 0:
        return None
    return {"kind": "radial", "stops": stops, "center": c, "radius": r}


def gradient_paint(g: object, w: float, h: float, alpha: float = 1.0):
    """渐变字段 → IR 渐变（框空间 pt）：比例坐标 × 框；径向半径 × 框的长边；色标 alpha = opacity × `alpha`
    （对象的填充 / 描边不透明度）。读不成 = None。与前端 `lib/gradient.gradientGeometry` 同源。"""
    r = read_gradient(g)
    if r is None:
        return None
    stops = tuple(
        GradientStop(st["offset"], hex2rgb(st["color"]), st["opacity"] * alpha) for st in r["stops"]
    )
    if r["kind"] == "linear":
        (x0, y0), (x1, y1) = r["from"], r["to"]
        return LinearGradient(stops, (x0 * w, y0 * h), (x1 * w, y1 * h))
    cx, cy = r["center"]
    return RadialGradient(stops, (cx * w, cy * h), r["radius"] * max(w, h, 1e-6))


def _stroke(
    o: dict,
    sw: float,
    *,
    join: str,
    dash: bool = True,
    opaque: bool = False,
    box: tuple[float, float] | None = None,
) -> Stroke:
    """对象的一条描边。线端 / 拐角 / 不透明度 / 线型都从对象上读（缺省 = 旧画法）；
    `dash=False` 给箭头端型（端型不画虚线，也不受点线强制圆头影响）。"""
    # 自定义虚线（pt 绝对值）优先于预设线型；它不是点线，线端照用户选的
    custom = valid_dash_pattern(o.get("dash_pattern")) if dash else None
    d = (None if custom else o.get("dash")) if dash else None
    alpha = 1.0 if opaque else _alpha(o)
    # 描边渐变（ADR 0186）：给了框尺寸（形状 / 路径的描边）且字段读得成才用；端型那几条不给 box，照用纯色
    grad = gradient_paint(o.get("stroke_gradient"), box[0], box[1], alpha) if box else None
    return Stroke(
        paint=grad or Paint(hex2rgb(o.get("color", "#000000")), alpha),
        width=sw,
        cap=stroke_cap(o.get("line_cap"), d),
        join=_line_join(o, join),
        dash=(tuple(round(v, 3) for v in custom) if custom else dash_pattern(d, sw))
        if dash
        else (),
        miter_limit=miter_limit(o.get("miter_limit")),
    )


def shape_inset(o: dict, sw: float) -> float:
    """形状的描边内缩量：有描边时缺省内侧（外沿贴框，`sw / 2`），`stroke_align` = center → 0、outside → `-sw / 2`；
    只有描边的形状不看对齐；无边框 0。与前端 `shapeGeometry.shapeInset` 同一条。"""
    kind = o.get("shape")
    if kind in STROKE_ONLY_SHAPES:
        return sw / 2
    if o.get("stroke") is False:
        return 0.0
    align = o.get("stroke_align")
    return 0.0 if align == "center" else -sw / 2 if align == "outside" else sw / 2


def shape_paths(o: dict, w: float, h: float) -> list[Path]:
    """一个 shape 对象（画布语义：`shape` / `stroke_pt` / `color` / `fill` / `fill_opacity`
    / `dash` / `corner_radius_mm` / `sides` / `start` / `end`）→ 框空间里的路径列表。

    `w, h` 是框的 pt 尺寸；毫米 → pt 由调用方换算（`corner_radius_mm` 除外：它是对象
    上的毫米字段，这里自己换）。"""
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    # `fill_opacity: 0` 是合法取值（完全透明的填充），不能被 `or 1.0` 当成「没设」——旧 facade 正是
    # 这么写的（`float(o.get("fill_opacity") or 1.0)`），U08 对拍时这一处是有意的差异
    fill_opacity = o.get("fill_opacity")
    fill = (
        Paint(hex2rgb(o["fill"]), 1.0 if fill_opacity is None else float(fill_opacity))
        if o.get("fill")
        else None
    )
    if fill is not None:
        # 填充渐变（ADR 0186）：只在有填充时生效，覆盖纯色；读不成照用纯色
        fill = gradient_paint(o.get("fill_gradient"), w, h, fill.alpha) or fill
    kind = o.get("shape")
    oid = str(o.get("id", ""))
    # 无边框（`stroke: false`，只对封闭形状）：不描边、填充铺满整个框（不再内缩半个线宽）；
    # 直线与大括号只有描边，不看这个字段
    stroke_on = kind in STROKE_ONLY_SHAPES or o.get("stroke") is not False
    inset = shape_inset(o, sw)
    filled_stroke = _stroke(o, sw, join=default_line_join(kind), box=(w, h)) if stroke_on else None
    if filled_stroke is None and fill is None and kind not in STROKE_ONLY_SHAPES:
        return []  # 既无填充也无边框：什么都不画（IR 不收「既不填也不描」的路径）

    if kind == "rect":
        radius_mm = float(o.get("corner_radius_mm") or 0)
        x0 = y0 = inset
        rw, rh = max(w - inset, inset) - x0, max(h - inset, inset) - y0
        if radius_mm > 0:
            frac = min(radius_mm * 72.0 / 25.4 / max(min(rw, rh), 0.001), 0.5)
            segs = rounded_rect_segments(x0, y0, rw, rh, frac * min(rw, rh))
        else:
            segs = rect_segments(x0, y0, rw, rh)
        return [Path(segs, fill=fill, stroke=filled_stroke, object_id=oid)]
    if kind == "ellipse":
        segs = ellipse_segments(
            inset, inset, max(w - inset, inset) - inset, max(h - inset, inset) - inset
        )
        return [Path(segs, fill=fill, stroke=filled_stroke, object_id=oid)]
    if kind == "triangle":
        pts = [(w / 2, inset), (w - inset, h - inset), (inset, h - inset)]
        return [Path(polyline_segments(pts, True), fill=fill, stroke=filled_stroke, object_id=oid)]
    if kind == "diamond":
        pts = [(w / 2, inset), (w - inset, h / 2), (w / 2, h - inset), (inset, h / 2)]
        return [Path(polyline_segments(pts, True), fill=fill, stroke=filled_stroke, object_id=oid)]
    if kind == "polygon":
        pts = polygon_points(int(o.get("sides") or 6), w, h, inset)
        return [Path(polyline_segments(pts, True), fill=fill, stroke=filled_stroke, object_id=oid)]
    if kind == "star":
        ratio = o.get("star_ratio")
        ratio = (
            float(ratio)
            if isinstance(ratio, (int, float))
            and not isinstance(ratio, bool)
            and math.isfinite(ratio)
            else DEFAULT_STAR_RATIO
        )
        pts = star_points(int(o.get("sides") or DEFAULT_STAR_POINTS), w, h, inset, ratio)
        return [Path(polyline_segments(pts, True), fill=fill, stroke=filled_stroke, object_id=oid)]
    if kind == "arc":
        return [
            Path(
                arc_segments(w, h, inset),
                stroke=_stroke(o, sw, join="miter", box=(w, h)),
                object_id=oid,
            )
        ]
    if kind == "brace":
        cx = w / 2
        tip_gap = min(h * 0.06, h / 2 - inset)
        segs: list[Segment] = [("M", w - inset, inset)]
        segs.append(curve_segment((w - inset, inset), (cx, inset), (cx, h * 0.25)))
        segs.append(("L", cx, h / 2 - tip_gap))
        segs.append(curve_segment((cx, h / 2 - tip_gap), (cx, h / 2), (inset, h / 2)))
        segs.append(curve_segment((inset, h / 2), (cx, h / 2), (cx, h / 2 + tip_gap)))
        segs.append(("L", cx, h * 0.75))
        segs.append(curve_segment((cx, h * 0.75), (cx, h - inset), (w - inset, h - inset)))
        return [Path(tuple(segs), stroke=_stroke(o, sw, join="miter", box=(w, h)), object_id=oid)]
    # line：端点与箭头同一套算法（框比例坐标 → 绝对 pt）；缺省即水平中线
    s = o.get("start") or {"rx": 0.0, "ry": 0.5}
    e = o.get("end") or {"rx": 1.0, "ry": 0.5}
    pts = [
        (float(s["rx"]) * w, float(s["ry"]) * h),
        (float(e["rx"]) * w, float(e["ry"]) * h),
    ]
    return [
        Path(
            polyline_segments(pts, False),
            stroke=_stroke(o, sw, join="miter", box=(w, h)),
            object_id=oid,
        )
    ]


# ---------------------------------------------------------------------------
# 箭头
# ---------------------------------------------------------------------------
def arrow_heads(o: dict) -> tuple[str, str]:
    """新旧端型统一：head_start/head_end 优先，缺失按旧 head 推导三角头。"""
    hs, he = o.get("head_start"), o.get("head_end")
    if hs is not None or he is not None:
        return str(hs or "none"), str(he or "none")
    legacy = o.get("head", "end")
    return (
        "triangle" if legacy == "both" else "none",
        "triangle" if legacy in ("end", "both") else "none",
    )


def _head_size(o: dict, which: str) -> float:
    size = o.get(f"head_size_{which}")
    return HEAD_SIZE_FACTOR.get(size, 1.0) if isinstance(size, str) else 1.0


def head_geometry(
    kind: str,
    tip: tuple[float, float],
    sign: float,
    u: tuple[float, float],
    sw: float,
    f: float,
) -> tuple[tuple | None, float]:
    """一个端型的几何 + 杆在这一端回缩的距离。与前端 `shapeGeometry.arrowGeometry` 的 `headOf` 逐点对应。

    `sign` = 1 是终点（向后是 -u）、-1 是起点；`u` 是 起点 → 终点 的单位向量。
    形状：`("poly", pts)` 实心多边形（triangle / stealth / diamond）、`("dot", cx, cy, r)` 实心圆、
    `("open", pts)` 开口折线、`("bar", pts)` 短线；不认识的端型 → `(None, 0)`（杆到尖、不画端型）。"""
    ux, uy = u
    nx, ny = -uy, ux
    length, half = sw * 4.0 * f, sw * 1.7 * f

    def at(d: float, s: float) -> tuple[float, float]:
        return (tip[0] - sign * ux * d + nx * s, tip[1] - sign * uy * d + ny * s)

    if kind == "triangle":
        return ("poly", [tip, at(length, half), at(length, -half)]), length * HEAD_TRIM["triangle"]
    if kind == "stealth":
        pts = [tip, at(length, half), at(length * STEALTH_NOTCH, 0.0), at(length, -half)]
        return ("poly", pts), length * HEAD_TRIM["stealth"]
    if kind == "diamond":
        pts = [tip, at(length / 2, half), at(length, 0.0), at(length / 2, -half)]
        return ("poly", pts), length * HEAD_TRIM["diamond"]
    if kind == "dot":
        cx, cy = at(half, 0.0)
        return ("dot", cx, cy, half), half
    if kind == "open":
        return ("open", [at(length, half), tip, at(length, -half)]), 0.0
    if kind == "bar":
        return ("bar", [at(0.0, half), at(0.0, -half)]), 0.0
    return None, 0.0


def arrow_geometry(o: dict, w: float, h: float) -> dict:
    """箭头的框空间几何：端点、回缩后的杆、两个端型（`head_geometry` 的形状）。
    与前端 `arrowGeometry` 同一个函数（共享向量接住）。"""
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    ax, ay = float(o["start"]["rx"]) * w, float(o["start"]["ry"]) * h
    bx, by = float(o["end"]["rx"]) * w, float(o["end"]["ry"]) * h
    dx, dy = bx - ax, by - ay
    ln = math.hypot(dx, dy) or 1.0
    u = (dx / ln, dy / ln)
    hs, he = arrow_heads(o)
    start_head, start_trim = head_geometry(hs, (ax, ay), -1.0, u, sw, _head_size(o, "start"))
    end_head, end_trim = head_geometry(he, (bx, by), 1.0, u, sw, _head_size(o, "end"))
    return {
        "a": (ax, ay),
        "b": (bx, by),
        "p1": (ax + u[0] * start_trim, ay + u[1] * start_trim),
        "p2": (bx - u[0] * end_trim, by - u[1] * end_trim),
        "start_head": start_head,
        "end_head": end_head,
    }


def arrow_paths(o: dict, w: float, h: float) -> list[Path]:
    """逐点复刻前端 ArrowView 几何（`arrow_geometry`）：帽长 4×线宽、帽半宽 1.7×线宽（再乘端型大小档位）、
    实心端型回缩线段；端型独立 + 虚线 + 描边不透明度 + 线端 / 拐角（缺省圆头 / 圆角）。"""
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    color = Paint(hex2rgb(o.get("color", "#000000")))  # 不透明度在组上（arrow_opacity）
    oid = str(o.get("id", ""))
    g = arrow_geometry(o, w, h)
    out = [
        Path(
            polyline_segments([g["p1"], g["p2"]], False),
            stroke=_stroke(o, sw, join="miter", opaque=True),
            object_id=oid,
        )
    ]
    # 端型的拐角：开口 V 缺省圆角（它有拐角），短线只有一段、沿用旧产物的 miter
    head_strokes = {
        "open": _stroke(o, sw, join="round", dash=False, opaque=True),
        "bar": _stroke(o, sw, join="miter", dash=False, opaque=True),
    }
    for head in (g["end_head"], g["start_head"]):
        if head is None:
            continue
        if head[0] == "poly":
            out.append(Path(polyline_segments(head[1], True), fill=color, object_id=oid))
        elif head[0] == "dot":
            _, cx, cy, r = head
            segs = ellipse_segments(cx - r, cy - r, 2 * r, 2 * r)
            out.append(Path(segs, fill=color, object_id=oid))
        else:  # open / bar：描边路径
            out.append(
                Path(polyline_segments(head[1], False), stroke=head_strokes[head[0]], object_id=oid)
            )
    return out


# ---------------------------------------------------------------------------
# 自由路径（ADR 0185）——与前端 `web/src/lib/pathGeom.ts` 的 `pathGeometry` 严格同源
# ---------------------------------------------------------------------------
def _pt(v: object) -> tuple[float, float] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            x, y = float(v[0]), float(v[1])
        except (TypeError, ValueError):
            return None
        if math.isfinite(x) and math.isfinite(y):
            return (x, y)
    return None


def read_path(o: dict) -> dict:
    """路径的缺省值统一读取；与前端 `types/document.readPath` 同一规则。

    手柄缺省 = 与锚点重合；`fill_rule` 缺省 nonzero；`stroke` 只有 `false` 关描边；端型缺省 none。
    锚点读不成数对的整个丢掉（坏数据不画，也不报错——与不认识的端型同一种保守）。"""
    subpaths = []
    for sp in o.get("subpaths") or ():
        if not isinstance(sp, dict):
            continue
        nodes = []
        for n in sp.get("nodes") or ():
            if not isinstance(n, dict):
                continue
            p = _pt(n.get("p"))
            if p is None:
                continue
            nodes.append({"p": p, "in": _pt(n.get("in")) or p, "out": _pt(n.get("out")) or p})
        subpaths.append({"closed": sp.get("closed") is True, "nodes": nodes})
    hs, he = o.get("head_start"), o.get("head_end")
    return {
        "subpaths": subpaths,
        "fill_rule": "evenodd" if o.get("fill_rule") == "evenodd" else "nonzero",
        "stroke_on": o.get("stroke") is not False,
        "head_start": hs if isinstance(hs, str) else "none",
        "head_end": he if isinstance(he, str) else "none",
    }


def _seg_to(a: dict, b: dict, out: list) -> None:
    if a["out"] == a["p"] and b["in"] == b["p"]:
        out.append(("L", *b["p"]))
    else:
        out.append(("C", *a["out"], *b["in"], *b["p"]))


def _subpath_segs(nodes: list[dict], closed: bool, out: list) -> None:
    out.append(("M", *nodes[0]["p"]))
    for i in range(1, len(nodes)):
        _seg_to(nodes[i - 1], nodes[i], out)
    if closed:
        _seg_to(nodes[-1], nodes[0], out)
        out.append(("Z",))


def _first_distinct(tip: tuple[float, float], cands) -> tuple[float, float] | None:
    for c in cands:
        if math.hypot(c[0] - tip[0], c[1] - tip[1]) > 1e-9:
            return c
    return None


def _unit_to(a, b) -> tuple[float, float]:
    if a is None or b is None:
        return (0.0, 0.0)
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy) or 1.0
    return (dx / ln, dy / ln)


def path_segments(o: dict, w: float, h: float, sw: float | None = None) -> dict:
    """路径在框空间（原点框左上、y 向下）里的几何：`fill`（全部子路径、未回缩）、`stroke`（开放子路径
    两端按实心端型回缩）、`heads`（`head_geometry` 的形状，每条开放子路径先终点后起点）、`head_half`。

    规则（与前端 `pathGeometry` 逐条相同）：比例坐标 × 框；前一锚点的出手柄与后一锚点的入手柄都与各自锚点
    重合 → `L`，否则 `C`；闭合子路径补「末 → 首」再 `Z`；少于两个锚点的子路径不画；端型只对开放子路径、
    且有描边时画，方向取那一端的切线（往里第一个不与端点重合的控制点），回缩 = 端点与相邻手柄一起平移。"""
    if sw is None:
        sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    r = read_path(o)
    f_start, f_end = _head_size(o, "start"), _head_size(o, "end")
    want_heads = r["stroke_on"] and (r["head_start"] != "none" or r["head_end"] != "none")
    fill: list = []
    stroke: list = []
    heads: list = []
    head_half = 0.0
    for sp in r["subpaths"]:
        raw = sp["nodes"]
        n = len(raw)
        if n < 2:
            continue
        nodes = [
            {k: (q[k][0] * w, q[k][1] * h) for k in ("p", "in", "out")}  # type: ignore[index]
            for q in raw
        ]
        _subpath_segs(nodes, sp["closed"], fill)
        if sp["closed"] or not want_heads:
            _subpath_segs(nodes, sp["closed"], stroke)
            continue
        first, last = nodes[0], nodes[-1]
        us = _unit_to(
            first["p"], _first_distinct(first["p"], (first["out"], nodes[1]["in"], nodes[1]["p"]))
        )
        ue = _unit_to(
            _first_distinct(last["p"], (last["in"], nodes[-2]["out"], nodes[-2]["p"])), last["p"]
        )
        e_shape, e_trim = head_geometry(r["head_end"], last["p"], 1.0, ue, sw, f_end)
        s_shape, s_trim = head_geometry(r["head_start"], first["p"], -1.0, us, sw, f_start)
        trimmed = list(nodes)
        if s_trim:
            d = (us[0] * s_trim, us[1] * s_trim)
            trimmed[0] = {
                "p": (first["p"][0] + d[0], first["p"][1] + d[1]),
                "in": first["in"],
                "out": (first["out"][0] + d[0], first["out"][1] + d[1]),
            }
        if e_trim:
            d = (-ue[0] * e_trim, -ue[1] * e_trim)
            trimmed[-1] = {
                "p": (last["p"][0] + d[0], last["p"][1] + d[1]),
                "in": (last["in"][0] + d[0], last["in"][1] + d[1]),
                "out": last["out"],
            }
        _subpath_segs(trimmed, False, stroke)
        if e_shape is not None:
            heads.append(e_shape)
            head_half = max(head_half, sw * 1.7 * f_end)
        if s_shape is not None:
            heads.append(s_shape)
            head_half = max(head_half, sw * 1.7 * f_start)
    return {"fill": fill, "stroke": stroke, "heads": heads, "head_half": head_half}


#: 路径的缺省拐角：与封闭形状 / 箭头相同（圆角）；与前端 `pathGeom.PATH_DEFAULT_JOIN` 同一个缺省。
PATH_DEFAULT_JOIN = "round"


def path_stroke_align(o: dict) -> str | None:
    """路径实际生效的描边对齐（ADR 0185 M2）：设了 inside / outside、有描边、每条画得出的子路径都闭合才生效；
    否则 None（居中）。与前端 `pathGeom.pathStrokeAlign` 同一条判据。"""
    align = o.get("stroke_align")
    if align not in ("inside", "outside"):
        return None
    r = read_path(o)
    if not r["stroke_on"]:
        return None
    drawn = [sp for sp in r["subpaths"] if len(sp["nodes"]) >= 2]
    return align if drawn and all(sp["closed"] for sp in drawn) else None


def outside_clip_pad(w: float, h: float, sw: float) -> float:
    """外侧对齐裁剪区的外扩量：与前端 `pathGeom.outsideClipPad` 同一个数。"""
    return max(w, h) + 4 * sw


def path_paths(o: dict, w: float, h: float) -> list[Node]:
    """一个 path 对象 → 框空间里的 IR 节点（画布 `PathView` / `PathBody` 一一对应）。

    没有端型：一条路径又填又描（描边不透明度在描边 Paint 上，与形状相同）。有端型：填充单独一条，
    描边 + 端型放进一个透明组（不透明度作用于「线 + 端型」整体，杆伸进实心端型的重叠处不叠深——与箭头
    同一做法，ADR 0174）；不透明时不包组。既不填也不描：什么都不画。"""
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    r = read_path(o)
    g = path_segments(o, w, h, sw)
    if not g["fill"]:
        return []
    fill_opacity = o.get("fill_opacity")
    fill = (
        Paint(hex2rgb(o["fill"]), 1.0 if fill_opacity is None else float(fill_opacity))
        if o.get("fill")
        else None
    )
    if fill is not None:
        fill = gradient_paint(o.get("fill_gradient"), w, h, fill.alpha) or fill  # ADR 0186
    oid = str(o.get("id", ""))
    rule = r["fill_rule"]
    if not r["stroke_on"]:
        return [Path(tuple(g["fill"]), fill=fill, fill_rule=rule, object_id=oid)] if fill else []
    align = path_stroke_align(o)
    if align is not None:
        # 描边对齐（ADR 0185 M2）：两倍线宽的描边裁到路径里面（内侧）/ 外面（外侧：大矩形 + 路径按奇偶）。
        # 虚线长度按原线宽算，只把线宽换成两倍——与前端 `PathBody` 同一画法
        seg = tuple(g["fill"])
        if align == "inside":
            clip = Path(seg, fill_rule=rule)
        else:
            pad = outside_clip_pad(w, h, sw)
            clip = Path(
                rect_segments(-pad, -pad, w + 2 * pad, h + 2 * pad) + seg, fill_rule="evenodd"
            )
        wide = dataclasses.replace(_stroke(o, sw, join=PATH_DEFAULT_JOIN, box=(w, h)), width=2 * sw)
        out_a: list[Node] = []
        if fill is not None:
            out_a.append(Path(seg, fill=fill, fill_rule=rule, object_id=oid))
        out_a.append(Group((Path(seg, stroke=wide, object_id=oid),), clip=clip, object_id=oid))
        return out_a
    if not g["heads"]:
        return [
            Path(
                tuple(g["fill"]),
                fill=fill,
                stroke=_stroke(o, sw, join=PATH_DEFAULT_JOIN, box=(w, h)),
                fill_rule=rule,
                object_id=oid,
            )
        ]
    out: list[Node] = []
    if fill is not None:
        out.append(Path(tuple(g["fill"]), fill=fill, fill_rule=rule, object_id=oid))
    color = Paint(hex2rgb(o.get("color", "#000000")))
    inner: list[Node] = [
        Path(
            tuple(g["stroke"]),
            stroke=_stroke(o, sw, join=PATH_DEFAULT_JOIN, opaque=True, box=(w, h)),
            object_id=oid,
        )
    ]
    head_strokes = {
        "open": _stroke(o, sw, join="round", dash=False, opaque=True),
        "bar": _stroke(o, sw, join="miter", dash=False, opaque=True),
    }
    for head in g["heads"]:
        if head[0] == "poly":
            inner.append(Path(polyline_segments(head[1], True), fill=color, object_id=oid))
        elif head[0] == "dot":
            _, cx, cy, rad = head
            inner.append(
                Path(
                    ellipse_segments(cx - rad, cy - rad, 2 * rad, 2 * rad),
                    fill=color,
                    object_id=oid,
                )
            )
        else:
            inner.append(
                Path(polyline_segments(head[1], False), stroke=head_strokes[head[0]], object_id=oid)
            )
    alpha = _alpha(o)
    if alpha < 1:
        out.append(Group(tuple(inner), opacity=alpha, object_id=oid))
    else:
        out.extend(inner)
    return out


def clip_polyline(
    pts: list[tuple[float, float]], r: tuple[float, float, float, float]
) -> list[list[tuple[float, float]]]:
    """折线裁进矩形 `r`（逐段 Liang–Barsky）：出框断开，返回框内的若干段。"""
    runs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t0, t1 = 0.0, 1.0
        ok = True
        for p, q in ((-dx, a[0] - r[0]), (dx, r[2] - a[0]), (-dy, a[1] - r[1]), (dy, r[3] - a[1])):
            if p == 0:
                if q < 0:
                    ok = False
                    break
                continue
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                ok = False
                break
        if not ok:
            if cur:
                runs.append(cur)
                cur = []
            continue
        s = (a[0] + dx * t0, a[1] + dy * t0)
        e = (a[0] + dx * t1, a[1] + dy * t1)
        if not cur or cur[-1] != s:
            if cur:
                runs.append(cur)
            cur = [s]
        if e != cur[-1]:
            cur.append(e)
        if t1 < 1.0:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [run for run in runs if len(run) >= 2]
