"""图例文字：三种「改图例上那行字」的动作必须分得清。

1. **直接改图例项文字**（`axes_i.legend.texts_j` 的 `text`）——只动图例，
   源曲线的 label 一个字节不变；
2. **改源曲线的 label**（`axes_i.lines_j` 的 `label`）——只改数据系列的名字，
   **不**回头覆盖图例上那行字（否则用户在图例里写的东西会被别处的改动悄悄
   抹掉）；
3. **触发图例重建**（`ncol` / `entry_order` 这类构建期参数）——文字对象整批
   换新，已应用的文字 override 必须被重放到新对象上。

三件事混成一个 setter 的表现是：换了列数之后，用户改过的图例项自己变回原样，
而且没有任何提示。这里逐条钉住它们，外加「重建之后不许留孤儿 override」。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""
import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

SCRIPT_NAME = "fig_legend.py"
ENTRY = "main"
STEM = "LegendFig"
T0 = "axes_0.legend.texts_0"
T1 = "axes_0.legend.texts_1"

LIBRARY = '''\
import numpy as np
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0.0, 6.0, 40)
    ax.plot(x, np.sin(x), label="alpha")
    ax.plot(x, np.cos(x), label="beta")
    ax.plot(x, np.sin(x) * 0.5, label="gamma")
    ax.legend()
    fig.savefig("LegendFig.pdf")
'''


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _render(figs, patches=()):
    w = _worker(figs)
    try:
        resp = w.override(STEM, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _field(man, gid, prop):
    return next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == prop)


def _legend_texts(man):
    return [_field(man, e["gid"], "text") for e in man["elements"]
            if e["role"] == "legend_text" and ".texts_" in e["gid"]]


def test_single_legend_item_is_its_own_element(library):
    man = _render(library)
    assert _legend_texts(man) == ["alpha", "beta", "gamma"]
    assert _el(man, T0)["role"] == "legend_text"


def test_editing_a_legend_item_leaves_the_source_line_label_alone(library):
    """改图例上那行字 ≠ 改数据系列的名字。混为一谈的话，用户想「只把图例写得
    好看点」就会连带改掉别处引用的系列名。"""
    man = _render(library, [{"gid": T0, "prop": "text", "value": "α 相"}])
    assert _legend_texts(man)[0] == "α 相"
    assert _field(man, "axes_0.lines_0", "label") == "alpha", "源曲线的 label 不该被动"


def test_editing_the_source_label_does_not_rewrite_the_legend(library):
    """改源曲线的 label 是**另一件事**：它改的是数据系列的名字，图例上那行字
    不跟着变——连触发一次重建也不变。

    这是有意的契约。图例文字自己是一个可编辑字段，让曲线 label 反向覆盖它，
    等于用户在图例里写的东西会被别处的改动悄悄抹掉。想改图例上的字就直接改
    图例项（上一条用例），两条路互不干扰。
    """
    man = _render(library, [{"gid": "axes_0.lines_0", "prop": "label", "value": "renamed"}])
    assert _field(man, "axes_0.lines_0", "label") == "renamed"
    assert _legend_texts(man)[0] == "alpha"
    man2 = _render(library, [
        {"gid": "axes_0.lines_0", "prop": "label", "value": "renamed"},
        {"gid": "axes_0.legend", "prop": "ncol", "value": 1},   # 触发重建
    ])
    assert _legend_texts(man2)[0] == "alpha", "重建也不该让曲线 label 覆盖图例文字"


def test_item_text_survives_a_legend_rebuild(library):
    """ncol / entry_order 是构建期参数：文字对象整批换新，已应用的文字
    override 必须重放到新对象上，否则改过的那一项会自己变回去。"""
    man = _render(library, [
        {"gid": T0, "prop": "text", "value": "AAA"},
        {"gid": T1, "prop": "text", "value": "BBB"},
        {"gid": "axes_0.legend", "prop": "ncol", "value": 3},
    ])
    assert _legend_texts(man)[:2] == ["AAA", "BBB"]


def test_item_text_follows_display_order_after_reorder(library):
    """`texts_j` 指的是**显示顺序**里的第 j 项（与 entry_order 的语义一致）。"""
    man = _render(library, [
        {"gid": "axes_0.legend", "prop": "entry_order", "value": [2, 0, 1]},
        {"gid": T0, "prop": "text", "value": "第一项"},
    ])
    assert _legend_texts(man) == ["第一项", "alpha", "beta"]


def test_rebuild_does_not_orphan_any_override(library):
    """重建之后 gid 集合不变 → 已有 override 一条都不会变成孤儿。"""
    patches = [
        {"gid": T0, "prop": "text", "value": "AAA"},
        {"gid": "axes_0.legend", "prop": "entry_order", "value": [1, 0, 2]},
        {"gid": "axes_0.legend", "prop": "ncol", "value": 2},
        {"gid": "axes_0.legend", "prop": "labelspacing", "value": 0.9},
    ]
    w = _worker(library)
    try:
        resp = w.override(STEM, patches)
        assert not resp["warnings"], resp["warnings"]
        gids = {e["gid"] for e in resp["manifest"]["elements"]}
        assert {p["gid"] for p in patches} <= gids, "override 指向的 gid 全都还在"
    finally:
        pool.discard(w)


def test_undo_restores_the_original_legend_text(library):
    base = _render(library)
    w = _worker(library)
    try:
        w.override(STEM, [{"gid": T0, "prop": "text", "value": "改过"}])
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    assert _legend_texts(back["manifest"]) == _legend_texts(base)


def test_hot_and_fresh_agree_on_text_plus_rebuild(library):
    """一步步改（先改字后换列数）== 全新 worker 一次性重放。"""
    steps = [
        [{"gid": T0, "prop": "text", "value": "AAA"}],
        [{"gid": T0, "prop": "text", "value": "AAA"},
         {"gid": "axes_0.legend", "prop": "ncol", "value": 2}],
    ]
    hot = _worker(library)
    try:
        for step in steps:
            resp = hot.override(STEM, step)
            assert not resp["warnings"], resp["warnings"]
        man_hot = resp["manifest"]
    finally:
        pool.discard(hot)
    man_fresh = _render(library, steps[-1])
    assert _legend_texts(man_hot) == _legend_texts(man_fresh)
    assert _el(man_hot, "axes_0.legend")["bbox"] == pytest.approx(
        _el(man_fresh, "axes_0.legend")["bbox"], abs=5e-3)
