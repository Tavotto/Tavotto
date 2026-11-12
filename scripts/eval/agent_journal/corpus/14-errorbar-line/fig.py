import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducibility
np.random.seed(42)

# 30 days
days = np.arange(0, 31)

# Number of subjects per group
n = 10

groups = {
    "Control": {"base": 100.0, "growth": 3.0, "noise": 15.0},
    "Treatment A": {"base": 100.0, "growth": 1.5, "noise": 12.0},
    "Treatment B": {"base": 100.0, "growth": 0.5, "noise": 10.0},
}

colors = {"Control": "#d62728", "Treatment A": "#2ca02c", "Treatment B": "#1f77b4"}
markers = {"Control": "o", "Treatment A": "s", "Treatment B": "^"}

fig, ax = plt.subplots(figsize=(8, 5))

for name, params in groups.items():
    # Synthetic tumour volume: exponential-like growth with per-subject noise
    volumes = np.array([
        params["base"] + params["growth"] * day
        + np.random.normal(0, params["noise"], size=n)
        for day in days
    ])

    mean = volumes.mean(axis=1)
    sem = volumes.std(axis=1, ddof=1) / np.sqrt(n)

    ax.errorbar(
        days,
        mean,
        yerr=sem,
        label=name,
        color=colors[name],
        marker=markers[name],
        markersize=5,
        linewidth=2,
        capsize=3,
        capthick=1.5,
        elinewidth=1.5,
        alpha=0.9,
    )

ax.set_xlabel("Time (days)")
ax.set_ylabel("Tumour volume (mm$^3$)")
ax.set_title("Mean ± SEM tumour volume over 30 days")
ax.legend()
ax.grid(True, linestyle="--", alpha=0.4)

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
plt.close(fig)
