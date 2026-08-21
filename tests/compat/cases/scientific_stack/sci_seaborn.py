"""Seaborn 的六种常见图。

Seaborn 生成的是 collections / containers / patches 的组合，是 artist 长尾
最集中的一处——CompatBench 的价值正在这里：分清 renderable / recognized /
editable，而不是硬给每个 artist 写 handler。
"""
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

DF = pd.DataFrame({
    "x": [1, 2, 3, 4, 5, 6] * 2,
    "y": [1.0, 2.0, 1.5, 3.0, 2.5, 4.0, 2.0, 1.0, 2.5, 2.0, 3.5, 3.0],
    "group": ["a"] * 6 + ["b"] * 6,
})
WIDE = pd.DataFrame([[1.0, 2.0, 3.0], [2.0, 3.0, 1.0], [3.0, 1.0, 2.0]],
                    index=["r1", "r2", "r3"], columns=["c1", "c2", "c3"])


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    # 同上：lineplot 在同一个 x 上有多个 y 时也会 bootstrap，显式关掉。
    sns.lineplot(data=DF, x="x", y="y", hue="group", errorbar=None, ax=ax)
    ax.set_title("sns.lineplot")
    fig.savefig("sci_sns_line.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    sns.scatterplot(data=DF, x="x", y="y", hue="group", ax=ax)
    ax.set_title("sns.scatterplot")
    fig.savefig("sci_sns_scatter.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    # `errorbar="sd"` 而不是默认的 ("ci", 95)：默认那档用 **bootstrap 重采样**
    # 算置信区间，`seed=None` 时每次跑出来的误差棒长度都不同。
    # corpus 的硬约定是「数据确定性」——CompatBench 自己就是这么抓到它的：
    # 同一份脚本在热会话与全新 worker 里画出的 lines_0 高度不一样，报成
    # replay 分歧。那不是 Tavotto 的 bug，是这条 case 写错了。
    sns.barplot(data=DF, x="group", y="y", errorbar="sd", ax=ax)
    ax.set_title("sns.barplot")
    fig.savefig("sci_sns_bar.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    sns.heatmap(WIDE, annot=True, ax=ax, cmap="viridis")
    ax.set_title("sns.heatmap")
    fig.savefig("sci_sns_heatmap.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    sns.boxplot(data=DF, x="group", y="y", ax=ax)
    ax.set_title("sns.boxplot")
    fig.savefig("sci_sns_box.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    sns.violinplot(data=DF, x="group", y="y", ax=ax)
    ax.set_title("sns.violinplot")
    fig.savefig("sci_sns_violin.pdf")
