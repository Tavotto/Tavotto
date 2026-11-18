"""`ax.table` 可拖（ADR 0100 修订 2026-11-18，接集合与位图）。

修前：表格登记成 `artist`，只开 visible / zorder，拖了只弹「暂不支持」（ADR 0100 结论表里「未修」那一行）。

表格每次 draw 按 `loc` / `bbox` 在子图框坐标里重排格子，格子与字都画在表格自己的 transform 上；
拖动把平移叠在这条 transform 上（与位置不是数据的集合 / 位图同一套）。

判据：显式 `bbox` 的表格初始框 == 按子图框独立算出来的矩形；拖动后框整体走同一个位移；再画不漂；
撤销逐位回基线；不挤动子图（`loc="bottom"` 的表格参与 constrained 排版：拖它不许把子图挤开）；
全量重放 == 热态。

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

SCRIPT_NAME = "axes_tables.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _draw(stem, **kw):
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(8, 4), **kw)
    ax.plot([0, 1], [0, 1])
    ax.table(cellText=[["a", "b"], ["c", "d"]], bbox=[0.1, 0.6, 0.3, 0.2])   # tables_0 显式框
    bx.plot([0, 1], [1, 0])
    bx.table(cellText=[["1", "2", "3"]], loc="bottom")                       # axes_1.tables_0
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _draw("Plain")
    _draw("Con", layout="constrained")
"""

DELTA = (0.03, 0.02)
TOL = 1e-6
CONSTRAINED_TOL = 5e-5
TABLES = ["axes_0.tables_0", "axes_1.tables_0"]
STEMS = ("Plain", "Con")


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("axes-tables")
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
def test_tables_claim_drag(hot, stem):
    man = _man(hot, stem)
    for gid in TABLES:
        assert man[gid]["role"] == "artist", gid
        assert man[gid]["draggable"] is True, gid
        assert man[gid]["drag_prop"] == "pos_frac", gid


@pytest.mark.parametrize("stem", STEMS)
def test_explicit_bbox_table_sits_where_an_independent_computation_says(hot, stem):
    man = _man(hot, stem)
    x, y, w, h = man["axes_0"]["bbox"]
    want = [x + 0.1 * w, y + (1 - 0.8) * h, 0.3 * w, 0.2 * h]  # top-origin
    assert man["axes_0.tables_0"]["bbox"] == pytest.approx(want, abs=_tol(stem) + 1e-4)


@pytest.mark.parametrize("stem", STEMS)
def test_table_drag_lands_and_undoes(hot, stem):
    tol = _tol(stem)
    base = _man(hot, stem)
    axes = {g: e["bbox"] for g, e in base.items() if e["role"] == "axes"}
    for gid in TABLES:
        el = base[gid]
        target = _moved(el)
        patch = {"gid": gid, "prop": "pos_frac", "value": target}
        for again in (False, True):
            got = _man(hot, stem, [patch])
            assert got[gid]["anchor"] == pytest.approx(target, abs=tol), f"{gid} again={again}"
            b = el["bbox"]
            want = [b[0] + DELTA[0], b[1] + DELTA[1], b[2], b[3]]
            assert got[gid]["bbox"] == pytest.approx(want, abs=tol), gid
            for g, bb in axes.items():
                assert got[g]["bbox"] == pytest.approx(bb, abs=tol), f"{gid} 挤动了 {g}"
            other = next(t for t in TABLES if t != gid)
            assert got[other]["bbox"] == pytest.approx(base[other]["bbox"], abs=tol), other
        back = _man(hot, stem)
        assert back[gid]["anchor"] == pytest.approx(el["anchor"], abs=1e-9), gid
        assert back[gid]["bbox"] == pytest.approx(el["bbox"], abs=1e-9), gid


def test_fresh_replay_matches_the_hot_session(hot, figs):
    plans = {}
    for stem in STEMS:
        base = _man(hot, stem)
        patches = [{"gid": g, "prop": "pos_frac", "value": _moved(base[g])} for g in TABLES]
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
