"""render-bench 夹具：Step。只依赖 numpy 与 matplotlib。"""

import matplotlib.pyplot as plt
import numpy as np


def main():
    x = np.linspace(0, 10, 200)
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.step(x, np.round(np.sin(x) * 4) / 4, where="mid", lw=1.2)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title("Step")
    fig.savefig("fig6_step.pdf")


if __name__ == "__main__":
    main()
