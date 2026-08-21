"""图例与色条：axes legend / figure legend / 单色条 / 多色条。"""
import matplotlib.pyplot as plt
import numpy as np

GRID = np.arange(36, dtype="float64").reshape(6, 6)


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2], [0, 1, 0], label="alpha")
    ax.plot([0, 1, 2], [1, 0, 1], label="beta")
    leg = ax.legend(title="series", loc="upper center", ncol=2)
    leg.get_frame().set_edgecolor("#888888")
    ax.set_title("Axes legend")
    fig.savefig("art_legend.pdf")

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(5.0, 2.2))
    a1.plot([0, 1, 2], [0, 1, 0], label="alpha")
    a2.plot([0, 1, 2], [1, 0, 1], label="beta")
    fig.legend(loc="lower center", ncol=2)
    fig.suptitle("Figure legend")
    fig.savefig("art_figure_legend.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    im = ax.imshow(GRID, cmap="viridis")
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("intensity (a.u.)")
    ax.set_title("Single colorbar")
    fig.savefig("art_colorbar.pdf")

    fig, (b1, b2) = plt.subplots(1, 2, figsize=(5.4, 2.4))
    i1 = b1.imshow(GRID, cmap="viridis")
    i2 = b2.imshow(GRID.T, cmap="plasma")
    fig.colorbar(i1, ax=b1).set_label("left")
    fig.colorbar(i2, ax=b2).set_label("right")
    fig.suptitle("Two colorbars")
    fig.savefig("art_multi_colorbar.pdf")
