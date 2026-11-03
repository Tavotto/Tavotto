"""FigureBench 夹具：2×2 子图、上下共享 x 轴；左列两个子图的刻度宽度不同，y 轴标题天然不对齐。"""

import matplotlib.pyplot as plt

X = [0, 1, 2, 3, 4, 5]
PANELS = (
    ("A: control", "Rate (1/s)", [0.0012, 0.0019, 0.0027, 0.0031, 0.0038, 0.0045]),
    ("B: treated", "Rate (1/s)", [0.0011, 0.0024, 0.0035, 0.0049, 0.0058, 0.0066]),
    ("C: dose", "Dose (mg)", [1, 3, 4, 6, 8, 9]),
    ("D: recovery", "Dose (mg)", [9, 7, 6, 4, 3, 2]),
)


def main():
    fig, axes = plt.subplots(2, 2, sharex=True, figsize=(6.0, 4.5))
    for ax, (title, ylabel, ys) in zip(axes.flat, PANELS):
        ax.plot(X, ys, color="#333333", linewidth=1.2, label=title)
        ax.set_title(title, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=8)
    for ax in axes[1]:
        ax.set_xlabel("Day", fontsize=8)
    fig.savefig("multi_panel.pdf")


if __name__ == "__main__":
    main()
