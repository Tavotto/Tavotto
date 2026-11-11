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

纯标准库。
"""

from __future__ import annotations

import math

from .ir import Paint, Path, Segment, Stroke, hex2rgb

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


def default_line_join(shape: object) -> str:
    """缺省拐角：封闭形状 / 箭头圆角；直线与大括号尖角。与前端 `shapeGeometry.defaultLineJoin` 同源。"""
    return "miter" if shape in ("line", "brace") else "round"


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


def _stroke(o: dict, sw: float, *, join: str, dash: bool = True, opaque: bool = False) -> Stroke:
    """对象的一条描边。线端 / 拐角 / 不透明度 / 线型都从对象上读（缺省 = 旧画法）；
    `dash=False` 给箭头端型（端型不画虚线，也不受点线强制圆头影响）。"""
    d = o.get("dash") if dash else None
    return Stroke(
        paint=Paint(hex2rgb(o.get("color", "#000000")), 1.0 if opaque else _alpha(o)),
        width=sw,
        cap=stroke_cap(o.get("line_cap"), d),
        join=_line_join(o, join),
        dash=dash_pattern(d, sw) if dash else (),
    )


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
    kind = o.get("shape")
    oid = str(o.get("id", ""))
    # 无边框（`stroke: false`，只对封闭形状）：不描边、填充铺满整个框（不再内缩半个线宽）；
    # 直线与大括号只有描边，不看这个字段
    stroke_on = kind in ("line", "brace") or o.get("stroke") is not False
    inset = sw / 2 if stroke_on else 0.0
    filled_stroke = _stroke(o, sw, join=default_line_join(kind)) if stroke_on else None
    if filled_stroke is None and fill is None and kind not in ("line", "brace"):
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
        return [Path(tuple(segs), stroke=_stroke(o, sw, join="miter"), object_id=oid)]
    # line：端点与箭头同一套算法（框比例坐标 → 绝对 pt）；缺省即水平中线
    s = o.get("start") or {"rx": 0.0, "ry": 0.5}
    e = o.get("end") or {"rx": 1.0, "ry": 0.5}
    pts = [
        (float(s["rx"]) * w, float(s["ry"]) * h),
        (float(e["rx"]) * w, float(e["ry"]) * h),
    ]
    return [Path(polyline_segments(pts, False), stroke=_stroke(o, sw, join="miter"), object_id=oid)]


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
