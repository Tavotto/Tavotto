"""路径文字（ADR 0190）：一行字沿一条路径排开。

**排字**与普通文字同一条路：`typography.layout_text`（同一张字体登记表、同一次 HarfBuzz、同一套富文本上下标）排成**一行**
（框宽给到足够大，所以不经过断行——`break_lines` 的规则一个字不碰），再按弧长把每个字放到路径上：字的**中点**落在路径上
s = 起点 + 前面的字宽 + 本字宽 / 2 处，字的基线顺着那里的切线、字的「上方」是切线的左法线（屏幕 y 向下时的 (sin θ, −cos θ)）。
起点 = `start_offset`（路径全长的比例）× 全长，再按对齐方式往回退（居中退半个字串宽、末尾退整个字串宽）；`flip` = 沿路径的
反方向走（字翻到路径另一侧、方向倒过来，Illustrator 的「翻转」）。开放路径上落在 [0, 全长] 之外的字不画（不悄悄折回来），
闭合路径首尾相接、绕回来。

**弧长**：路径（第一条画得出的子路径）按 `geometry.path_segments` 的填充几何展平成折线——直线段原样，三次贝塞尔按 t 等分
`FLATTEN_STEPS` 段——再累加长度。前端 `lib/pathText` 用同一个展平规则（起点柄、点在路径上落起点），
`tests/golden/path_text_vectors.json` 对拍。纯标准库。
"""

from __future__ import annotations

import bisect
import math

from . import geometry

FLATTEN_STEPS = 32
#: 排一行用的框宽（pt）：足够大，`break_lines` 不会断行
ONE_LINE_PT = 1.0e6


def _cubic(p0, p1, p2, p3, t):
    mt = 1 - t
    return (
        mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0],
        mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1],
    )


def polyline(segs) -> tuple[list[tuple[float, float]], bool]:
    """填充几何（M / L / C / Z 段，框空间）→ 第一条子路径的折线与是否闭合。"""
    pts: list[tuple[float, float]] = []
    closed = False
    for s in segs:
        op = s[0]
        if op == "M":
            if pts:
                break
            pts.append((s[1], s[2]))
        elif op == "L" and pts:
            pts.append((s[1], s[2]))
        elif op == "C" and pts:
            p0 = pts[-1]
            p1, p2, p3 = (s[1], s[2]), (s[3], s[4]), (s[5], s[6])
            for k in range(1, FLATTEN_STEPS + 1):
                pts.append(_cubic(p0, p1, p2, p3, k / FLATTEN_STEPS))
        elif op == "Z" and pts:
            closed = True
            if pts[-1] != pts[0]:
                pts.append(pts[0])
            break
    return pts, closed


def arc_table(pts) -> list[float]:
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return cum


def point_at(pts, cum, s: float, closed: bool) -> tuple[float, float, float] | None:
    """弧长 s 处的点与切线角（弧度，框空间 y 向下）。开放路径越界 = None；闭合路径绕回。"""
    total = cum[-1]
    if total <= 0 or len(pts) < 2:
        return None
    if closed:
        s %= total
    elif s < -1e-9 or s > total + 1e-9:
        return None
    s = min(max(s, 0.0), total)
    i = max(1, bisect.bisect_left(cum, s))
    i = min(i, len(cum) - 1)
    # 跳过零长度段（重合的点），否则切线算不出来
    while i < len(cum) - 1 and cum[i] - cum[i - 1] <= 1e-12:
        i += 1
    seg = cum[i] - cum[i - 1]
    a, b = pts[i - 1], pts[i]
    u = 0.0 if seg <= 1e-12 else (s - cum[i - 1]) / seg
    return (
        a[0] + (b[0] - a[0]) * u,
        a[1] + (b[1] - a[1]) * u,
        math.atan2(b[1] - a[1], b[0] - a[0]),
    )


def path_of(o: dict, w: float, h: float) -> tuple[list[tuple[float, float]], list[float], bool]:
    """路径文字对象的路径（框空间 pt）：折线、弧长表、是否闭合；`flip` 时反过来走。"""
    segs = geometry.path_segments(o, w, h)["fill"]
    pts, closed = polyline(segs)
    if o.get("flip") is True:
        pts = pts[::-1]
    return pts, arc_table(pts), closed


def start_of(total: float, run_width: float, o: dict) -> float:
    """第一个字左缘所在的弧长：起点比例 × 全长，再按对齐往回退。"""
    off = o.get("start_offset")
    off = float(off) if isinstance(off, (int, float)) and math.isfinite(off) else 0.0
    anchor = max(0.0, min(1.0, off)) * total
    align = o.get("text_align")
    if align == "center":
        return anchor - run_width / 2
    if align == "end":
        return anchor - run_width
    return anchor


def line_text(o: dict) -> dict:
    """路径文字 → 排一行用的文字对象（与普通文字同一批字段，框宽给大，换行并成空格）。"""
    text = " ".join(str(o.get("text") or "").split())
    return {
        "type": "text",
        "id": o.get("id", ""),
        "x_mm": 0.0,
        "y_mm": 0.0,
        "w_mm": ONE_LINE_PT * 25.4 / 72.0,
        "h_mm": 10.0,
        "text": text,
        "size_pt": o.get("size_pt", 10),
        "bold": bool(o.get("bold")),
        "italic": bool(o.get("italic")),
        "color": o.get("color", "#000000"),
        "align": "left",
        **({"font_family": o["font_family"]} if o.get("font_family") else {}),
        **({"source_font": o["source_font"]} if o.get("source_font") else {}),
    }
