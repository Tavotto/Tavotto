import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducible synthetic data
rng = np.random.default_rng(42)

# Build a spatial grid (coordinates in Angstrom)
x = np.linspace(-5.0, 5.0, 300)
y = np.linspace(-5.0, 5.0, 300)
X, Y = np.meshgrid(x, y)

# Synthetic 2D potential energy surface: a few overlapping Gaussian wells
# and barriers plus a smooth background to look like an energy landscape.
def gaussian(cx, cy, amp, sx, sy):
    return amp * np.exp(-((X - cx) ** 2) / (2 * sx**2) - (Y - cy) ** 2 / (2 * sy**2))

V = (
    gaussian(-2.0, 2.0, -3.0, 1.2, 1.2)
    + gaussian(2.0, -2.0, -2.5, 1.0, 1.0)
    + gaussian(2.5, 2.5, -1.5, 0.9, 0.9)
    + gaussian(0.0, 0.0, 2.0, 1.5, 1.5)
    + gaussian(-3.0, -2.5, 1.0, 0.8, 0.8)
    + 0.1 * (X**2 + Y**2)
    + 0.05 * rng.standard_normal(X.shape)
)

fig, ax = plt.subplots(figsize=(8, 6))

# Filled contour plot
cf = ax.contourf(X, Y, V, levels=40, cmap="viridis")

# Contour lines on top
cs = ax.contour(X, Y, V, levels=cf.levels, colors="white", linewidths=0.5, alpha=0.6)

# Colorbar
cbar = fig.colorbar(cf, ax=ax)
cbar.set_label("Potential energy (kJ/mol)")

# Axis labels in Angstrom
ax.set_xlabel(r"x (Å)")
ax.set_ylabel(r"y (Å)")
ax.set_title("2D potential energy surface")

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
