"""FigureBench 夹具：对数 y 轴上的指数衰减（数据全为正，写死）。"""

import matplotlib.pyplot as plt

X = [0, 1, 2, 3, 4, 5, 6]
Y = [100.0, 37.0, 13.5, 5.0, 1.8, 0.67, 0.25]


def main():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.semilogy(X, Y, "o-", color="#2ca02c", markersize=4, label="decay")
    ax.set_xlabel("Time (h)", fontsize=9)
    ax.set_ylabel("Activity (Bq)", fontsize=9)
    fig.savefig("log_decay.pdf")


if __name__ == "__main__":
    main()
