import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducible synthetic data
np.random.seed(42)

# Angle grid (full circle)
theta = np.linspace(0, 2 * np.pi, 360)

# Three frequencies (labels in GHz)
frequencies = ["2.4 GHz", "5.0 GHz", "8.0 GHz"]

# Synthetic angular emission intensity patterns:
# each pattern is a sum of lobes with a different shape per frequency.
patterns = {}
patterns["2.4 GHz"] = np.abs(
    2.0 * np.cos(theta) + 0.3 * np.sin(3 * theta)
) + 0.4
patterns["5.0 GHz"] = np.abs(
    1.5 * np.cos(theta - np.pi / 3) + 0.4 * np.sin(4 * theta)
) + 0.6
patterns["8.0 GHz"] = np.abs(
    1.2 * np.cos(theta - np.pi / 2) + 0.5 * np.sin(2 * theta)
) + 0.8

# Add small deterministic noise (fixed seed) to mimic measured data
for f in frequencies:
    patterns[f] += np.random.uniform(0.0, 0.15, size=theta.shape)

# Polar plot
fig = plt.figure(figsize=(7, 7))
ax = fig.add_subplot(111, projection="polar")
ax.set_theta_zero_location("N")   # 0 deg at top
ax.set_theta_direction(-1)        # clockwise

colors = ["tab:blue", "tab:orange", "tab:green"]
for f, c in zip(frequencies, colors):
    ax.plot(theta, patterns[f], label=f, color=c, linewidth=2)

ax.set_title("Angular Emission Intensity of an Antenna", va="bottom", pad=20)
ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1))

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
print("saved fig.png")
