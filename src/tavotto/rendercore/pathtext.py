"""路径文字（ADR 0190）：一行字沿一条路径排开。

**排字**与普通文字同一条路：`typography.layout_text`（同一张字体登记表、同一次 HarfBuzz、同一套富文本上下标）排成**一行**
（框宽给到足够大，所以不经过断行——`break_lines` 的规则一个字不碰），再按弧长把每个字放到路径上：字的**中点**落在路径上
s = 起点 + 前面的字宽 + 本字宽 / 2 处，字的基线顺着那里的切线、字的「上方」是切线的左法线（屏幕 y 向下时的 (sin θ, −cos θ)）；
基线再沿这条法线离开路径「离线」那么远（`offset_pt`：缺省自动 = 线宽 / 2 + 0.2 em + 降部深度，字不碰线）——字排在
离线后的那条平行线上（`parallel`），按它的弧长与切线落位，离线多大字距都不变。
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


def parallel(pts, closed: bool, off: float) -> list[tuple[float, float]]:
    """排字用的平行折线：每个顶点沿字的上方（切线左法线，框空间 y 向下时 (dy, −dx)）平移 `off`（pt）。不对外暴露，只给落字用。

    顶点法线 = 相邻两段法线之和再归一（不按斜接放大，尖角处离开原线也不超过 `off`）；首尾：开放路径用那一段自己的，
    闭合路径两头用「末段 + 首段」，首尾仍重合。内侧离得比曲率半径还远时平行线会折回去打结——往回走的点直接丢掉
    （与原线同一段方向点积 ≤ 0），字沿剩下的单调折线排，不会来回跳。"""
    n = len(pts)
    if n < 2 or abs(off) < 1e-12:
        return list(pts)
    seg: list[tuple[float, float] | None] = []
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        seg.append(None if ln <= 1e-12 else (dy / ln, -dx / ln))
    # 零长度段借前一段（开头那几段借后面第一段）的法线
    last = next((v for v in seg if v is not None), None)
    if last is None:
        return list(pts)
    filled: list[tuple[float, float]] = []
    for v in seg:
        last = v if v is not None else last
        filled.append(last)

    def normal(i: int) -> tuple[float, float]:
        if closed and (i == 0 or i == n - 1):
            a, b = filled[-1], filled[0]
        elif i == 0:
            return filled[0]
        elif i == n - 1:
            return filled[-1]
        else:
            a, b = filled[i - 1], filled[i]
        sx, sy = a[0] + b[0], a[1] + b[1]
        ln = math.hypot(sx, sy)
        return b if ln <= 1e-9 else (sx / ln, sy / ln)

    raw = []
    for i, q in enumerate(pts):
        nx, ny = normal(i)
        raw.append((q[0] + nx * off, q[1] + ny * off))
    out = [raw[0]]
    last_i = 0
    for i in range(1, n):
        d0 = (pts[i][0] - pts[last_i][0], pts[i][1] - pts[last_i][1])
        d1 = (raw[i][0] - out[-1][0], raw[i][1] - out[-1][1])
        if d0[0] * d1[0] + d0[1] * d1[1] > 0:
            out.append(raw[i])
            last_i = i
        elif i == n - 1:
            out[-1] = raw[i]  # 末点一定要到：替掉最后一个保留的点
    return out


def layout(o: dict, pts, closed: bool, advances, off: float):
    """逐字落位：在离线后的平行线（`parallel`）上按弧长排，字距就是字宽、不随离线变。回 (每个字的 (中点 x, 中点 y, 切线角)
    或 None——落在开放路径外面的字不画, 有没有放不下的字)。平行线退化（长度为 0）时退回原线、沿法线平移。"""
    lp = parallel(pts, closed, off)
    cum = arc_table(lp)
    shift = 0.0
    if cum[-1] <= 1e-9:
        lp, cum, shift = list(pts), arc_table(pts), off
    run_w = sum(advances)
    s = start_of(cum[-1], run_w, o)
    out = []
    overflow = False
    for adv in advances:
        mid = point_at(lp, cum, s + adv / 2, closed)
        s += adv
        if mid is None:
            overflow = True
            out.append(None)
            continue
        mx, my, th = mid
        if shift:
            mx, my = mx + math.sin(th) * shift, my - math.cos(th) * shift
        out.append((mx, my, th))
    return out, overflow


#: 自动离线的间隙（em）：字最靠线的那一边与线的描边外缘之间留多少
AUTO_GAP_EM = 0.2


def _finite(v) -> float | None:
    return (
        float(v)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        else None
    )


def auto_offset_pt(o: dict, descender: float) -> float:
    """自动离线（pt）：基线离开路径中心线 = 线宽 / 2 + 间隙 + 降部深度。字头朝外、基线朝线——翻面只是换到另一侧、
    方向倒过来，最靠线的仍是降部，所以两种情况同一个式子。`descender` 是主字体 em 单位的下降（负数）；线宽取
    导出载荷里的 `line_width_pt`（生成时一起成组的那条线的描边宽度，前端查不到就不发 = 0）。"""
    size = _finite(o.get("size_pt")) or 10.0
    line = max(_finite(o.get("line_width_pt")) or 0.0, 0.0)
    return line / 2 + AUTO_GAP_EM * size + max(-descender, 0.0) * size


def offset_pt(o: dict, descender: float) -> float:
    """实际用的离线：写了 `baseline_offset_pt`（可为负，0 = 基线贴在线中心）就用它，没写 = 自动。"""
    manual = _finite(o.get("baseline_offset_pt"))
    return manual if manual is not None else auto_offset_pt(o, descender)


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
