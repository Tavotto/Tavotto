"""子图里位置不是数据的线可拖（ADR 0100 修订 2026-11-18）。

用户来图（世界地图上 30 条震中 → 标签的折线引线）：引线是
`Line2D(..., transform=ax.transAxes)` + `ax.add_line`，带白色描边路径效果。修前 `instrument`
把 `ax.lines` 里的每一条都当数据曲线，`draggable=False`：只能改颜色粗细，拖了只弹「数据系列不能拖」。

判据按 **transform** 而不是所在列表：x、y 都不经过 `transData`、且落在子图框 / 图幅 / 英寸坐标上的线
可拖（与图级的线同一套 `("line", "pos_frac")`）；数据曲线、`axhline` / `axvline`、
`get_yaxis_transform()` 这种一边是数据的、`transData + 平移` 整条是数据的，都仍不拖。

合同与 `test_figure_level_artists` 相同：写下的左下角就是量到的左下角；再画一次不漂；撤销逐位回
基线；子图不被挤动；一次性 worker 全量重放 == 热态；constrained 排版下同样成立。

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

SCRIPT_NAME = "axes_layout_lines.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import matplotlib.transforms as mt
from matplotlib.lines import Line2D


def _draw(stem, **kw):
    fig, ax = plt.subplots(figsize=(6, 4), **kw)
    ax.plot([0, 1], [0, 1])                                              # lines_0 数据
    ax.axhline(0.5)                                                      # lines_1 y 是数据
    ax.axvline(0.5)                                                      # lines_2 x 是数据
    lead = Line2D([0.3, 0.5, 0.5, 0.7], [0.3, 0.3, 0.8, 0.8], transform=ax.transAxes,
                  color="r", lw=1.35, linestyle=(0, (3.0, 1.8)), dash_capstyle="round")
    lead.set_path_effects([pe.Stroke(linewidth=2.3, foreground=(1, 1, 1, 0.8)), pe.Normal()])
    ax.add_line(lead)                                                    # lines_3 引线（用户来图）
    ax.add_line(Line2D([0, 1], [0, 1], transform=ax.transData
                       + mt.ScaledTranslation(0.1, 0, fig.dpi_scale_trans)))  # lines_4 数据 + 平移
    ax.add_line(Line2D([0.2, 0.4], [0.9, 0.9], transform=fig.transFigure))   # lines_5 图幅坐标
    ax.add_line(Line2D([0, 1], [0.3, 0.3], transform=ax.get_yaxis_transform()))  # lines_6 x 是数据
    ax.add_line(Line2D([0.1, 0.1], [0.1, 0.4], transform=ax.transAxes
                       + mt.ScaledTranslation(0.05, 0, fig.dpi_scale_trans)))  # lines_7 子图框 + 英寸
    ax.text(0.7, 0.8, "Label", transform=ax.transAxes)
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

EXPECT = {
    "axes_0.lines_0": False,
    "axes_0.lines_1": False,
    "axes_0.lines_2": False,
    "axes_0.lines_3": True,
    "axes_0.lines_4": False,
    "axes_0.lines_5": True,
    "axes_0.lines_6": False,
    "axes_0.lines_7": True,
}
DRAGGABLE = [g for g, d in EXPECT.items() if d]
STEMS = ("Plain", "Con")


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("axes-layout-lines")
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


def _moved(el):
    a = el["anchor"]
    return [a[0] + DELTA[0], a[1] + DELTA[1]]


def _tol(stem):
    return CONSTRAINED_TOL if stem == "Con" else TOL


@pytest.mark.parametrize("stem", STEMS)
def test_only_layout_anchored_lines_claim_drag(hot, stem):
    """按 transform 判：数据 / 一边数据 / 数据 + 平移不拖；子图框、图幅、子图框 + 英寸可拖。"""
    man = _man(hot, stem)
    for gid, draggable in EXPECT.items():
        assert man[gid]["role"] == "line", gid
        assert man[gid]["draggable"] is draggable, gid
        if draggable:
            assert man[gid]["drag_prop"] == "pos_frac", gid
            assert man[gid].get("anchor"), gid
    # 样式字段照旧：可拖不拿走颜色 / 线宽 / 线型
    props = {f["prop"] for f in man["axes_0.lines_3"]["editable"]}
    assert {"color", "linewidth", "linestyle"} <= props


@pytest.mark.parametrize("stem", STEMS)
def test_layout_line_drag_lands_and_undoes(hot, stem):
    """写下的左下角就是量到的左下角；再画一次不漂；撤销逐位回基线；子图不被挤动。"""
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
            for g, bb in axes.items():
                assert got[g]["bbox"] == pytest.approx(bb, abs=tol), f"{gid} 挤动了 {g}"
            # 别的线不跟着走
            for other in EXPECT:
                if other != gid:
                    assert got[other]["bbox"] == pytest.approx(base[other]["bbox"], abs=tol), (
                        f"{gid} 带动了 {other}"
                    )
        back = _man(hot, stem)
        assert back[gid]["anchor"] == pytest.approx(el["anchor"], abs=1e-9), gid
        assert back[gid]["bbox"] == pytest.approx(el["bbox"], abs=1e-9), gid


def test_dragged_line_keeps_style_edits(hot):
    """拖过的引线再改颜色 / 线宽：两条 override 互不撤销。"""
    base = _man(hot, "Plain")
    gid = "axes_0.lines_3"
    target = _moved(base[gid])
    patches = [
        {"gid": gid, "prop": "pos_frac", "value": target},
        {"gid": gid, "prop": "color", "value": "#0000ff"},
        {"gid": gid, "prop": "linewidth", "value": 3.0},
    ]
    got = _man(hot, "Plain", patches)
    fields = {f["prop"]: f["value"] for f in got[gid]["editable"]}
    assert fields["color"] == "#0000ff"
    assert fields["linewidth"] == pytest.approx(3.0)
    assert got[gid]["anchor"] == pytest.approx(target, abs=TOL)
    _man(hot, "Plain")


def test_fresh_replay_matches_the_hot_session(hot, figs):
    """写回事务不变式：热态逐步拖与一次性 worker 全量重放落成同一张图。"""
    plans = {}
    for stem in STEMS:
        base = _man(hot, stem)
        patches = [{"gid": g, "prop": "pos_frac", "value": _moved(base[g])} for g in DRAGGABLE]
        patches.append({"gid": "axes_0.lines_3", "prop": "color", "value": "#00ff00"})
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
