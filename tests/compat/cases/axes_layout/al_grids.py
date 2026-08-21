"""网格布局四种写法：subplots / GridSpec / subplot_mosaic / subplot2grid。"""
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec


def main():
    fig, axes = plt.subplots(2, 2, figsize=(4.6, 3.2))
    for k, ax in enumerate(axes.ravel()):
        ax.plot([0, 1, 2], [k, k + 1, k])
        ax.set_title(f"panel {k + 1}", fontsize=8)
    fig.suptitle("plt.subplots grid")
    fig.savefig("ax_subplots.pdf")

    fig = plt.figure(figsize=(4.6, 3.2))
    gs = GridSpec(2, 3, figure=fig)
    fig.add_subplot(gs[0, :2]).plot([1, 2, 3])
    fig.add_subplot(gs[0, 2]).plot([3, 2, 1])
    fig.add_subplot(gs[1, :]).plot([2, 1, 3])
    fig.suptitle("GridSpec")
    fig.savefig("ax_gridspec.pdf")

    fig, axd = plt.subplot_mosaic([["wide", "wide"], ["left", "right"]],
                                  figsize=(4.6, 3.2))
    axd["wide"].plot([1, 3, 2])
    axd["left"].scatter([1, 2], [2, 1])
    axd["right"].bar(["p", "q"], [2, 3])
    fig.suptitle("subplot_mosaic")
    fig.savefig("ax_mosaic.pdf")

    fig = plt.figure(figsize=(4.6, 3.2))
    plt.subplot2grid((2, 2), (0, 0), colspan=2).plot([1, 2, 3])
    plt.subplot2grid((2, 2), (1, 0)).plot([3, 1, 2])
    plt.subplot2grid((2, 2), (1, 1)).plot([2, 3, 1])
    fig.suptitle("subplot2grid")
    fig.savefig("ax_subplot2grid.pdf")
