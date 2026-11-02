"""render-bench 夹具：Line。只依赖 numpy 与 matplotlib。"""

import matplotlib.pyplot as plt
import numpy as np


def main():
    x = np.linspace(0, 10, 200)
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot(x, np.sin(x), lw=1.2, label="sin")
    ax.plot(x, np.cos(x), lw=1.2, ls="--", label="cos")
    ax.legend()
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title("Line")
    fig.savefig("fig1_line.pdf")


if __name__ == "__main__":
    main()
