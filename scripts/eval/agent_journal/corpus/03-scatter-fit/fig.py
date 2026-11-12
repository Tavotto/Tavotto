import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Synthetic data with a fixed seed for reproducibility
rng = np.random.default_rng(42)
n = 60
predicted = np.linspace(0, 10, n)
measured = predicted + rng.normal(0, 1.2, n)

# Linear fit
slope, intercept = np.polyfit(predicted, measured, 1)
fit = slope * predicted + intercept

# R^2
ss_res = np.sum((measured - fit) ** 2)
ss_tot = np.sum((measured - np.mean(measured)) ** 2)
r2 = 1 - ss_res / ss_tot

fig, ax = plt.subplots(figsize=(6, 6))

ax.scatter(predicted, measured, s=30, alpha=0.7, label="data")
ax.plot(predicted, fit, color="red", lw=2, label=f"fit: y = {slope:.2f}x + {intercept:.2f}")
ax.plot([predicted.min(), predicted.max()],
        [predicted.min(), predicted.max()],
        color="gray", ls="--", lw=1.5, label="y = x")

ax.text(0.05, 0.95, f"$R^2$ = {r2:.3f}",
        transform=ax.transAxes, fontsize=14, va="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.7))

ax.set_xlabel("Predicted")
ax.set_ylabel("Measured")
ax.set_title("Measured vs Predicted")
ax.legend()
ax.set_aspect("equal", adjustable="box")

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
