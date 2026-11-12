import numpy as np
import matplotlib.pyplot as plt

# Fixed random seed for reproducibility
np.random.seed(42)

# 2θ range
two_theta = np.linspace(10.0, 80.0, 2000)

# Four samples with distinct synthetic peak positions / phases
samples = [
    ("Sample A", [18.0, 22.5, 27.0, 31.5, 38.0, 45.5, 52.0, 58.5, 65.0, 72.5]),
    ("Sample B", [15.5, 21.0, 25.5, 30.0, 35.5, 41.0, 47.5, 54.0, 60.5, 68.0, 76.0]),
    ("Sample C", [19.0, 23.0, 28.5, 33.0, 39.5, 44.0, 50.0, 56.0, 62.0, 70.0, 77.5]),
    ("Sample D", [16.5, 20.0, 26.0, 29.5, 34.0, 40.0, 46.0, 51.5, 57.0, 63.5, 69.5, 75.0]),
]


def gaussian(x, center, width, height):
    """Return a Gaussian peak centered at `center`."""
    return height * np.exp(-0.5 * ((x - center) / width) ** 2)


def synthetic_xrd(x, peaks, noise_level=0.05):
    """Build a synthetic XRD pattern from a set of Gaussian peaks plus noise."""
    intensity = np.zeros_like(x)
    for i, center in enumerate(peaks):
        # Vary peak height and width to look like a real diffractogram
        height = 1.0 - 0.12 * i + np.random.uniform(-0.05, 0.15)
        width = np.random.uniform(0.18, 0.35)
        intensity += gaussian(x, center, width, height)
    # Smooth background that decays with 2θ (like real XRD background)
    background = 0.25 * np.exp(-((x - 10.0) / 60.0)) + 0.02
    intensity += background
    # Random measurement noise
    intensity += np.random.normal(0.0, noise_level, size=x.shape)
    return intensity


fig, ax = plt.subplots(figsize=(9, 7))

# Vertical offset between stacked patterns
offset = 1.6

for i, (name, peaks) in enumerate(samples):
    intensity = synthetic_xrd(two_theta, peaks)
    # Offset each pattern vertically and normalize to a common scale
    y = intensity / intensity.max() + i * offset
    ax.plot(two_theta, y, lw=1.2, label=name)
    # Place the sample name next to its curve, near the low-angle start
    ax.text(
        10.2,
        i * offset + 1.05,
        name,
        fontsize=11,
        va="bottom",
        ha="left",
    )

# Labels and formatting
ax.set_xlabel(r"2$\theta$ (degrees)", fontsize=13)
ax.set_ylabel("Intensity (a.u.)", fontsize=13)
ax.set_xlim(10, 80)
ax.set_ylim(-0.3, (len(samples) - 1) * offset + 1.6)

# Hide y-axis ticks since intensity is offset/arbitrary
ax.set_yticks([])

ax.set_title("Stacked XRD Patterns", fontsize=14)
ax.grid(axis="x", linestyle=":", alpha=0.5)

plt.tight_layout()
plt.savefig("fig.png", dpi=150)
print("Saved fig.png")
