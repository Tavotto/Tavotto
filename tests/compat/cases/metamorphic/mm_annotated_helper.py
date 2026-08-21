"""曲线 + annotate 箭头——helper/wrapper + Path 保存。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。
"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUTDIR = Path(".")


def draw(fig, ax):
    T = np.linspace(0.0, 6.0, 40)
    ax.plot(T, np.exp(-T / 3.0), color="#2F6FB2")
    ax.annotate("decay", xy=(2.0, 0.51), xytext=(3.4, 0.80),
                arrowprops=dict(arrowstyle="->", color="#2A6F3C"))
    ax.set_title("Annotated decay")
    ax.set_xlabel("t (s)")
    return fig


def save(fig, name):
    fig.savefig(OUTDIR / f"{name}.pdf")


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    save(draw(fig, ax), "mm_annotated_helper")
