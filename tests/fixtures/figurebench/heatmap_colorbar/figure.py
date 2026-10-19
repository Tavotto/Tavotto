"""FigureBench 夹具：固定 4×5 矩阵的热图 + 显式色阶范围 + 带标签的色条（数据写死）。"""

import matplotlib.pyplot as plt

Z = [
    [0.5, 1.2, 2.8, 3.1, 2.0],
    [1.1, 3.4, 6.2, 5.9, 3.3],
    [0.9, 4.0, 9.1, 7.7, 4.2],
    [0.2, 1.8, 3.9, 3.0, 1.4],
]


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.8))
    im = ax.imshow(Z, cmap="magma", vmin=0, vmax=10)
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("Intensity (a.u.)", fontsize=8)
    ax.set_title("Map", fontsize=9)
    fig.savefig("heatmap_colorbar.pdf")


if __name__ == "__main__":
    main()
