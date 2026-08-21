"""排版引擎与纵横比：tight_layout / constrained_layout / aspect="equal"。"""
import matplotlib.pyplot as plt
import numpy as np

GRID = np.arange(36, dtype="float64").reshape(6, 6)


def main():
    fig, axes = plt.subplots(1, 2, figsize=(4.6, 2.2))
    axes[0].plot([1, 2, 3])
    axes[0].set_ylabel("a very long y label to force reflow")
    axes[1].plot([3, 2, 1])
    axes[1].set_title("tight")
    fig.tight_layout()
    fig.savefig("ax_tight_layout.pdf")

    fig, axes = plt.subplots(1, 2, figsize=(4.6, 2.2), layout="constrained")
    im = axes[0].imshow(GRID, cmap="viridis")
    fig.colorbar(im, ax=axes[0])
    axes[1].plot([1, 3, 2])
    axes[1].set_title("constrained")
    fig.savefig("ax_constrained_layout.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.imshow(GRID, cmap="cividis")
    ax.set_aspect("equal")
    ax.set_title('aspect="equal"')
    fig.savefig("ax_aspect_equal.pdf")
