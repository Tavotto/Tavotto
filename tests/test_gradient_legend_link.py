"""渐变峰的图例色块跟着改（ADR 0167 修订 2026-11-03）。

脚本自己用色带画的图例方块不是 matplotlib 图例：引擎按「同一个子图里、两端原色对得上」配对，**互相唯一**才自动配上；
几块同色就不猜，等用户点。配对写进 override（`legend: [集合, 色块]` / `null`），重放按记下的来。

钉：自动配对与图例文字；同色不猜；改峰时配上的方块一起改、别的方块不动；`legend: null` 时方块不动；
手动挑哪块改哪块；撤销时方块回到原色；换配对后热态 == 全新重放。

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

SCRIPT_NAME = "fig_gradlegend.py"
ENTRY = "main"
STEM = "GradLegend"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import PathPatch
from matplotlib.path import Path

ORANGE = np.linspace((1.0, 0.85, 0.7), (0.85, 0.3, 0.05), 32)
BLUE = np.linspace((0.85, 0.9, 1.0), (0.1, 0.35, 0.75), 32)


def peak(ax, x, c, ramp):
    bg = 0.1 + 0 * x
    y = bg + np.exp(-((x - c) / 1.0) ** 2)
    edges = np.linspace(float(y.min()), float(y.max()), 129)
    t = (edges[-1] - (edges[:-1] + edges[1:]) / 2) / (edges[-1] - edges[0])
    rgb = np.column_stack([np.interp(t, np.linspace(0, 1, 32), ramp[:, k]) for k in range(3)])
    verts = [[(x[0], a), (x[-1], a), (x[-1], b), (x[0], b)] for a, b in zip(edges[:-1], edges[1:])]
    coll = PolyCollection(verts, facecolors=rgb, edgecolors="none")
    ax.add_collection(coll)
    poly = np.vstack([np.column_stack([x, y]), np.column_stack([x[::-1], bg[::-1]]), [[x[0], y[0]]]])
    coll.set_clip_path(PathPatch(Path(poly), transform=ax.transData))


def swatches(ax, entries):
    verts, colors = [], []
    for cy, ramp, name in entries:
        for j, idx in enumerate(np.linspace(31, 0, 16).astype(int)):
            y0 = cy - 0.03 + j * 0.06 / 16
            verts.append([(0.72, y0), (0.76, y0), (0.76, y0 + 0.06 / 16), (0.72, y0 + 0.06 / 16)])
            colors.append(ramp[idx])
        ax.text(0.775, cy, name, transform=ax.transAxes, va="center")
    ax.add_collection(PolyCollection(verts, facecolors=colors, transform=ax.transAxes), autolim=False)


def main():
    fig, (ax, ax2, ax3, ax4) = plt.subplots(1, 4, figsize=(18.0, 3.0))
    x = np.linspace(0.0, 10.0, 300)
    # axes_0：两个不同色的峰（fill_0 橙、fill_1 蓝），图例两块（fill_2：上 Orange、下 Blue）——各自唯一
    peak(ax, x, 3.0, ORANGE)
    peak(ax, x, 7.0, BLUE)
    swatches(ax, [(0.9, ORANGE, "Orange"), (0.75, BLUE, "Blue")])
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1.3)
    # axes_1：两个同色峰 + 两块同色图例——不猜
    peak(ax2, x, 3.0, BLUE)
    peak(ax2, x, 7.0, BLUE)
    swatches(ax2, [(0.9, BLUE, "B1"), (0.75, BLUE, "B2")])
    ax2.set_xlim(0, 10)
    ax2.set_ylim(0, 1.3)
    # axes_2：两个同色峰 + 一块匹配的图例（fill_2）——手动让两个峰都跟同一块（P1 撤销顺序）
    peak(ax3, x, 3.0, BLUE)
    peak(ax3, x, 7.0, BLUE)
    swatches(ax3, [(0.9, BLUE, "Shared")])
    ax3.set_xlim(0, 10)
    ax3.set_ylim(0, 1.3)
    # axes_3：一个峰 + 自成一个 PolyCollection（16 条、不裁剪）的图例色块——它自己不是对手
    peak(ax4, x, 3.0, ORANGE)
    swatches(ax4, [(0.9, ORANGE, "Solo")])
    ax4.set_xlim(0, 10)
    ax4.set_ylim(0, 1.3)
    fig.savefig("GradLegend.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("gradlegend-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _render(figs, *seq):
    """在一个 worker 里依次发几组 patches，回最后一次的 manifest。"""
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    try:
        w.ensure_built()
        resp = None
        for patches in seq or ([],):
            resp = w.override(STEM, list(patches))
            assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _grad(man, gid):
    el = next(e for e in man["elements"] if e["gid"] == gid)
    return next(f for f in el["editable"] if f["prop"] == "gradient")["value"]


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _close(a, b, tol=3):
    return all(abs(x - y) <= tol for x, y in zip(_rgb(a), _rgb(b)))


def _cand(value, ref):
    return next(c for c in value["legend"]["candidates"] if c["ref"] == list(ref))


@pytest.fixture(scope="module")
def manifest(library):
    return _render(library)


def test_unique_colours_pair_automatically_and_name_the_legend_text(manifest):
    orange = _grad(manifest, "axes_0.fill_0")["legend"]
    blue = _grad(manifest, "axes_0.fill_1")["legend"]
    assert len(orange["candidates"]) == 1 and len(blue["candidates"]) == 1
    assert orange["auto"] == orange["linked"] == orange["candidates"][0]["ref"]
    assert orange["candidates"][0]["gid"] == "axes_0.fill_2"
    assert orange["candidates"][0]["caption"] == "Orange"
    assert blue["candidates"][0]["caption"] == "Blue"
    assert orange["candidates"][0]["ref"] != blue["candidates"][0]["ref"]


def test_same_colours_are_not_guessed(manifest):
    v = _grad(manifest, "axes_1.fill_0")["legend"]
    assert len(v["candidates"]) == 2
    assert v["auto"] is None and v["linked"] is None


NEW = ["#ffd9de", "#b00020"]


def test_recolouring_a_peak_recolours_its_auto_paired_swatch_only(library, manifest):
    before = _grad(manifest, "axes_0.fill_1")["legend"]["candidates"][0]["stops"]
    ref = _grad(manifest, "axes_0.fill_0")["legend"]["auto"]
    man = _render(library, [{"gid": "axes_0.fill_0", "prop": "gradient", "value": {"stops": NEW}}])
    mine = _cand(_grad(man, "axes_0.fill_0"), ref)["stops"]
    assert _close(mine[0], NEW[0]) and _close(mine[1], NEW[1]), mine
    other = _grad(man, "axes_0.fill_1")["legend"]["candidates"][0]["stops"]
    assert other == before, "蓝色那块不该被橙色峰带着改"


def test_legend_null_leaves_the_swatch_alone(library, manifest):
    ref = _grad(manifest, "axes_0.fill_0")["legend"]["auto"]
    before = _cand(_grad(manifest, "axes_0.fill_0"), ref)["stops"]
    man = _render(
        library,
        [{"gid": "axes_0.fill_0", "prop": "gradient", "value": {"stops": NEW, "legend": None}}],
    )
    v = _grad(man, "axes_0.fill_0")
    assert v["legend"]["linked"] is None
    assert _cand(v, ref)["stops"] == before


def test_picking_one_of_the_same_coloured_swatches_recolours_that_one(library, manifest):
    a, b = (c["ref"] for c in _grad(manifest, "axes_1.fill_0")["legend"]["candidates"])
    before_b = _cand(_grad(manifest, "axes_1.fill_0"), b)["stops"]
    man = _render(
        library,
        [{"gid": "axes_1.fill_0", "prop": "gradient", "value": {"stops": NEW, "legend": a}}],
    )
    v = _grad(man, "axes_1.fill_0")
    assert v["legend"]["linked"] == a
    picked = _cand(v, a)["stops"]
    assert _close(picked[0], NEW[0]) and _close(picked[1], NEW[1])
    assert _cand(v, b)["stops"] == before_b


def test_undo_returns_the_swatch_to_its_script_colours(library, manifest):
    ref = _grad(manifest, "axes_0.fill_0")["legend"]["auto"]
    before = _cand(_grad(manifest, "axes_0.fill_0"), ref)["stops"]
    man = _render(
        library,
        [{"gid": "axes_0.fill_0", "prop": "gradient", "value": {"stops": NEW}}],
        [],
    )
    assert _cand(_grad(man, "axes_0.fill_0"), ref)["stops"] == before


def test_switching_the_pick_matches_a_fresh_replay(library, manifest):
    """热会话先配 a 再换 b，与全新 worker 只重放「配 b」的结果相同：a 回到原色、b 是新色。"""
    a, b = (c["ref"] for c in _grad(manifest, "axes_1.fill_0")["legend"]["candidates"])
    first = {"gid": "axes_1.fill_0", "prop": "gradient", "value": {"stops": NEW, "legend": a}}
    final = {"gid": "axes_1.fill_0", "prop": "gradient", "value": {"stops": NEW, "legend": b}}
    hot = _render(library, [first], [final])
    fresh = _render(library, [final])
    assert _grad(hot, "axes_1.fill_0") == _grad(fresh, "axes_1.fill_0")


def test_two_peaks_sharing_one_swatch_undo_one_matches_fresh_replay(library):
    """Codex #1695 P1：两个同色峰手动跟同一块，各改成不同颜色，撤销 A 后色块应是 B 的颜色（与只重放 B 相同）。"""
    ref = _grad(_render(library), "axes_2.fill_0")["legend"]["candidates"][0]["ref"]
    xa = ["#ffd9de", "#b00020"]
    xb = ["#d9ffd9", "#007020"]
    a = {"gid": "axes_2.fill_0", "prop": "gradient", "value": {"stops": xa, "legend": ref}}
    b = {"gid": "axes_2.fill_1", "prop": "gradient", "value": {"stops": xb, "legend": ref}}
    hot = _render(library, [a, b], [b])
    fresh = _render(library, [b])
    hot_stops = _cand(_grad(hot, "axes_2.fill_1"), ref)["stops"]
    assert hot_stops == _cand(_grad(fresh, "axes_2.fill_1"), ref)["stops"]
    assert _close(hot_stops[0], xb[0]) and _close(hot_stops[1], xb[1]), hot_stops
    # 反过来撤 B，剩 A
    hot2 = _render(library, [a, b], [a])
    fresh2 = _render(library, [a])
    assert (
        _cand(_grad(hot2, "axes_2.fill_0"), ref)["stops"]
        == _cand(_grad(fresh2, "axes_2.fill_0"), ref)["stops"]
    )


def test_swatch_drawn_as_its_own_collection_is_not_a_rival(library):
    """Codex #1695 P2：图例色块自成一个 16 条的 PolyCollection（不裁剪）不算对手，唯一对得上就自动配上。"""
    v = _grad(_render(library), "axes_3.fill_0")["legend"]
    assert len(v["candidates"]) == 1
    assert v["auto"] == v["candidates"][0]["ref"]
    assert v["linked"] == v["auto"]
