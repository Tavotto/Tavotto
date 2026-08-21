"""参考线与参考带：hlines/vlines、axhline/axvline、axhspan/axvspan。

hlines/vlines 出的是 LineCollection——这是长尾里最高频的一类 artist。
"""
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2, 3], [1, 3, 2, 4])
    ax.hlines([1.5, 2.5], xmin=0, xmax=3, colors="#B4473C", linestyles="--")
    ax.vlines([1.0, 2.0], ymin=1, ymax=4, colors="#5B8C5A")
    ax.set_title("hlines / vlines")
    fig.savefig("art_hlines_vlines.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2, 3], [1, 3, 2, 4])
    ax.axhline(2.0, color="#B4473C", linewidth=1.2, label="threshold")
    ax.axvline(1.5, color="#5B8C5A", linestyle=":")
    ax.set_title("axhline / axvline")
    ax.legend()
    fig.savefig("art_axhline_axvline.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2, 3], [1, 3, 2, 4])
    ax.axhspan(1.8, 2.6, color="#9BC4E2", alpha=0.4)
    ax.axvspan(0.8, 1.4, color="#E2C89B", alpha=0.4)
    ax.set_title("axhspan / axvspan")
    fig.savefig("art_axhspan_axvspan.pdf")
