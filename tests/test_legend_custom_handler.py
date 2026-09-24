"""脚本自定义 handler 的图例项，在图例重建之后仍是脚本画的那一格（用户 Figure2，2026-09-24）。

用户脚本给图例加了一项色带：`legend(handles + [ColormapLegendKey(cmap)], …,
handler_map={ColormapLegendKey: HandlerColormap()})`，handler 一格画 24 段矩形 + 一个描边。
matplotlib 只把第一个 artist 放进 `legend_handles`，条目模型的「脚本原样」因此只剩第一段：
改内边距 / 列数 / 顺序任何一条（全都走 `rebuild_legend`）之后，色带变成一块浅色纯色、描边消失。

同一张图里还有一个代理示意线：脚本特意给「Free space」配了更短的虚线 `(0, (3, 2))`，而图中
那条线是 `(0, (5, 3))`。`get_linestyle()` 对两者都回 `'--'`，指纹相等 → 误判成跟随源 →
第一次 apply 的同步就把图例上的短虚线换回了源的长虚线。

本文件钉住：

1. 布局值不变的重建与脚本原样逐像素相同（色带那一格没有被换成纯色）；
2. 定格的项不摆 handle_* 控件；
3. 虚线节奏不同的代理项默认 custom，任何 apply 之后都还是脚本给的节奏；
4. 热态 == 全新 worker 一次性重放。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_legend_custom_handler.py"
ENTRY = "main"
STEM = "Custom"
LEG = "axes_0.legend"
FREE, THEORY, BAND = (f"{LEG}.texts_{j}" for j in range(3))

#: 与用户脚本同形：代理示意线换了虚线节奏 + 一项自定义 handler 画的色带
LIBRARY = """\
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D


class ColormapKey:
    def __init__(self, cmap):
        self.cmap = cmap


class HandlerColormap(HandlerBase):
    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height,
                       fontsize, trans):
        steps = 24
        artists = [
            mpl.patches.Rectangle(
                (xdescent + width * i / steps, ydescent), width / steps + 0.05, height,
                transform=trans, facecolor=orig_handle.cmap((i + 0.5) / steps),
                edgecolor="none",
            )
            for i in range(steps)
        ]
        artists.append(
            mpl.patches.Rectangle(
                (xdescent, ydescent), width, height, transform=trans,
                facecolor="none", edgecolor="0.3", linewidth=0.8,
            )
        )
        return artists


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0.0, 2.0, 20)
    ax.axhline(0.5, color="#7a8798", lw=1.5, ls=(0, (5, 3)), label="Free space")
    ax.plot(x, x / 2.0, color="#e64b35", lw=1.5, label="Theory")
    handles, labels = ax.get_legend_handles_labels()
    handles[0] = Line2D([], [], color="#7a8798", lw=1.5, ls=(0, (3, 2)))
    handles.append(ColormapKey(mpl.colormaps["viridis"]))
    labels.append("Band")
    ax.legend(handles, labels, loc="upper left", handlelength=3.0,
              handler_map={ColormapKey: HandlerColormap()})
    fig.savefig("Custom.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-custom-handler")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture(scope="module")
def hot(library):
    w = _worker(library)
    try:
        yield w
    finally:
        pool.discard(w)


def _man(worker, patches=()):
    resp = worker.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _fields(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里"
    return {f["prop"]: f for f in hits[0]["editable"]}


def _entry(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)["legend_entry"]


def _png(worker, patches, tag):
    return worker.preview_png(STEM, list(patches), 380, tag).read_bytes()


def _legend_value(man, prop):
    return _fields(man, LEG)[prop]["value"]


def test_a_rebuild_with_unchanged_layout_is_pixel_identical(hot):
    """把内边距 / 示意线长「改」成它现在的值：走一遍 `rebuild_legend`，画面必须与脚本原样
    逐像素相同。修之前色带那一格在这一步变成第一段的纯色、描边消失。"""
    man = _man(hot)
    same = [
        {"gid": LEG, "prop": "borderpad", "value": _legend_value(man, "borderpad")},
        {"gid": LEG, "prop": "handlelength", "value": _legend_value(man, "handlelength")},
    ]
    original = _png(hot, [], "orig")
    rebuilt = _png(hot, same, "rebuilt")
    assert rebuilt == original
    _man(hot)


def test_reordering_keeps_the_band_and_it_follows_its_row(hot):
    """重排之后色带仍是色带：与原样不同（它换了一行），但与「只重排、不含色带」那张
    也不同——色带要是塌成纯色，这两张的差别只剩一行浅色块。"""
    man = _man(hot, [{"gid": LEG, "prop": "entry_order", "value": [2, 0, 1]}])
    assert [e for e in man["elements"] if e["gid"] == BAND]
    moved = _png(hot, [{"gid": LEG, "prop": "entry_order", "value": [2, 0, 1]}], "moved")
    hidden = _png(
        hot,
        [
            {"gid": LEG, "prop": "entry_order", "value": [2, 0, 1]},
            {"gid": BAND, "prop": "visible", "value": False},
        ],
        "hidden",
    )
    assert moved != _png(hot, [], "orig2")
    assert moved != hidden
    _man(hot)


def test_the_frozen_band_offers_no_handle_controls(hot):
    """一格里画了 25 个 artist，没有「那一条」示意线的样式可改：不摆 handle_*。"""
    fields = _fields(_man(hot), BAND)
    assert not [p for p in fields if p.startswith("handle_")]
    assert "binding" not in fields, "色带没有源，也不伪造一个"


def test_a_proxy_with_its_own_dash_rhythm_is_custom_not_following(hot):
    """代理示意线的虚线节奏与源不同：源找得到（label 相同），默认却是 custom。"""
    man = _man(hot)
    assert _entry(man, FREE) == {
        "index": 0,
        "source_gid": "axes_0.lines_0",
        "binding_default": "custom",
    }
    assert _entry(man, THEORY)["binding_default"] == "follow_source"


def test_an_unrelated_edit_does_not_swap_the_proxy_dash(hot):
    """任何一次 apply 尾部都跑图例同步。修之前那一步就把短虚线换成了源的长虚线：
    改一条与图例无关的属性，图例那一格也跟着变了。"""
    original = _png(hot, [], "orig3")
    touched = _png(hot, [{"gid": "axes_0.lines_1", "prop": "alpha", "value": 1.0}], "touched")
    assert touched == original
    _man(hot)


def test_hot_equals_fresh_replay(hot, library):
    patches = [
        {"gid": LEG, "prop": "borderpad", "value": 1.2},
        {"gid": LEG, "prop": "ncol", "value": 2},
        {"gid": LEG, "prop": "entry_order", "value": [2, 1, 0]},
    ]
    hot_png = _png(hot, patches, "hot")
    w = _worker(library)
    try:
        fresh_png = _png(w, patches, "fresh")
    finally:
        pool.discard(w)
    assert hot_png == fresh_png
    _man(hot)
