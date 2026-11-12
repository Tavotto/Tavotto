import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducible synthetic data
rng = np.random.default_rng(42)

years = np.arange(2000, 2026)

# Generate synthetic energy mix shares that sum to 1 (100%) per year
n = len(years)
categories = ["Coal", "Gas", "Nuclear", "Wind", "Solar"]

# Base trends plus noise, then normalized
base = np.array([
    0.40 - 0.015 * np.arange(n),      # coal declines
    0.30 + 0.002 * np.arange(n),      # gas rises slowly
    0.20 - 0.003 * np.arange(n),      # nuclear declines slowly
    0.06 + 0.004 * np.arange(n),      # wind rises
    0.04 + 0.012 * np.arange(n),      # solar rises fastest
])

noise = rng.uniform(0.9, 1.1, size=base.shape)
raw = base * noise
shares = raw / raw.sum(axis=0)

fig, ax = plt.subplots(figsize=(10, 6))

ax.stackplot(years, shares, labels=categories, alpha=0.85)

ax.set_title("Energy Mix Shares (2000-2025)")
ax.set_xlabel("Year")
ax.set_ylabel("Share of Generation")
ax.set_ylim(0, 1)

ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)

fig.tight_layout()
fig.savefig("fig.png", bbox_inches="tight", dpi=150)
