import numpy as np
import matplotlib.pyplot as plt

# Fixed random seed for reproducibility
rng = np.random.default_rng(42)

# Synthetic microscopy images: 2 x 3 grid of random noise fields
fig, axes = plt.subplots(2, 3, figsize=(9, 6))

titles = [
    "DAPI",
    "GFP",
    "Texas Red",
    "Cy5",
    "Brightfield",
    "Phase Contrast",
]

for ax, title in zip(axes.ravel(), titles):
    # Random noise field, slightly smoothed to look like a microscopy channel
    noise = rng.normal(0, 1, size=(256, 256))
    ax.imshow(noise, cmap="grey")
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])

    # Scale bar: annotate bottom-left corner with a bar of arbitrary length
    ax.annotate(
        "",
        xy=(0.15, 0.10),
        xytext=(0.05, 0.10),
        xycoords="axes fraction",
        arrowprops=dict(arrowstyle="-", color="white", lw=2),
    )
    ax.text(0.10, 0.06, "10 \u00b5m", transform=ax.transAxes,
            color="white", fontsize=8, ha="center", va="top")

plt.tight_layout()
fig.savefig("fig.png", dpi=150)
