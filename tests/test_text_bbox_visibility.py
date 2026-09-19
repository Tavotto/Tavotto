"""文字背景框的显隐只由 `bbox_visible` 决定，其它 bbox_* 只改样式（#412）。

语义：`bbox_visible = true` 显示；显式 `false` 或不在列表里 = 脚本原样（脚本自己
`set_bbox` 过就显示，没有就不显示）。样式那五条落下时框要是还没有，现建一个**不可见**
的、把样式写进去等开关来开——它们永远不改显隐。第一版是「首次改任何背景属性即出现
背景框」，于是同一份列表两条路两张图：热态里撤掉 / 关掉开关时其它样式值没变被跳过、
框留在隐藏，清空重放时样式的 setter 又把框建出来并露出来。

判据两把尺子：manifest 的 `bbox_visible` 取值（检查器显示什么），像素（用户看见什么）。
「热态 == FRESH」用一条全新 worker 只见最终列表来量——两条路必须一张图。
本进程不 import matplotlib：一切经 `pool.one_shot()` 起的 worker。
"""

from __future__ import annotations

import hashlib

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_bbox.py"
ENTRY = "main"
STEM = "BoxText"
#: 两段文字：`texts_0` 脚本自己带框（黄底黑边），`texts_1` 没有框。
LIBRARY = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2], [0, 1, 0.5])
    ax.text(0.3, 0.8, "boxed", bbox=dict(boxstyle="square,pad=0.3", facecolor="#ffee88", edgecolor="black"))
    ax.text(1.3, 0.2, "plain")
    fig.savefig("BoxText.png")
"""

BOXED, PLAIN = "axes_0.texts_0", "axes_0.texts_1"


def _p(gid, prop, value):
    return {"gid": gid, "prop": prop, "value": value}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("bbox-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


@pytest.fixture
def worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    yield w
    pool.discard(w)


def _apply(w, patches):
    resp = w.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _png(w, patches, tag):
    return hashlib.sha1(w.preview_png(STEM, list(patches), 360, tag).read_bytes()).hexdigest()


def _field(man, gid, prop):
    el = next(e for e in man["elements"] if e["gid"] == gid)
    return next(f for f in el["editable"] if f["prop"] == prop)["value"]


def _fresh_pair(library, patches, tag):
    """只见过 `patches` 的全新 worker：(manifest, 像素)。"""
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    try:
        return _apply(w, patches), _png(w, patches, tag)
    finally:
        pool.discard(w)


def test_fixture_has_one_boxed_and_one_plain_text(worker):
    man = _apply(worker, [])
    assert _field(man, BOXED, "bbox_visible") is True
    assert _field(man, PLAIN, "bbox_visible") is False


def test_a_style_override_alone_does_not_show_a_box(worker):
    """没有框的文字只改样式：检查器仍报「无框」，画面一个像素不变——框现建了但不可见。"""
    base_png = _png(worker, [], "base")
    styled = [_p(PLAIN, "bbox_facecolor", "#ff00ff"), _p(PLAIN, "bbox_linewidth", 2.0)]
    man = _apply(worker, styled)
    assert _field(man, PLAIN, "bbox_visible") is False
    assert _field(man, PLAIN, "bbox_facecolor").lower() == "#ff00ff"  # 样式写进去了，等开关
    assert _png(worker, styled, "styled") == base_png


def test_the_switch_shows_the_box_and_the_style_is_already_there(worker):
    base_png = _png(worker, [], "base")
    on = [_p(PLAIN, "bbox_visible", True), _p(PLAIN, "bbox_facecolor", "#ff00ff")]
    man = _apply(worker, on)
    assert _field(man, PLAIN, "bbox_visible") is True
    assert _png(worker, on, "on") != base_png


@pytest.mark.parametrize(
    "case_id,steps",
    [
        (
            "A1-switch-removed",
            [
                [_p(PLAIN, "bbox_visible", True), _p(PLAIN, "bbox_edgecolor", "#ff00ff")],
                [_p(PLAIN, "bbox_edgecolor", "#ff00ff")],
            ],
        ),
        (
            "A2-switch-toggled-off",
            [
                [_p(PLAIN, "bbox_visible", True), _p(PLAIN, "bbox_linewidth", 2.0)],
                [_p(PLAIN, "bbox_visible", False), _p(PLAIN, "bbox_linewidth", 2.0)],
            ],
        ),
    ],
)
def test_turning_the_switch_off_wins_over_remaining_style(worker, library, case_id, steps):
    """#412 的两条最小复现：热态走两步，末态与只见末步的全新 worker **manifest + 像素**
    都相等，且框是藏着的——其它样式不许在另一条执行历史里把它复活。"""
    for step in steps:
        hot_man = _apply(worker, step)
    hot_png = _png(worker, steps[-1], f"{case_id}-hot")
    fresh_man, fresh_png = _fresh_pair(library, steps[-1], f"{case_id}-fresh")
    assert _field(hot_man, PLAIN, "bbox_visible") is False, case_id
    assert hot_man == fresh_man, case_id
    assert hot_png == fresh_png, f"{case_id}：manifest 一样但画出来不一样"


def test_a_script_made_box_stays_visible_under_style_edits_and_hides_on_false(worker):
    """脚本自己带框的文字：改样式不碰显隐（框还在、颜色变了）；显式 false 才藏。"""
    base_png = _png(worker, [], "base")
    styled = [_p(BOXED, "bbox_facecolor", "#00ff00")]
    man = _apply(worker, styled)
    assert _field(man, BOXED, "bbox_visible") is True
    assert _field(man, BOXED, "bbox_facecolor").lower() == "#00ff00"
    assert _png(worker, styled, "styled") != base_png
    off = [_p(BOXED, "bbox_visible", False)]
    man = _apply(worker, off)
    assert _field(man, BOXED, "bbox_visible") is False
    assert _png(worker, off, "off") != base_png
    # 撤掉一切：脚本原样（黄底黑边，框可见），像素回到基线
    man = _apply(worker, [])
    assert _field(man, BOXED, "bbox_visible") is True
    assert _field(man, BOXED, "bbox_facecolor").lower() == "#ffee88"
    assert _png(worker, [], "back") == base_png


def test_undo_of_everything_removes_the_box_we_created(worker):
    """开关 + 样式都撤掉：现建的框整个摘掉，回到「没有框」（不是「有一个不可见的框」）——
    检查器读到的是 BBOX_DEFAULTS，像素回到基线。"""
    base_png = _png(worker, [], "base")
    base = _apply(worker, [])
    _apply(worker, [_p(PLAIN, "bbox_visible", True), _p(PLAIN, "bbox_facecolor", "#ff00ff")])
    back = _apply(worker, [])
    assert _field(back, PLAIN, "bbox_visible") is False
    assert _field(back, PLAIN, "bbox_facecolor") == _field(base, PLAIN, "bbox_facecolor")
    assert _png(worker, [], "back") == base_png
