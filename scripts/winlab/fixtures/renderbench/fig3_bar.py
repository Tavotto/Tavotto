"""render-bench 夹具：Bar。只依赖 numpy 与 matplotlib。"""

import matplotlib.pyplot as plt
import numpy as np


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.bar(np.arange(8), np.abs(np.sin(np.arange(8))) + 0.2)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title("Bar")
    fig.savefig("fig3_bar.pdf")


if __name__ == "__main__":
    main()
