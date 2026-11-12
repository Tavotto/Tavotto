"""Log-log plot of power spectral density vs frequency for three samples.

Three synthetic colored-noise samples with different power-law exponents
(PSD ~ f^-alpha) are generated from a fixed random seed and their power
spectral densities are estimated with Welch's method. A power-law guide
line is overlaid and labelled with its slope.
"""

import numpy as np
import matplotlib.pyplot as plt

# Fixed seed for reproducibility
rng = np.random.default_rng(42)

# Sampling parameters
FS = 1000.0          # sampling frequency (Hz)
N = 2**16            # number of samples per signal
DT = 1.0 / FS

# Power-law exponents for the three samples (PSD ~ f^-alpha)
ALPHAS = [1.0, 1.7, 2.5]
COLORS = ["tab:blue", "tab:orange", "tab:green"]


def power_law_noise(n, alpha, dt):
    """Generate a real signal whose PSD follows f^-alpha.

    White-noise Fourier amplitudes are given random phases and scaled by
    f^(-alpha/2) before transforming back to the time domain.
    """
    freqs = np.fft.rfftfreq(n, d=dt)
    amplitudes = np.ones_like(freqs)
    amplitudes[1:] = freqs[1:] ** (-alpha / 2.0)  # avoid division by zero
    phases = np.exp(2j * np.pi * rng.random(freqs.size))
    spectrum = amplitudes * phases
    return np.fft.irfft(spectrum, n=n)


def welch_psd(x, dt, nperseg=4096, overlap=0.5):
    """Estimate the one-sided PSD with Welch's method (Hann window)."""
    step = int(nperseg * (1.0 - overlap))
    window = np.hanning(nperseg)
    freqs = np.fft.rfftfreq(nperseg, d=dt)
    psd = np.zeros_like(freqs)
    nseg = 0
    for start in range(0, len(x) - nperseg + 1, step):
        segment = x[start:start + nperseg] * window
        psd += np.abs(np.fft.rfft(segment)) ** 2
        nseg += 1
    psd /= nseg
    # Normalize so that the result is a proper one-sided PSD estimate
    psd /= dt * np.sum(window ** 2)
    return freqs, psd


fig, ax = plt.subplots(figsize=(7.5, 5.5))

for alpha, color in zip(ALPHAS, COLORS):
    signal = power_law_noise(N, alpha, DT)
    freqs, psd = welch_psd(signal, DT)
    ax.loglog(freqs[1:], psd[1:], color=color, lw=1.2,
              label=rf"sample, $\alpha = {alpha}$")

# Power-law guide line: PSD ~ C * f^-2 (slope -2 on a log-log plot)
slope = -2.0
f_guide = np.array([freqs[1], freqs[-1]])
psd_guide = 1e3 * f_guide ** slope
ax.loglog(f_guide, psd_guide, "k--", lw=1.5,
          label=rf"guide line, slope = {slope}")

ax.set_xlabel("Frequency (Hz)")
ax.set_ylabel(r"Power spectral density (Hz$^{-1}$)")
ax.set_title("Power spectral density vs frequency (log-log)")
ax.grid(True, which="both", ls=":", alpha=0.5)
ax.legend(frameon=False)

fig.tight_layout()
fig.savefig("fig.png", dpi=150)
