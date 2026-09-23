"""与 issue #457 同量级的可参数化图：4 个 axes、~245 段文字、~116 个箭头注释、图例 3 项，220 × 250 mm。"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent


def main():
    rng = np.random.default_rng(7)
    fig, axes = plt.subplots(2, 2, figsize=(220 / 25.4, 250 / 25.4))
    for k, ax in enumerate(axes.flat):
        x = np.linspace(0, 10, 60)
        for j in range(3):
            ax.plot(x, np.sin(x + j) + 0.2 * rng.standard_normal(60), label=f"series {j}")
        # ~29 arrow annotations per axes → 116 total
        for i in range(29):
            xi = x[i * 2]
            ax.annotate(
                f"p{k}-{i}",
                xy=(xi, np.sin(xi + k % 3)),
                xytext=(xi + 0.3, np.sin(xi + k % 3) + 0.8 + 0.05 * i),
                arrowprops=dict(arrowstyle="->", lw=0.6),
                fontsize=6,
            )
        # ~32 plain texts per axes → 128 total (with annotations' text ≈ 245)
        for i in range(32):
            ax.text(0.2 + (i % 8) * 1.2, -1.6 + (i // 8) * 0.25, f"t{k}.{i}", fontsize=5)
        ax.set_xlabel("x (unit)")
        ax.set_ylabel("y (unit)")
        ax.set_title(f"Panel {k}")
    axes.flat[0].legend(loc="upper right")
    fig.savefig(OUT / "big_fig.pdf")
    fig.savefig(OUT / "big_fig.png", dpi=150)
    fig.savefig(OUT / "big_fig.svg")


if __name__ == "__main__":
    main()
