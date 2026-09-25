"""GEO 节的 G1 / G2 夹具脚本（matplotlib 用户脚本，由 Tavotto worker 执行）。

确定性：没有随机数，所有坐标写死。三个 stem：

* ``G1``  —— 简单几何：已知物理图幅 127 × 81.28 mm（5.0 × 3.2 in），
  单标题（左对齐，非对称）、一条曲线、图例（右下）、一条注释文字（左上偏）、
  一个独立形状（FancyBboxPatch，非对称位置）、一条独立箭头。
* ``G2``  —— 混合场景：两个 Axes（左右并排，左边带 twinx 孪生轴）、多段文字、
  两个图例、父子跟随（标题/轴标签是 Axes 的孩子）、两个面板同名的 ``axes_N.title``。
  锁定对象是**文档层**属性（``panel.lockedGids``），不在脚本里。
* ``G2big`` —— 规模：一个 Axes 上 100 条独立 ``ax.text``（GEO-02 的 100 成员档）。

哪些可编辑、哪些应跟随（执行前写定，见 ledger 的 expected_outcome）：
  可拖（drag_prop=pos_frac/loc_frac）：标题、轴标签、ax.text、图例、形状；
  可移动/缩放（position）：Axes；
  跟随：Axes 的标题/轴标签/图例嵌在 Axes 的 <g> 里，未被单独摆过时随 Axes 走；
  twinx 孪生轴由 manifest 的 follow_gids 点名。
"""

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

matplotlib.use("Agg")

X = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
Y = [0.0, 9.0, 2.0, 10.0, 4.0, 7.0]


def g1():
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    fig.subplots_adjust(left=0.14, right=0.93, bottom=0.17, top=0.86)
    ax.plot(X, Y, color="#1f77b4", lw=1.5, label="signal")
    ax.set_title("G1 title", loc="left")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("value")
    ax.legend(loc="lower right")
    ax.text(0.4, 8.2, "note A", color="#123456")
    ax.add_patch(
        FancyBboxPatch((3.3, 0.6), 0.9, 1.4, boxstyle="round,pad=0.05", fc="#ffdd99", ec="#444444")
    )
    ax.add_patch(FancyArrowPatch((1.2, 6.0), (2.4, 3.0), arrowstyle="-|>", mutation_scale=10))
    fig.savefig("G1.pdf")
    plt.close(fig)


def g2():
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(6.4, 3.0))
    fig.subplots_adjust(left=0.1, right=0.88, bottom=0.18, top=0.84, wspace=0.45)
    a0.plot(X, Y, label="left")
    tw = a0.twinx()
    tw.plot(X, [v / 10 for v in Y], color="#d62728", label="twin")
    a0.set_title("panel a")
    a0.set_xlabel("x0")
    a0.legend(loc="upper left")
    a0.text(0.5, 1.0, "t0")
    a0.text(3.5, 8.5, "t1")
    a1.plot(X, list(reversed(Y)), label="right")
    a1.set_title("panel b")
    a1.set_ylabel("y1")
    a1.legend(loc="lower left")
    a1.text(1.0, 2.0, "t2")
    a1.add_patch(FancyBboxPatch((3.0, 5.0), 1.0, 2.0, boxstyle="square,pad=0.0", fc="#cce5ff"))
    fig.savefig("G2.pdf")
    plt.close(fig)


def g2big():
    fig, ax = plt.subplots(figsize=(6.0, 6.0))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    for i in range(100):
        ax.text(0.3 + (i % 10) * 0.95, 0.3 + (i // 10) * 0.95, f"n{i:02d}", fontsize=6)
    fig.savefig("G2big.pdf")
    plt.close(fig)


def main():
    g1()
    g2()
    g2big()


if __name__ == "__main__":
    main()
