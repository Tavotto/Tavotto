"""整张图 / SubFigure 上直接挂的元素：登记、拖动、撤销、全量重放（ADR 0100「不做」那一行的补完）。

修前（matplotlib 3.11.2 实测）：`instrument` 在整张图这一层只登记 `fig.texts`、`fig.legends` 与
`fig.artists` 里的锚定框。于是

* `fig.add_artist(Polygon(..., transform=fig.transFigure))`——跨子图的连接梯形（用户来图：B 的高亮窗口
  连到 D 的帧序列），以及同样画法的矩形、分隔线 `Line2D`、`ConnectionPatch`、`fig.add_artist(Text)`、
  图级 `LineCollection`、`fig.figimage` 的位图——**全部点不中**；
* SubFigure 里的 suptitle / 文字 / 图例 / 形状同样一个都没登记，普查只报一行「SubFigure × 2」；
* 子图里的 `ConnectionPatch` 按独立箭头出 `arrow_endpoints`，端点取的是构造时的占位 (0,0)→(1,1)：
  手柄画在图幅对角，拖了一个像素不动。

判据的主语：**同一个热 worker** 里写下一条 override 之后那份 manifest 中目标元素的 `anchor`
（左下角，figure 分数、y 向下）。期望值是测试侧独立算的：基线加一个写死的位移。另外三条合同：
同一份列表再渲染一次不漂、撤销（空列表）逐位回到基线、一次性 worker 全量重放落在同一处。

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

SCRIPT_NAME = "fig_level_artists.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import ConnectionPatch, FancyArrowPatch, Polygon, Rectangle
from matplotlib.text import Text


def _root(stem, **kw):
    fig = plt.figure(figsize=(6, 4), **kw)
    a1 = fig.add_subplot(121)
    a2 = fig.add_subplot(122)
    a1.plot([0, 1], [0, 1])
    a2.plot([0, 1], [1, 0])
    tf = fig.transFigure
    fig.add_artist(Polygon([(0.1, 0.1), (0.3, 0.1), (0.2, 0.3)], transform=tf,
                           facecolor="orange", alpha=0.3, zorder=0))      # artists_0
    fig.add_artist(Line2D([0.1, 0.9], [0.5, 0.5], transform=tf, color="r"))  # artists_1
    fig.add_artist(Text(0.5, 0.05, "figure note", transform=tf))          # artists_2
    fig.add_artist(LineCollection([[(0.1, 0.92), (0.3, 0.92)]], transform=tf))  # artists_3
    fig.add_artist(FancyArrowPatch((0.6, 0.9), (0.8, 0.9), transform=tf,
                                   arrowstyle="->"))                      # artists_4
    fig.add_artist(ConnectionPatch((0.5, 0.5), (0.5, 0.5), "data", "data",
                                   axesA=a1, axesB=a2))                   # artists_5
    fig.patches.append(Rectangle((0.8, 0.1), 0.05, 0.05, transform=tf))  # patches_0
    fig.figimage(np.linspace(0, 1, 100).reshape(10, 10), 10, 10)          # images_0
    a1.add_patch(ConnectionPatch((0.2, 0.2), (0.8, 0.8), "data", "data",
                                 axesA=a1, axesB=a2, color="g"))          # axes_0.arrows_0
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def _subfigs(stem):
    fig = plt.figure(figsize=(6, 3))
    for k, sf in enumerate(fig.subfigures(1, 2)):
        ax = sf.add_subplot()
        ax.plot([0, 1], [k, 1 - k], label="line")
        sf.suptitle(f"panel {k}")                                          # subfigs_k.texts_0
        sf.legend()                                                         # subfigs_k.legend_0
        sf.add_artist(Rectangle((0.05, 0.05), 0.2, 0.2, transform=sf.transSubfigure,
                                facecolor="0.8"))                          # subfigs_k.artists_0
        sf.set_facecolor("0.95")
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def _nested(stem):
    fig = plt.figure(figsize=(6, 4))
    outer = fig.subfigures(1, 1)
    inner = outer.subfigures(1, 2)
    lines = [sf.add_subplot().plot([0, 1], [k, 1 - k], label=f"s{k}")[0]
             for k, sf in enumerate(inner)]
    outer.legend(handles=lines)                                          # subfigs_0.legend_0
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _nested("Nest")
    _root("Root")
    _root("RootC", layout="constrained")
    _subfigs("Sub")
"""

DELTA = (0.03, 0.02)
TOL = 1e-6
#: constrained 排版的迭代收敛尾巴（同 test_drag_coverage）
CONSTRAINED_TOL = 5e-5

DRAGGABLE = {
    "Root": ["fig.artists_0", "fig.artists_1", "fig.artists_2", "fig.patches_0"],
    "RootC": ["fig.artists_0", "fig.artists_1", "fig.artists_2", "fig.patches_0"],
    "Sub": ["fig.subfigs_0.texts_0", "fig.subfigs_1.artists_0", "fig.subfigs_0.legend_0"],
}


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("fig-level")
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


def _resp(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp


def _man(worker, stem, patches=()):
    return {e["gid"]: e for e in _resp(worker, stem, patches)["manifest"]["elements"]}


def _unsupported(worker, stem):
    return _resp(worker, stem)["manifest"].get("unsupported") or []


def _field(el, prop):
    return next(f["value"] for f in el["editable"] if f["prop"] == prop)


def _moved(el):
    a = el["anchor"]
    return [a[0] + DELTA[0], a[1] + DELTA[1]]


def test_root_figure_artists_are_registered(hot):
    """`fig.add_artist` / `fig.patches` / `fig.figimage` 的每一件都进元素表、角色与子图里的同族一致，
    普查不再报整张图这一层的漏网。"""
    man = _man(hot, "Root")
    expect = {
        "fig.artists_0": ("patch", True),
        "fig.artists_1": ("line", True),
        "fig.artists_2": ("text", True),
        "fig.artists_3": ("linecoll", False),
        "fig.artists_4": ("arrow_patch", False),
        "fig.artists_5": ("arrow_patch", False),
        "fig.patches_0": ("patch", True),
        "fig.images_0": ("image", False),
    }
    for gid, (role, draggable) in expect.items():
        assert gid in man, gid
        assert man[gid]["role"] == role, (gid, man[gid]["role"])
        assert man[gid]["draggable"] is draggable, gid
        if draggable:
            assert man[gid]["drag_prop"] == "pos_frac", gid
    # 图级独立箭头照旧出端点（独立箭头那一套），落在写下的 transFigure 坐标上
    ends = [v for pt in man["fig.artists_4"]["arrow_endpoints"] for v in pt]
    assert ends == pytest.approx([0.6, 0.1, 0.8, 0.1], abs=1e-3)
    assert not [r for r in _unsupported(hot, "Root") if r["where"].startswith("fig")]


def test_connection_patch_never_claims_endpoints(hot):
    """`ConnectionPatch` 的两端由 xyA / xyB 决定，不出端点（端点手柄拖了画面不动）；出描边几何，选得中。"""
    man = _man(hot, "Root")
    for gid in ("fig.artists_5", "axes_0.arrows_0"):
        assert man[gid]["role"] == "arrow_patch", gid
        assert "arrow_endpoints" not in man[gid], gid
        assert "geometry" in man[gid], gid


def test_subfigure_contents_are_registered(hot):
    man = _man(hot, "Sub")
    for k in (0, 1):
        p = f"fig.subfigs_{k}"
        assert man[f"{p}.texts_0"]["role"] == "text"
        assert man[f"{p}.legend_0"]["role"] == "legend"
        assert man[f"{p}.artists_0"]["role"] == "patch"
    # SubFigure 本身与它的背景是结构件，不是漏网
    assert _unsupported(hot, "Sub") == []


@pytest.mark.parametrize("stem", list(DRAGGABLE))
def test_figure_level_drag_lands_and_undoes(hot, stem):
    """宣称可拖的图级元素：写下的左下角就是量到的左下角；再画一次不漂；撤销逐位回基线；子图不动。"""
    tol = CONSTRAINED_TOL if stem == "RootC" else TOL
    base = _man(hot, stem)
    axes = {g: e["bbox"] for g, e in base.items() if e["role"] == "axes"}
    for gid in DRAGGABLE[stem]:
        el = base[gid]
        prop = el["drag_prop"]
        target = _moved(el)
        patch = {"gid": gid, "prop": prop, "value": target}
        for again in (False, True):
            got = _man(hot, stem, [patch])
            assert got[gid]["anchor"] == pytest.approx(target, abs=tol), f"{gid} again={again}"
            for g, bb in axes.items():
                assert got[g]["bbox"] == pytest.approx(bb, abs=tol), f"{gid} 挤动了 {g}"
        back = _man(hot, stem)
        assert back[gid]["anchor"] == pytest.approx(el["anchor"], abs=1e-9), gid
        assert back[gid]["bbox"] == pytest.approx(el["bbox"], abs=1e-9), gid


def test_figure_level_style_edits_apply_and_undo(hot):
    base = _man(hot, "Root")
    edits = [
        ("fig.artists_0", "facecolor", "#0000ff"),
        ("fig.artists_0", "zorder", 5),
        ("fig.artists_1", "color", "#00ff00"),
        ("fig.artists_2", "text", "changed"),
        ("fig.artists_3", "color", "#00ff00"),
        ("fig.images_0", "visible", False),
    ]
    for gid, prop, value in edits:
        got = _man(hot, "Root", [{"gid": gid, "prop": prop, "value": value}])
        assert _field(got[gid], prop) == value, (gid, prop)
        back = _man(hot, "Root")
        assert _field(back[gid], prop) == _field(base[gid], prop), (gid, prop)


def test_fresh_replay_matches_the_hot_session(hot, figs):
    """写回事务不变式：热态逐步拖 / 改样式与一次性 worker 全量重放落成同一张图。"""
    plans = {}
    for stem, gids in DRAGGABLE.items():
        base = _man(hot, stem)
        patches = [{"gid": g, "prop": base[g]["drag_prop"], "value": _moved(base[g])} for g in gids]
        if stem.startswith("Root"):
            patches.append({"gid": "fig.artists_0", "prop": "zorder", "value": 5})
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
            tol = CONSTRAINED_TOL if stem == "RootC" else 1e-9
            for g in got:
                assert replay[g]["bbox"] == pytest.approx(got[g]["bbox"], abs=tol), f"{stem} {g}"
    finally:
        pool.discard(fresh)


def test_subfigure_legend_entries_follow_their_source(hot):
    """SubFigure 的图例项与子图里的一样跟随源对象：改源的颜色示意线跟着变；撤掉示意线的改动就
    立刻派生回源此刻的样子。修前两处漏：并排 SubFigure 各有一条同样长相的线，按整张图找源并列、
    一条都绑不上；`sync_legends` 不走 SubFigure（#1887 Codex 评审）。"""
    entry = "fig.subfigs_0.legend_0.texts_0"
    man = _man(hot, "Sub")
    src = man[entry]["legend_entry"]["source_gid"]
    assert _field(man[entry], "binding") == "follow_source"
    recolor = {"gid": src, "prop": "color", "value": "#00ff00"}
    got = _man(hot, "Sub", [recolor])
    assert _field(got[entry], "handle_color") == "#00ff00"
    custom = {"gid": entry, "prop": "handle_color", "value": "#123456"}
    got = _man(hot, "Sub", [recolor, custom])
    assert _field(got[entry], "handle_color") == "#123456", "改过示意线 = 脱开"
    # 撤掉示意线那条 = 回到跟随，立刻派生成源此刻的样子
    got = _man(hot, "Sub", [recolor])
    assert _field(got[entry], "binding") == "follow_source"
    assert _field(got[entry], "handle_color") == "#00ff00"
    follow = {"gid": entry, "prop": "binding", "value": "follow_source"}
    got = _man(hot, "Sub", [follow, recolor])
    assert _field(got[entry], "handle_color") == "#00ff00"
    back = _man(hot, "Sub")
    assert _field(back[entry], "handle_color") == _field(man[entry], "handle_color")


def test_outer_subfigure_legend_binds_sources_in_nested_subfigures(hot):
    """外层 SubFigure 的图例引用内层 SubFigure 里的曲线：照样绑上源、跟着源变（#1887 评审：
    `SubFigure.figure` 在 3.10+ 指向根，沿它走链会跳过外层，源被排除在候选之外）。"""
    man = _man(hot, "Nest")
    for j in (0, 1):
        entry = f"fig.subfigs_0.legend_0.texts_{j}"
        src = man[entry]["legend_entry"]["source_gid"]
        got = _man(hot, "Nest", [{"gid": src, "prop": "color", "value": "#00ff00"}])
        assert _field(got[entry], "handle_color") == "#00ff00", entry
    _man(hot, "Nest")
