import numpy as np
import matplotlib.pyplot as plt

# Fixed random seed for reproducible synthetic data
rng = np.random.default_rng(42)

# Generate a 12x12 random matrix and make it a symmetric correlation matrix
n = 12
A = rng.standard_normal((n, n))
sym = (A + A.T) / 2
# Set diagonal to 1 so it resembles a correlation matrix
np.fill_diagonal(sym, 1.0)

# Variable labels
labels = [f"Var {i + 1}" for i in range(n)]

fig, ax = plt.subplots(figsize=(7, 6))
im = ax.imshow(sym, cmap="viridis", vmin=-1, vmax=1)

# Colorbar
cbar = fig.colorbar(im, ax=ax)
cbar.set_label("Correlation")

# Tick labels for each variable
ax.set_xticks(np.arange(n))
ax.set_yticks(np.arange(n))
ax.set_xticklabels(labels, rotation=45, ha="right")
ax.set_yticklabels(labels)

# Minor ticks to delineate cells
ax.set_xticks(np.arange(n + 1) - 0.5, minor=True)
ax.set_yticks(np.arange(n + 1) - 0.5, minor=True)
ax.grid(which="minor", color="white", linewidth=0.5)
ax.tick_params(which="minor", length=0)

ax.set_title("12x12 Correlation Matrix Heatmap")

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
print("Saved fig.png")
