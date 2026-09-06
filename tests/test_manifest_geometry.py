"""manifest 的**路径几何**（`geometry`）：选中轮廓与命中判据的数据源。

钉三件事：

1. **形状对**——曲线、含 NaN 的曲线、fill_between、`ax.fill()` 的 Polygon、
   带贝塞尔的 PathPatch 各自出什么；散点出的是**每一颗 marker 的轮廓**（标记数
   有上限，超了整组退回 bbox）；箭头**有意不出**（它有自己的契约）。
2. **坐标约定对**——figure 分数、**y 向下**（top-origin），与 bbox 同一套；
   每个点都落在自己 bbox 的范围内。
3. **是派生数据**——xlim / scale / axes position / figure 尺寸一变就跟着重算，
   而且热会话算出来的与全新 worker 全量重放算出来的**逐位相同**。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import re
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_geometry.py"
ENTRY = "main"
STEM = "GeomFig"

#: 一个元素逐颗描 marker 轮廓的标记数上限（散点与只有 marker 的 Line2D **共用
#: 这一个数**），从 `engine/pathgeom.py` 的源码里读——本进程不 import matplotlib，
#: 也就 import 不了 pathgeom；正则钉死常量名，谁改名这里就先红。
MAX_MARKERS = int(
    re.search(
        r"^MAX_MARKERS = (\d+)$",
        (Path(__file__).resolve().parents[1] / "src/tavotto/engine/pathgeom.py").read_text(
            encoding="utf-8"
        ),
        re.M,
    ).group(1)
)

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, PathPatch
from matplotlib.path import Path


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0.0, 10.0, 60)

    # lines_0：一条从左下到右上的直线（用来钉 y 向下的坐标约定）
    ax.plot([0.0, 10.0], [0.0, 1.0], color="#333333")
    # lines_1：中间有一段 NaN 的曲线 → 必须拆成两条子路径
    y = np.sin(x) * 0.3 + 0.5
    y[20:25] = np.nan
    ax.plot(x, y, label="wave")
    # lines_2：只有 marker 没有连线 → 每颗 marker 一条闭合轮廓（不是穿过它们的折线）
    ax.plot(x[::10], np.full(6, 0.9), linestyle="None", marker="o", ms=8)

    # fill_0：fill_between，同样被 NaN 断成两块
    ax.fill_between(x, 0.0, y, alpha=0.3)
    # patches_0：ax.fill() 造出来的 Polygon（闭合）
    ax.fill([1.0, 3.0, 2.0], [0.10, 0.22, 0.05], color="#B34700")
    # patches_1：带三次贝塞尔的 PathPatch（必须被拍平成折线）
    ax.add_patch(PathPatch(
        Path([[5.0, 0.05], [6.0, 0.30], [7.0, -0.05], [8.0, 0.15]],
             [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4]),
        fill=False, edgecolor="#2A6F3C"))
    # arrows_2：独立箭头 —— 走 arrow_endpoints 那套契约，不出 geometry
    ax.add_patch(FancyArrowPatch(posA=(1.0, 0.7), posB=(4.0, 0.85),
                                 transform=ax.transData, arrowstyle="-|>",
                                 mutation_scale=8, color="#76008A"))
    # lines_3：两万点的带噪谱线 —— 抽稀那条路的性能与保真度都靠它看住
    rng = np.random.RandomState(0)
    dense = np.linspace(0.0, 10.0, 20000)
    ax.plot(dense, 0.6 + np.sin(dense * 3.0) * 0.2 + rng.normal(0, 0.03, dense.size))
    # lines_4：既有连线又有 marker（"-o"）→ 只描折线，行为与从前一致
    ax.plot(x[::10], np.full(6, 1.0), "-o", ms=8)
    # lines_5：只有 marker + markevery=2 → 只出真被画出来的那几颗
    ax.plot(x[::10], np.full(6, 0.8), linestyle="None", marker="o", ms=8, markevery=2)
    # lines_6：空心 marker（mfc="none"）→ 只描边、没有填充语义
    ax.plot(x[::10], np.full(6, 0.7), linestyle="None", marker="o", ms=8, mfc="none")
    # scatter_1：三颗大小不同的圆 marker → 每颗一条闭合轮廓，位置就是数据点
    ax.scatter([2.0, 4.0, 6.0], [0.2, 0.4, 0.6], s=[30.0, 120.0, 400.0], label="pts")
    # scatter_2：空心 marker（facecolors="none"）→ 只描边、没有填充语义
    ax.scatter([8.0, 9.0], [0.3, 0.5], s=80.0, facecolors="none", edgecolors="#2A6F3C")
    ax.set_xlim(0.0, 10.0)
    ax.set_ylim(-0.2, 1.1)
    ax.legend()
    fig.savefig("GeomFig.pdf")

    # 标记数上限的两张对照图，**各自单独一张**：同一张图上两组加起来会先撞上
    # TOTAL_BUDGET，那时「超过上限的那组没有 geometry」就成了预算在替上限挡，
    # 上限本身写错一位也测不出来（第一版就是这么在变异下活下来的）。
    rng2 = np.random.RandomState(1)
    for stem, n in (("CapFig", __CAP__), ("OverCapFig", __CAP__ + 1)):
        fig2, ax2 = plt.subplots(figsize=(4.0, 3.0))
        ax2.scatter(rng2.uniform(0.0, 1.0, n), rng2.uniform(0.0, 1.0, n), s=4)
        fig2.savefig(f"{stem}.pdf")
    # 只有 marker 的 Line2D 与散点**共用同一个上限**：同样两张对照图
    for stem, n in (("LineCapFig", __CAP__), ("LineOverCapFig", __CAP__ + 1)):
        fig3, ax3 = plt.subplots(figsize=(4.0, 3.0))
        ax3.plot(rng2.uniform(0.0, 1.0, n), rng2.uniform(0.0, 1.0, n),
                 ls="None", marker="o", ms=2)
        fig3.savefig(f"{stem}.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("geom-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY.replace("__CAP__", str(MAX_MARKERS)), encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _manifest(figs, patches=(), stem=STEM):
    w = _worker(figs)
    try:
        resp = w.override(stem, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------
def test_line_geometry_is_a_polyline_in_top_origin_fractions(library):
    """一条从数据左下到右上的直线：figure 分数里 y **向下**，所以起点的 y
    必须比终点大。这条断言就是坐标约定本身——写反了图上的一切都会上下颠倒。"""
    man = _manifest(library)
    el = _el(man, "axes_0.lines_0")
    geom = el["geometry"]
    assert geom["kind"] == "polyline"
    assert len(geom["paths"]) == 1
    assert geom["stroke"] is True and geom["fill"] is False
    pts = geom["paths"][0]["points"]
    assert len(pts) >= 2
    assert not geom["paths"][0]["closed"]
    assert pts[0][0] < pts[-1][0], "x 应当从左到右"
    assert pts[0][1] > pts[-1][1], "top-origin：数据 y 增大 = 分数 y 减小"
    # 每个点都在自己的 bbox 里（bbox 仍是那个包围盒，geometry 不替代它）
    bx, by, bw, bh = el["bbox"]
    for px, py in pts:
        assert bx - 2e-3 <= px <= bx + bw + 2e-3
        assert by - 2e-3 <= py <= by + bh + 2e-3


def test_nan_breaks_a_line_into_several_subpaths(library):
    """一条断开的曲线是**多条线**，不是一条穿过空洞的线。"""
    geom = _el(_manifest(library), "axes_0.lines_1")["geometry"]
    assert geom["kind"] == "multi_path"
    assert len(geom["paths"]) == 2, geom["paths"]
    assert all(len(p["points"]) >= 2 and not p["closed"] for p in geom["paths"])
    # 断口两侧不该被连起来：第一条的末点与第二条的首点之间有明显空档
    gap = abs(geom["paths"][1]["points"][0][0] - geom["paths"][0]["points"][-1][0])
    assert gap > 0.02, f"NaN 断口没断开（gap={gap}）"


#: lines_2 / lines_5 / lines_6 的六个数据点（x = linspace(0, 10, 60)[::10]）
_MARKER_XS = [10.0 / 59 * i for i in (0, 10, 20, 30, 40, 50)]


def _marker_centers_and_radii(geom):
    """每条子路径的中心（figure 分数）与 x 向半径换算成的 **pt**（图宽 4.0 in）。"""
    out = []
    for path in geom["paths"]:
        pts = path["points"]
        cx = sum(px for px, _ in pts) / len(pts)
        cy = sum(py for _, py in pts) / len(pts)
        rx = max(abs(px - cx) for px, _ in pts)
        out.append((cx, cy, rx * 4.0 * 72.0))
    return out


def _data_to_frac(man, dx, dy):
    """数据 → figure 分数：x ∈ [0,10]、y ∈ [-0.2,1.1]，axes 框来自 manifest。"""
    ax_x, ax_y, ax_w, ax_h = _el(man, "axes_0")["bbox"]
    return ax_x + ax_w * dx / 10.0, ax_y + ax_h * (1.0 - (dy + 0.2) / 1.3)


def test_marker_only_line_outlines_every_marker(library):
    """`plot(..., ls="None", marker="o", ms=8)`（用户反馈 2026-09-06 第 2 条的延伸）：
    画出来的墨迹是一颗颗点，选中时也要**逐颗描 marker 的轮廓**，而不是一个大
    包围矩形，更不是那条图上并不存在的折线。

    三件事一起钉：颗数（6 个数据点 = 6 条闭合子路径）、位置（每条的中心就是
    那个数据点）、大小（半径 = ms/2 = 4 pt——`markersize` 是 pt，要乘 dpi/72
    落到像素再换成 figure 分数，忽略它半径就不是这个数）。
    """
    man = _manifest(library)
    el = _el(man, "axes_0.lines_2")
    assert el["role"] == "line"
    geom = el["geometry"]
    assert geom["kind"] == "multi_path"
    assert geom["fill"] is True and geom["stroke"] is True
    assert len(geom["paths"]) == 6, "六个数据点就是六条轮廓"
    assert all(p["closed"] and len(p["points"]) >= 6 for p in geom["paths"]), geom["paths"]
    for (cx, cy, r_pt), dx in zip(_marker_centers_and_radii(geom), _MARKER_XS, strict=True):
        ex, ey = _data_to_frac(man, dx, 0.9)
        assert cx == pytest.approx(ex, abs=3e-3)
        assert cy == pytest.approx(ey, abs=3e-3)
        assert r_pt == pytest.approx(4.0, abs=0.15), f"ms=8 的轮廓半径应当是 4 pt，得到 {r_pt}"


def test_line_with_markers_still_traces_only_the_polyline(library):
    """`plot(..., "-o")` 既有连线又有 marker：**行为不变**，只描那条折线。

    折线本来就穿过每颗 marker 的中心，命中容差之内每颗都点得中；再叠一层
    marker 轮廓只会多出几十条闭合子路径，而 geometry 的 `fill` 是整份一个标志，
    前端把「闭合或 fill」的子路径都当面积——混在一起会让那条折线被当成多边形。
    """
    man = _manifest(library)
    geom = _el(man, "axes_0.lines_4")["geometry"]
    assert geom["kind"] == "polyline"
    assert len(geom["paths"]) == 1 and not geom["paths"][0]["closed"]
    assert geom["fill"] is False and geom["stroke"] is True
    # 六个点共线，RDP 合法地只留首尾；钉的是「一条从第一个点到最后一个点的折线」
    pts = geom["paths"][0]["points"]
    assert pts[0][0] == pytest.approx(_data_to_frac(man, _MARKER_XS[0], 1.0)[0], abs=3e-3)
    assert pts[-1][0] == pytest.approx(_data_to_frac(man, _MARKER_XS[-1], 1.0)[0], abs=3e-3)


def test_markevery_only_outlines_the_markers_actually_drawn(library):
    """`markevery=2`：六个点里只画了第 0/2/4 颗，轮廓也只能有这三颗。"""
    man = _manifest(library)
    geom = _el(man, "axes_0.lines_5")["geometry"]
    assert len(geom["paths"]) == 3, "markevery=2 只抽到三颗"
    got = [cx for cx, _, _ in _marker_centers_and_radii(geom)]
    want = [_data_to_frac(man, _MARKER_XS[i], 0.8)[0] for i in (0, 2, 4)]
    assert got == pytest.approx(want, abs=3e-3)


def test_hollow_marker_line_has_stroke_but_no_fill_semantics(library):
    """`mfc="none"` 的 marker 是空心圈：命中只该在描边附近、选中不该铺底色
    （与空心散点同一语义）。"""
    geom = _el(_manifest(library), "axes_0.lines_6")["geometry"]
    assert geom["fill"] is False and geom["stroke"] is True
    assert len(geom["paths"]) == 6


def test_fill_between_gives_closed_paths(library):
    geom = _el(_manifest(library), "axes_0.fill_0")["geometry"]
    assert geom["fill"] is True
    assert len(geom["paths"]) == 2, "NaN 把填充也断成两块"
    assert all(p["closed"] for p in geom["paths"])
    assert all(len(p["points"]) >= 3 for p in geom["paths"])


def test_polygon_patch_is_registered_and_closed(library):
    """`ax.fill()` 出的 Polygon 以前根本没登记过（选不中）。"""
    man = _manifest(library)
    el = _el(man, "axes_0.patches_0")
    assert el["role"] == "patch"
    assert {f["prop"] for f in el["editable"]} >= {
        "facecolor",
        "edgecolor",
        "linewidth",
        "alpha",
        "visible",
        "fill",
    }
    geom = el["geometry"]
    assert geom["kind"] == "path" and len(geom["paths"]) == 1
    assert geom["paths"][0]["closed"] is True
    assert len(geom["paths"][0]["points"]) == 3, "三角形就是三个顶点"
    assert geom["fill"] is True


def test_path_patch_curves_are_flattened_to_a_polyline(library):
    """贝塞尔在 **display 空间**里被细分成折线：容差天然是显示像素级的，
    前端不必也不该去猜控制点。"""
    geom = _el(_manifest(library), "axes_0.patches_1")["geometry"]
    pts = geom["paths"][0]["points"]
    assert len(pts) > 8, f"三次贝塞尔应当被拍平成多段折线，实际只有 {len(pts)} 点"
    assert geom["fill"] is False and geom["stroke"] is True


def test_arrow_keeps_its_own_contract_and_gets_no_geometry(library):
    """独立箭头有自己的 `arrow_endpoints`（端点手柄 / 沿线命中 / shift 锁角）。
    通用 geometry 插进来会变成两套并存——这条是防它被顺手覆盖的回归。"""
    el = _el(_manifest(library), "axes_0.arrows_2")
    assert "geometry" not in el
    assert len(el["arrow_endpoints"]) == 2


def test_scatter_outlines_every_marker_where_the_data_puts_it(library):
    """散点的 geometry 是**每一颗 marker 各一条闭合轮廓**（用户反馈：选中散点
    罩的是一个大矩形，而不是像曲线那样描出各个点）。

    三件事一起钉：颗数对（3 颗 = 3 条路径）、位置对（每条轮廓的中心就是那
    个数据点在 figure 分数里的落点，y 向下）、大小对（s 越大轮廓越大——
    `set_sizes` 的 √s 缩放矩阵没被漏掉的证据）。
    """
    man = _manifest(library)
    el = _el(man, "axes_0.scatter_1")
    assert el["role"] == "scatter"
    geom = el["geometry"]
    assert geom["kind"] == "multi_path"
    assert geom["fill"] is True and geom["stroke"] is True
    assert len(geom["paths"]) == 3, "三颗点就是三条轮廓"
    assert all(p["closed"] and len(p["points"]) >= 6 for p in geom["paths"]), geom["paths"]
    # 数据 → figure 分数：x ∈ [0,10]、y ∈ [-0.2,1.1]，axes 框来自 manifest
    ax_x, ax_y, ax_w, ax_h = _el(man, "axes_0")["bbox"]
    radii = []
    for path, (dx, dy) in zip(geom["paths"], [(2.0, 0.2), (4.0, 0.4), (6.0, 0.6)], strict=True):
        pts = path["points"]
        cx = sum(px for px, _ in pts) / len(pts)
        cy = sum(py for _, py in pts) / len(pts)
        assert cx == pytest.approx(ax_x + ax_w * dx / 10.0, abs=3e-3)
        assert cy == pytest.approx(ax_y + ax_h * (1.0 - (dy + 0.2) / 1.3), abs=3e-3)
        radii.append(max(abs(px - cx) for px, _ in pts))
    assert radii[0] < radii[1] < radii[2], f"s=30/120/400 的轮廓应当依次变大：{radii}"
    # 散点的 bbox 是**圆心**的包围盒（`Collection.get_datalim` 对 display 空间的
    # marker 只算 offsets，不算 marker 本身的墨迹），所以钉的是「每颗的中心在
    # bbox 里」，而不是像曲线那样「每个点都在 bbox 里」——最边上那颗有半个
    # marker 伸在 bbox 之外，这是 matplotlib 的口径，不是几何算错了
    bx, by, bw, bh = el["bbox"]
    for path in geom["paths"]:
        pts = path["points"]
        cx = sum(px for px, _ in pts) / len(pts)
        cy = sum(py for _, py in pts) / len(pts)
        assert bx - 3e-3 <= cx <= bx + bw + 3e-3
        assert by - 3e-3 <= cy <= by + bh + 3e-3


def test_hollow_scatter_has_stroke_but_no_fill_semantics(library):
    """`facecolors="none"` 的散点是空心圈：命中只该在描边附近、选中不该铺底色。"""
    geom = _el(_manifest(library), "axes_0.scatter_2")["geometry"]
    assert geom["fill"] is False and geom["stroke"] is True
    assert len(geom["paths"]) == 2


@pytest.mark.parametrize(
    "stems,gid",
    [
        (("CapFig", "OverCapFig"), "axes_0.scatter_0"),
        (("LineCapFig", "LineOverCapFig"), "axes_0.lines_0"),
    ],
    ids=["scatter", "marker-only-line"],
)
def test_marker_cap_is_one_number(library, stems, gid):
    """标记数上限：正好 `MAX_MARKERS` 颗仍出轮廓，多一颗就整组退回 bbox；散点与
    只有 marker 的 Line2D **是同一个数**（消费侧的账不分它们来自哪种 artist）。

    这条钉的是**那个常量本身**：上限是消费侧的账（manifest JSON / 每次指针
    移动的距离计算 / 覆盖层 d 串都随点数线性长），改它之前先看 pathgeom 里
    它抬头的那段理由。
    """
    at_cap = _el(_manifest(library, stem=stems[0]), gid)
    over_cap = _el(_manifest(library, stem=stems[1]), gid)
    assert len(at_cap["geometry"]["paths"]) == MAX_MARKERS
    # 多一颗就没有——而且那张图上只有它，点数远在 TOTAL_BUDGET 之内，
    # 挡下它的只能是标记数上限本身
    assert "geometry" not in over_cap, "超过上限的标记组应当退回 bbox"
    assert over_cap["bbox"]


def test_geometry_carries_the_axes_clip_box(library):
    """子图里的曲线被裁在 axes 框内；不带裁剪框的话，数据伸出去的那一截会在
    画布上描出一段图里根本没有的墨迹。"""
    geom = _el(_manifest(library), "axes_0.lines_1")["geometry"]
    clip = geom["clip"]
    axes_bbox = _el(_manifest(library), "axes_0")["bbox"]
    assert clip == pytest.approx(axes_bbox, abs=2e-3)


# ---------------------------------------------------------------------------
# 派生数据：几何一变就重算，且热会话 == 全新 worker
# ---------------------------------------------------------------------------
#: 会让图重排的动作。`figsize` 单列：这张图没有 tight/constrained layout，
#: axes 的**分数**落位与图幅无关，所以图幅一变 geometry 反而应当**不变**——
#: 拿它当「变了没有」的正例会得到一条永远红的假断言。
_RESHAPING = [
    ("xlim", [{"gid": "axes_0", "prop": "xlim", "value": [2.0, 8.0]}]),
    ("ylim", [{"gid": "axes_0", "prop": "ylim", "value": [0.0, 0.8]}]),
    (
        "xscale-log",
        [
            {"gid": "axes_0", "prop": "xlim", "value": [0.5, 10.0]},
            {"gid": "axes_0", "prop": "xscale", "value": "log"},
        ],
    ),
    ("position", [{"gid": "axes_0", "prop": "position", "value": [0.2, 0.25, 0.6, 0.5]}]),
    ("aspect", [{"gid": "axes_0", "prop": "aspect", "value": "equal"}]),
]
_FIGSIZE = ("figsize", [{"gid": "figure", "prop": "size_mm", "value": [160.0, 60.0]}])


@pytest.mark.parametrize("name,patches", _RESHAPING, ids=[c[0] for c in _RESHAPING])
def test_geometry_is_regenerated_by_anything_that_reflows(library, name, patches):
    base = _el(_manifest(library), "axes_0.lines_0")["geometry"]
    after = _el(_manifest(library, patches), "axes_0.lines_0")["geometry"]
    assert after["paths"][0]["points"] != base["paths"][0]["points"], (
        f"{name} 之后 geometry 没跟着重算"
    )


@pytest.mark.parametrize(
    "name,patches", [*_RESHAPING, _FIGSIZE], ids=[c[0] for c in [*_RESHAPING, _FIGSIZE]]
)
def test_hot_geometry_matches_a_fresh_worker_replay(library, name, patches):
    """热会话一步步改出来的 geometry 与全新 worker 一次性重放**逐位相同**。

    geometry 是派生数据，所以它其实是「两条腿的图形状态一不一致」的一个更细
    的探针：bbox 只看得见包围盒，路径连中间的每一个拐点都要对上。
    """
    hot = _worker(library)
    try:
        for i in range(len(patches)):
            hot.override(STEM, patches[: i + 1])
        man_hot = hot.override(STEM, patches)["manifest"]
    finally:
        pool.discard(hot)
    man_fresh = _manifest(library, patches)

    for gid in (
        "axes_0.lines_0",
        "axes_0.lines_1",
        "axes_0.fill_0",
        "axes_0.patches_0",
        "axes_0.patches_1",
        "axes_0.scatter_1",
    ):
        a = _el(man_hot, gid).get("geometry")
        b = _el(man_fresh, gid).get("geometry")
        assert a == b, f"{gid} 的 geometry 在热路与全新 worker 之间分岔（{name}）"


def test_a_very_long_curve_is_thinned_but_keeps_its_envelope(library):
    """两万点的带噪谱线：点数压到上限之内，而**纵向包络仍然精确**。

    抽稀走的是「按段取极值」：留下来的每一个点都是曲线上的真实点，每一小段
    的上下沿一个不少。所以这里能拿 bbox 当尺子——包络掉了的话，geometry 的
    纵向跨度会明显小于 bbox。
    """
    el = _el(_manifest(library), "axes_0.lines_3")
    geom = el["geometry"]
    pts = [p for path in geom["paths"] for p in path["points"]]
    assert 2 < len(pts) <= 600, len(pts)
    ys = [p[1] for p in pts]
    bx, by, bw, bh = el["bbox"]
    assert min(ys) == pytest.approx(by, abs=3e-3)
    assert max(ys) == pytest.approx(by + bh, abs=3e-3)
    xs = [p[0] for p in pts]
    assert min(xs) == pytest.approx(bx, abs=3e-3)
    assert max(xs) == pytest.approx(bx + bw, abs=3e-3)


def test_geometry_point_count_stays_bounded(library):
    """点数有确定性上限：抽稀容差逐档放大，绝不靠随意抽点。"""
    man = _manifest(library)
    for el in man["elements"]:
        geom = el.get("geometry")
        if not geom:
            continue
        for path in geom["paths"]:
            assert len(path["points"]) <= 600, (el["gid"], len(path["points"]))
