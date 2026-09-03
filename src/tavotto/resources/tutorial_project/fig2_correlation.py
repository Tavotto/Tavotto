"""教程图 2：散点 + 线性拟合 + 一条**故意**只有 7 pt 的说明文字。

那条 "n = 60" 的小字低于出版规范的 8 pt 下限——教程用它演示「检查」面板
怎样把问题定位到具体元素，以及怎样在图内把它改回合规字号。
"""

import matplotlib.pyplot as plt
import numpy as np
from paper_style import COL_1, PALETTE, save


def main():
    rng = np.random.default_rng(20260902)  # 固定种子：图是可复现的
    x = rng.normal(0, 1, 60)
    y = 0.8 * x + rng.normal(0, 0.5, 60)

    fig, ax = plt.subplots(figsize=(COL_1, COL_1 * 0.72))
    ax.scatter(x, y, s=12, color=PALETTE[0], alpha=0.75, label="Observed")
    fit = np.poly1d(np.polyfit(x, y, 1))
    xs = np.linspace(x.min(), x.max(), 50)
    ax.plot(xs, fit(xs), color=PALETTE[1], lw=1.2, label="Linear fit")
    r2 = 1 - np.sum((y - fit(x)) ** 2) / np.sum((y - y.mean()) ** 2)
    ax.text(0.97, 0.05, f"n = 60, R² = {r2:.2f}", transform=ax.transAxes, ha="right", fontsize=7)
    ax.set_xlabel("Normalised load (a.u.)")
    ax.set_ylabel("Normalised response (a.u.)")
    ax.set_title("Load–response correlation")
    ax.legend(loc="upper left")
    save(fig, "Fig2_correlation")


if __name__ == "__main__":
    main()
