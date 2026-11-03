"""import scienceplots：同目录 helper 与 PyPI 包重名——它是本地文件，不是缺依赖。"""
import matplotlib.pyplot as plt

import scienceplots
from scienceplots import PALETTE


def main():
    scienceplots.apply()
    fig, ax = plt.subplots(figsize=(3.2, 2.2))
    ax.plot([1, 3, 2, 4], color=PALETTE[0])
    ax.set_title("Shadowing helper")
    fig.savefig("shape_helper_shadow.pdf")


if __name__ == "__main__":
    main()
