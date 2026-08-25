import numpy as np
import matplotlib.pyplot as plt

rng = np.random.default_rng(42)
x = np.linspace(0, 10, 36)
y = 2.1 * x + 1.3 + rng.normal(0, 1.2, x.size)
k, b = np.polyfit(x, y, 1)

fig, ax = plt.subplots(figsize=(3.4, 2.6))
ax.scatter(x, y, s=14, alpha=0.75, label="Measured")
ax.plot(x, k * x + b, lw=1.2, color="#b03a2e",
        label=f"Fit: y = {k:.2f}x + {b:.2f}")
ax.set_xlabel("Concentration (mM)")
ax.set_ylabel("Signal (a.u.)")
ax.set_title("Calibration curve")
ax.legend(loc="upper left")
fig.tight_layout()
fig.savefig("calibration.pdf")
