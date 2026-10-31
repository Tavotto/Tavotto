"""Synthetic nested flowchart: independent boxes, text, and annotation arrows.

The twelve boxes exercise the geometry of a reported flowchart without including
any of the user's research text, counts, or results.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def main():
    fig, ax = plt.subplots(figsize=(13, 11), dpi=150)
    ax.set(xlim=(0, 100), ylim=(0, 100))
    ax.axis("off")
    boxes = [
        (2, 70, 14, 18),
        (2, 48, 14, 20),
        (2, 26, 14, 20),
        (2, 4, 14, 20),
        (20, 72, 76, 17),
        (20, 50, 38, 17),
        (64, 50, 32, 17),
        (20, 28, 38, 17),
        (64, 28, 32, 17),
        (20, 4, 76, 19),
        (22, 6, 35, 11),
        (59, 6, 35, 11),
    ]
    for i, (x, y, w, h) in enumerate(boxes):
        patch = FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.4,rounding_size=1.0",
            linewidth=1.8,
            facecolor="#F1F5F9",
            edgecolor="#0284C7",
            label=f"Flow box {i + 1}",
        )
        ax.add_patch(patch)
        # Keep text clear of the interior point used for direct box selection.
        ax.text(x + w / 2, y + h * 0.7, f"Step {i + 1}", ha="center", va="center", fontsize=10)
    for start, end in [
        ((58, 72), (58, 67)),
        ((58, 58.5), (64, 58.5)),
        ((39, 50), (39, 45)),
        ((58, 36.5), (64, 36.5)),
        ((39, 28), (39, 23)),
    ]:
        ax.annotate(
            "", xy=end, xytext=start, arrowprops={"arrowstyle": "->", "color": "#1E293B", "lw": 1.8}
        )
    fig.tight_layout()
    fig.savefig("Flowchart.pdf")
    plt.show()


if __name__ == "__main__":
    main()
