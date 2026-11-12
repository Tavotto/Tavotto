import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit


def sigmoid(x, top, bottom, log_ic50, hill):
    """Four-parameter logistic (sigmoid) dose-response curve."""
    return bottom + (top - bottom) / (1.0 + 10.0 ** (hill * (x - log_ic50)))


# Synthetic dose-response data with a fixed seed (no input files).
rng = np.random.default_rng(42)

# Log10 concentrations (uM).
x = np.linspace(-9.0, -4.0, 12)

# Per-cell-line "true" parameters: (top, bottom, logIC50, hill).
cell_lines = {
    "Cell Line A": (100.0, 5.0, -7.0, 1.0),
    "Cell Line B": (100.0, 8.0, -6.2, 1.3),
    "Cell Line C": (95.0, 15.0, -5.5, 0.8),
}

fig, axes = plt.subplots(1, 3, figsize=(12, 4), sharey=True)

for ax, (name, params) in zip(axes, cell_lines.items()):
    top, bottom, log_ic50, hill = params
    y_true = sigmoid(x, top, bottom, log_ic50, hill)
    # Add experimental noise.
    y = y_true + rng.normal(0.0, 3.0, size=x.size)

    # Fit the sigmoid to the noisy data.
    p0 = [100.0, 10.0, -6.5, 1.0]
    popt, _ = curve_fit(sigmoid, x, y, p0=p0, maxfev=20000)

    # Plot data and fit.
    ax.scatter(x, y, color="tab:blue", s=30, zorder=3, label="Data")
    x_fine = np.linspace(x.min(), x.max(), 200)
    ax.plot(x_fine, sigmoid(x_fine, *popt), color="tab:red", lw=2, label="Sigmoid fit")

    ax.set_title(name)
    ax.set_xlabel("log10 concentration (µM)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)

axes[0].set_ylabel("Cell viability (%)")

fig.suptitle("Dose–Response Curves for Three Cell Lines", fontsize=14)
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig("fig.png", dpi=150)
print("Saved fig.png")
