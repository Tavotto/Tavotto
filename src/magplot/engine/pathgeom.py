"""渲染时派生的**路径几何**（worker 子进程内使用）。

manifest 的 bbox 回答的是「这个元素占了多大一块」——排版、缩放、对齐都够用，
但它当不了选中轮廓，也当不了命中判据：一条斜曲线、一块 fill_between、一个
多边形，包围盒里绝大部分是空白。拿它画选择框，用户看到的是一个跟图形对不上
的矩形；拿它做命中，用户在空白处点一下就误选了别的东西。

这里把 matplotlib **真正画出来的那条路径**取出来，交给前端沿路径描边与命中。

约定（改动前先读）：

* 坐标与 bbox 同一套：**figure 分数、y 向下（top-origin）**。
* geometry 是**渲染派生数据**：每次 `build_manifest` 现算，不进用户文档、
  不是 override、不参与写回。xlim / scale / axes position / figsize / aspect /
  色条方向——任何会触发重排的操作，下一版 manifest 里它自然就是新的。
* 取路径一律走 artist 自己的 `get_path()/get_paths()` + `get_transform()`，
  非仿射那一段（对数轴）先 `transform_path_non_affine` 再交给仿射部分——
  与 `Line2D.draw` / `Collection._prepare_points` 同一条路，不另起炉灶。
  自己拿 `get_xydata()` 乘矩阵会在对数轴、单位转换、drawstyle="steps-*"
  上各错一次。
* 贝塞尔由 `Path.cleaned(curves=False)` 在 **display 空间**里自适应细分成
  折线：容差天然是显示像素级的，前端不必也不该去猜控制点。
* NaN / masked 断点由 `remove_nans=True` 拆成多条子路径——一条断开的曲线
  是多条线，不是一条穿过空洞的直线。
* 全程走 numpy 数组，**不逐点做 Python 循环**：两万点的谱线上那条循环本身
  就要 500ms 以上，比整次渲染还慢（见 `_display_subpaths` 的注释）。

点数控制（确定性，不随机抽点）：先按 `_TOL_PX` 显示像素做 Ramer–Douglas–
Peucker 抽稀，超过 `_MAX_POINTS` 就按 `_TOL_GROWTH` 逐档放大容差重来，
仍超上限才等距抽（保端点）。整份 manifest 另有一个总点数预算，用完之后的
元素不再出 geometry——前端对没有 geometry 的元素本来就退回 bbox，这是
**有意的降级**，并会在 stderr 上说明是哪一个元素被降级了。
"""
from __future__ import annotations

import sys

import numpy as np
from matplotlib.collections import PolyCollection
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, PathPatch, Polygon
from matplotlib.path import Path

#: RDP 抽稀容差（display 像素）。0.4px 在任何缩放下都看不出偏差，
#: 而一条 5000 点的谱线通常能掉到两三百点。
_TOL_PX = 0.4
#: 单条子路径的点数上限（600 点 ≈ 一条 600px 宽曲线每像素一个点）。
_MAX_POINTS = 600
_TOL_GROWTH = 1.6
_TOL_ROUNDS = 8
#: 超过这个点数先按段取极值压一遍再做 RDP（见 `_block_extremes` 的性能注释）
_DECIMATE_ABOVE = 4 * _MAX_POINTS
#: 一份 manifest 的总点数预算（超出的元素退回 bbox）。
TOTAL_BUDGET = 8000
#: 坐标保留位数（figure 分数）。5 位 ≈ 600px 图上 0.006px，远细于抽稀容差。
_ND = 5


class Budget:
    """一份 manifest 的 geometry 点数预算（build_manifest 每次新建一个）。

    用完之后的元素不再出 geometry，前端对它们退回 bbox。这是**有意的降级**，
    但降级必须说出来——静默降级的表现是「同一张图上有的曲线选得准、有的
    选不准」，而没人知道为什么。
    """

    def __init__(self, total: int = TOTAL_BUDGET):
        self.left = int(total)
        self.skipped = 0

    def take(self, n: int) -> bool:
        if n > self.left:
            self.skipped += 1
            return False
        self.left -= n
        return True


# ---------------------------------------------------------------------------
# display 空间的子路径
# ---------------------------------------------------------------------------
def _display_subpaths(path: Path, transform) -> list[tuple]:
    """`path` 经 `transform` 落到 display 像素后的子路径列表。

    返回 [(点数组 (N,2), 是否闭合)]，点是 display 像素（bottom-origin）。

    走 `Path.cleaned()` 一次拿到整段 **numpy 数组**，而不是 `iter_segments`
    逐段迭代：后者在两万点的谱线上要跑两万次 Python 循环，光这一步就比整次
    渲染还慢（实测 +550ms/次）。NaN 拆分、贝塞尔细分都在同一次 C 调用里完成。
    """
    if path is None or len(path.vertices) == 0:
        return []
    if not transform.is_affine:
        # 非仿射（对数 / symlog / logit 轴）：先把非仿射那一段作用在路径上，
        # 剩下的仿射矩阵才喂得进 cleaned（它只接受仿射变换）
        path = transform.transform_path_non_affine(path)
        transform = transform.get_affine()

    cleaned = path.cleaned(transform=transform, remove_nans=True, curves=False)
    verts = np.asarray(cleaned.vertices, dtype=float)
    codes = cleaned.codes
    if codes is None:
        return [(verts, False)] if len(verts) >= 2 else []

    codes = np.asarray(codes)
    starts = np.flatnonzero(codes == Path.MOVETO)
    out: list[tuple] = []
    for k, s in enumerate(starts):
        e = int(starts[k + 1]) if k + 1 < len(starts) else len(codes)
        seg = codes[s:e]
        # 子路径 = MOVETO + 一串 LINETO；CLOSEPOLY / STOP 的顶点是占位符
        # （实测是 (1,0) / (0,0)），必须掐掉，不然闭合三角会多出一个假顶点
        tail = np.flatnonzero(seg[1:] != Path.LINETO)
        end = s + (int(tail[0]) + 1 if len(tail) else len(seg))
        closed = bool(len(tail) and seg[int(tail[0]) + 1] == Path.CLOSEPOLY)
        pts = verts[s:end]
        if len(pts) >= 2:
            out.append((pts, closed))
    return out


# ---------------------------------------------------------------------------
# 抽稀
# ---------------------------------------------------------------------------
def _rdp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Ramer–Douglas–Peucker，显式栈（不递归）+ numpy 算垂距。

    保留首尾与所有偏离超过 `tol` 的转折点——「形状关键点」正是它挑出来的
    那些，所以抽稀之后视觉上仍是同一条线。
    """
    n = len(pts)
    if n <= 2 or tol <= 0:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    tol2 = tol * tol
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = pts[i + 1:j]
        a = pts[i]
        b = pts[j]
        d = b - a
        den = float(d[0] * d[0] + d[1] * d[1])
        rel = seg - a
        if den == 0.0:
            d2 = rel[:, 0] ** 2 + rel[:, 1] ** 2
        else:
            t = np.clip((rel[:, 0] * d[0] + rel[:, 1] * d[1]) / den, 0.0, 1.0)
            e = rel - t[:, None] * d
            d2 = e[:, 0] ** 2 + e[:, 1] ** 2
        k = int(np.argmax(d2))
        if float(d2[k]) > tol2:
            k += i + 1
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return pts[keep]


def _block_extremes(pts: np.ndarray, blocks: int) -> np.ndarray:
    """把点按**顺序**切成 `blocks` 段，每段留 x/y 各自的最小最大点（外加首尾）。

    为什么需要它：RDP 在「两万点的带噪谱线」上是这条链路唯一的性能悬崖——
    噪声让几乎每个点都成为转折点，栈递归退化成上万次 numpy 调用（实测一条
    26ms、八条 360ms，比整次渲染还慢）。先按段取极值把点压到千级，再交给
    RDP，形状信息一点没丢：用户看到的「墨迹带」正是每一小段的上下沿，而
    这里保的就是它。

    刻意**不**按 x 分桶而按顺序分段：按 x 分桶会把回头的路径（闭合多边形、
    fill_between 的上下两条边）搅成一团。顺序分段对任何路径都成立。
    确定性、可复现，与「随意抽点」无关。
    """
    n = len(pts)
    bid = np.minimum((np.arange(n) * blocks) // n, blocks - 1)
    picks = [np.array([0, n - 1])]
    for col in (0, 1):
        order = np.lexsort((pts[:, col], bid))
        sb = bid[order]
        lo = np.searchsorted(sb, np.arange(blocks), "left")
        hi = np.searchsorted(sb, np.arange(blocks), "right")
        ne = hi > lo
        picks.append(order[lo[ne]])
        picks.append(order[hi[ne] - 1])
    return pts[np.unique(np.concatenate(picks))]


def _thin(points: np.ndarray) -> np.ndarray:
    """抽稀到 `_MAX_POINTS` 以内。

    超长路径先按段取极值压到千级（`_block_extremes`），再做 RDP：容差逐档
    放大，而且**在上一轮的结果上继续抽**——RDP 的输出是输入的子集，对子集
    再抽一次与从头用更大容差抽的形状同一量级，却不必反复重头跑八遍。
    最后仍超上限才等距抽（保端点）。全程确定性，不随机。
    """
    if len(points) <= 2:
        return points
    if len(points) > _DECIMATE_ABOVE:
        # 长路径按段取极值就够了：每段留 x/y 的上下沿，点数直接落到上限之内，
        # 而**每一个留下的点都是曲线上的真实点**，纵向包络逐段精确。这一档
        # 之后不再跑 RDP——在这个尺度上它只能再省一半点，却要多花五倍时间
        # （噪声让几乎每个点都是转折点，实测 1.8ms → 15ms）。
        points = _block_extremes(points, _MAX_POINTS // 4)
        if len(points) <= _MAX_POINTS:
            return points
    out = _rdp(points, _TOL_PX)
    tol = _TOL_PX
    rounds = 0
    while len(out) > _MAX_POINTS and rounds < _TOL_ROUNDS:
        tol *= _TOL_GROWTH
        out = _rdp(out, tol)
        rounds += 1
    if len(out) > _MAX_POINTS:
        step = int(np.ceil(len(out) / _MAX_POINTS))
        idx = np.arange(0, len(out), step)
        if idx[-1] != len(out) - 1:
            idx = np.append(idx, len(out) - 1)
        out = out[idx]
    return out


# ---------------------------------------------------------------------------
# 元素 → geometry
# ---------------------------------------------------------------------------
def _to_frac(points: np.ndarray, W: float, H: float) -> list:
    """display 像素（bottom-origin）→ figure 分数（top-origin），与 bbox 同源。"""
    a = np.empty_like(points)
    a[:, 0] = points[:, 0] / W
    a[:, 1] = 1.0 - points[:, 1] / H
    return np.round(a, _ND).tolist()


def _clip_rect(artist, W: float, H: float):
    """元素的裁剪框（figure 分数、top-origin）；不是矩形裁剪就不给。

    子图里的曲线与填充都被裁在 axes 框内，轮廓画到框外就是画了一段图上
    根本没有的墨迹。裁剪路径是任意形状时（`set_clip_path`）这里表达不了，
    宁可不给——前端只会少裁一点，不会画出错的形状。
    """
    if not artist.get_clip_on():
        return None
    if artist.get_clip_path() is not None:
        return None
    bb = artist.get_clip_box()
    if bb is None or bb.width <= 0 or bb.height <= 0:
        return None
    return [round(bb.x0 / W, _ND), round(1.0 - bb.y1 / H, _ND),
            round(bb.width / W, _ND), round(bb.height / H, _ND)]


def _pack(subpaths, W, H, *, fill: bool, stroke: bool, clip, budget) -> dict | None:
    paths = []
    total = 0
    for pts, closed in subpaths:
        thin = _thin(pts)
        if len(thin) < 2:
            continue
        total += len(thin)
        paths.append({"points": _to_frac(thin, W, H), "closed": bool(closed)})
    if not paths:
        return None
    if not budget.take(total):
        return None
    kind = "multi_path" if len(paths) > 1 else ("path" if paths[0]["closed"] else "polyline")
    geom = {"kind": kind, "paths": paths, "fill": bool(fill), "stroke": bool(stroke)}
    if clip is not None:
        geom["clip"] = clip
    return geom


def _has_paint(color) -> bool:
    """颜色有没有真的画出来（RGBA 的 alpha > 0，且不是 'none'）。"""
    try:
        arr = np.atleast_2d(np.asarray(color, dtype=float))
    except (TypeError, ValueError):
        return bool(color) and str(color) != "none"
    if arr.size == 0:
        return False
    if arr.shape[1] >= 4:
        return bool(np.any(arr[:, 3] > 0))
    return True


def _collection_subpaths(coll) -> list[tuple[list, bool]]:
    """Collection 的全部路径（含 offsets 平移），与 `_iter_collection` 同一口径。"""
    trans = coll.get_transform()
    paths = coll.get_paths()
    if not paths:
        return []
    try:
        offs = np.asarray(coll.get_offsets(), dtype=float)
        toffs = coll.get_offset_transform().transform(offs)
    except Exception:  # noqa: BLE001 — 取不到偏移就按无偏移处理
        toffs = np.zeros((1, 2))
    if len(toffs) == 0:
        toffs = np.zeros((1, 2))
    out: list[tuple] = []
    for i, p in enumerate(paths):
        dx, dy = toffs[i % len(toffs)]
        for pts, closed in _display_subpaths(p, trans):
            if dx or dy:
                pts = pts + np.asarray([float(dx), float(dy)])
            out.append((pts, closed))
    return out


def element_geometry(artist, W: float, H: float, budget: Budget) -> dict | None:
    """一个 artist 的路径几何；不支持的类型返回 None（前端退回 bbox）。

    **散点（PathCollection）刻意不给**：每个 marker 一条小路径，几百个点位
    就是几百条路径，既撑爆 manifest 也没人真的需要沿单个 marker 描边。
    散点继续用 bbox，这是**有意的降级**，不是遗漏（见 tests 里的同名断言）。
    **箭头（FancyArrowPatch）也不给**：它有自己的 `arrow_endpoints` 契约
    （端点手柄、沿线命中、shift 锁角），通用 geometry 插进来只会两套并存。
    """
    try:
        if isinstance(artist, FancyArrowPatch):
            return None
        if isinstance(artist, Line2D):
            # 只有 marker、没有连线的 Line2D（`plot(..., ls="None", marker="o")`）
            # 画出来的墨迹是一颗颗点，那条穿过它们的折线图上根本不存在——
            # 描它等于画一条假线。这一档**有意**退回 bbox（与散点同一取舍）。
            if str(artist.get_linestyle()).lower() in ("none", "", " "):
                return None
            subs = _display_subpaths(artist.get_path(), artist.get_transform())
            return _pack(subs, W, H, fill=False, stroke=True,
                         clip=_clip_rect(artist, W, H), budget=budget)
        if isinstance(artist, PolyCollection):
            subs = _collection_subpaths(artist)
            lw = artist.get_linewidths()
            return _pack(subs, W, H,
                         fill=_has_paint(artist.get_facecolor()),
                         stroke=_has_paint(artist.get_edgecolor()) and bool(len(lw)) and lw[0] > 0,
                         clip=_clip_rect(artist, W, H), budget=budget)
        if isinstance(artist, (Polygon, PathPatch)):
            subs = _display_subpaths(artist.get_path(), artist.get_transform())
            return _pack(subs, W, H,
                         fill=bool(artist.get_fill()) and _has_paint(artist.get_facecolor()),
                         stroke=_has_paint(artist.get_edgecolor()) and artist.get_linewidth() > 0,
                         clip=_clip_rect(artist, W, H), budget=budget)
    except Exception as exc:  # noqa: BLE001 — 取几何失败只是少一条轮廓，不拦渲染
        print(f"[geometry] {type(artist).__name__} 取路径失败: {exc}", file=sys.stderr)
    return None
