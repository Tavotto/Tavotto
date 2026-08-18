"""manifest 的**路径几何**（`geometry`）：选中轮廓与命中判据的数据源。

钉三件事：

1. **形状对**——曲线、含 NaN 的曲线、fill_between、`ax.fill()` 的 Polygon、
   带贝塞尔的 PathPatch 各自出什么；箭头与散点**有意不出**（各有各的契约 /
   有意的 bbox 降级）。
2. **坐标约定对**——figure 分数、**y 向下**（top-origin），与 bbox 同一套；
   每个点都落在自己 bbox 的范围内。
3. **是派生数据**——xlim / scale / axes position / figure 尺寸一变就跟着重算，
   而且热会话算出来的与全新 worker 全量重放算出来的**逐位相同**。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""
import pytest

from magplot.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（MM_WORKER_PYTHON）")

SCRIPT_NAME = "fig_geometry.py"
ENTRY = "main"
STEM = "GeomFig"

LIBRARY = '''\
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
    # lines_2：只有 marker 没有连线 → **有意**不出 geometry（那条折线图上不存在）
    ax.plot(x[::10], np.full(6, 0.9), linestyle="None", marker="o")

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
    ax.scatter([2.0, 4.0, 6.0], [0.2, 0.4, 0.6], label="pts")
    ax.set_xlim(0.0, 10.0)
    ax.set_ylim(-0.2, 1.1)
    ax.legend()
    fig.savefig("GeomFig.pdf")
'''


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("geom-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _manifest(figs, patches=()):
    w = _worker(figs)
    try:
        resp = w.override(STEM, list(patches))
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


def test_marker_only_line_falls_back_to_bbox_on_purpose(library):
    """`linestyle="None"` 的曲线画出来是一颗颗点，那条穿过它们的折线并不存在。
    **有意**不出 geometry（描它等于画一条假线），退回 bbox。"""
    el = _el(_manifest(library), "axes_0.lines_2")
    assert "geometry" not in el
    assert el["bbox"]


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
        "facecolor", "edgecolor", "linewidth", "alpha", "visible", "fill"}
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


def test_scatter_stays_on_bbox_deliberately(library):
    """散点**有意**不出 geometry：一个 marker 一条小路径会撑爆 manifest，
    而沿单个 marker 描边也没人需要。这是记录在案的降级，不是遗漏。"""
    el = next(e for e in _manifest(library)["elements"] if e["role"] == "scatter")
    assert "geometry" not in el
    assert el["bbox"]


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
    ("xscale-log", [{"gid": "axes_0", "prop": "xlim", "value": [0.5, 10.0]},
                    {"gid": "axes_0", "prop": "xscale", "value": "log"}]),
    ("position", [{"gid": "axes_0", "prop": "position",
                   "value": [0.2, 0.25, 0.6, 0.5]}]),
    ("aspect", [{"gid": "axes_0", "prop": "aspect", "value": "equal"}]),
]
_FIGSIZE = ("figsize", [{"gid": "figure", "prop": "size_mm", "value": [160.0, 60.0]}])


@pytest.mark.parametrize("name,patches", _RESHAPING, ids=[c[0] for c in _RESHAPING])
def test_geometry_is_regenerated_by_anything_that_reflows(library, name, patches):
    base = _el(_manifest(library), "axes_0.lines_0")["geometry"]
    after = _el(_manifest(library, patches), "axes_0.lines_0")["geometry"]
    assert after["paths"][0]["points"] != base["paths"][0]["points"], \
        f"{name} 之后 geometry 没跟着重算"


@pytest.mark.parametrize("name,patches", [*_RESHAPING, _FIGSIZE],
                         ids=[c[0] for c in [*_RESHAPING, _FIGSIZE]])
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

    for gid in ("axes_0.lines_0", "axes_0.lines_1", "axes_0.fill_0",
                "axes_0.patches_0", "axes_0.patches_1"):
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
