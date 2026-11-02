"""render-bench 夹具：Scatter。只依赖 numpy 与 matplotlib。"""

import matplotlib.pyplot as plt
import numpy as np


def main():
    x = np.linspace(0, 10, 200)
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.scatter(x, np.sin(x) + 0.1 * np.cos(7 * x), s=8)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title("Scatter")
    fig.savefig("fig2_scatter.pdf")


if __name__ == "__main__":
    main()
