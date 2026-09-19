"""manifest 的颜色字段不把「没有颜色」显示成一个实色（#427）。

`mcolors.to_hex` 默认丢掉 alpha：没设边色的 patch 解析出 `(0,0,0,0)`，字段值 `#000000`，
检查器摆出一条并不存在的黑边（线宽 1.0 也在那儿）。现在 `overrides.to_hex` 见到 alpha 为 0
报 `NO_COLOR`（`"none"`）；半透明照报 RGB（alpha 另有字段）。Patch 族的 `facecolor` 另有
一条：`fill` 是这一组的开关，关着时字段值仍是开了会画的那个色（`_patch_face_hex`），
与 `bbox_visible` 下的 `bbox_facecolor` 同一模型——`'none'` 才是「没有颜色」。
本进程不 import matplotlib：一切经 `pool.one_shot()` 起的 worker。
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

SCRIPT_NAME = "fig_nocolour.py"
ENTRY = "main"
STEM = "NoColour"
#: 与 `engine/overrides.NO_COLOR` 同值（本进程不 import overrides——它顶层 import matplotlib）；
#: 两侧同源由 `tests/test_no_color_pair.py` 用 AST 看着。
NO_COLOR = "none"
LIBRARY = """\
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.set_xlim(0, 8)
    ax.set_ylim(0, 3)
    ax.add_patch(Rectangle((0.3, 0.3), 1.4, 1.4, facecolor="#B34700"))                       # patches_0 没设边色
    ax.add_patch(Rectangle((2.0, 0.3), 1.4, 1.4, facecolor="#2A6F3C", edgecolor="#000080"))  # patches_1 显式边色
    ax.add_patch(Rectangle((3.7, 0.3), 1.4, 1.4, facecolor="#8844AA", fill=False, edgecolor="k"))  # patches_2 fill 关着
    ax.add_patch(Rectangle((5.4, 0.3), 1.4, 1.4, facecolor="none", edgecolor="#AA0000"))    # patches_3 面就是 none
    ax.add_patch(Rectangle((7.1, 0.3), 0.6, 1.4, facecolor="#336699", alpha=0.4))            # patches_4 半透明
    ax.plot([1, 3, 5, 7], [2.4, 2.6, 2.4, 2.6], marker="o", markerfacecolor="none", color="#222222")
    fig.savefig("NoColour.png")
"""


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    figs = tmp_path_factory.mktemp("nocolour-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    try:
        resp = w.override(STEM, [])
        assert not (resp.get("warnings") or []), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _field(man, gid, prop):
    el = next(e for e in man["elements"] if e["gid"] == gid)
    return next(f for f in el["editable"] if f["prop"] == prop)["value"]


def test_an_unset_edge_reads_none_not_black(manifest):
    assert _field(manifest, "axes_0.patches_0", "edgecolor") == NO_COLOR
    assert _field(manifest, "axes_0.patches_1", "edgecolor") == "#000080"


def test_fill_off_keeps_the_face_colour_it_would_paint(manifest):
    """`fill` 是开关：关着时 facecolor 字段仍是开了会画的那个色，不是 none。"""
    assert _field(manifest, "axes_0.patches_2", "fill") is False
    assert _field(manifest, "axes_0.patches_2", "facecolor") == "#8844aa"


def test_a_face_that_is_none_reads_none_even_with_fill_on(manifest):
    assert _field(manifest, "axes_0.patches_3", "fill") is True
    assert _field(manifest, "axes_0.patches_3", "facecolor") == NO_COLOR


def test_semi_transparent_is_still_a_colour(manifest):
    """只有 alpha == 0 算「无」：半透明照报 RGB，透明度另有字段。"""
    assert _field(manifest, "axes_0.patches_4", "facecolor") == "#336699"
    assert _field(manifest, "axes_0.patches_4", "alpha") == 0.4


def test_hollow_marker_face_reads_none(manifest):
    assert _field(manifest, "axes_0.lines_0", "markerfacecolor") == NO_COLOR
    assert _field(manifest, "axes_0.lines_0", "markeredgecolor") == "#222222"


def test_no_colour_is_settable_back_and_hot_equals_fresh(tmp_path):
    """`none` 也是 setter 认的值（matplotlib 自己的写法）：把边色改成 none 再看，字段回 none、
    热态与只见最终列表的全新 worker manifest 相等。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    patches = [{"gid": "axes_0.patches_1", "prop": "edgecolor", "value": NO_COLOR}]
    hot = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    hot.ensure_built()
    fresh = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    fresh.ensure_built()
    try:
        hot.override(STEM, [])
        a = hot.override(STEM, patches)
        b = fresh.override(STEM, patches)
        assert not (a.get("warnings") or []), a["warnings"]
        assert _field(a["manifest"], "axes_0.patches_1", "edgecolor") == NO_COLOR
        assert a["manifest"] == b["manifest"]
    finally:
        pool.discard(hot)
        pool.discard(fresh)
