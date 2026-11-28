"""示意线改色时标记点颜色跟着变（真机 2026-11-28）。

脚本里曲线显式设了 markerfacecolor / markeredgecolor（与线同色）：只 `set_color` 的话示意线变了、
示意的标记点还是旧色。与线同色的标记跟着改；白芯 / 'none' / 另一种色保持不动；撤销（新色 → 旧色）对称。
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
# 引擎模块在 worker 里按平铺名互相 import（`import pathgeom`）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"))

from matplotlib.collections import PathCollection  # noqa: E402
from matplotlib.colors import same_color, to_rgba  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

import legendmodel as lm  # noqa: E402


def _line(**kw):
    return Line2D([0, 1], [0, 1], marker="s", **kw)


def test_same_color_markers_follow_and_undo_is_symmetric():
    h = _line(color="#7030a0", markerfacecolor="#7030a0", markeredgecolor="#7030a0")
    lm._handle_write(h, "handle_color", "orange")
    assert same_color(h.get_color(), "orange")
    assert same_color(h.get_markerfacecolor(), "orange")
    assert same_color(h.get_markeredgecolor(), "orange")
    # 读回仍是新线色；撤销把旧色写回，标记点跟着回去
    assert same_color(lm._handle_read(h, "handle_color"), "orange")
    lm._handle_write(h, "handle_color", "#7030a0")
    assert same_color(h.get_markerfacecolor(), "#7030a0")


def test_hollow_and_none_markers_stay():
    h = _line(color="b", markerfacecolor="white", markeredgecolor="b")
    lm._handle_write(h, "handle_color", "orange")
    assert same_color(h.get_markerfacecolor(), "white")
    assert same_color(h.get_markeredgecolor(), "orange")
    h2 = _line(color="b", markerfacecolor="none", markeredgecolor="r")
    lm._handle_write(h2, "handle_color", "orange")
    assert h2.get_markerfacecolor() == "none"
    assert same_color(h2.get_markeredgecolor(), "r")


def test_auto_marker_colors_are_not_pinned():
    h = _line(color="b")
    lm._handle_write(h, "handle_color", "orange")
    assert h._markerfacecolor == "auto" or h._markerfacecolor is None
    assert same_color(h.get_markerfacecolor(), "orange")


def test_patch_and_collection_handles_unchanged():
    p = Rectangle((0, 0), 1, 1, facecolor="b", edgecolor="k")
    lm._handle_write(p, "handle_color", "orange")
    assert same_color(p.get_facecolor(), "orange") and same_color(p.get_edgecolor(), "k")
    c = PathCollection([], facecolors=["b"], edgecolors=["k"])
    lm._handle_write(c, "handle_color", "orange")
    assert to_rgba("orange") == tuple(c.get_facecolor()[0])
    assert same_color(c.get_edgecolor()[0], "k")
