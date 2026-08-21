"""分布类：直方 / 二维直方 / 箱线 / 小提琴 / 事件 / 饼。

样本用确定性的等差数列而不是 np.random——随机数会让像素基线与
manifest 元素数在两次运行之间漂移。
"""
import matplotlib.pyplot as plt
import numpy as np

SAMPLE = np.concatenate([np.linspace(0.0, 3.0, 40), np.linspace(1.0, 2.0, 60)])
SAMPLE2 = np.concatenate([np.linspace(-1.0, 2.0, 50), np.linspace(0.0, 1.0, 50)])


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.hist(SAMPLE, bins=12, color="#2F6FB2", edgecolor="white")
    ax.set_title("Histogram")
    fig.savefig("art_hist.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    ax.hist2d(SAMPLE, SAMPLE2, bins=8, cmap="viridis")
    ax.set_title("2D histogram")
    fig.savefig("art_hist2d.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    # 不传 tick_labels：那个关键字是 matplotlib **3.9** 才有的（此前叫
    # labels）。corpus 必须能在 pyproject 宣称的下界（3.8）上跑起来，
    # 否则 minimum 档会把「我的 case 写错了」报成「Tavotto 不兼容」。
    ax.boxplot([SAMPLE, SAMPLE2])
    ax.set_xticks([1, 2], ["A", "B"])
    ax.set_title("Boxplot")
    fig.savefig("art_boxplot.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.violinplot([SAMPLE, SAMPLE2], showmeans=True)
    ax.set_title("Violinplot")
    fig.savefig("art_violinplot.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.eventplot([np.linspace(0, 1, 12), np.linspace(0.2, 0.9, 8)],
                 colors=["#2F6FB2", "#B4473C"])
    ax.set_title("Eventplot")
    fig.savefig("art_eventplot.pdf")

    fig, ax = plt.subplots(figsize=(3.0, 3.0))
    ax.pie([30, 25, 20, 25], labels=["a", "b", "c", "d"], autopct="%1.0f%%")
    ax.set_title("Pie")
    fig.savefig("art_pie.pdf")
