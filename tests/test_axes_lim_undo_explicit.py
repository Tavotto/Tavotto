"""撤销坐标范围改动时，回到脚本写下的**那个范围**，不是重新自动缩放出来的另一个（2026-11-19）。

CompatBench 的「拖动」阶段（#2057）在夜跑里抓到：`sci_sns_bar` / `sci_sns_violin` 改一次 xlim 再撤销，柱 / 小提琴
整体变宽，元素表回不到基线。seaborn 的分类轴写的是 `ax.set_xlim(-.5, n - .5, auto=None)`——范围显式、自动缩放
的开关却开着；从前范围的「原样」只记了「自动缩放开着」，撤销时按 matplotlib 默认边距重新自动缩放。现在原样连
当时的范围一起记（`overrides._AutoscaleAt`），撤销放回范围、再打开自动缩放。

这里不用 seaborn（corpus 里那两个 case 需要它）：同样的写法 `set_xlim(..., auto=None)` 直接写在脚本里。

合同：改 xlim / ylim 再撤销 → 元素表与基线逐位相同、== 全新 worker 重放；自动缩放仍然开着
（`_AUTOSCALE` 当初的理由：撤销之后再换 log 轴要照常缩放——那条序列热态 == 重放）。

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

SCRIPT_NAME = "lim_undo.py"
SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    # seaborn 分类轴的写法：显式范围、auto=None（自动缩放开关不动，仍是开着的）
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar([0, 1, 2], [3, 5, 4], width=0.8)
    ax.set_xlim(-0.5, 2.5, auto=None)
    fig.savefig("Cat.pdf")

    # 小提琴式的填充（PolyCollection），同样的轴写法
    fig, ax = plt.subplots(figsize=(4, 3))
    y = np.linspace(-2, 2, 50)
    w = 0.4 * np.exp(-y ** 2)
    for k in range(3):
        ax.fill_betweenx(y, k - w, k + w, alpha=0.6)
    ax.set_xlim(-0.5, 2.5, auto=None)
    fig.savefig("Vio.pdf")

    # 对照：真自动缩放的轴（没写过范围）
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([1, 2, 3], [2, 50, 104])
    fig.savefig("Auto.pdf")

    # An empty fixed locator on a shared axis and a configured log scale,
    # both authored in the script before the worker applies overrides.
    fig, (upper, lower) = plt.subplots(2, sharex=True, figsize=(4, 4))
    upper.plot([1, 2, 5], [-6, 3, 16])
    lower.plot([1, 3, 5], [2, 3, 5])
    lower.set_xticks([])
    upper.set_yscale("symlog", linthresh=3.5, base=2)
    upper.set_ylim(-8, 20)
    upper.set_yticks([-8, -4, 0, 4, 16], labels=["-8", "-4", "zero", "four", "sixteen"])
    fig.savefig("Authored.pdf")
"""

STEMS = ("Cat", "Vio", "Auto")


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("lim-undo")
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


def _lim(el, prop):
    return next(f["value"] for f in el["editable"] if f["prop"] == prop)


def _boxes(man):
    return {g: e.get("bbox") for g, e in man.items()}


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("prop", ["xlim", "ylim"])
def test_undoing_a_limit_change_restores_the_scripts_own_limits(hot, stem, prop):
    base = _man(hot, stem)
    lo, hi = _lim(base["axes_0"], prop)
    d = (hi - lo) * 0.25
    _man(hot, stem, [{"gid": "axes_0", "prop": prop, "value": [lo + d, hi + d]}])
    back = _man(hot, stem)
    assert _lim(back["axes_0"], prop) == pytest.approx([lo, hi], abs=1e-12)
    for g, bb in _boxes(base).items():
        assert back[g].get("bbox") == pytest.approx(bb, abs=1e-9), f"{stem} {prop} {g}"


@pytest.mark.parametrize("stem", ["Cat", "Auto"])
def test_limit_then_undo_then_log_scale_equals_fresh_replay(hot, figs, stem):
    """`_AUTOSCALE` 当初的那条序列：ylim → 撤销 → yscale=log。撤销后自动缩放仍开着，换 log 轴照常缩放，
    热态 == 只有 yscale 的全新重放。"""
    base = _man(hot, stem)
    lo, hi = _lim(base["axes_0"], "ylim")
    _man(hot, stem, [{"gid": "axes_0", "prop": "ylim", "value": [lo + 1, hi + 1]}])
    log = [{"gid": "axes_0", "prop": "yscale", "value": "log"}]
    got = _man(hot, stem, log)
    fresh = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    try:
        fresh.ensure_built()
        want = _man(fresh, stem, log)
    finally:
        pool.discard(fresh)
    assert _lim(got["axes_0"], "ylim") == pytest.approx(_lim(want["axes_0"], "ylim"), rel=1e-9)
    for g, bb in _boxes(want).items():
        assert got[g].get("bbox") == pytest.approx(bb, abs=1e-9), f"{stem} {g}"
    _man(hot, stem)


@pytest.mark.parametrize("stem", ["Cat", "Vio"])
def test_undo_keeps_an_inversion_that_is_still_applied(hot, stem):
    """撤掉 xlim、invert_x 还在：放回的范围按翻转的方向排，不把翻转抹掉。"""
    base = _man(hot, stem)
    lo, hi = _lim(base["axes_0"], "xlim")
    inv = {"gid": "axes_0", "prop": "invert_x", "value": True}
    _man(hot, stem, [inv, {"gid": "axes_0", "prop": "xlim", "value": [lo + 0.3, hi + 0.3]}])
    back = _man(hot, stem, [inv])
    got = _lim(back["axes_0"], "xlim")
    assert got[0] > got[1], "翻转还在"
    assert sorted(got) == pytest.approx(sorted([lo, hi]), abs=1e-12)
    _man(hot, stem)


def test_scale_undo_restores_authored_tickers_and_scale_configuration(hot, figs):
    stem = "Authored"
    baseline = _man(hot, stem)
    starting = hot.preview_png(stem, [], 480, "scale-original").read_bytes()
    assert not any(g.endswith("xticks") for g in baseline)
    horizontal = {"gid": "axes_0", "prop": "xscale", "value": "log"}
    vertical = {"gid": "axes_0", "prop": "yscale", "value": "linear"}
    _man(hot, stem, [horizontal, vertical])
    assert (
        hot.preview_png(stem, [horizontal, vertical], 480, "scale-edited").read_bytes() != starting
    )
    undone = _man(hot, stem)
    assert _boxes(undone) == _boxes(baseline)
    assert not any(g.endswith("xticks") for g in undone)
    assert hot.preview_png(stem, [], 480, "scale-undone").read_bytes() == starting

    sibling = {"gid": "axes_1", "prop": "xscale", "value": "symlog"}
    _man(hot, stem, [horizontal, sibling])
    after = _man(hot, stem, [sibling])
    fresh = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    try:
        want = _man(fresh, stem, [sibling])
        assert _lim(after["axes_0"], "xscale") == "symlog"
        assert _boxes(after) == _boxes(want)
        assert (
            hot.preview_png(stem, [sibling], 480, "sibling").read_bytes()
            == fresh.preview_png(stem, [sibling], 480, "sibling").read_bytes()
        )
    finally:
        pool.discard(fresh)
    _man(hot, stem)
    assert hot.preview_png(stem, [], 480, "scale-final").read_bytes() == starting
