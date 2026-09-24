"""误差棒改色要连横杠一起改（2026-09-24，#544 评审时撞出来的既有缺陷）。

`errorbar` 的横杠是 Line2D 的 marker（`_` / `|`），颜色在 **marker 边色**上；误差棒 `color` 原来只
`set_color`，线与竖线组变了、横杠留在原色——图上与图例里都看得见（跟随源的图例如实照抄）。

本文件钉住：

1. 改色之后整张预览里**一个原色像素都不剩**（线 / 竖线组 / 横杠都换了）；
2. 撤销到底与脚本原样逐像素相同（getter 取回的是 setter 认得的完整原样）；
3. 改色后的样子与脚本原本就用那个颜色画的逐像素相同：跟着线色走的 marker 边 / 面一起换，脚本显式
   设的（空心 `mfc='none'`、黑边 `mec='k'`）不动。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import sys
from pathlib import Path

import pytest

from tavotto.engine import pool

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = "fig_errorbar_cap_color.py"
STEM = "EbCap"
EB = "axes_0.errorbar_0"
HOLLOW = "axes_0.errorbar_1"
BLACK_EDGE = "axes_0.errorbar_2"
ORIGINAL = (0x1F, 0x77, 0xB4)
LIBRARY = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.0, 2.2))
    ax.errorbar([0, 1, 2], [1, 2, 1], yerr=0.3, capsize=8, capthick=2, color="#1f77b4")
    ax.errorbar([0, 1, 2], [0, 0.5, 0], yerr=0.2, fmt="o", mfc="none", capsize=4,
                color="#2ca02c")
    ax.errorbar([0, 1, 2], [-0.6, -0.3, -0.6], yerr=0.15, fmt="s", mec="k", capsize=4,
                color="#ff7f0e")
    ax.set_axis_off()
    fig.savefig("EbCap.pdf")
"""


@pytest.fixture(scope="module")
def hot(tmp_path_factory):
    figs = tmp_path_factory.mktemp("errorbar-cap-color")
    (figs / SCRIPT).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT, str(figs), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _pixels(worker, patches, tag):
    w, h, n, px = pdfread.decode_png_any(
        worker.preview_png(STEM, list(patches), 380, tag).read_bytes()
    )
    return [tuple(px[i : i + 3]) for i in range(0, len(px), n)]


def _near(p, rgb, tol=6):
    return all(abs(a - b) <= tol for a, b in zip(p, rgb))


def test_recolouring_leaves_no_pixel_of_the_original_colour(hot):
    before = _pixels(hot, [], "before")
    assert sum(_near(p, ORIGINAL) for p in before) > 50, "前提：原色确实画在图上"
    after = _pixels(hot, [{"gid": EB, "prop": "color", "value": "#d62728"}], "red")
    assert sum(_near(p, ORIGINAL) for p in after) == 0, "横杠（marker 边色）留在了原色"
    resp = hot.override(STEM, [])
    assert not (resp.get("warnings") or []), resp["warnings"]


def test_undo_to_zero_is_pixel_identical(hot):
    original = hot.preview_png(STEM, [], 380, "orig").read_bytes()
    hot.preview_png(STEM, [{"gid": EB, "prop": "color", "value": "#d62728"}], 380, "tmp")
    assert hot.preview_png(STEM, [], 380, "undone").read_bytes() == original


def test_recoloured_equals_drawn_in_that_colour(hot, tmp_path_factory):
    """改色后的样子 == 脚本原本就用这个颜色画的样子（逐像素）。三条误差棒都比：实线 + 横杠那条、
    `mfc='none'` 的空心圆那条（改完仍是空心）、`mec='k'` 黑边方块那条（黑边不被涂掉）。"""
    figs = tmp_path_factory.mktemp("errorbar-cap-color-red")
    red_script = "fig_errorbar_cap_color_red.py"
    (figs / red_script).write_text(
        LIBRARY.replace('"#1f77b4"', '"#d62728"')
        .replace('color="#2ca02c"', 'color="#9467bd"')
        .replace('color="#ff7f0e"', 'color="#17becf"')
        .replace("EbCap.pdf", "EbCapRed.pdf"),
        encoding="utf-8",
    )
    r = pool.one_shot(red_script, str(figs), "main")
    r.ensure_built()
    try:
        expected = r.preview_png("EbCapRed", [], 380, "red").read_bytes()
    finally:
        pool.discard(r)
    recoloured = hot.preview_png(
        STEM,
        [
            {"gid": EB, "prop": "color", "value": "#d62728"},
            {"gid": HOLLOW, "prop": "color", "value": "#9467bd"},
            {"gid": BLACK_EDGE, "prop": "color", "value": "#17becf"},
        ],
        380,
        "both",
    ).read_bytes()
    hot.override(STEM, [])
    assert recoloured == expected


#: 蓝线配显式红边 marker：「跟不跟着线色」必须按脚本原样判一次、之后沿用。现判的话改成红之后
#: 边色恰好等于线色，撤销或再改色时会被误判成「跟着」（#556 评审）。
EXPLICIT_SCRIPT = "fig_errorbar_explicit_edge.py"
EXPLICIT_STEM = "EbEdge"
EXPLICIT_LIBRARY = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.0, 2.2))
    ax.errorbar([0, 1, 2], [1, 2, 1], yerr=0.3, fmt="o", ms=9, mew=2, mec="red",
                capsize=6, color="#1f77b4")
    ax.set_title("t")
    ax.set_axis_off()
    fig.savefig("EbEdge.pdf")
"""


@pytest.fixture(scope="module")
def explicit(tmp_path_factory):
    figs = tmp_path_factory.mktemp("errorbar-explicit-edge")
    (figs / EXPLICIT_SCRIPT).write_text(EXPLICIT_LIBRARY, encoding="utf-8")
    return figs


def test_an_explicit_marker_edge_survives_recolour_and_undo(explicit):
    w = pool.one_shot(EXPLICIT_SCRIPT, str(explicit), "main")
    w.ensure_built()
    try:
        keep = [{"gid": "axes_0.title", "prop": "text", "value": "t2"}]
        expected = w.preview_png(EXPLICIT_STEM, keep, 380, "e-keep").read_bytes()
        resp = w.override(
            EXPLICIT_STEM, keep + [{"gid": "axes_0.errorbar_0", "prop": "color", "value": "red"}]
        )
        assert not (resp.get("warnings") or []), resp["warnings"]
        # 只撤掉改色（留一条别的 override，走逐条还原）：红边仍是红的，线回到蓝
        resp = w.override(EXPLICIT_STEM, keep)
        assert not (resp.get("warnings") or []), resp["warnings"]
        assert w.preview_png(EXPLICIT_STEM, keep, 380, "e-undone").read_bytes() == expected
    finally:
        pool.discard(w)


def test_recolouring_twice_equals_a_fresh_replay_of_the_last_colour(explicit):
    """红 → 绿的热态 == 全新 worker 直接改绿（写回的前提：热态 == 全量重放）。"""
    green = [{"gid": "axes_0.errorbar_0", "prop": "color", "value": "#2ca02c"}]
    w = pool.one_shot(EXPLICIT_SCRIPT, str(explicit), "main")
    w.ensure_built()
    try:
        w.override(EXPLICIT_STEM, [{"gid": "axes_0.errorbar_0", "prop": "color", "value": "red"}])
        hot = w.preview_png(EXPLICIT_STEM, green, 380, "e-hot").read_bytes()
    finally:
        pool.discard(w)
    f = pool.one_shot(EXPLICIT_SCRIPT, str(explicit), "main")
    f.ensure_built()
    try:
        fresh = f.preview_png(EXPLICIT_STEM, green, 380, "e-fresh").read_bytes()
    finally:
        pool.discard(f)
    assert hot == fresh


def test_an_explicit_edge_equal_to_the_line_colour_is_still_explicit(tmp_path_factory):
    """脚本显式写了 `mec='#1f77b4'`，恰好等于线色：它仍是脚本的样式，系列改红后保持蓝边。按渲染出的
    颜色相等去判「跟不跟着线色」会把它当成跟随、一起涂红（#556 评审）。期望值 = 脚本原本就是红线蓝边。"""
    src = EXPLICIT_LIBRARY.replace('mec="red"', 'mec="#1f77b4"')
    assert 'mec="#1f77b4"' in src, "前提：替换真的落在了脚本上"
    figs = tmp_path_factory.mktemp("errorbar-explicit-same")
    (figs / EXPLICIT_SCRIPT).write_text(src, encoding="utf-8")
    red = "fig_errorbar_explicit_same_red.py"
    (figs / red).write_text(
        src.replace('color="#1f77b4"', 'color="red"').replace("EbEdge.pdf", "EbEdgeRed.pdf"),
        encoding="utf-8",
    )
    w = pool.one_shot(EXPLICIT_SCRIPT, str(figs), "main")
    w.ensure_built()
    try:
        got = w.preview_png(
            EXPLICIT_STEM, [{"gid": "axes_0.errorbar_0", "prop": "color", "value": "red"}], 380, "s"
        ).read_bytes()
    finally:
        pool.discard(w)
    r = pool.one_shot(red, str(figs), "main")
    r.ensure_built()
    try:
        expected = r.preview_png("EbEdgeRed", [], 380, "s-exp").read_bytes()
    finally:
        pool.discard(r)
    assert got == expected
