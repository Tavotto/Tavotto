"""FigureBench 夹具：两条带标签的折线 + 图例 + 带单位的轴标题（数据写死）。"""

import matplotlib.pyplot as plt

T = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
CONTROL = [1.0, 1.4, 1.9, 2.1, 2.6, 2.9, 3.3]
TREATED = [1.0, 1.8, 2.7, 3.5, 4.1, 4.8, 5.2]


def main():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.plot(T, CONTROL, color="#1f77b4", linewidth=1.5, label="control")
    ax.plot(T, TREATED, color="#d62728", linewidth=1.5, label="treated")
    ax.set_title("Response", fontsize=10)
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Signal (mV)", fontsize=9)
    ax.set_xlim(0, 3)
    ax.legend(loc="upper left", fontsize=8)
    fig.savefig("line_two_series.pdf")


if __name__ == "__main__":
    main()
