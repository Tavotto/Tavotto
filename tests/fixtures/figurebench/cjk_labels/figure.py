"""FigureBench 夹具：中英混排的标题与轴标题（文字本身是要保持的内容）。"""

import matplotlib.pyplot as plt

X = [0, 1, 2, 3, 4]
Y = [0.0, 0.8, 1.5, 1.9, 2.1]


def main():
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.plot(X, Y, color="#1f77b4", linewidth=1.5, label="样品 A")
    ax.set_title("响应曲线 Response", fontsize=10)
    ax.set_xlabel("时间 (s)", fontsize=9)
    ax.set_ylabel("位移 (μm)", fontsize=9)
    fig.savefig("cjk_labels.pdf")


if __name__ == "__main__":
    main()
