"""等值线：contour（线）/ contourf（面）/ 带标注的 contour。"""
import matplotlib.pyplot as plt
import numpy as np

_x = np.linspace(-2.0, 2.0, 30)
_y = np.linspace(-2.0, 2.0, 30)
XX, YY = np.meshgrid(_x, _y)
ZZ = np.exp(-(XX ** 2 + YY ** 2))


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.contour(XX, YY, ZZ, levels=6, cmap="viridis")
    ax.set_title("contour")
    fig.savefig("art_contour.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    cf = ax.contourf(XX, YY, ZZ, levels=8, cmap="viridis")
    fig.colorbar(cf, ax=ax)
    ax.set_title("contourf")
    fig.savefig("art_contourf.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    cs = ax.contour(XX, YY, ZZ, levels=4, colors="#2F6FB2")
    ax.clabel(cs, inline=True, fontsize=7)
    ax.set_title("contour + clabel")
    fig.savefig("art_contour_labels.pdf")
