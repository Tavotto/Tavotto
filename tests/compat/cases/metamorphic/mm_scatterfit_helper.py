"""散点 + 拟合线——helper/wrapper + Path 保存。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUTDIR = Path(".")


def draw(fig, ax):
    X = np.linspace(0.0, 5.0, 20)
    Y = 1.8 * X + 0.6
    ax.scatter(X, Y + np.array([0.3, -0.2] * 10), s=18, label="data")
    ax.plot(X, Y, color="#5B8C5A", label="fit")
    ax.set_title("Scatter with fit")
    ax.set_xlabel("dose")
    ax.set_ylabel("response")
    ax.legend()
    return fig


def save(fig, name):
    fig.savefig(OUTDIR / f"{name}.pdf")


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    save(draw(fig, ax), "mm_scatterfit_helper")
