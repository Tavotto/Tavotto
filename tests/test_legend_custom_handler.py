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
3. 虚线节奏不同的代理项默认 custom（不跟随，`sync_legends` 就不会从源重派生它）；
4. 热态 == 全新 worker 一次性重放。

「任何 apply 之后虚线不变」没有单独写成像素用例：worker 的每一次预览都先 apply，拿来当
「原样」的那张同样经过了同步，两边恒等——判据落在 3 的绑定结论上。

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


# ---------------------------------------------------------------------------
# 源找到了、默认却是 custom 的自定义格子（#544 评审 P2）
# ---------------------------------------------------------------------------
#: 同一个色带，图里另有一个同名（「Band」）同类型（Rectangle）的形状：`bind_legend_entries`
#: 按 label 找到它当源，指纹对不上 → 默认 custom。定格与否要看有效绑定，不看有没有源。
SRC_SCRIPT = "fig_legend_custom_handler_src.py"
SRC_STEM = "CustomSrc"
SRC_LIBRARY = LIBRARY.replace(
    "    handles, labels = ax.get_legend_handles_labels()\n",
    '    ax.add_patch(mpl.patches.Rectangle((0.2, 0.1), 0.4, 0.1, facecolor="#dddddd",\n'
    '                                       label="Band"))\n'
    "    handles, labels = ax.get_legend_handles_labels()\n"
    "    handles, labels = handles[:2], labels[:2]\n",
).replace('fig.savefig("Custom.pdf")', 'fig.savefig("CustomSrc.pdf")')


@pytest.fixture(scope="module")
def hot_src(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-custom-handler-src")
    (figs / SRC_SCRIPT).write_text(SRC_LIBRARY, encoding="utf-8")
    w = pool.one_shot(SRC_SCRIPT, str(figs), ENTRY)
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _src_man(worker, patches=()):
    resp = worker.override(SRC_STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def test_the_fixture_really_has_a_custom_bound_source(hot_src):
    """前提先钉住：这一项确实找到了源、默认 custom——否则下面两条量的是「无源」那种。"""
    info = _entry(_src_man(hot_src), BAND)
    assert info["binding_default"] == "custom"
    assert info["source_gid"].startswith("axes_0.patches_")


def test_a_custom_bound_band_survives_a_rebuild(hot_src):
    man = _src_man(hot_src)
    same = [{"gid": LEG, "prop": "borderpad", "value": _legend_value(man, "borderpad")}]
    original = hot_src.preview_png(SRC_STEM, [], 380, "src-orig").read_bytes()
    rebuilt = hot_src.preview_png(SRC_STEM, same, 380, "src-rebuilt").read_bytes()
    assert rebuilt == original
    fields = _fields(man, BAND)
    assert not [p for p in fields if p.startswith("handle_")]
    assert fields["binding"]["value"] == "custom", "有源的项仍给绑定开关"
    _src_man(hot_src)


#: 有源、默认跟随的多 artist 项（误差棒：竖线 + 两端横杠）。断开跟随时示意线换成
#: 「脚本原样」——那必须是整格的，不能只剩第一条线。
ERR_SCRIPT = "fig_legend_errorbar_detach.py"
ERR_STEM = "ErrDetach"
ERR_LIBRARY = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.errorbar([0, 1, 2], [1, 2, 1], yerr=0.3, capsize=6, color="#1f77b4", label="Err")
    ax.legend(loc="upper left", handlelength=3.0)
    fig.savefig("ErrDetach.pdf")
"""


def test_detaching_a_following_multi_artist_entry_keeps_the_whole_cell(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-errorbar-detach")
    (figs / ERR_SCRIPT).write_text(ERR_LIBRARY, encoding="utf-8")
    w = pool.one_shot(ERR_SCRIPT, str(figs), ENTRY)
    w.ensure_built()
    try:
        entry = f"{LEG}.texts_0"
        man = w.override(ERR_STEM, [])["manifest"]
        assert _entry(man, entry)["binding_default"] == "follow_source", "前提：它在跟随源"
        original = w.preview_png(ERR_STEM, [], 380, "err-orig").read_bytes()
        detached = w.preview_png(
            ERR_STEM, [{"gid": entry, "prop": "binding", "value": "custom"}], 380, "err-det"
        ).read_bytes()
        assert detached == original
    finally:
        pool.discard(w)
