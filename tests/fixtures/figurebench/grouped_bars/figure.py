"""FigureBench 夹具：两组并列柱状图（四个类别，高度写死）。"""

import matplotlib.pyplot as plt

X = [0, 1, 2, 3]
A = [3.2, 4.5, 2.9, 5.1]
B = [2.8, 4.9, 3.6, 4.4]
WIDTH = 0.35


def main():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.bar(X, A, width=WIDTH, color="#4c72b0", label="A")
    ax.bar([x + WIDTH for x in X], B, width=WIDTH, color="#55a868", label="B")
    ax.set_xticks([x + WIDTH / 2 for x in X], ["w1", "w2", "w3", "w4"])
    ax.set_ylabel("Yield (g)", fontsize=9)
    ax.legend(fontsize=8)
    fig.savefig("grouped_bars.pdf")


if __name__ == "__main__":
    main()
