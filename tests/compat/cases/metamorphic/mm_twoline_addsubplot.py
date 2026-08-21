"""双曲线折线图 + 图例——Figure + add_subplot（不经 pyplot）。

同一张视觉结果，只换代码组织方式。同族之间**绘图正文逐字相同**，
Tavotto 的兼容表现不该因为写法而剧烈波动。

这一支**完全不 import pyplot**：纯 Figure 对象，没有 figure 管理器，
因此 pyplot 兜底一张都不该多捕获。
"""
from matplotlib.figure import Figure
import numpy as np


def build_figure():
    fig = Figure(figsize=(3.8, 2.4))
    ax = fig.add_subplot(1, 1, 1)
    X = np.linspace(0.0, 6.0, 40)
    ax.plot(X, np.sin(X), label="sin", color="#2F6FB2")
    ax.plot(X, np.cos(X), label="cos", color="#B4473C", linestyle="--")
    ax.set_title("Two curves")
    ax.set_xlabel("x (rad)")
    ax.set_ylabel("amplitude")
    ax.legend()
    fig.savefig("mm_twoline_addsubplot.pdf")
