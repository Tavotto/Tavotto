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
