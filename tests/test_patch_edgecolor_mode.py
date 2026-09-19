"""Patch 边色的「脚本原样」是一个模式（没设），不是一个值——撤销要把模式还回去（#423）。

机制：`Patch.get_edgecolor()` 回解析后的 RGBA，而脚本原样常常是 `_original_edgecolor is None`
（matplotlib 按 `patch.force_edgecolor` / 有没有面自己决定画不画边，花纹颜色停在
`rcParams['hatch.color']`）。按值写回 `(0,0,0,0)` 把模式换成「显式透明」，3.10 及以前
`_set_edgecolor` 顺手把 `_hatch_color` 也写成同一个值——之后再加花纹，斜线是透明的。
修法：getter 回 `_PatchEdge`（原始设定 + ≤3.10 的 `_hatch_color` 快照），setter 认得它。

判据：热态「设边色 → 撤掉 → 加花纹」与只见最终列表的全新 worker **像素逐字节相等**。
3.11 起花纹颜色是独立属性，边色不再碰它——这些用例在 3.11 上本来就绿，判得出差别的是
3.8 / 3.10（发行 runtime 是 3.11，用户自己的环境不一定）。本进程不 import matplotlib。
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

SCRIPT_NAME = "fig_edge.py"
ENTRY = "main"
STEM = "Edge"
#: `patches_0` 没设边色（模式 = 没设）；`patches_1` 脚本显式给了边色（模式 = 值）；
#: 一组柱（bar_series，逐柱走同一套 getter / setter；没有 hatch 控件）。
LIBRARY = """\
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.set_xlim(0, 6)
    ax.set_ylim(0, 3)
    ax.add_patch(Rectangle((0.3, 0.3), 1.6, 1.6, facecolor="#B34700"))
    ax.add_patch(Rectangle((2.3, 0.3), 1.6, 1.6, facecolor="#2A6F3C", edgecolor="#000080", linewidth=2))
    ax.bar([4.6, 5.4], [1.4, 2.2], width=0.6, label="bars")
    fig.savefig("Edge.png")
"""

UNSET, EXPLICIT = "axes_0.patches_0", "axes_0.patches_1"


def _p(gid, prop, value):
    return {"gid": gid, "prop": prop, "value": value}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("edge-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture
def worker(library):
    w = _worker(library)
    yield w
    pool.discard(w)


def _apply(w, patches) -> dict:
    resp = w.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _png(w, patches, tag) -> str:
    return hashlib.sha1(w.preview_png(STEM, list(patches), 360, tag).read_bytes()).hexdigest()


def _fresh(library, patches, tag):
    w = _worker(library)
    try:
        return _apply(w, patches), _png(w, patches, tag)
    finally:
        pool.discard(w)


def _gids(man):
    return {e["gid"] for e in man["elements"]}


def test_fixture_exposes_the_three_elements(worker):
    man = _apply(worker, [])
    assert {UNSET, EXPLICIT}.issubset(_gids(man)), sorted(_gids(man))
    assert any(e["role"] == "bar_series" for e in man["elements"])


@pytest.mark.parametrize("gid", [UNSET, EXPLICIT])
def test_undoing_an_edge_colour_then_hatching_matches_a_fresh_worker(worker, library, gid):
    """#423 的最小复现（没设边色的那块）+ 对照（脚本显式给了边色的那块）：
    热态「设边色 → 撤掉 → 加花纹」== 只见 `[hatch]` 的全新 worker，manifest 与像素都相等。"""
    _apply(worker, [_p(gid, "edgecolor", "#ff00ff")])
    hot_man = _apply(worker, [_p(gid, "hatch", "/")])
    hot_png = _png(worker, [_p(gid, "hatch", "/")], f"{gid}-hot")
    fresh_man, fresh_png = _fresh(library, [_p(gid, "hatch", "/")], f"{gid}-fresh")
    assert hot_man == fresh_man
    assert hot_png == fresh_png, f"{gid}：manifest 一样但画出来不一样（花纹颜色）"


def test_undo_alone_restores_the_original_pixels(worker):
    """撤掉边色要回到脚本原样的像素——没设的那块回「没有边」，显式的那块回海军蓝。"""
    base = _png(worker, [], "base")
    for gid in (UNSET, EXPLICIT):
        _apply(worker, [_p(gid, "edgecolor", "#ff00ff")])
        assert _png(worker, [_p(gid, "edgecolor", "#ff00ff")], f"{gid}-on") != base
        _apply(worker, [])
        assert _png(worker, [], f"{gid}-off") == base, f"{gid}：撤掉边色没有回到脚本原样"


def test_bar_series_edge_colour_undo_restores_the_script_look(worker):
    """柱系列逐柱走同一套 getter / setter（`_bar_handler` 收集每根柱的 `_PatchEdge`）：
    撤掉整组边色要回到脚本原样。柱系列没有 hatch 控件，花纹那一格量不到，这里只守
    「模式回去了」的可见部分。"""
    man = _apply(worker, [])
    bars = next(e["gid"] for e in man["elements"] if e["role"] == "bar_series")
    base = _png(worker, [], "bars-base")
    assert _png(worker, [_p(bars, "edgecolor", "#ff00ff")], "bars-on") != base
    _apply(worker, [_p(bars, "edgecolor", "#ff00ff")])
    assert _apply(worker, []) == man
    assert _png(worker, [], "bars-off") == base


def test_undoing_an_edge_colour_then_unfilling_matches_a_fresh_worker(worker, library):
    """模式与值的第二处分别（花纹之外）：`set_fill(False)` 让 matplotlib 按 `_original_edgecolor`
    重算边——原样是「没设」就按 `patch.edgecolor` 画出轮廓，是「显式透明」就什么都没有。
    热态「设边色 → 撤掉 → 去掉填充」== 只见 `[fill=False]` 的全新 worker。"""
    _apply(worker, [_p(UNSET, "edgecolor", "#ff00ff")])
    hot_man = _apply(worker, [_p(UNSET, "fill", False)])
    hot_png = _png(worker, [_p(UNSET, "fill", False)], "unfill-hot")
    fresh_man, fresh_png = _fresh(library, [_p(UNSET, "fill", False)], "unfill-fresh")
    assert hot_man == fresh_man
    assert hot_png == fresh_png, "去掉填充之后的轮廓：模式按值写回就画不出来"
