/**
 * playground 内置示例。三条纪律（ADR 0007）：
 *   * 就是普通的科研 matplotlib 脚本——不 import pyodide / js / 任何
 *     Tavotto 专有 API，Tavotto 接的是用户**本来就在写**的图；
 *   * 小到能读完；
 *   * 真的经过 Pyodide 执行——不是预烤的 manifest（那是在演示假东西）。
 */
export interface Example {
  id: string
  /** i18n key 的尾段（dialogs:playground.example*） */
  labelKey: string
  filename: string
  source: string
}

export const EXAMPLES: Example[] = [
  {
    id: 'kinetics',
    labelKey: 'exampleKinetics',
    filename: 'kinetics.py',
    // 标题 / 轴标签 / 图例 / 两条 Line2D 全齐——语义选择一眼可见
    source: `import numpy as np
import matplotlib.pyplot as plt

t = np.linspace(0, 60, 200)
fast = 1 - np.exp(-t / 8)
slow = 1 - np.exp(-t / 24)

fig, ax = plt.subplots(figsize=(3.4, 2.5))
ax.plot(t, fast, lw=1.4, label="Catalyst A")
ax.plot(t, slow, lw=1.4, ls="--", label="Blank")
ax.set_xlabel("Reaction time (min)")
ax.set_ylabel("Conversion")
ax.set_title("Reaction kinetics")
ax.set_xlim(0, 60)
ax.set_ylim(0, 1.05)
ax.legend(loc="lower right")
fig.savefig("kinetics.pdf")
`,
  },
  {
    id: 'scatter-fit',
    labelKey: 'exampleScatter',
    filename: 'calibration.py',
    source: `import numpy as np
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
fig.savefig("calibration.pdf")
`,
  },
  {
    id: 'annotated',
    labelKey: 'exampleAnnotated',
    filename: 'spectrum.py',
    source: `import numpy as np
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
fig.savefig("spectrum.pdf")
`,
  },
]
