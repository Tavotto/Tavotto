import numpy as np
import matplotlib.pyplot as plt

w = np.linspace(400, 800, 400)
peak = lambda c, s, a: a * np.exp(-((w - c) / s) ** 2)
signal = peak(520, 18, 1.0) + peak(645, 30, 0.55) + 0.04

fig, ax = plt.subplots(figsize=(3.6, 2.5))
ax.plot(w, signal, lw=1.3)
ax.fill_between(w, 0, signal, alpha=0.18)
ax.annotate("Q-band", xy=(645, 0.6), xytext=(700, 0.85),
            arrowprops=dict(arrowstyle="->", lw=0.9), fontsize=9)
ax.set_xlabel("Wavelength (nm)")
ax.set_ylabel("Absorbance")
ax.set_title("Absorption spectrum")
ax.set_xticks([400, 500, 600, 700, 800])
ax.set_ylim(0, 1.1)
fig.tight_layout()
fig.savefig("spectrum.pdf")
