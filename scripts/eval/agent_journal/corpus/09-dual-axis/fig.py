import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducible synthetic data
rng = np.random.default_rng(42)

# Time axis (seconds)
t = np.linspace(0, 10, 500)

# Synthetic voltage (V): sine plus small noise
voltage = 5.0 + 2.0 * np.sin(2 * np.pi * 0.5 * t) + rng.normal(0, 0.1, t.size)

# Synthetic current (mA): roughly follows voltage, with its own noise
current = 20.0 + 8.0 * np.sin(2 * np.pi * 0.5 * t + 0.3) + rng.normal(0, 0.4, t.size)

fig, ax1 = plt.subplots(figsize=(8, 5))

# Left axis: voltage
ax1.set_xlabel("Time (s)")
ax1.set_ylabel("Voltage (V)", color="tab:blue")
(line1,) = ax1.plot(t, voltage, color="tab:blue", label="Voltage")
ax1.tick_params(axis="y", labelcolor="tab:blue")

# Right axis: current
ax2 = ax1.twinx()
ax2.set_ylabel("Current (mA)", color="tab:red")
(line2,) = ax2.plot(t, current, color="tab:red", label="Current")
ax2.tick_params(axis="y", labelcolor="tab:red")

# Combined legend
lines = [line1, line2]
ax1.legend(lines, [ln.get_label() for ln in lines], loc="upper right")

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
