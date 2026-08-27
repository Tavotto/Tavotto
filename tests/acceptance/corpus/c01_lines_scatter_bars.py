"""验收 corpus 之一：最常见的三类图 + 误差棒。

这些脚本是**视觉基线的输入**，所以有两条与普通示例不同的纪律：

* **一切数值都写死**，不用随机数。哪怕 `np.random.seed(0)` 固定了序列，
  numpy 换代时 Generator 的实现仍可能变，那会让整片 corpus 在一次无关的
  依赖升级里同时变红，而没有任何一处产品代码改动——这种误报会直接摧毁
  这条门禁的可信度。
* **不 import paper_style**。它是图库方言不是引擎依赖（见 CLAUDE.md），
  corpus 要验的是 Tavotto 对**任意** matplotlib 脚本的处理能力，
  而不是对某一份样式模块的处理能力。savefig 由 worker 拦截。
"""

import matplotlib.pyplot as plt
import numpy as np


def _lin(n, lo, hi):
    """确定性的等间距序列——比 linspace 更明确地表达「这里没有随机」。"""
    return np.linspace(lo, hi, n)


def line():
    """基础折线：两条曲线 + 图例 + 网格。覆盖最常见的可参数化面板形态。"""
    x = _lin(120, 0, 10)
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.plot(x, np.sin(x), lw=1.3, color="#1f4e79", label="sin")
    ax.plot(x, np.cos(x) * 0.7, lw=1.3, ls="--", color="#c1440e", label="0.7 cos")
    ax.set_xlabel("x")
    ax.set_ylabel("amplitude")
    ax.set_title("Basic line")
    ax.grid(True, lw=0.4, alpha=0.4)
    ax.legend(loc="upper right", frameon=True)
    fig.savefig("c01_line.pdf")


def scatter():
    """散点：manifest 刻意**不给** geometry（见 CLAUDE.md 的路径几何一节），
    所以这个 case 同时看护「散点降级为 bbox」那条约定还在生效。"""
    x = _lin(40, 0, 4)
    y = x**1.6 - 2.0
    sizes = 12 + (x * 6)
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.scatter(x, y, s=sizes, c="#2a6f4e", alpha=0.75, edgecolors="none", label="run A")
    ax.scatter(x, -y * 0.5, s=18, c="#a8331c", marker="^", label="run B")
    ax.set_xlabel("dose")
    ax.set_ylabel("response")
    ax.set_title("Scatter with size mapping")
    ax.legend(loc="upper left")
    fig.savefig("c01_scatter.pdf")


def bar():
    """分组柱状：bar_series 在 manifest 里是伪元素，实测不可预览必须回退后端。"""
    # 标签刻意用英文：中文的覆盖由 c03_cjk 专门承担，那里会显式声明 CJK 字体。
    # 这里混中文的话，没装/没声明字体的机器上会画成一排方框，而这个 case
    # 要验的是分组柱状的几何，不是字体回退。
    labels = ["ctrl", "low", "mid", "high"]
    a = [12.0, 18.5, 24.0, 29.5]
    b = [10.5, 15.0, 21.5, 26.0]
    idx = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.bar(idx - 0.18, a, width=0.34, color="#1f4e79", label="2024")
    ax.bar(idx + 0.18, b, width=0.34, color="#8fb3d9", label="2025")
    ax.set_xticks(idx)
    ax.set_xticklabels(labels)
    ax.set_ylabel("yield (%)")
    ax.set_title("Grouped bars")
    ax.legend()
    fig.savefig("c01_bar.pdf")


def errorbar():
    """误差棒：errorbar.* 同样是 manifest 伪元素，gid 在 SVG 里不存在。"""
    x = _lin(8, 1, 8)
    y = np.array([2.1, 3.4, 4.0, 5.2, 5.9, 6.1, 6.8, 7.0])
    yerr = np.array([0.2, 0.25, 0.3, 0.22, 0.35, 0.28, 0.4, 0.3])
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.errorbar(
        x,
        y,
        yerr=yerr,
        fmt="o-",
        lw=1.1,
        ms=4,
        color="#4a2c6b",
        ecolor="#9b86b8",
        capsize=3,
        label="mean ± sd",
    )
    ax.set_xlabel("cycle")
    ax.set_ylabel("capacity")
    ax.set_title("Errorbar")
    ax.legend()
    fig.savefig("c01_errorbar.pdf")


def main():
    line()
    scatter()
    bar()
    errorbar()


if __name__ == "__main__":
    main()
