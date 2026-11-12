import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducible synthetic data
np.random.seed(42)

# Four catalysts, three conditions
catalysts = ["Cat A", "Cat B", "Cat C", "Cat D"]
conditions = ["Condition 1", "Condition 2", "Condition 3"]

n_catalysts = len(catalysts)
n_conditions = len(conditions)

# Synthetic means and standard deviations
means = np.random.uniform(low=10, high=40, size=(n_catalysts, n_conditions))
stds = np.random.uniform(low=1, high=5, size=(n_catalysts, n_conditions))

x = np.arange(n_conditions)
width = 0.2  # width of each bar

fig, ax = plt.subplots(figsize=(9, 6))

colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]

for i, (catalyst, color) in enumerate(zip(catalysts, colors)):
    offset = (i - (n_catalysts - 1) / 2) * width
    ax.bar(
        x + offset,
        means[i],
        width,
        yerr=stds[i],
        capsize=4,
        label=catalyst,
        color=color,
        edgecolor="black",
        linewidth=0.6,
    )

ax.set_xticks(x)
ax.set_xticklabels(conditions)
ax.set_xlabel("Condition")
ax.set_ylabel("Yield (%)")
ax.set_title("Catalyst Performance Across Conditions")
ax.legend(title="Catalyst")
ax.grid(axis="y", linestyle="--", alpha=0.4)
ax.set_ylim(0, means.max() + stds.max() + 5)

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
print("Saved fig.png")
