"""均值曲线 + 误差带——helper/wrapper + Path 保存。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUTDIR = Path(".")


def draw(fig, ax):
    X = np.linspace(0.0, 5.0, 30)
    Y = np.sqrt(X)
    ax.plot(X, Y, color="#B4473C", label="mean")
    ax.fill_between(X, Y - 0.15, Y + 0.15, alpha=0.3, label="±sd")
    ax.set_title("Error band")
    ax.legend()
    return fig


def save(fig, name):
    fig.savefig(OUTDIR / f"{name}.pdf")


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    save(draw(fig, ax), "mm_errorband_helper")
