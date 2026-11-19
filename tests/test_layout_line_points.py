"""位置不是数据的线可以逐段 / 逐点拖（ADR 0100 修订 2026-11-19）。

用户拍板：引线要能「拖动某一段，当成自由线段一样拖」；数据曲线不支持移动。引擎侧：可拖的线
（`transform=ax.transAxes` 的引线、图上的分隔线）在 manifest 里出 `line_points`（每个顶点的 figure
分数、top-origin），前端拖一段 = 那一段两个端点同走一个位移、写 `("line", "points_frac")`（全部顶点）。

判据两个，互相独立：`line_points` 是写下的值；**画出来的几何**（`geometry` 路径点，draw 时另一段
代码量的）必须落在同一处，初始顶点也要等于按子图框独立算出来的位置。合同照旧：再画一次不漂、
撤销逐位回基线、constrained 下成立、全量重放 == 热态；先整条平移（`pos_frac`）再逐段编辑也一样。

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

SCRIPT_NAME = "layout_line_points.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

LEAD = [(0.2, 0.3), (0.5, 0.3), (0.5, 0.8), (0.7, 0.8)]


def _draw(stem, **kw):
    fig, ax = plt.subplots(figsize=(6, 4), **kw)
    ax.plot([0, 1], [0, 1])                                              # lines_0 数据曲线
    lead = Line2D([p[0] for p in LEAD], [p[1] for p in LEAD], transform=ax.transAxes,
                  color="r", lw=1.35, linestyle=(0, (3.0, 1.8)))
    lead.set_path_effects([pe.Stroke(linewidth=2.3, foreground="white"), pe.Normal()])
    ax.add_line(lead)                                                    # lines_1 引线
    t = np.linspace(0.1, 0.9, 100)
    ax.add_line(Line2D(t, 0.1 + 0.05 * np.sin(20 * t), transform=ax.transAxes))  # lines_2 长线
    fig.add_artist(Line2D([0.1, 0.9], [0.04, 0.04], transform=fig.transFigure))  # fig.artists_0
    ax.set_ylabel("y")
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _draw("Plain")
    _draw("Con", layout="constrained")
"""

LEAD = [(0.2, 0.3), (0.5, 0.3), (0.5, 0.8), (0.7, 0.8)]
DELTA = (0.03, 0.02)
TOL = 1e-6
CONSTRAINED_TOL = 5e-5
GEOM_TOL = 2e-5
STEMS = ("Plain", "Con")
LEADER = "axes_0.lines_1"


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("layout-line-points")
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


def _tol(stem):
    return CONSTRAINED_TOL if stem == "Con" else TOL


def _flat(pts):
    return [v for p in pts for v in p]


def _drawn_extent(el):
    pts = [p for path in el["geometry"]["paths"] for p in path["points"]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _segment_moved(pts, i, delta=DELTA):
    """拖第 i 段：顶点 i 与 i+1 同走一个位移，其余不动。"""
    out = [list(p) for p in pts]
    for k in (i, i + 1):
        out[k] = [out[k][0] + delta[0], out[k][1] + delta[1]]
    return out


@pytest.mark.parametrize("stem", STEMS)
def test_only_short_layout_lines_expose_points(hot, stem):
    man = _man(hot, stem)
    assert "line_points" not in man["axes_0.lines_0"]  # 数据曲线：不可拖，不出顶点
    assert man["axes_0.lines_0"]["draggable"] is False
    assert len(man[LEADER]["line_points"]) == 4
    assert len(man["fig.artists_0"]["line_points"]) == 2
    # 100 个顶点的长线：可整条拖，但不出逐点
    assert man["axes_0.lines_2"]["draggable"] is True
    assert "line_points" not in man["axes_0.lines_2"]


@pytest.mark.parametrize("stem", STEMS)
def test_points_sit_where_an_independent_computation_says(hot, stem):
    man = _man(hot, stem)
    x, y, w, h = man["axes_0"]["bbox"]
    want = [[x + px * w, y + (1 - py) * h] for px, py in LEAD]
    assert _flat(man[LEADER]["line_points"]) == pytest.approx(_flat(want), abs=_tol(stem) + 1e-6)


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("seg", [0, 1, 2])
def test_dragging_one_segment_moves_its_two_ends_and_what_is_drawn(hot, stem, seg):
    tol = _tol(stem)
    base = _man(hot, stem)
    pts = base[LEADER]["line_points"]
    target = _segment_moved(pts, seg)
    patch = {"gid": LEADER, "prop": "points_frac", "value": _flat(target)}
    axes_box = base["axes_0"]["bbox"]
    for again in (False, True):
        got = _man(hot, stem, [patch])
        assert _flat(got[LEADER]["line_points"]) == pytest.approx(_flat(target), abs=tol), again
        # 画出来的那条线：外接框 = 新顶点的外接框（描边宽度不变，所以与基线同样的外扩量）
        e0, e1 = _drawn_extent(base[LEADER]), _drawn_extent(got[LEADER])
        p0 = [
            min(p[0] for p in pts),
            min(p[1] for p in pts),
            max(p[0] for p in pts),
            max(p[1] for p in pts),
        ]
        p1 = [
            min(p[0] for p in target),
            min(p[1] for p in target),
            max(p[0] for p in target),
            max(p[1] for p in target),
        ]
        pad0 = [e0[k] - p0[k] for k in range(4)]
        pad1 = [e1[k] - p1[k] for k in range(4)]
        assert pad1 == pytest.approx(pad0, abs=tol + GEOM_TOL), (seg, again)
        assert got["axes_0"]["bbox"] == pytest.approx(axes_box, abs=tol)
    back = _man(hot, stem)
    assert _flat(back[LEADER]["line_points"]) == pytest.approx(_flat(pts), abs=1e-9)
    assert back[LEADER]["bbox"] == pytest.approx(base[LEADER]["bbox"], abs=1e-9)


@pytest.mark.parametrize("stem", STEMS)
def test_whole_line_move_through_points_and_figure_level_line(hot, stem):
    tol = _tol(stem)
    base = _man(hot, stem)
    for gid in (LEADER, "fig.artists_0"):
        pts = base[gid]["line_points"]
        target = [[p[0] + DELTA[0], p[1] + DELTA[1]] for p in pts]
        got = _man(hot, stem, [{"gid": gid, "prop": "points_frac", "value": _flat(target)}])
        assert _flat(got[gid]["line_points"]) == pytest.approx(_flat(target), abs=tol), gid
        a = base[gid]["anchor"]
        assert got[gid]["anchor"] == pytest.approx([a[0] + DELTA[0], a[1] + DELTA[1]], abs=tol), gid
    _man(hot, stem)


def test_wrong_vertex_count_is_rejected_without_moving_anything(hot):
    base = _man(hot, "Plain")
    resp = hot.override(
        "Plain", [{"gid": LEADER, "prop": "points_frac", "value": [0.1, 0.2, 0.3, 0.4]}]
    )
    assert resp.get("warnings"), "顶点个数不对必须报出来"
    got = {e["gid"]: e for e in resp["manifest"]["elements"]}
    assert got[LEADER]["line_points"] == base[LEADER]["line_points"]
    _man(hot, "Plain")


def test_move_then_segment_edit_replays_the_same(hot, figs):
    """先整条平移（`pos_frac`，#2018 的写法，存量文档里可能有）再逐段编辑：热态 == 全量重放。"""
    plans = {}
    for stem in STEMS:
        base = _man(hot, stem)
        a = base[LEADER]["anchor"]
        moved = _man(hot, stem, [{"gid": LEADER, "prop": "pos_frac", "value": [a[0] + 0.02, a[1]]}])
        seg = _segment_moved(moved[LEADER]["line_points"], 1)
        patches = [
            {"gid": LEADER, "prop": "pos_frac", "value": [a[0] + 0.02, a[1]]},
            {"gid": LEADER, "prop": "points_frac", "value": _flat(seg)},
        ]
        got = _man(hot, stem, patches)
        assert _flat(got[LEADER]["line_points"]) == pytest.approx(_flat(seg), abs=_tol(stem))
        plans[stem] = (patches, got)
        _man(hot, stem)
    fresh = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    try:
        fresh.ensure_built()
        for stem, (patches, got) in plans.items():
            replay = _man(fresh, stem, patches)
            tol = CONSTRAINED_TOL if stem == "Con" else 1e-9
            for g in got:
                assert replay[g]["bbox"] == pytest.approx(got[g]["bbox"], abs=tol), f"{stem} {g}"
            assert _flat(replay[LEADER]["line_points"]) == pytest.approx(
                _flat(got[LEADER]["line_points"]), abs=tol
            )
    finally:
        pool.discard(fresh)
