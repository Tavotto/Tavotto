"""三维曲面 + 三维散点（固定数组，无随机数）。"""
import matplotlib.pyplot as plt
import numpy as np

U = np.linspace(-2, 2, 24)
X, Y = np.meshgrid(U, U)
Z = np.exp(-(X ** 2 + Y ** 2) / 2)
# 确定性的伪散布点：无理数步长取小数部分
K = np.arange(30)
PX = (K * 0.6180339887 % 1) * 4 - 2
PY = (K * 0.7548776662 % 1) * 4 - 2


def main():
    fig = plt.figure(figsize=(3.2, 3.0))
    ax = fig.add_subplot(projection="3d")
    ax.plot_surface(X, Y, Z, cmap="viridis", linewidth=0, antialiased=False)
    ax.scatter(PX, PY, np.full(30, 1.1), color="#d62728", s=8)
    ax.view_init(elev=28, azim=-60)
    ax.set_title("surface")
    fig.savefig("art_3d_surface_scatter.pdf")


if __name__ == "__main__":
    main()
