"""双曲线折线图 + 图例——helper/wrapper + Path 保存。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUTDIR = Path(".")


def draw(fig, ax):
    X = np.linspace(0.0, 6.0, 40)
    ax.plot(X, np.sin(X), label="sin", color="#2F6FB2")
    ax.plot(X, np.cos(X), label="cos", color="#B4473C", linestyle="--")
    ax.set_title("Two curves")
    ax.set_xlabel("x (rad)")
    ax.set_ylabel("amplitude")
    ax.legend()
    return fig


def save(fig, name):
    fig.savefig(OUTDIR / f"{name}.pdf")


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    save(draw(fig, ax), "mm_twoline_helper")
