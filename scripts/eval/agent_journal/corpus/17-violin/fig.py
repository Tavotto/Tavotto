import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducibility
rng = np.random.default_rng(42)

# Four genotypes and their synthetic gene expression levels
genotypes = ["WT", "MutA", "MutB", "MutC"]
n = 200
data = [
    rng.normal(loc=10.0, scale=1.2, size=n),  # WT
    rng.normal(loc=12.0, scale=1.5, size=n),  # MutA
    rng.normal(loc=13.5, scale=1.4, size=n),  # MutB
    rng.normal(loc=11.5, scale=1.6, size=n),  # MutC
]

fig, ax = plt.subplots(figsize=(8, 6))

# Violin plot
parts = ax.violinplot(data, positions=np.arange(len(genotypes)), showmedians=True)

# Style violins
colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]
for i, body in enumerate(parts["bodies"]):
    body.set_facecolor(colors[i])
    body.set_edgecolor("black")
    body.set_alpha(0.75)
for key in ("cbars", "cmins", "cmaxes", "cmedians"):
    parts[key].set_edgecolor("black")
    parts[key].set_linewidth(1.5)

# Mark medians explicitly
for i, d in enumerate(data):
    med = np.median(d)
    ax.scatter(i, med, color="black", zorder=3, s=30)

# Significance asterisks between pairs
def add_sig(x1, x2, y, n_stars):
    """Draw a significance bracket with asterisks between two violin positions."""
    ax.plot([x1, x1, x2, x2], [y, y + 0.15, y + 0.15, y], lw=1, color="black")
    ax.text((x1 + x2) / 2, y + 0.2, "*" * n_stars, ha="center", va="bottom", fontsize=14)

add_sig(0, 1, 17.0, 3)  # WT vs MutA
add_sig(0, 2, 18.5, 3)  # WT vs MutB
add_sig(0, 3, 20.0, 1)  # WT vs MutC
add_sig(2, 3, 17.0, 2)  # MutB vs MutC

ax.set_xticks(np.arange(len(genotypes)))
ax.set_xticklabels(genotypes)
ax.set_xlabel("Genotype")
ax.set_ylabel("Gene expression level")
ax.set_title("Gene Expression Levels by Genotype")
ax.set_ylim(5, 21.5)

ax.grid(axis="y", linestyle="--", alpha=0.3)

plt.tight_layout()
plt.savefig("fig.png", dpi=150)
