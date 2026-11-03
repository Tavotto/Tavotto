"""科研二维图的常见构图：radar、柱+误差棒+hatch、图例独占 Axes、带注释的 heatmap。"""
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

PALETTE = ["#1b6ca8", "#e07b39", "#3a9e5f", "#8e5ba8"]
HATCHES = ["", "//", ".."]
HEAT = np.array([[0.1, 0.4, 0.7], [0.3, 0.9, 0.2], [0.6, 0.5, 0.8]])


def radar():
    labels = ["speed", "memory", "accuracy", "recall", "cost"]
    theta = np.linspace(0, 2 * np.pi, len(labels), endpoint=False)
    fig = plt.figure(figsize=(3.0, 3.0))
    ax = fig.add_subplot(projection="polar")
    for vals, c, name in (([0.8, 0.55, 0.9, 0.7, 0.4], PALETTE[0], "ours"),
                          ([0.6, 0.85, 0.75, 0.5, 0.7], PALETTE[1], "base")):
        t = np.append(theta, theta[0])
        v = np.append(vals, vals[0])
        ax.plot(t, v, color=c, lw=1.2, label=name)
        ax.fill(t, v, color=c, alpha=0.25)
    ax.set_thetagrids(np.degrees(theta), labels)
    ax.set_ylim(0, 1)
    ax.set_title("radar")
    fig.savefig("res_radar.pdf")


def draw_bars(ax):
    x = np.arange(3)
    for i, (h, c) in enumerate(zip(HATCHES, PALETTE)):
        ax.bar(x + i * 0.25, [1.0 + i * 0.2, 1.6 - i * 0.1, 0.8 + i * 0.3], 0.25,
               yerr=[0.1, 0.15, 0.08], capsize=2, color=c, hatch=h,
               edgecolor="black", lw=0.6, label=f"m{i}")
    ax.set_xticks(x + 0.25, ["A", "B", "C"])


def bars_errorbar_hatch():
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    draw_bars(ax)
    ax.set_title("bars")
    ax.legend(frameon=False)
    fig.savefig("res_bars_err_hatch.pdf")


def legend_only_axes():
    fig = plt.figure(figsize=(4.0, 2.4))
    ax = fig.add_axes([0.1, 0.15, 0.6, 0.75])
    lax = fig.add_axes([0.74, 0.15, 0.24, 0.75])
    draw_bars(ax)
    ax.set_title("bars")
    lax.axis("off")
    lax.legend(handles=[Patch(facecolor=c, hatch=h, edgecolor="black", label=f"m{i}")
                        for i, (h, c) in enumerate(zip(HATCHES, PALETTE))],
               loc="center", frameon=False)
    fig.savefig("res_legend_axes.pdf")


def heatmap_annotated():
    fig, ax = plt.subplots(figsize=(3.0, 2.6))
    im = ax.imshow(HEAT, cmap="viridis", vmin=0, vmax=1)
    for (r, c), v in np.ndenumerate(HEAT):
        ax.text(c, r, f"{v:.1f}", ha="center", va="center", color="w" if v < 0.5 else "k")
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax.set_title("heatmap")
    fig.savefig("res_heatmap_annot.pdf")


def main():
    radar()
    bars_errorbar_hatch()
    legend_only_axes()
    heatmap_annotated()


if __name__ == "__main__":
    main()
