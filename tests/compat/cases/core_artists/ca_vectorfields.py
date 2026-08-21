"""矢量场：quiver / streamplot / barbs。"""
import matplotlib.pyplot as plt
import numpy as np

_x = np.linspace(-2.0, 2.0, 8)
_y = np.linspace(-2.0, 2.0, 8)
XX, YY = np.meshgrid(_x, _y)
U = -YY
V = XX


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.quiver(XX, YY, U, V, color="#2F6FB2")
    ax.set_title("quiver")
    fig.savefig("art_quiver.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.streamplot(_x, _y, U, V, color="#5B8C5A")
    ax.set_title("streamplot")
    fig.savefig("art_streamplot.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.barbs(XX[::2, ::2], YY[::2, ::2], U[::2, ::2] * 5, V[::2, ::2] * 5)
    ax.set_title("barbs")
    fig.savefig("art_barbs.pdf")
