import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducible synthetic data
np.random.seed(42)

# 24 hours, sampled every 5 minutes
hours = np.linspace(0, 24, 24 * 12 + 1)

# Three sensors with distinct baselines and diurnal profiles plus noise
sensor1 = 20 + 5 * np.sin((hours - 6) / 24 * 2 * np.pi) + np.random.normal(0, 0.6, hours.size)
sensor2 = 22 + 4 * np.sin((hours - 7) / 24 * 2 * np.pi) + np.random.normal(0, 0.8, hours.size)
sensor3 = 18 + 6 * np.sin((hours - 5) / 24 * 2 * np.pi) + np.random.normal(0, 0.7, hours.size)

plt.figure(figsize=(9, 5))
plt.plot(hours, sensor1, label="Sensor 1", linewidth=1.5)
plt.plot(hours, sensor2, label="Sensor 2", linewidth=1.5)
plt.plot(hours, sensor3, label="Sensor 3", linewidth=1.5)

plt.xlabel("Time (hours)")
plt.ylabel("Temperature (°C)")
plt.title("Sensor Temperature Over 24 Hours")
plt.legend()
plt.grid(True, linestyle="--", alpha=0.4)

plt.tight_layout()
plt.savefig("fig.png", dpi=150)
