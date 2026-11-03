"""twinx + subplots_adjust 手动留白：右侧第二 y 轴不能被布局吃掉。"""
import matplotlib.pyplot as plt
import numpy as np

T = np.linspace(0, 10, 50)


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    fig.subplots_adjust(left=0.15, right=0.82, bottom=0.18, top=0.86)
    ax.plot(T, np.sin(T), color="#1b6ca8")
    ax2 = ax.twinx()
    ax2.plot(T, 10 + 3 * np.cos(T), color="#e07b39", ls="--")
    ax.set_title("twin")
    fig.savefig("lay_twin_manual.pdf")


if __name__ == "__main__":
    main()
