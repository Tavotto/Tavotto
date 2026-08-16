"""示例 2：一个脚本出两张面板（柱形 + 散点）。

一脚本多产物是常态：注册表按 stem 把每个产物映射回本脚本，
两张面板都能独立进编辑态。
"""
import numpy as np

import matplotlib.pyplot as plt
from paper_style import COL_1, PALETTE, save


def main():
    _bars()
    _scatter()


def _bars():
    groups = ["A", "B", "C", "D"]
    values = [3.2, 4.8, 2.6, 5.9]
    errs = [0.3, 0.4, 0.25, 0.5]

    fig, ax = plt.subplots(figsize=(COL_1 * 0.8, COL_1 * 0.62))
    ax.bar(groups, values, yerr=errs, capsize=2.5, width=0.6,
           color=PALETTE, edgecolor="black", linewidth=0.5)
    ax.set_ylabel("Yield (mg $\\mathrm{h^{-1}}$)")
    ax.set_title("Sample comparison")
    ax.set_ylim(0, 7)
    save(fig, "Fig2_yield")


def _scatter():
    rng = np.random.default_rng(20260816)   # 固定种子：图是可复现的
    x = rng.normal(0, 1, 60)
    y = 0.8 * x + rng.normal(0, 0.5, 60)

    fig, ax = plt.subplots(figsize=(COL_1 * 0.8, COL_1 * 0.62))
    ax.scatter(x, y, s=12, color=PALETTE[0], alpha=0.75, label="Observed")
    fit = np.poly1d(np.polyfit(x, y, 1))
    xs = np.linspace(x.min(), x.max(), 50)
    ax.plot(xs, fit(xs), color=PALETTE[1], lw=1.2, label="Linear fit")
    ax.set_xlabel("Normalised load")
    ax.set_ylabel("Normalised response")
    ax.set_title("Correlation")
    ax.legend(loc="upper left")
    save(fig, "Fig2_correlation")


if __name__ == "__main__":
    main()
