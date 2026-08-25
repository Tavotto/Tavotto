import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 60, 200)
fast = 1 - np.exp(-t / 8)
slow = 1 - np.exp(-t / 24)

fig, ax = plt.subplots(figsize=(3.4, 2.5))
ax.plot(t, fast, lw=1.4, label="Catalyst A")
ax.plot(t, slow, lw=1.4, ls="--", label="Blank")
ax.set_xlabel("Reaction time (min)")
ax.set_ylabel("Conversion")
ax.set_title("Reaction kinetics", fontsize=9)
ax.set_xlim(0, 60)
ax.set_ylim(0, 1.05)
ax.legend(loc="lower right")
fig.tight_layout()
fig.savefig("kinetics.pdf")
