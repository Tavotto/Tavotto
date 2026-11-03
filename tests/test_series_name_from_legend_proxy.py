"""系列线没有自己的 label、图例用代理句柄时，显示名借图例条目的文字。

科研脚本常见写法：`ax.plot(..., color=c, marker=m)` 不写 label，图例另用
`Line2D([], [], color=c, marker=m, label="pH=1")` 代理句柄再 `ax.legend(handles=...)`。
元素树里本来是一串「曲线 N」，用户分不出哪条是 pH=1。

合同（判据在 `legendmodel.legend_proxy_names`，只改显示名，不改 gid / 字段）：

1. 图例里恰好一个代理句柄与该线的示意线样式精确一致 → 「曲线 “文字”」，散点同理；
2. 歧义不命名：多条线命中同一个句柄、或一条线命中多个条目；
3. 样式差一项（marker 面色 / 边宽 / 线宽）不命名；
4. 有自己 label 的线、以及图例句柄就是该线本身的正常路径，名字不变；
5. gid 不因命名而变（用例里的 gid 断言就是序号原样）。

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

ENTRY = "main"

#: 每个场景一张图；返回的 fig 存成 `Fig.pdf`
SCRIPTS = {
    # 正例：两条线（各自样式不同）+ 一条图例里没有的线 + 一个散点，图例全用代理句柄
    "positive": """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    x = np.arange(5.0)
    ax.plot(x, x, color="#c0392b", marker="o", markerfacecolor="white", lw=1.8, markersize=5.5)
    ax.plot(x, x + 1, color="#2980b9", marker="s", markerfacecolor="white", lw=1.8, markersize=5.5)
    ax.plot(x, x + 2, color="#777777", lw=0.7, linestyle="--")
    ax.scatter(x, x + 3, color="#27ae60", marker="^", s=30)
    proxy = ax.scatter([], [], color="#27ae60", marker="^", s=30, label="pts")
    proxy.remove()  # 只借它当图例句柄，不留在图里
    ax.legend(handles=[
        Line2D([], [], color="#c0392b", marker="o", markerfacecolor="white", lw=1.8, markersize=5.5, label="pH=1"),
        Line2D([], [], color="#2980b9", marker="s", markerfacecolor="white", lw=1.8, markersize=5.5, label="pH=5"),
        proxy,
    ])
    fig.savefig("Fig.pdf")
""",
    # 歧义 A：两条线样式完全一样，图例只有一个句柄
    "ambiguous_lines": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r", marker="o")
    ax.plot([0, 1], [1, 2], color="r", marker="o")
    ax.legend(handles=[Line2D([], [], color="r", marker="o", label="same")])
    fig.savefig("Fig.pdf")
""",
    # 歧义 B：一条线，图例里有两个样式一样的代理句柄（文字不同）
    "ambiguous_entries": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r", marker="o")
    ax.legend(handles=[
        Line2D([], [], color="r", marker="o", label="a"),
        Line2D([], [], color="r", marker="o", label="b"),
    ])
    fig.savefig("Fig.pdf")
""",
    # 样式差一项：marker 面色 / 边宽 / 线宽各差一条
    "style_off_by_one": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r", marker="o", markerfacecolor="white")
    ax.plot([0, 1], [1, 2], color="b", marker="o", markeredgewidth=2.0)
    ax.plot([0, 1], [2, 3], color="g", linewidth=2.0)
    ax.legend(handles=[
        Line2D([], [], color="r", marker="o", markerfacecolor="yellow", label="face"),
        Line2D([], [], color="b", marker="o", markeredgewidth=1.0, label="edge"),
        Line2D([], [], color="g", linewidth=1.5, label="width"),
    ])
    fig.savefig("Fig.pdf")
""",
    # 散点同色、只差 marker 形状 / 只差大小：不命名
    "scatter_shape_off": """\
import matplotlib.pyplot as plt

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.scatter([0, 1], [0, 1], color="g", marker="^", s=30)
    ax.scatter([0, 1], [1, 2], color="b", marker="s", s=30)
    shape = ax.scatter([], [], color="g", marker="o", s=30, label="circle")
    size = ax.scatter([], [], color="b", marker="s", s=80, label="big")
    shape.remove()
    size.remove()
    ax.legend(handles=[shape, size])
    fig.savefig("Fig.pdf")
""",
    # marker 半填充方向不同 / 另一半颜色不同：不命名
    "fillstyle_off": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r", marker="o", fillstyle="left")
    ax.plot([0, 1], [1, 2], color="b", marker="o", fillstyle="left", markerfacecoloralt="y")
    ax.legend(handles=[
        Line2D([], [], color="r", marker="o", fillstyle="right", label="dir"),
        Line2D([], [], color="b", marker="o", fillstyle="left", markerfacecoloralt="g", label="alt"),
    ])
    fig.savefig("Fig.pdf")
""",
    # 虚线间隙色 / 线帽不同：不命名（全部可见样式参与比对，不靠逐维列举）
    "gap_cap_off": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r", linestyle="--", gapcolor="k")
    ax.plot([0, 1], [1, 2], color="b", lw=6, solid_capstyle="round")
    ax.legend(handles=[
        Line2D([], [], color="r", linestyle="--", gapcolor="y", label="gap"),
        Line2D([], [], color="b", lw=6, solid_capstyle="butt", label="cap"),
    ])
    fig.savefig("Fig.pdf")
""",
    # 自定义虚线序列不同（properties() 都报 "--"）：不命名
    "dash_seq_off": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r", linestyle=(0, (3, 2)))
    ax.legend(handles=[Line2D([], [], color="r", linestyle=(0, (5, 3)), label="dash")])
    fig.savefig("Fig.pdf")
""",
    # 类型级自定义 handler（一格两条线，第一条恒为黑）：说不清整格长相，整张图例不借名
    "custom_handler": """\
import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D

class TwoLines(HandlerBase):
    def create_artists(self, legend, orig, x0, y0, w, h, fs, trans):
        a = Line2D([x0, x0 + w], [y0, y0], color="k", transform=trans)
        b = Line2D([x0, x0 + w], [y0 + h, y0 + h], color=orig.get_color(), transform=trans)
        return [a, b]

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="b")
    ax.legend(handles=[Line2D([], [], color="r", label="red")], handler_map={Line2D: TwoLines()})
    fig.savefig("Fig.pdf")
""",
    # HandlerTuple（matplotlib 自带、一格画两条线）：第一条与无名黑线一样，整格长相不同，不借名；
    # 同一张图例里普通的单 artist 代理照常借名
    "tuple_handler": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="k")
    ax.plot([0, 1], [1, 2], color="b", marker="s")
    k = Line2D([], [], color="k")
    r = Line2D([], [], color="r")
    b = Line2D([], [], color="b", marker="s")
    ax.legend(handles=[(k, r), b], labels=["tup", "blue"])
    fig.savefig("Fig.pdf")
""",
    # markerfirst=False（文字在示意前）：照常借名，tuple 格照常不借
    "marker_last": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="k")
    ax.plot([0, 1], [1, 2], color="b", marker="s")
    k = Line2D([], [], color="k")
    r = Line2D([], [], color="r")
    b = Line2D([], [], color="b", marker="s")
    ax.legend(handles=[(k, r), b], labels=["tup", "blue"], markerfirst=False)
    fig.savefig("Fig.pdf")
""",
    # 系列侧的类型级 handler 一格画两条线，代理按实例用单线 handler：两侧格子长相不同，不借名
    "source_multi_cell": """\
import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerBase, HandlerLine2D
from matplotlib.lines import Line2D

class TwoLines(HandlerBase):
    def create_artists(self, legend, orig, x0, y0, w, h, fs, trans):
        a = Line2D([x0, x0 + w], [y0, y0], color=orig.get_color(), transform=trans)
        b = Line2D([x0, x0 + w], [y0 + h, y0 + h], color="m", transform=trans)
        return [a, b]

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r")
    proxy = Line2D([], [], color="r")
    ax.legend(handles=[proxy], labels=["red"], handler_map={Line2D: TwoLines(), proxy: HandlerLine2D()})
    fig.savefig("Fig.pdf")
""",
    # 代理 visible=False：图例里只有字没有示意，与看得见的线长相不同，不借名
    "invisible_proxy": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r")
    ax.legend(handles=[Line2D([], [], color="r", visible=False, label="ghost")])
    fig.savefig("Fig.pdf")
""",
    # 颜色写法不同但画出来一样（"red" vs "#ff0000"）：照常借名
    "color_spelling": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="red")
    ax.legend(handles=[Line2D([], [], color="#ff0000", label="r")])
    fig.savefig("Fig.pdf")
""",
    # 多子图共用的 fig.legend：两个子图里的线都按图级图例借名
    "fig_legend": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(6, 3))
    a1.plot([0, 1], [0, 1], color="r", marker="o")
    a2.plot([0, 1], [1, 0], color="b", marker="s")
    fig.legend(handles=[
        Line2D([], [], color="r", marker="o", label="red"),
        Line2D([], [], color="b", marker="s", label="blue"),
    ])
    fig.savefig("Fig.pdf")
""",
    # a1 的图例句柄其实是 a2 里带 label 的线；a1 里同样长相的无名线不能借它的字
    "cross_axes_source": """\
import matplotlib.pyplot as plt

def main():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(6, 3))
    a1.plot([0, 1], [0, 1], color="g")
    (other,) = a2.plot([0, 1], [1, 0], color="g", label="from a2")
    a1.legend(handles=[other])
    fig.savefig("Fig.pdf")
""",
    # 共用图例挂在第一个子图上，描述两个子图里的线；两张图例对同一条线给不同的字则不命名
    "shared_axes_legend": """\
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

def main():
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(8, 3))
    a1.plot([0, 1], [0, 1], color="r", marker="o")
    a2.plot([0, 1], [1, 0], color="b", marker="s")
    a3.plot([0, 1], [0, 1], color="m", marker="^")
    a1.legend(handles=[
        Line2D([], [], color="r", marker="o", label="red"),
        Line2D([], [], color="b", marker="s", label="blue"),
        Line2D([], [], color="m", marker="^", label="one"),
    ])
    a3.legend(handles=[Line2D([], [], color="m", marker="^", label="two")])
    fig.savefig("Fig.pdf")
""",
    # 自定义单 artist handler：线一律黑色，走向随源（蓝 → 水平、红 → 斜线）——长相不同，不借名
    "custom_geometry": """\
import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D

class Slant(HandlerBase):
    def create_artists(self, legend, orig, x0, y0, w, h, fs, trans):
        y1 = y0 + h if orig.get_color() == "r" else y0
        return [Line2D([x0, x0 + w], [y0, y1], color="k", transform=trans)]

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="b")
    ax.legend(handles=[Line2D([], [], color="r", label="red")], handler_map={Line2D: Slant()})
    fig.savefig("Fig.pdf")
""",
    # 有自己 label 的线与普通 ax.legend()：行为不变
    "own_label": """\
import matplotlib.pyplot as plt

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="r", label="alpha")
    ax.plot([0, 1], [1, 2], color="b")
    ax.plot([0, 1], [2, 3], color="r")  # 与 alpha 同样式、无 label：别借 alpha 条目的字
    ax.legend()
    fig.savefig("Fig.pdf")
""",
}


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("series-proxy-name")
    for name, src in SCRIPTS.items():
        (d / f"{name}.py").write_text(src, encoding="utf-8")
    return d


def _labels(figs, name):
    w = pool.one_shot(f"{name}.py", str(figs), ENTRY)
    w.ensure_built()
    try:
        man = w.override("Fig", [])["manifest"]
    finally:
        pool.discard(w)
    return {e["gid"]: e["label"] for e in man["elements"] if e["role"] in ("line", "scatter")}


def test_proxy_handle_names_line_and_scatter(figs):
    got = _labels(figs, "positive")
    assert got["axes_0.lines_0"] == "曲线 “pH=1”"
    assert got["axes_0.lines_1"] == "曲线 “pH=5”"
    assert got["axes_0.lines_2"] == "曲线 3", "图例里没有对应句柄的线保持原样"
    assert got["axes_0.scatter_0"] == "散点 “pts”"


def test_ambiguous_lines_stay_unnamed(figs):
    got = _labels(figs, "ambiguous_lines")
    assert got == {"axes_0.lines_0": "曲线 1", "axes_0.lines_1": "曲线 2"}


def test_one_line_matching_two_entries_stays_unnamed(figs):
    assert _labels(figs, "ambiguous_entries") == {"axes_0.lines_0": "曲线 1"}


def test_style_off_by_one_stays_unnamed(figs):
    got = _labels(figs, "style_off_by_one")
    assert got == {
        "axes_0.lines_0": "曲线 1",
        "axes_0.lines_1": "曲线 2",
        "axes_0.lines_2": "曲线 3",
    }


def test_own_label_untouched(figs):
    got = _labels(figs, "own_label")
    assert got["axes_0.lines_0"] == "曲线 “alpha”"
    assert got["axes_0.lines_1"] == "曲线 2"
    assert got["axes_0.lines_2"] == "曲线 3"


def test_scatter_marker_or_size_off_stays_unnamed(figs):
    assert _labels(figs, "scatter_shape_off") == {
        "axes_0.scatter_0": "散点系列 1",
        "axes_0.scatter_1": "散点系列 2",
    }


def test_marker_fillstyle_or_alt_color_off_stays_unnamed(figs):
    assert _labels(figs, "fillstyle_off") == {
        "axes_0.lines_0": "曲线 1",
        "axes_0.lines_1": "曲线 2",
    }


def test_gapcolor_or_capstyle_off_stays_unnamed(figs):
    assert _labels(figs, "gap_cap_off") == {"axes_0.lines_0": "曲线 1", "axes_0.lines_1": "曲线 2"}


def test_custom_dash_sequence_off_stays_unnamed(figs):
    assert _labels(figs, "dash_seq_off") == {"axes_0.lines_0": "曲线 1"}


def test_custom_handler_legend_lends_no_names(figs):
    assert _labels(figs, "custom_handler") == {"axes_0.lines_0": "曲线 1"}


def test_multi_artist_cell_lends_no_name(figs):
    assert _labels(figs, "tuple_handler") == {
        "axes_0.lines_0": "曲线 1",
        "axes_0.lines_1": "曲线 “blue”",
    }


def test_marker_last_legend_still_reads_cells(figs):
    assert _labels(figs, "marker_last") == {
        "axes_0.lines_0": "曲线 1",
        "axes_0.lines_1": "曲线 “blue”",
    }


def test_multi_artist_source_cell_borrows_no_name(figs):
    assert _labels(figs, "source_multi_cell") == {"axes_0.lines_0": "曲线 1"}


def test_invisible_proxy_lends_no_name(figs):
    assert _labels(figs, "invisible_proxy") == {"axes_0.lines_0": "曲线 1"}


def test_equivalent_color_spelling_still_borrows(figs):
    assert _labels(figs, "color_spelling") == {"axes_0.lines_0": "曲线 “r”"}


def test_figure_legend_names_series_in_every_axes(figs):
    assert _labels(figs, "fig_legend") == {
        "axes_0.lines_0": "曲线 “red”",
        "axes_1.lines_0": "曲线 “blue”",
    }


def test_handle_from_another_axes_is_not_lent(figs):
    got = _labels(figs, "cross_axes_source")
    assert got["axes_0.lines_0"] == "曲线 1"
    assert got["axes_1.lines_0"] == "曲线 “from a2”"


def test_shared_legend_on_first_axes_names_other_axes(figs):
    assert _labels(figs, "shared_axes_legend") == {
        "axes_0.lines_0": "曲线 “red”",
        "axes_1.lines_0": "曲线 “blue”",
        "axes_2.lines_0": "曲线 1",  # 两张图例给了 one / two：冲突不命名
    }


def test_custom_handler_cell_geometry_off_stays_unnamed(figs):
    assert _labels(figs, "custom_geometry") == {"axes_0.lines_0": "曲线 1"}
