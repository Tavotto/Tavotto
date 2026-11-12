import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Fixed random seed for reproducible synthetic experiment data.
rng = np.random.default_rng(42)

fig, axes = plt.subplots(2, 2, figsize=(9, 7))

# (a) Line plot: signal vs. time with noise.
t = np.linspace(0, 10, 100)
signal = np.sin(t) + 0.15 * rng.standard_normal(t.size)
axes[0, 0].plot(t, signal, color="tab:blue", lw=1.5)
axes[0, 0].set_title("(a) Signal vs. time")
axes[0, 0].set_xlabel("Time (s)")
axes[0, 0].set_ylabel("Signal (a.u.)")

# (b) Histogram: distribution of measurements.
measurements = rng.normal(loc=5.0, scale=1.2, size=500)
axes[0, 1].hist(measurements, bins=25, color="tab:green", edgecolor="white")
axes[0, 1].set_title("(b) Measurement distribution")
axes[0, 1].set_xlabel("Value")
axes[0, 1].set_ylabel("Count")

# (c) Scatter plot: two correlated variables.
x = rng.uniform(0, 10, 60)
y = 1.5 * x + 2.0 + rng.standard_normal(x.size) * 1.5
axes[1, 0].scatter(x, y, color="tab:orange", alpha=0.75)
axes[1, 0].set_title("(c) Dose vs. response")
axes[1, 0].set_xlabel("Dose")
axes[1, 0].set_ylabel("Response")

# (d) Bar chart: mean response per condition.
conditions = ["A", "B", "C", "D"]
means = rng.uniform(2, 9, len(conditions))
errors = rng.uniform(0.3, 0.8, len(conditions))
axes[1, 1].bar(conditions, means, yerr=errors, color="tab:red",
               edgecolor="black", capsize=4)
axes[1, 1].set_title("(d) Mean response by condition")
axes[1, 1].set_xlabel("Condition")
axes[1, 1].set_ylabel("Mean response")

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
