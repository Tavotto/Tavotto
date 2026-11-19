"""位置不是数据的集合与位图可拖（ADR 0100 修订 2026-11-18，接 #2018 的线）。

修前（实测）：`transform=ax.transAxes` 手画的图例点（散点）、线组、图形集合、贴在子图框里的
图片，以及 `fig.add_artist(LineCollection(..., transform=fig.transFigure))`，全部 `draggable=False`；
子图里那几种的选中框还是**整块子图**（`get_datalim(transData)` 是空框，退回 tightbbox），点子图里
任何地方都选中它们。

判据的主语有两个，互相独立：

* manifest 的 `anchor`（左下角，figure 分数、y 向下）——拖动写下的值；它与 setter 同一把尺
  （`pathgeom.layout_extent`），单看它证明不了画面真的挪了；
* **画出来的几何**（`geometry` 的路径点，draw 时另一段代码量的）与位图的 `bbox`（matplotlib 自己的
  window extent）——它们必须整体走同一个位移，初始位置也要等于按子图框独立算出来的值。

合同照旧：再画一次不漂、撤销逐位回基线、不挤动子图与别的元素、拖出子图框也还在元素表里、
constrained 下成立、一次性 worker 全量重放 == 热态。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "axes_layout_collections.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection, PatchCollection, PolyCollection
from matplotlib.patches import Circle


def _draw(stem, **kw):
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(8, 4), **kw)
    ax.plot([0, 1], [0, 1])
    ax.scatter([0.2, 0.3], [0.8, 0.7], transform=ax.transAxes, s=60)       # scatter_0 手画图例点
    ax.add_collection(LineCollection([[(0.5, 0.1), (0.6, 0.2)], [(0.6, 0.1), (0.7, 0.2)]],
                                     transform=ax.transAxes))             # linecoll_1
    ax.add_collection(PatchCollection([Circle((0.8, 0.8), 0.05)], transform=ax.transAxes))  # collections_2
    ax.scatter([0.4, 0.6], [0.3, 0.5])                                     # scatter_3 数据散点
    ax.hlines([0.2, 0.4], 0, 1)                                            # linecoll_4 数据线组
    bx.imshow(np.arange(16.0).reshape(4, 4), extent=(0.1, 0.4, 0.1, 0.4),
              transform=bx.transAxes)                                      # axes_1.images_0
    bx.imshow(np.arange(4.0).reshape(2, 2), extent=(2, 3, 2, 3))           # axes_1.images_1 数据坐标
    bx.set_xlim(0, 4); bx.set_ylim(0, 4)
    fig.add_artist(LineCollection([[(0.45, 0.1), (0.55, 0.1)]], transform=fig.transFigure))  # fig.artists_0
    ax.scatter([0.1], [0.5], transform=ax.transAxes, s=80, marker="^")      # scatter_5 单点图例点
    ax.add_collection(PolyCollection([[(0.75, 0.3), (0.9, 0.3), (0.9, 0.45)]], array=[1.0],
                                     transform=ax.transAxes))             # fill_6 着色面片
    ax.set_ylabel("y")
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _draw("Plain")
    _draw("Con", layout="constrained")
"""

DELTA = (0.03, 0.02)
TOL = 1e-6
#: constrained 排版的迭代收敛尾巴（同 test_drag_coverage）
CONSTRAINED_TOL = 5e-5
#: manifest 几何点按 5 位小数取整
GEOM_TOL = 2e-5

EXPECT = {
    "axes_0.scatter_0": ("scatter", True),
    "axes_0.linecoll_1": ("linecoll", True),
    "axes_0.collections_2": ("collection", True),
    "axes_0.scatter_3": ("scatter", False),
    "axes_0.linecoll_4": ("linecoll", False),
    "axes_1.images_0": ("image", True),
    "axes_1.images_1": ("image", False),
    "fig.artists_0": ("linecoll", True),
    "axes_0.scatter_5": ("scatter", True),
    "axes_0.fill_6": ("fill", True),
}
DRAGGABLE = [g for g, (_r, d) in EXPECT.items() if d]
STEMS = ("Plain", "Con")


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("axes-layout-collections")
    (d / SCRIPT_NAME).write_text(SCRIPT, encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def hot(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


def _moved(el, delta=DELTA):
    a = el["anchor"]
    return [a[0] + delta[0], a[1] + delta[1]]


def _tol(stem):
    return CONSTRAINED_TOL if stem == "Con" else TOL


def _drawn_extent(el):
    """画出来的几何的外接框 [x0, y0, x1, y1]（figure 分数、y 向下）。

    比外接框不比逐点：曲线（圆）按显示像素展平，挪到别处后中间采样点会换位置，端点不会。"""
    pts = [p for path in el["geometry"]["paths"] for p in path["points"]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _axes_frac(ax_bbox, fx, fy):
    """子图框分数 → figure 分数（top-origin），只用子图自己的 bbox 算，与被测的量框无关。"""
    x, y, w, h = ax_bbox
    return [x + fx * w, y + (1 - fy) * h]


@pytest.mark.parametrize("stem", STEMS)
def test_only_layout_anchored_collections_claim_drag(hot, stem):
    man = _man(hot, stem)
    for gid, (role, draggable) in EXPECT.items():
        assert man[gid]["role"] == role, gid
        assert man[gid]["draggable"] is draggable, gid
        if draggable:
            assert man[gid]["drag_prop"] == "pos_frac", gid
    # 子图框里的图片不铺满子图：几何编辑归它自己；数据坐标的 imshow 照旧代理回宿主子图
    assert "geom_gid" not in man["axes_1.images_0"]
    # 着色的面片（`_is_area_field`）可拖时同样不代理：代理着的话前端先看 resizable，拖走整个子图
    for gid in ("axes_0.fill_6", "axes_0.collections_2"):
        assert "geom_gid" not in man[gid] and not man[gid].get("resizable"), gid
    assert man["axes_1.images_1"].get("geom_gid") == "axes_1"


@pytest.mark.parametrize("stem", STEMS)
def test_initial_positions_match_an_independent_computation(hot, stem):
    """锚点与选中框落在按子图框独立算出来的位置上；子图里的不再是整块子图。"""
    man = _man(hot, stem)
    tol = _tol(stem) + GEOM_TOL
    ax0, ax1 = man["axes_0"]["bbox"], man["axes_1"]["bbox"]
    # 散点：落位 = 两个点的中心，左下角是 (0.2, 0.7)
    assert man["axes_0.scatter_0"]["anchor"] == pytest.approx(_axes_frac(ax0, 0.2, 0.7), abs=tol)
    # 线组：顶点范围左下角 (0.5, 0.1)
    assert man["axes_0.linecoll_1"]["anchor"] == pytest.approx(_axes_frac(ax0, 0.5, 0.1), abs=tol)
    # 位图：extent 的左下角 (0.1, 0.1)，宽高 = 0.3 个子图框
    img = man["axes_1.images_0"]
    assert img["anchor"] == pytest.approx(_axes_frac(ax1, 0.1, 0.1), abs=tol)
    assert img["bbox"][2] == pytest.approx(0.3 * ax1[2], abs=tol)
    # 单点散点：选中框是 marker 那一块（不是零面积退回整块子图），锚点是那个点
    one = man["axes_0.scatter_5"]
    assert one["anchor"] == pytest.approx(_axes_frac(ax0, 0.1, 0.5), abs=tol)
    # 选中框 = marker 的真实大小：s=80 pt² 的三角形边长约 √80 pt，图幅 8×4 in（Codex #2024：
    # 只用整体 transform 量的话是单位路径 ≈ 1 像素）
    side = 80**0.5 / 72
    assert 0.6 * side / 8 < one["bbox"][2] < 1.6 * side / 8, one["bbox"]
    assert 0.6 * side / 4 < one["bbox"][3] < 1.6 * side / 4, one["bbox"]
    for gid in ("axes_0.scatter_0", "axes_0.linecoll_1", "axes_0.collections_2"):
        bb = man[gid]["bbox"]
        assert bb[2] < 0.5 * ax0[2] and bb[3] < 0.5 * ax0[3], f"{gid} 的框还是整块子图: {bb}"


@pytest.mark.parametrize("stem", STEMS)
def test_drag_moves_what_is_drawn_and_undoes(hot, stem):
    """写下的左下角就是量到的；画出来的几何 / 位图框整体走同一个位移；再画不漂；撤销回基线。"""
    tol = _tol(stem)
    base = _man(hot, stem)
    axes = {g: e["bbox"] for g, e in base.items() if e["role"] == "axes"}
    for gid in DRAGGABLE:
        el = base[gid]
        target = _moved(el)
        patch = {"gid": gid, "prop": "pos_frac", "value": target}
        for again in (False, True):
            got = _man(hot, stem, [patch])
            assert got[gid]["anchor"] == pytest.approx(target, abs=tol), f"{gid} again={again}"
            if "geometry" in el:
                want = [v + DELTA[k % 2] for k, v in enumerate(_drawn_extent(el))]
                assert _drawn_extent(got[gid]) == pytest.approx(want, abs=tol + GEOM_TOL), gid
            else:
                b = el["bbox"]
                want = [b[0] + DELTA[0], b[1] + DELTA[1], b[2], b[3]]
                assert got[gid]["bbox"] == pytest.approx(want, abs=tol), gid
            for g, bb in axes.items():
                assert got[g]["bbox"] == pytest.approx(bb, abs=tol), f"{gid} 挤动了 {g}"
            for other in EXPECT:
                if other != gid:
                    assert got[other]["bbox"] == pytest.approx(base[other]["bbox"], abs=tol), (
                        f"{gid} 带动了 {other}"
                    )
        back = _man(hot, stem)
        assert back[gid]["anchor"] == pytest.approx(el["anchor"], abs=1e-9), gid
        assert back[gid]["bbox"] == pytest.approx(el["bbox"], abs=1e-9), gid


def test_dragged_out_of_the_axes_is_still_selectable(hot):
    """拖到子图框外面（图幅边距里）：元素还在元素表里、框是有限值，能再选中拖回来。"""
    base = _man(hot, "Plain")
    for gid in ("axes_0.scatter_0", "axes_0.linecoll_1", "axes_1.images_0"):
        target = [0.01, 0.99]
        got = _man(hot, "Plain", [{"gid": gid, "prop": "pos_frac", "value": target}])
        assert gid in got, gid
        assert got[gid]["anchor"] == pytest.approx(target, abs=TOL), gid
        assert all(abs(v) < 10 for v in got[gid]["bbox"]), gid
        back = _man(hot, "Plain")
        assert back[gid]["bbox"] == pytest.approx(base[gid]["bbox"], abs=1e-9), gid


def test_dragged_collection_keeps_style_edits(hot):
    base = _man(hot, "Plain")
    gid = "axes_0.scatter_0"
    target = _moved(base[gid])
    style = next(f for f in base[gid]["editable"] if f["prop"] in ("facecolor", "color"))
    patches = [
        {"gid": gid, "prop": "pos_frac", "value": target},
        {"gid": gid, "prop": style["prop"], "value": "#0000ff"},
    ]
    got = _man(hot, "Plain", patches)
    fields = {f["prop"]: f["value"] for f in got[gid]["editable"]}
    assert fields[style["prop"]] == "#0000ff"
    assert got[gid]["anchor"] == pytest.approx(target, abs=TOL)
    _man(hot, "Plain")


def test_fresh_replay_matches_the_hot_session(hot, figs):
    """写回事务不变式：热态逐步拖与一次性 worker 全量重放落成同一张图。"""
    plans = {}
    for stem in STEMS:
        base = _man(hot, stem)
        patches = [{"gid": g, "prop": "pos_frac", "value": _moved(base[g])} for g in DRAGGABLE]
        got = None
        for n in range(1, len(patches) + 1):
            got = _man(hot, stem, patches[:n])
        plans[stem] = (patches, got)
        _man(hot, stem)
    fresh = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    try:
        fresh.ensure_built()
        for stem, (patches, got) in plans.items():
            replay = _man(fresh, stem, patches)
            assert replay.keys() == got.keys(), stem
            tol = CONSTRAINED_TOL if stem == "Con" else 1e-9
            for g in got:
                assert replay[g]["bbox"] == pytest.approx(got[g]["bbox"], abs=tol), f"{stem} {g}"
    finally:
        pool.discard(fresh)
