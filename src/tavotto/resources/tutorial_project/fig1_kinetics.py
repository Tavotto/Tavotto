"""教程图 1：两条动力学曲线 + 图例（最典型的可编辑的图）。

在 Tavotto 里进入这张图的图内编辑，可以直接点中标题、坐标轴标签、图例、
任意一条曲线，改字号 / 颜色 / 线宽或拖动位置。改动存成 override，本文件
一个字都不改。
"""

import matplotlib.pyplot as plt
import numpy as np
from paper_style import COL_1, PALETTE, save


def main():
    t = np.linspace(0, 60, 200)
    fast = 1 - np.exp(-t / 8)
    slow = 1 - np.exp(-t / 24)

    fig, ax = plt.subplots(figsize=(COL_1, COL_1 * 0.72))
    # 边距按图幅定死：轴标签、刻度、标题都落在 figsize 之内。默认边距下
    # 轴标签会伸到图幅外——磁盘上的 PDF 靠 bbox_inches="tight" 把它救回来，
    # 但按图幅出图的地方（编辑器、导出）就会把它裁掉。
    fig.subplots_adjust(left=0.19, right=0.96, bottom=0.21, top=0.89)
    ax.plot(t, fast, color=PALETTE[0], lw=1.0, label="Catalyst (k = 0.125 min$^{-1}$)")
    ax.plot(t, slow, color=PALETTE[1], lw=1.0, ls="--", label="Blank (k = 0.042 min$^{-1}$)")
    ax.set_xlabel("Reaction time (min)")
    ax.set_ylabel("Conversion α (–)")
    ax.set_title("Reaction kinetics at 25 °C")
    ax.set_xlim(0, 60)
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower right")
    save(fig, "Fig1_kinetics")


if __name__ == "__main__":
    main()
