import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

# Fixed seed so the synthetic data is reproducible.
np.random.seed(42)

# Potential window (V vs RHE): forward (anodic) sweep then reverse sweep.
E_start, E_vertex = 0.0, 1.0
E_fwd = np.linspace(E_start, E_vertex, 400)
E_rev = E_fwd[::-1]
E_full = np.concatenate([E_fwd, E_rev])

# Redox couple parameters.
E0 = 0.5      # formal potential, V vs RHE
sigma = 0.06  # peak width, V

scan_rates = [10, 25, 50, 100, 200]  # mV/s

# Colour each curve by its scan rate (log scale spans a decade).
cmap = plt.get_cmap("viridis")
norm = mcolors.LogNorm(vmin=min(scan_rates), vmax=max(scan_rates))

fig, ax = plt.subplots(figsize=(7, 5))

for v in scan_rates:
    # Randles-Sevcik: peak current scales with sqrt(scan rate).
    ip = 0.35 * np.sqrt(v)  # mA cm^-2

    # Peak separation grows with scan rate (quasi-reversible behaviour).
    dE = 0.02 + 0.03 * np.log10(v / 10)

    # Anodic (forward) and cathodic (reverse) Gaussian peaks.
    j_fwd = ip * np.exp(-((E_fwd - (E0 + dE / 2)) ** 2) / (2 * sigma ** 2))
    j_rev = -ip * np.exp(-((E_rev - (E0 - dE / 2)) ** 2) / (2 * sigma ** 2))

    # Double-layer charging baseline plus small experimental noise.
    j_dl = 0.03 * np.ones_like(E_full)
    j = np.concatenate([j_fwd, j_rev]) + j_dl
    j += np.random.normal(0, 0.012, size=j.shape)

    ax.plot(E_full, j, lw=2, color=cmap(norm(v)), label=f"{v} mV s$^{{-1}}$")

ax.set_xlabel("Potential (V vs RHE)")
ax.set_ylabel("Current density (mA cm$^{-2}$)")
ax.set_title("Cyclic voltammetry curves at different scan rates")
ax.axhline(0, color="gray", lw=0.8, ls="--")
ax.legend(title="Scan rate", frameon=False)
ax.grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
