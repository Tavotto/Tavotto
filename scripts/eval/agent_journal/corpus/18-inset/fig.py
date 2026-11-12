import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1.inset_locator import inset_axes, mark_inset

# Fixed seed for reproducibility
rng = np.random.default_rng(42)

# Synthetic stress-strain data: linear elastic region followed by
# yielding and hardening up to fracture.
n = 400
strain = np.linspace(0.0, 0.12, n)

E = 200e3  # Young's modulus (elastic slope)
yield_strain = 0.002
yield_stress = E * yield_strain  # 400 MPa

stress = np.empty_like(strain)
for i, e in enumerate(strain):
    if e <= yield_strain:
        stress[i] = E * e
    else:
        # Post-yield hardening curve approaching ultimate stress
        plastic = e - yield_strain
        stress[i] = yield_stress + 300e3 * plastic / (1.0 + 8.0 * plastic)

# Add a little measurement noise
noise = rng.normal(0.0, 2.5, size=stress.shape)
stress = stress + noise

fig, ax = plt.subplots(figsize=(8, 6))

ax.plot(strain, stress, color="tab:blue", lw=2, label="Stress-strain curve")
ax.set_xlabel("Strain")
ax.set_ylabel("Stress (MPa)")
ax.set_title("Stress-strain curve with elastic-region inset")
ax.grid(True, linestyle="--", alpha=0.5)
ax.legend(loc="lower right")

# Inset zooming into the elastic region near the origin.
axins = inset_axes(ax, width="40%", height="40%", loc="upper left",
                   bbox_to_anchor=(0.02, 0.02, 1, 1),
                   bbox_transform=ax.transAxes)

elastic_mask = strain <= yield_strain * 1.5
axins.plot(strain[elastic_mask], stress[elastic_mask],
           color="tab:red", lw=2)
axins.set_xlim(0, yield_strain * 1.5)
axins.set_ylim(0, yield_stress * 1.5)
axins.set_title("Elastic region", fontsize=8)
axins.tick_params(labelsize=7)
axins.grid(True, linestyle="--", alpha=0.5)

# Draw a connecting box around the zoomed region.
mark_inset(ax, axins, loc1=1, loc2=3, fc="none", ec="0.4", lw=1)

fig.savefig("fig.png", dpi=150, bbox_inches="tight")
print("Saved fig.png")
