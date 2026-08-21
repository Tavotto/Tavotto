"""标题族：suptitle / 左右标题 / 轴标签 labelpad。"""
import matplotlib.pyplot as plt


def main():
    fig, axes = plt.subplots(1, 2, figsize=(4.6, 2.2))
    axes[0].plot([1, 2, 3])
    axes[1].plot([3, 2, 1])
    fig.suptitle("Figure level suptitle")
    axes[0].set_title("left panel")
    fig.savefig("ax_suptitle.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    ax.plot([1, 3, 2])
    ax.set_title("centre")
    ax.set_title("(a)", loc="left")
    ax.set_title("n = 12", loc="right")
    ax.set_xlabel("x", labelpad=10)
    ax.set_ylabel("y", labelpad=12)
    fig.savefig("ax_title_left_right.pdf")
