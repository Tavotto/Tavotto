"""SubFigure 里的子图：`axes.position` 按根图幅报、按根图幅落（ADR 0100 修订 2026-11-13）。

修前（matplotlib 3.11.2 实测）：manifest 的 `position` 直接取 `ax.get_position()`——SubFigure 里那是
**子图幅**的分数；前端按根图幅算位移再写回，setter 又按子图幅落，于是 1×2 子图幅里的子图拖 4.6 mm
只走 2.3 mm，右半边那一格的子图连起点都对不上（属性页的 mm 读数也是错的）。

判据的主语：**同一个热 worker** 里写下一条 override 之后，目标子图在 manifest 里的 `bbox`（根图幅的
分数，与其余元素同一口径）。期望值是测试侧独立算的：基线 bbox 加一个写死的位移。另外四条合同：
`position` 与 `bbox` 是同一个矩形（读数口径一致）、再画一次不漂、撤销逐位回基线、一次性 worker
全量重放落在同一处。

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

SCRIPT_NAME = "subfig_axes_position.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _plain(stem, **kw):
    fig = plt.figure(figsize=(6, 3), **kw)
    for k, sf in enumerate(fig.subfigures(1, 2, wspace=0.05)):
        ax = sf.add_subplot()
        ax.plot([0, 1], [k, 1 - k])
        ax.set_title(f"panel {k}")
        sf.set_facecolor("0.95")
    fig.savefig(stem + ".pdf", **({"bbox_inches": "tight"} if stem == "SubT" else {}))
    plt.close(fig)


def _nested(stem):
    fig = plt.figure(figsize=(6, 4))
    top, bottom = fig.subfigures(2, 1)
    top.add_subplot().plot([0, 1])
    for sf in bottom.subfigures(1, 2):
        sf.add_subplot().plot([1, 0])
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _plain("Sub")
    _plain("SubC", layout="constrained")
    _plain("SubT")
    _nested("SubN")
"""

DELTA = (0.03, 0.02)
TOL = 1e-6
#: constrained 排版的迭代收敛尾巴（同 test_drag_coverage）
CONSTRAINED_TOL = 5e-5


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("subfig-pos")
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


def _pos(el):
    return next(f["value"] for f in el["editable"] if f["prop"] == "position")


def _axes(man):
    return {g: e for g, e in man.items() if e["role"] == "axes"}


def _tol(stem):
    return CONSTRAINED_TOL if stem == "SubC" else TOL


def _bbox_of_pos(pos):
    """`position`（根图幅分数，bottom-origin `[x, y, w, h]`）→ manifest `bbox`（`[x, y, w, h]`，
    y 向下、左上角）。"""
    x, y, w, h = pos
    return [x, 1.0 - y - h, w, h]


STEMS = ["Sub", "SubC", "SubT", "SubN"]


@pytest.mark.parametrize("stem", STEMS)
def test_position_reads_in_root_figure_fractions(hot, stem):
    """`position` 与 `bbox` 是同一个矩形：前端拿 `position` 算的位移、对齐、mm 读数都与画面一致。"""
    axes = _axes(_man(hot, stem))
    assert len(axes) >= 2, stem
    for gid, el in axes.items():
        assert _bbox_of_pos(_pos(el)) == pytest.approx(el["bbox"], abs=1e-4), f"{stem} {gid}"


@pytest.mark.parametrize("stem", STEMS)
def test_subfigure_axes_drag_lands_and_undoes(hot, stem):
    """拖子图：写下的位置就是画出来的位置（修前只走一半）；再画一次不漂；别的子图不动；撤销回基线。"""
    tol = _tol(stem)
    base = _man(hot, stem)
    boxes = {g: e["bbox"] for g, e in _axes(base).items()}
    for gid in boxes:
        x, y, w, h = _pos(base[gid])
        target = [x + DELTA[0], y - DELTA[1], w, h]
        bx, by, bw, bh = boxes[gid]
        want = [bx + DELTA[0], by + DELTA[1], bw, bh]
        patch = {"gid": gid, "prop": "position", "value": target}
        for again in (False, True):
            got = _man(hot, stem, [patch])
            assert got[gid]["bbox"] == pytest.approx(want, abs=tol), f"{stem} {gid} again={again}"
            assert _pos(got[gid]) == pytest.approx(target, abs=tol), f"{stem} {gid} again={again}"
            for g, bb in boxes.items():
                if g != gid:
                    assert got[g]["bbox"] == pytest.approx(bb, abs=tol), f"{gid} 挤动了 {g}"
        back = _man(hot, stem)
        for g, bb in boxes.items():
            assert back[g]["bbox"] == pytest.approx(bb, abs=tol), f"{stem} {gid} 撤销 {g}"


def test_fresh_replay_matches_the_hot_session(hot, figs):
    """写回事务不变式：热态逐个拖子图与一次性 worker 全量重放落成同一张图。"""
    plans = {}
    for stem in STEMS:
        base = _man(hot, stem)
        patches = []
        for gid, el in _axes(base).items():
            x, y, w, h = _pos(el)
            patches.append({"gid": gid, "prop": "position", "value": [x + 0.02, y + 0.01, w, h]})
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
            for g in got:
                assert replay[g]["bbox"] == pytest.approx(got[g]["bbox"], abs=_tol(stem)), (
                    f"{stem} {g}"
                )
    finally:
        pool.discard(fresh)
