"""示例 1：两条动力学曲线 + 图例（最典型的可参数化面板）。

在 Tavotto 里双击这张图，可以直接点中标题、坐标轴标签、图例、任意一条曲线，
改字号/颜色/线宽或拖动位置——改动存成 override，本文件一个字都不会被动。
"""
import numpy as np

import matplotlib.pyplot as plt
from paper_style import COL_1, PALETTE, save


def main():
    t = np.linspace(0, 60, 200)
    fast = 1 - np.exp(-t / 8)
    slow = 1 - np.exp(-t / 24)

    fig, ax = plt.subplots(figsize=(COL_1, COL_1 * 0.72))
    ax.plot(t, fast, color=PALETTE[0], lw=1.2, label="Catalyst (k = 0.125 $\\mathrm{min^{-1}}$)")
    ax.plot(t, slow, color=PALETTE[1], lw=1.2, ls="--", label="Blank (k = 0.042 $\\mathrm{min^{-1}}$)")
    ax.set_xlabel("Reaction time (min)")
    ax.set_ylabel("Conversion")
    ax.set_title("Reaction kinetics")
    ax.set_xlim(0, 60)
    ax.set_ylim(0, 1.05)
    ax.legend(loc="lower right")
    save(fig, "Fig1_kinetics")


if __name__ == "__main__":
    main()
