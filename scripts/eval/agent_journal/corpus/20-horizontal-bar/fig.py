import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Fixed seed for reproducible synthetic data.
rng = np.random.default_rng(42)

n_features = 15
feature_names = [f"Feature {i+1}" for i in range(n_features)]
importances = rng.uniform(0.01, 1.0, size=n_features)

# Sort ascending so the largest importance appears at the top of the bar chart.
order = np.argsort(importances)
sorted_names = [feature_names[i] for i in order]
sorted_importances = importances[order]

fig, ax = plt.subplots(figsize=(8, 6))

colors = plt.cm.viridis(np.linspace(0.15, 0.85, n_features))
bars = ax.barh(sorted_names, sorted_importances, color=colors)

# Write the importance value at the end of each bar.
for bar, value in zip(bars, sorted_importances):
    ax.text(
        bar.get_width() + 0.01,
        bar.get_y() + bar.get_height() / 2,
        f"{value:.3f}",
        va="center",
        ha="left",
        fontsize=9,
    )

ax.set_xlabel("Importance")
ax.set_title("Feature Importances")
ax.set_xlim(0, sorted_importances.max() * 1.15)
ax.grid(axis="x", linestyle="--", alpha=0.4)
ax.set_axisbelow(True)

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
print("Saved fig.png")
