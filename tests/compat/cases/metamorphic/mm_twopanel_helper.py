"""左右两块面板——helper/wrapper + Path 保存。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUTDIR = Path(".")


def draw(fig, ax):
    X = np.linspace(0.0, 4.0, 30)
    ax.plot(X, X ** 2)
    ax.set_title("left")
    right = fig.add_subplot(1, 2, 2)
    right.plot(X, np.sqrt(X), color="#B4473C")
    right.set_title("right")
    fig.tight_layout()
    return fig


def save(fig, name):
    fig.savefig(OUTDIR / f"{name}.pdf")


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    save(draw(fig, ax), "mm_twopanel_helper")
