import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde

# Fixed random seed for reproducibility
rng = np.random.default_rng(42)

# Generate synthetic particle size data (nm)
# Log-normal-like distribution typical of particle sizes
n = 2000
data = rng.lognormal(mean=np.log(100), sigma=0.4, size=n)
data = data[(data >= 10) & (data <= 500)]  # clip to a plausible nm range

mean = np.mean(data)

# Create figure
fig, ax = plt.subplots(figsize=(8, 5))

# Histogram (density-normalized)
counts, bins, _ = ax.hist(
    data,
    bins=40,
    density=True,
    alpha=0.6,
    color="steelblue",
    edgecolor="white",
    label="Particle sizes",
)

# Smoothed density curve (KDE)
kde = gaussian_kde(data)
xs = np.linspace(data.min(), data.max(), 500)
ax.plot(xs, kde(xs), color="crimson", linewidth=2, label="Density estimate")

# Mean marked by a vertical dashed line
ax.axvline(mean, color="black", linestyle="--", linewidth=1.5,
           label=f"Mean = {mean:.1f} nm")

ax.set_xlabel("Particle size (nm)")
ax.set_ylabel("Density")
ax.set_title("Histogram of particle sizes with density curve")
ax.legend()

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
print(f"Saved fig.png (mean = {mean:.2f} nm)")
