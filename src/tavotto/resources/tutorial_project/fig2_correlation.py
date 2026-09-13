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
    # 边距按图幅定死：轴标签、刻度、标题都落在 figsize 之内。默认边距下
    # 轴标签会伸到图幅外——磁盘上的 PDF 靠 bbox_inches="tight" 把它救回来，
    # 但按图幅出图的地方（编辑器、导出）就会把它裁掉。
    fig.subplots_adjust(left=0.19, right=0.95, bottom=0.21, top=0.89)
    ax.scatter(x, y, s=12, color=PALETTE[0], alpha=0.75, label="Observed")
    coef, cov = np.polyfit(x, y, 1, cov=True)
    fit = np.poly1d(coef)
    xs = np.linspace(x.min(), x.max(), 50)
    # 拟合线的 95% 置信带：投稿时通常要求给出拟合的不确定度
    se = np.sqrt(cov[0, 0] * xs**2 + 2 * cov[0, 1] * xs + cov[1, 1])
    ax.fill_between(
        xs, fit(xs) - 1.96 * se, fit(xs) + 1.96 * se, color=PALETTE[1], alpha=0.15, lw=0
    )
    ax.plot(xs, fit(xs), color=PALETTE[1], lw=1.0, label="Linear fit")
    r2 = 1 - np.sum((y - fit(x)) ** 2) / np.sum((y - y.mean()) ** 2)
    ax.text(0.97, 0.05, f"n = 60, R² = {r2:.2f}", transform=ax.transAxes, ha="right", fontsize=7)
    ax.set_xlabel("Normalised load (a.u.)")
    ax.set_ylabel("Normalised response (a.u.)")
    ax.set_title("Load–response correlation")
    ax.legend(loc="upper left")
    save(fig, "Fig2_correlation")


if __name__ == "__main__":
    main()
