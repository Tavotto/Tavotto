"""导出修改后的 .py（ADR 0171）自己的扫描图库：`invariant_figures` 里没有的元素都加在这里。

`invariant_figures` 是全套不变式共用的那份，别的用例按编号写死了它的元素（`collections_6` …），
所以导出脚本要的图（多列图例、带框文字、箭头……）**只往这里追加**，一张图一个 stem，
加完把 stem 写进 `STEMS`。`tests/test_script_adjust.py` 的扫描与覆盖断言都读这里。
"""

from __future__ import annotations

SCRIPT_NAME = "fig_scriptadjust.py"
ENTRY = "main"
STEMS: tuple[str, ...] = ("AdjLegend", "AdjProxy", "AdjGrid", "AdjGrid3D", "AdjSeries")

LIBRARY = """\\
import numpy as np
import matplotlib.pyplot as plt


def main():
    rng = np.random.RandomState(0)
    x = np.linspace(0.5, 6.0, 24)

    # ---- AdjLegend：带标题的两列图例（标题、列距、锚点；InvMix / InvCont 的图例没有标题） ----
    # 条目来自曲线、散点与柱：示意线三种类型都在。图例放在左下、离曲线远一点，改间距时框变大不出画布。
    fig, ax = plt.subplots(figsize=(4.4, 3.2))
    ax.plot(x, np.sin(x) + 2.0, marker="o", markersize=5, label="sin")
    ax.plot(x, np.cos(x) + 2.0, linestyle="--", label="cos")
    ax.scatter(x, rng.rand(len(x)) + 3.2, s=30, marker="^", label="noise")
    ax.bar([1.0, 2.0, 3.0], [0.5, 0.8, 0.6], width=0.4, label="bars")
    ax.set_ylim(-1.8, 4.6)
    ax.legend(loc="lower left", ncol=2, title="Series", title_fontsize=9, fontsize=8)
    fig.savefig("AdjLegend.pdf")

    # ---- AdjProxy：代理示意线 + markerscale（图例条目没有可收集的源对象） ----
    # 重建只能从图例里现成的示意线出发，而那份已经乘过一次 markerscale：再交给 legend() 会再乘一次
    from matplotlib.lines import Line2D

    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    ax.plot(x, np.sin(x), color="#2a6f3c")
    proxy = Line2D([], [], color="#b34700", marker="o", markersize=6, linestyle="none")
    ax.legend([proxy], ["proxy"], loc="upper right", markerscale=2)
    fig.savefig("AdjProxy.pdf")

    # ---- AdjGrid：网格与边框（InvMix 的网格默认关；这张图只开了 x 网格、边框粗细不一、带镜像刻度）----
    # 整图扫描把 grid_x / grid_y 各翻一次：x 关、y 开，开着的那一边才看得出样式；样式改在两个轴上。
    fig, ax = plt.subplots(figsize=(4.4, 3.2))
    ax.plot(x, np.sin(x), label="sin")
    ax.plot(x, np.cos(x), label="cos")
    ax.grid(True, axis="x", color="#99aadd", linestyle="-.", linewidth=0.6)
    for side, w in (("top", 0.4), ("right", 1.6)):
        ax.spines[side].set_linewidth(w)
    ax.spines["left"].set_edgecolor("#aa3333")
    ax.tick_params(top=True, right=True)
    fig.savefig("AdjGrid.pdf")

    # ---- AdjGrid3D：3D 轴的网格开关（Inv3D 一族不进整图扫描，所以单独一张只有一条线的 3D 图） ----
    fig = plt.figure(figsize=(4.0, 3.4))
    ax3 = fig.add_subplot(projection="3d")
    ax3.plot(x, np.sin(x), np.cos(x), color="#336699")
    fig.savefig("AdjGrid3D.pdf")

    # ---- AdjSeries：茎压在图例框上 ----
    # 图例框的层级是 5、茎默认 2，在框下面；整图扫描把茎的 zorder 抬到 7.5，茎才压到框上。InvCont 里茎离图例很远，
    # 层级那一条在那里是隐形的（变异实测：写成常量扫描仍然绿）。
    fig, ax = plt.subplots(figsize=(4.4, 3.2))
    ax.stem([0.8, 1.6, 5.2, 5.7, 6.2], [1.0, 1.8, 3.3, 3.4, 3.5], label="stems")
    ax.set_xlim(0.2, 6.8)
    ax.set_ylim(0.0, 3.7)
    ax.legend(loc="upper right")
    fig.savefig("AdjSeries.pdf")
"""
