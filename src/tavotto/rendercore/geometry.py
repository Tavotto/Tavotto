"""画布形状 / 箭头 → `ir.Path`（统一实施包 U06，ADR 0059）。

Arrow / Shape 不是 IR 里的节点：它们在编译期变成路径（`Path` 段 + 填充 / 描边），
写入器只认路径，不为每种图标建插件。

## 坐标

所有函数在**对象自己的框空间**里出几何：原点在框的左上角、**y 向下**、单位 pt、
框是 `(0, 0) – (w, h)`。这与旧 facade（`pdfbackend/pymupdf_backend.py` 的 `_draw_shape`
/ `_draw_arrow`）以及前端 `web/src/lib/shapeGeometry.ts` 是**同一套公式、同一个空间**
——`_polygon_points` / `_dash_pattern` 那一对严格同源对（`docs/rules/repo/same-origin-pairs.md`）
在这里只是换了个宿主，数字一个没动。把框空间放到页面（y 向上）上是 `plan.compile_page()`
的事：一个 `Group.transform` 完成平移 + y 翻转（+ 旋转），本模块不知道页面有多高。

描边语义与旧 facade 逐条相同：rect / ellipse 描边居中内缩半线宽（外沿贴合框），
polygon / triangle / diamond 同样内缩，line / brace 只描边、圆线帽；`dotted` 的
「点」全靠圆线帽画出来（段长 0.01 × 线宽）。

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


def dash_pattern(dash: object, sw: float) -> tuple[float, ...]:
    """虚线间距按线宽比例换算；与前端 shapeGeometry.dashArray 同一比例。空元组 = 实线。"""
    if dash == "dashed":
        return (round(sw * 4, 3), round(sw * 2.5, 3))
    if dash == "dotted":
        return (round(max(sw * 0.01, 0.01), 3), round(sw * 2, 3))
    return ()


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
def _stroke(o: dict, sw: float, *, join: str = "miter", dash: bool = True) -> Stroke:
    return Stroke(
        paint=Paint(hex2rgb(o.get("color", "#000000"))),
        width=sw,
        cap="round",
        join=join,
        dash=dash_pattern(o.get("dash"), sw) if dash else (),
    )


def shape_paths(o: dict, w: float, h: float) -> list[Path]:
    """一个 shape 对象（画布语义：`shape` / `stroke_pt` / `color` / `fill` / `fill_opacity`
    / `dash` / `corner_radius_mm` / `sides` / `start` / `end`）→ 框空间里的路径列表。

    `w, h` 是框的 pt 尺寸；毫米 → pt 由调用方换算（`corner_radius_mm` 除外：它是对象
    上的毫米字段，这里自己换）。"""
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    fill = Paint(hex2rgb(o["fill"]), float(o.get("fill_opacity") or 1.0)) if o.get("fill") else None
    inset = sw / 2
    kind = o.get("shape")
    oid = str(o.get("id", ""))
    filled_stroke = _stroke(o, sw, join="round")

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
        return [Path(tuple(segs), stroke=_stroke(o, sw), object_id=oid)]
    # line：端点与箭头同一套算法（框比例坐标 → 绝对 pt）；缺省即水平中线
    s = o.get("start") or {"rx": 0.0, "ry": 0.5}
    e = o.get("end") or {"rx": 1.0, "ry": 0.5}
    pts = [
        (float(s["rx"]) * w, float(s["ry"]) * h),
        (float(e["rx"]) * w, float(e["ry"]) * h),
    ]
    return [Path(polyline_segments(pts, False), stroke=_stroke(o, sw), object_id=oid)]


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


def arrow_paths(o: dict, w: float, h: float) -> list[Path]:
    """逐点复刻前端 ArrowView 几何：帽长 4×线宽、帽半宽 1.7×线宽、仅 triangle 端线段
    回缩 0.75×帽长、圆线帽；端型独立 + 虚线。"""
    sw = max(float(o.get("stroke_pt", 1.0)), 0.05)
    color = Paint(hex2rgb(o.get("color", "#000000")))
    oid = str(o.get("id", ""))
    ax, ay = float(o["start"]["rx"]) * w, float(o["start"]["ry"]) * h
    bx, by = float(o["end"]["rx"]) * w, float(o["end"]["ry"]) * h
    dx, dy = bx - ax, by - ay
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    nx, ny = -uy, ux
    head_len, head_half = sw * 4.0, sw * 1.7
    hs, he = arrow_heads(o)
    trim = head_len * 0.75
    p1 = (ax + (ux * trim if hs == "triangle" else 0), ay + (uy * trim if hs == "triangle" else 0))
    p2 = (bx - (ux * trim if he == "triangle" else 0), by - (uy * trim if he == "triangle" else 0))
    out = [Path(polyline_segments([p1, p2], False), stroke=_stroke(o, sw), object_id=oid)]
    for tip_x, tip_y, sign, kind in ((bx, by, 1.0, he), (ax, ay, -1.0, hs)):
        if kind == "none":
            continue
        base_x, base_y = tip_x - sign * ux * head_len, tip_y - sign * uy * head_len
        wing1 = (base_x + nx * head_half, base_y + ny * head_half)
        wing2 = (base_x - nx * head_half, base_y - ny * head_half)
        if kind == "triangle":
            out.append(
                Path(
                    polyline_segments([(tip_x, tip_y), wing1, wing2], True),
                    fill=color,
                    object_id=oid,
                )
            )
        elif kind == "open":
            out.append(
                Path(
                    polyline_segments([wing1, (tip_x, tip_y), wing2], False),
                    stroke=Stroke(color, sw, cap="round", join="round"),
                    object_id=oid,
                )
            )
        elif kind == "bar":
            out.append(
                Path(
                    polyline_segments(
                        [
                            (tip_x + nx * head_half, tip_y + ny * head_half),
                            (tip_x - nx * head_half, tip_y - ny * head_half),
                        ],
                        False,
                    ),
                    stroke=Stroke(color, sw, cap="round"),
                    object_id=oid,
                )
            )
    return out
