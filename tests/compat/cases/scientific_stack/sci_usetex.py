"""text.usetex 的图：依赖系统 TeX 工具链（latex + dvipng / dvips 等），缺则报告为环境依赖。"""
import matplotlib.pyplot as plt


def main():
    plt.rcParams.update({"text.usetex": True, "font.family": "serif", "font.size": 9})
    fig, ax = plt.subplots(figsize=(2.4, 1.8))
    ax.plot([0, 1, 2], [0.0, 1.0, 0.5])
    ax.set_title(r"$\alpha_{i} = \frac{1}{2}$")
    ax.set_xlabel(r"time $t$ (s)")
    fig.savefig("sci_usetex.pdf")


if __name__ == "__main__":
    main()
