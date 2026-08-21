"""带数值标签的柱状图——helper/wrapper + Path 保存。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
from pathlib import Path

import matplotlib.pyplot as plt

OUTDIR = Path(".")


def draw(fig, ax):
    names = ["a", "b", "c", "d"]
    values = [3.0, 5.0, 2.0, 4.0]
    bars = ax.bar(names, values, color="#9BC4E2", edgecolor="#2F6FB2")
    for rect, v in zip(bars, values):
        ax.text(rect.get_x() + rect.get_width() / 2, v + 0.1, f"{v:.1f}",
                ha="center", fontsize=8)
    ax.set_title("Labelled bars")
    ax.set_ylabel("count")
    return fig


def save(fig, name):
    fig.savefig(OUTDIR / f"{name}.pdf")


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    save(draw(fig, ax), "mm_barlabel_helper")
