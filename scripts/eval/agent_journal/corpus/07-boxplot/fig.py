import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducibility
np.random.seed(42)

# Five treatment groups
groups = ["Control", "Drug A", "Drug B", "Drug C", "Drug D"]

# Synthetic cell viability data (%), base means and spreads per group
means = [95.0, 82.0, 70.0, 58.0, 44.0]
stds = [6.0, 8.0, 9.0, 10.0, 11.0]
n_per_group = 30

data = [np.clip(np.random.normal(m, s, n_per_group), 0, 100) for m, s in zip(means, stds)]

fig, ax = plt.subplots(figsize=(8, 6))

# Box plot
box = ax.boxplot(
    data,
    tick_labels=groups,
    patch_artist=True,
    showmeans=True,
    widths=0.5,
    boxprops=dict(linewidth=1.5, color="black"),
    whiskerprops=dict(linewidth=1.5, color="black"),
    capprops=dict(linewidth=1.5, color="black"),
    medianprops=dict(linewidth=2, color="firebrick"),
    meanprops=dict(marker="D", markerfacecolor="white", markeredgecolor="black", markersize=6),
)

# Colors for each box
colors = ["#d62728", "#ff7f0e", "#2ca02c", "#1f77b4", "#9467bd"]
for patch, color in zip(box["boxes"], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.5)

# Jittered individual data points on top
for i, (d, color) in enumerate(zip(data, colors), start=1):
    x = np.random.normal(i, 0.06, size=len(d))
    ax.scatter(x, d, color=color, alpha=0.7, edgecolors="black", linewidths=0.4, s=40, zorder=3)

ax.set_xticks(range(1, len(groups) + 1))
ax.set_xticklabels(groups)
ax.set_ylabel("Cell viability (%)")
ax.set_xlabel("Treatment group")
ax.set_title("Cell Viability Across Treatment Groups")
ax.set_ylim(0, 110)
ax.grid(axis="y", linestyle="--", alpha=0.3)
ax.set_axisbelow(True)

plt.tight_layout()
plt.savefig("fig.png", dpi=150)
print("Saved fig.png")
