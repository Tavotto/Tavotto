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

散点（PathCollection，`_marker_subpaths`）与**只有 marker 没有连线的 Line2D**
（`_line_marker_subpaths`）出的是**每一颗 marker 的轮廓**，两者共用同一段盖章
逻辑 `_stamp_markers`：形状只拍平一次、按尺度分档抽稀、其余各颗是一次批量仿射；
标记数超过 `MAX_MARKERS`（两种 artist 同一个数）整组退回 bbox，同样在 stderr
上说明。既有连线又有 marker 的 Line2D 仍只描折线（理由见 `element_geometry`）。
"""

from __future__ import annotations

import sys

import numpy as np
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D, _mark_every_path
from matplotlib.markers import MarkerStyle
from matplotlib.patches import FancyArrowPatch, PathPatch, Polygon
from matplotlib.path import Path
from matplotlib.transforms import Affine2D

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
#: 一个元素逐颗出 marker 轮廓的**标记数上限**（散点 PathCollection 与只有 marker
#: 的 Line2D 共用这一个数——消费侧的账不分它们来自哪种 artist），超过就整组退回
#: bbox。
#:
#: 上限量的是消费侧，不是生产侧——生产侧每个 marker 只是一次小矩阵乘（形状
#: 只拍平一次，见 `_stamp_markers`），两万颗也只要几十毫秒；贵的是下游：
#: 每次渲染往返都要带的 manifest JSON、前端每次指针移动沿**全部**线段算的距离、
#: 每次选中都要重建的覆盖层 d 串，三处都随点数线性长。一颗圆 marker 抽稀后
#: ≈ 11 点，500 颗 ≈ 5500 点、JSON +110KB，仍在 TOTAL_BUDGET 之内并给同图的
#: 曲线留了余量；再往上就是把 issue #181 的账（4 万次 plot 挂 20 万节点）
#: 换个形态搬回来。改这个数之前先看 `tests/test_manifest_geometry.py` 里钉它的
#: 那两条。
MAX_MARKERS = 500
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
        seg = pts[i + 1 : j]
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
    return [
        round(bb.x0 / W, _ND),
        round(1.0 - bb.y1 / H, _ND),
        round(bb.width / W, _ND),
        round(bb.height / H, _ND),
    ]


def _pack(
    subpaths,
    W,
    H,
    *,
    fill: bool,
    stroke: bool,
    clip,
    budget,
    stroke_pt: float = 0.0,
    thinned: bool = False,
) -> dict | None:
    """子路径 → geometry 字典。`thinned=True` 表示调用方已经抽过稀（散点的几百个
    副本共用一个抽稀过的形状，再逐个跑一遍 RDP 只是白白多几十毫秒）。"""
    paths = []
    total = 0
    for pts, closed in subpaths:
        thin = pts if thinned else _thin(pts)
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
    # 描边宽度（pt）。前端的命中容差是**中心线**容差，一条 12pt 的粗线有
    # 半宽 ≈2.1mm 落在 1.5mm 容差之外——点在明明画出来的像素上却选不中它，
    # 而改成按路径命中之前的 bbox 判据是能选中的。宽度只有引擎知道，
    # 让它随几何一起下来，前端不推算。
    if stroke and stroke_pt > 0:
        geom["stroke_pt"] = round(float(stroke_pt), 3)
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


def _collection_subpaths(coll, budget: "Budget | None" = None) -> list[tuple[list, bool]]:
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
    # **按渲染器的循环走**：offsets 比 paths 多时 matplotlib 会循环使用路径、
    # 每个 offset 画一份（`Collection.draw` 里就是 `zip(cycle(paths), offsets)`
    # 那套语义）。只枚举 `len(paths)` 的话后面那些副本一条都不出——而元素一旦
    # 有了 geometry，前端就不再退回整体 bbox，于是那些**明明画出来的**多边形
    # 变得既点不中也框不到。
    #
    # 这里**不管 `get_transforms()`**：PolyCollection 的逐点变换表是空的
    # （fill_between / stackplot 的多边形本身就在数据空间里）。带逐点缩放的
    # 是散点那一族，走 `_marker_subpaths`——它有尺寸矩阵要乘，两条路别合并。
    count = len(paths) if len(toffs) <= 1 else max(len(paths), len(toffs))
    out: list[tuple] = []
    total = 0
    for i in range(count):
        dx, dy = toffs[i % len(toffs)]
        for pts, closed in _display_subpaths(paths[i % len(paths)], trans):
            if dx or dy:
                pts = pts + np.asarray([float(dx), float(dy)])
            out.append((pts, closed))
            total += len(pts)
        # 超预算就**当场收手**。`_pack` 最后也会用 `budget.take()` 拒掉整份，
        # 但那是在把几千个副本都算完之后——hexbin 那种一条基路径配上万个
        # offset 的集合会在这里空转半天，而结果注定是要丢的。
        if budget is not None and total > budget.left:
            return []
    return out


def _marker_subpaths(coll, budget: Budget) -> list[tuple] | None:
    """PathCollection（散点）每一颗 marker 的 display 空间轮廓。

    返回 [(点数组 (K,2)，是否闭合)]，点是 display 像素、**已经抽稀**；标记数
    超过 `MAX_MARKERS` 返回 None（调用方退回 bbox）；一颗都画不出来
    返回 []。

    渲染语义跟 Agg 的 `draw_path_collection` 走：第 i 颗用 `paths[i % Np]`、
    逐点变换 `get_transforms()[i % Nt]`（散点的尺寸就在这里——`set_sizes`
    把 √s·dpi/72 写成一个缩放矩阵）、偏移 `offsets[i % No]`，总数
    `max(Np, No)`。整体变换的非仿射段先作用到路径上，仿射段接在逐点变换之后
    ——与 `Collection._prepare_points` / `_iter_collection_raw_paths` 同一个顺序。

    **形状只拍平一次。** 逐颗调 `Path.cleaned()` 一颗要 1.8ms（500 颗就是
    0.9 秒，比整次渲染慢一个量级），而每颗 marker 的贝塞尔都是同一条单位路径
    经不同的仿射变换——仿射映射把曲线映到曲线、把弦映到弦，所以拿**最大**的
    那颗拍平抽稀之后，其余各颗的顶点 = 同一组顶点经 `A_i ∘ A_max⁻¹`，逐位落在
    各自的真实曲线上，弦误差只会更小。剩下的就是一次批量矩阵乘加偏移。
    """
    paths = list(coll.get_paths())
    if not paths:
        return []
    master = coll.get_transform()
    if not master.is_affine:
        paths = [master.transform_path_non_affine(p) for p in paths]
        master = master.get_affine()
    per = np.asarray(coll.get_transforms(), dtype=float).reshape(-1, 3, 3)
    try:
        offs = np.asarray(coll.get_offsets(), dtype=float).reshape(-1, 2)
        toffs = coll.get_offset_transform().transform(offs) if len(offs) else offs
    except Exception:  # noqa: BLE001 — 取不到偏移就按无偏移处理
        toffs = np.zeros((0, 2))
    return _stamp_markers(paths, np.asarray(master.get_matrix(), dtype=float), per, toffs, budget)


def _line_marker_subpaths(line: Line2D, budget: Budget) -> list[tuple] | None:
    """只有 marker、没有连线的 Line2D 每一颗 marker 的 display 空间轮廓。

    返回值约定与 `_marker_subpaths` 相同（None = 超过 `MAX_MARKERS`，退回 bbox）。

    渲染语义跟 `Line2D.draw` 的 marker 段走：所有点**同一个** marker、同一个
    尺寸（`markersize` pt × dpi/72，`','` 像素 marker 不缩放）、marker 自己的
    `get_transform()`；点是 `get_xydata()`（单位换算之后、**忽略 drawstyle**
    ——阶梯线的 marker 画在数据点上，不在阶梯拐角上，`draw` 里就是为此临时把
    drawstyle 换回 default 再取点的），经 `get_transform()` 落到 display
    （非仿射段照旧先作用在路径上）；`markevery` 交给 matplotlib 自己的
    `_mark_every_path`（int / tuple / slice / 掩码 / 按轴对角线比例的 float 都是
    它在解释，自己重写一份必然在某一档上分岔）；NaN 点画不出来，不出。
    半填充（`fillstyle="left"` 等）的 marker 是两个半片各画一次
    （`get_path()` + `get_alt_path()`），这里也各出一条——每颗点两条子路径，
    与画出来的墨迹一致。

    形状只有一份、尺寸只有一个，所以这是 `_stamp_markers` 最简单的一档：
    整体矩阵是单位阵，逐点矩阵只有一个（半填充时两个），偏移就是各数据点。
    """
    marker = getattr(line, "_marker", None) or MarkerStyle(line.get_marker(), line.get_fillstyle())
    if not marker or not line.get_markersize() > 0:
        return []
    xy = np.asarray(line.get_xydata(), dtype=float).reshape(-1, 2)
    if len(xy) == 0:
        return []
    trans = line.get_transform()
    tpath = Path(xy)
    if not trans.is_affine:
        tpath = trans.transform_path_non_affine(tpath)
        trans = trans.get_affine()
    markevery = line.get_markevery()
    if markevery is not None:
        tpath = _mark_every_path(markevery, tpath, trans, line.axes)
    pts = trans.transform(np.asarray(tpath.vertices, dtype=float).reshape(-1, 2))
    pts = pts[np.all(np.isfinite(pts), axis=1)]
    if len(pts) == 0:
        return []

    fig = line.get_figure()
    w = float(line.get_markersize()) * (float(fig.dpi) if fig is not None else 72.0) / 72.0
    scale = 1.0 if str(marker.get_marker()) == "," else w
    paths = [marker.get_path()]
    per = [marker.get_transform().scale(scale).get_matrix()]
    alt = marker.get_alt_path()
    if alt is not None:
        paths.append(alt)
        per.append(marker.get_alt_transform().scale(scale).get_matrix())
        # 两个半片轮流用：第 i 个戳记取 paths[i % 2]、per[i % 2]、点 pts[i]，
        # 所以每个点重复一次，让两个半片挨着落在同一个点上
        pts = np.repeat(pts, 2, axis=0)
    return _stamp_markers(paths, np.eye(3), np.asarray(per, dtype=float), pts, budget)


def _stamp_markers(
    paths: list, mm: np.ndarray, per: np.ndarray, toffs: np.ndarray, budget: Budget
) -> list[tuple] | None:
    """把 `paths` 按「第 i 个戳记 = M · per[i % Nt] 作用在 paths[i % Np] 上，再平移
    toffs[i % No]」的语义盖成 `max(Np, No)` 个 marker 轮廓（散点与只有 marker 的
    Line2D 共用这一段；语义与 Agg `draw_path_collection` 同源）。

    `mm` 是整体仿射矩阵（display 空间），`per` 是 (Nt,3,3) 的逐戳记矩阵，`toffs`
    是 (No,2) 的 display 偏移。返回值约定见 `_marker_subpaths`。
    """
    n_paths, n_tr, n_off = len(paths), len(per), len(toffs)
    count = max(n_paths, n_off)
    if count > MAX_MARKERS:
        return None

    if n_tr:
        # 每颗的线性尺度 = √|det|；全零（s=0）的一颗什么都画不出来，直接不出
        dets = np.abs(per[:, 0, 0] * per[:, 1, 1] - per[:, 0, 1] * per[:, 1, 0])
        k = int(np.argmax(dets))
        if not dets[k] > 0:
            return []
        ref = Affine2D(per[k]) + Affine2D(mm)
        # A_i ∘ A_k⁻¹ = M·P_i·P_k⁻¹·M⁻¹（display → display 的仿射映射）
        rel = mm @ per @ np.linalg.inv(per[k]) @ np.linalg.inv(mm)
    else:
        dets = None
        ref = Affine2D(mm)
        rel = np.eye(3)[None]

    idx = np.arange(count)
    keep = np.ones(count, dtype=bool)
    if n_off:
        keep &= np.all(np.isfinite(toffs[idx % n_off]), axis=1)
    if dets is not None:
        keep &= dets[idx % n_tr] > 0
    idx = idx[keep]
    if len(idx) == 0:
        return []

    # 抽稀容差按尺度分档：用最大那颗的形状去描一颗只有它 1/5 大的 marker，
    # 点数是它的两倍而肉眼看不出差别（气泡图上 500 颗就多出四千个点，直接
    # 撞上 TOTAL_BUDGET）。ρ_i = 线性尺度 / 最大线性尺度 ∈ (0, 1]，第 b 档在
    # 最大形状的空间里用 `_TOL_PX·_TOL_GROWTH^b` 抽稀，落到第 i 颗自己的空间
    # 里容差就是 ≤ _TOL_PX（且 > _TOL_PX / _TOL_GROWTH）——绝不比曲线抽得粗。
    # 每一档只跑一次 RDP（在上一档的结果上继续抽，`_thin` 同一思路）。
    if dets is not None:
        rho = np.sqrt(dets[idx % n_tr] / dets[k])
        bucket = np.floor(np.log(1.0 / rho) / np.log(_TOL_GROWTH)).astype(int)
        bucket = np.clip(bucket, 0, _TOL_ROUNDS)
    else:
        bucket = np.zeros(len(idx), dtype=int)

    # shapes[pi][b] = 第 pi 条路径在第 b 档容差下的 [(点数组, 闭合)]
    shapes: list[dict[int, list[tuple]]] = []
    for p in paths:
        base = [(_thin(pts), closed) for pts, closed in _display_subpaths(p, ref)]
        shapes.append({0: [(pts, c) for pts, c in base if len(pts) >= 2]})

    def level(pi: int, b: int) -> list[tuple]:
        cache = shapes[pi]
        if b not in cache:
            prev = level(pi, b - 1)
            tol = _TOL_PX * _TOL_GROWTH**b
            cache[b] = [
                (pts, c) for pts, c in ((_rdp(pts, tol), c) for pts, c in prev) if len(pts) >= 2
            ]
        return cache[b]

    path_of = idx % n_paths if n_paths > 1 else np.zeros(len(idx), dtype=int)
    groups: list[tuple[int, int, np.ndarray]] = []
    total = 0
    for pi in range(n_paths):
        for b in np.unique(bucket[path_of == pi]):
            mine = idx[(path_of == pi) & (bucket == b)]
            subs = level(pi, int(b))
            groups.append((pi, int(b), mine))
            total += len(mine) * sum(len(pts) for pts, _ in subs)
    # 注定超预算的整组直接收手，不把几百个副本都算完再被 `_pack` 拒掉
    if total > budget.left:
        return []

    # 按 marker 原序输出（第 i 颗的子路径挨在一起），不按分档的计算顺序：
    # 测试与排障都指望「第 i 条路径就是第 i 颗点」，而分档只是计算上的分组
    slot: dict[int, list[tuple]] = {}
    for pi, b, mine in groups:
        subs = level(pi, b)
        if not subs:
            continue
        mats = rel[mine % n_tr] if n_tr else rel[np.zeros(len(mine), dtype=int)]
        shift = toffs[mine % n_off] if n_off else np.zeros((len(mine), 2))
        for pts, closed in subs:
            hom = np.column_stack([pts, np.ones(len(pts))])
            # (n,3,3) × (K,3) → (n,K,3)：每颗 marker 一份顶点
            moved = np.einsum("nij,kj->nki", mats, hom)[:, :, :2] + shift[:, None, :]
            for j, i in enumerate(mine.tolist()):
                slot.setdefault(i, []).append((moved[j], closed))
    return [sub for i in sorted(slot) for sub in slot[i]]


def element_geometry(artist, W: float, H: float, budget: Budget) -> dict | None:
    """一个 artist 的路径几何；不支持的类型返回 None（前端退回 bbox）。

    **散点（PathCollection）与只有 marker 的 Line2D 给的是每一颗 marker 的轮廓**
    （2026-09-06，用户反馈：选中散点时罩一个大矩形、而不是像曲线那样描出各个
    点）。标记数超过 `MAX_MARKERS` 整组退回 bbox，是**有意的降级**，stderr 上说明。
    **箭头（FancyArrowPatch）不给**：它有自己的 `arrow_endpoints` 契约
    （端点手柄、沿线命中、shift 锁角），通用 geometry 插进来只会两套并存。
    """
    try:
        if isinstance(artist, FancyArrowPatch):
            return None
        if isinstance(artist, PathCollection):
            subs = _marker_subpaths(artist, budget)
            if subs is None:
                n = max(len(artist.get_paths()), len(np.atleast_2d(artist.get_offsets())))
                print(
                    f"[geometry] 散点 {n} 个标记超过 MAX_MARKERS={MAX_MARKERS}，退回 bbox",
                    file=sys.stderr,
                )
                return None
            lw = np.asarray(artist.get_linewidths(), dtype=float).ravel()
            lw_max = float(lw.max()) if lw.size else 0.0
            return _pack(
                subs,
                W,
                H,
                # 'x' / '+' 这类不闭合的 marker 没有内部可填：facecolor 在集合上
                # 照样有值（scatter 把 edgecolors 设成 'face'），但填充语义不成立
                fill=_has_paint(artist.get_facecolor()) and any(c for _, c in subs),
                stroke=_has_paint(artist.get_edgecolor()) and lw_max > 0,
                # 逐颗线宽可以不同，命中容差取最宽的那颗（宁可多容一点）
                stroke_pt=lw_max,
                clip=_clip_rect(artist, W, H),
                budget=budget,
                thinned=True,
            )
        if isinstance(artist, Line2D):
            if str(artist.get_linestyle()).lower() in ("none", "", " "):
                # 只有 marker、没有连线的 Line2D（`plot(..., ls="None", marker="o")`）
                # 画出来的墨迹是一颗颗点，那条穿过它们的折线图上根本不存在——
                # 描它等于画一条假线。所以出的是**每一颗 marker 的轮廓**（2026-09-06，
                # 用户反馈：这样画的「散点图」选中时也该逐颗描，不是一个大矩形），
                # 与散点同一取舍、同一上限。既没线也没 marker 的什么都没画，退回 bbox。
                subs = _line_marker_subpaths(artist, budget)
                if subs is None:
                    print(
                        f"[geometry] 曲线 {len(artist.get_xydata())} 个标记超过 MAX_MARKERS="
                        f"{MAX_MARKERS}，退回 bbox",
                        file=sys.stderr,
                    )
                    return None
                if not subs:
                    return None
                alpha = artist.get_alpha()
                mew = float(artist.get_markeredgewidth() or 0.0)
                return _pack(
                    subs,
                    W,
                    H,
                    # `get_markerfacecolor()` 已把 fillstyle="none" 解释成 'none'、
                    # 'auto' 解释成线色；'x' / '+' 这类不闭合的 marker 没有内部可填
                    fill=_has_paint(to_rgba(artist.get_markerfacecolor(), alpha))
                    and any(c for _, c in subs),
                    stroke=_has_paint(to_rgba(artist.get_markeredgecolor(), alpha)) and mew > 0,
                    stroke_pt=mew,
                    clip=_clip_rect(artist, W, H),
                    budget=budget,
                    thinned=True,
                )
            # 既有连线又有 marker（`plot(..., "-o")`）：**只描折线**，与从前一致。
            # 折线本来就穿过每颗 marker 的中心，命中容差之内每颗都点得中；再叠一层
            # marker 轮廓只是多几十条闭合子路径，而 geometry 的 `fill` 是整份一个
            # 标志、前端把「闭合或 fill」的子路径都按面积算——实心 marker 的 fill
            # 会把那条折线一起变成多边形。两种语义要并存得先给 geometry 分层，
            # 不值得为这一档开先例。
            subs = _display_subpaths(artist.get_path(), artist.get_transform())
            return _pack(
                subs,
                W,
                H,
                fill=False,
                stroke=True,
                stroke_pt=float(artist.get_linewidth() or 0.0),
                clip=_clip_rect(artist, W, H),
                budget=budget,
            )
        if isinstance(artist, PolyCollection):
            subs = _collection_subpaths(artist, budget)
            lw = artist.get_linewidths()
            return _pack(
                subs,
                W,
                H,
                fill=_has_paint(artist.get_facecolor()),
                stroke=_has_paint(artist.get_edgecolor()) and bool(len(lw)) and lw[0] > 0,
                stroke_pt=float(lw[0]) if len(lw) else 0.0,
                clip=_clip_rect(artist, W, H),
                budget=budget,
            )
        if isinstance(artist, (Polygon, PathPatch)):
            subs = _display_subpaths(artist.get_path(), artist.get_transform())
            return _pack(
                subs,
                W,
                H,
                fill=bool(artist.get_fill()) and _has_paint(artist.get_facecolor()),
                stroke=_has_paint(artist.get_edgecolor()) and artist.get_linewidth() > 0,
                stroke_pt=float(artist.get_linewidth() or 0.0),
                clip=_clip_rect(artist, W, H),
                budget=budget,
            )
    except Exception as exc:  # noqa: BLE001 — 取几何失败只是少一条轮廓，不拦渲染
        print(f"[geometry] {type(artist).__name__} 取路径失败: {exc}", file=sys.stderr)
    return None
