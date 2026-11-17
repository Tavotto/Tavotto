"""撤销之后热会话 == 全量重放：刻度文字显隐（按侧）与箭头颜色（边色 + 填充）。

两个用户报告的症状，根因都是「getter 记的原样比 setter 写的少」：

1. **刻度文字显隐**：旧 setter 只写 `labelbottom` / `labelleft`，getter 读的是「画着的第一条」。
   色条、twinx 的文字在**右边**——隐藏是空操作，撤销时 `setter(True)` 反倒把左边那排打开，
   之后再改刻度，界面里多出一排刻度文字，重新跑一遍又没有。
2. **箭头颜色**：getter 只记边色，撤销走 `set_color(边色)` 把填充一起盖掉（`fc≠ec` 的箭头
   撤销后帽子变成边色）。

量法：同一个热 worker 走完整条序列，每一步与只见过那份列表的全新 worker 比刻度文字登记表与
380 px 预览像素。夹具先证明「改了真的变了」（改色 / 隐藏后像素 ≠ 原样），不变式才不是空转。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

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

SCRIPT_NAME = "fig_undo_sides.py"
ENTRY = "main"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch


def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    im = ax.imshow(np.arange(20).reshape(4, 5))
    fig.colorbar(im, ax=ax)
    fig.savefig("Cbar.pdf")

    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1, 2], [1, 3, 2])
    ax2 = ax.twinx()
    ax2.plot([0, 1, 2], [10, 5, 30], color="r")
    fig.savefig("Twin.pdf")

    # 文字在上边 + 两侧都开 + 次刻度：按侧 × 按档都得原样回来
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1])
    ax.xaxis.tick_top()
    ax.tick_params(axis="y", labelright=True)
    ax.minorticks_on()
    fig.savefig("Sides.pdf")

    # 脚本自己藏了主刻度文字（tick_params 默认只动主刻度）
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1])
    ax.tick_params(axis="x", labelbottom=False)
    fig.savefig("Hidden.pdf")

    # 脚本只藏了边上那一条（防重叠的常见写法）：整侧仍算开着，撤销要逐条回到原样
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1])
    fig.canvas.draw()
    ax.xaxis.get_major_ticks()[0].label1.set_visible(False)
    fig.savefig("Edge.pdf")

    fig, ax = plt.subplots(figsize=(4, 3))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.annotate(
        "", xy=(0.8, 0.8), xytext=(0.2, 0.2),
        arrowprops=dict(arrowstyle="simple", mutation_scale=40, fc="blue", ec="red", lw=2),
    )
    ax.add_patch(FancyArrowPatch(
        (0.1, 0.9), (0.6, 0.6), arrowstyle="fancy", mutation_scale=40,
        fc="yellow", ec="black", alpha=0.7,
    ))
    fig.savefig("Arrow.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("undo-sides-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _labels(man) -> list[str]:
    return sorted(e["gid"] for e in man["elements"] if e.get("role") == "ticklabel")


def _png(w, stem, patches, tag) -> str:
    return hashlib.sha1(w.preview_png(stem, list(patches), 380, tag).read_bytes()).hexdigest()


def _state(w, stem, patches, tag):
    resp = w.override(stem, list(patches))
    assert not resp.get("warnings"), resp["warnings"]
    return _labels(resp["manifest"]), _png(w, stem, patches, tag)


def _fresh(figs, stem, patches, tag):
    w = _worker(figs)
    try:
        return _state(w, stem, patches, tag)
    finally:
        pool.discard(w)


def _vis(gid, v):
    return {"gid": gid, "prop": "visible", "value": v}


def _font(gid, v):
    return {"gid": gid, "prop": "fontsize", "value": v}


def _color(gid, v):
    return {"gid": gid, "prop": "color", "value": v}


def _alpha(gid, v):
    return {"gid": gid, "prop": "alpha", "value": v}


ANN = "axes_0.texts_0.arrow"
PATCH = "axes_0.arrows_0"

#: (stem, 撤销栈依次经过的全量列表)。第一份列表都是「改了」，下面单独量它真的改了像素。
SEQUENCES = {
    "cbar-hide-undo-font": (
        "Cbar",
        [[_vis("axes_1.yticks", False)], [], [_font("axes_1.yticks", 12.0)]],
    ),
    "cbar-hide-show": ("Cbar", [[_vis("axes_1.yticks", False)], [_vis("axes_1.yticks", True)]]),
    "twin-hide-undo-font": (
        "Twin",
        [[_vis("axes_1.yticks", False)], [], [_font("axes_1.yticks", 12.0)]],
    ),
    "twin-hide-show-undo": (
        "Twin",
        [[_vis("axes_1.yticks", False)], [_vis("axes_1.yticks", True)], []],
    ),
    "sides-x": ("Sides", [[_vis("axes_0.xticks", False)], [_vis("axes_0.xticks", True)], []]),
    "sides-y": ("Sides", [[_vis("axes_0.yticks", False)], [], [_vis("axes_0.yticks", True)]]),
    "hidden-show-undo": (
        "Hidden",
        [[_vis("axes_0.xticks", True)], [], [_vis("axes_0.xticks", False)], []],
    ),
    "edge-hide-undo": (
        "Edge",
        [[_vis("axes_0.xticks", False)], [], [_font("axes_0.xticks", 12.0)]],
    ),
    "edge-hide-show-undo": (
        "Edge",
        [[_vis("axes_0.xticks", False)], [_vis("axes_0.xticks", True)], []],
    ),
    "arrow-annotation": ("Arrow", [[_color(ANN, "#00ff00")], []]),
    "arrow-patch": ("Arrow", [[_color(PATCH, "#00ff00")], []]),
    "arrow-alpha-order": (
        "Arrow",
        [
            [_alpha(PATCH, 0.3)],
            [_alpha(PATCH, 0.3), _color(PATCH, "#00ff00")],
            [_color(PATCH, "#00ff00")],
            [],
            [_alpha(PATCH, 0.3)],
        ],
    ),
}


@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_hot_equals_fresh_through_undo(library, name):
    stem, seq = SEQUENCES[name]
    base = _fresh(library, stem, [], "base")
    hot = _worker(library)
    try:
        for i, patches in enumerate(seq):
            got = _state(hot, stem, patches, f"hot{i}")
            want = _fresh(library, stem, patches, f"fresh{i}")
            assert got[0] == want[0], f"{name} 第 {i} 步刻度文字登记分岔：{patches}"
            assert got[1] == want[1], f"{name} 第 {i} 步热会话像素 ≠ 全量重放：{patches}"
            if i == 0:
                # 夹具不空转：第一步的改动必须真的画出来（旧代码在色条 / twinx 上隐藏是空操作）
                assert want[1] != base[1], f"{name} 第一步没改动像素，夹具量不到东西"
            if not patches:
                assert got == base, f"{name} 第 {i} 步全撤之后没回到脚本原样"
    finally:
        pool.discard(hot)


@pytest.mark.parametrize("stem", ["Cbar", "Twin"])
def test_hiding_right_side_labels_really_hides_them(library, stem):
    """色条 / twinx 的刻度文字在右边：隐藏要把它们真的去掉，而不是去关左边那排不存在的字。"""
    gid, prefix = "axes_1.yticks", "axes_1.yticklabels_"
    before, _ = _fresh(library, stem, [], "before")
    after, _ = _fresh(library, stem, [_vis(gid, False)], "after")
    assert any(g.startswith(prefix) for g in before)
    assert not any(g.startswith(prefix) for g in after)


@pytest.mark.parametrize(
    ("stem", "patches"),
    [
        ("Cbar", [_vis("axes_1.yticks", False)]),
        ("Twin", [_vis("axes_1.yticks", False)]),
        ("Sides", [_vis("axes_0.xticks", False), _vis("axes_0.yticks", False)]),
        ("Hidden", [_vis("axes_0.xticks", True)]),
        ("Twin", [_vis("axes_1.yticks", True), _font("axes_1.yticks", 12.0)]),
        ("Edge", [_vis("axes_0.xticks", True)]),
    ],
    ids=["cbar-hide", "twin-hide", "sides-hide", "hidden-show", "twin-show", "edge-show"],
)
def test_exported_script_hides_and_shows_the_same_sides(tmp_path, stem, patches):
    """导出修改后的 .py（`scriptadjust._tick_statement`）与引擎同口径：隐藏关两侧，显示保留脚本原样那几侧。"""
    from support.scriptadjust_harness import _replays_identically

    (tmp_path / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(tmp_path), ENTRY)
    w.ensure_built()
    try:
        assert _replays_identically(w, SCRIPT_NAME, tmp_path, stem, patches)
    finally:
        pool.discard(w)
