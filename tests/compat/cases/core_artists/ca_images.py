"""位图与网格类：imshow / matshow / pcolor / pcolormesh / hexbin。

pcolormesh 出的是 QuadMesh，pcolor 出的是 PolyCollection——形态完全不同，
分开测才看得出「哪一个 Tavotto 认得」。
"""
import matplotlib.pyplot as plt
import numpy as np

GRID = np.arange(64, dtype="float64").reshape(8, 8)
XS = np.linspace(0.0, 4.0, 60)
YS = (XS * 1.3) % 3.0


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    im = ax.imshow(GRID, cmap="viridis")
    fig.colorbar(im, ax=ax)
    ax.set_title("imshow")
    fig.savefig("art_imshow.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.matshow(GRID, cmap="plasma")
    ax.set_title("matshow")
    fig.savefig("art_matshow.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.pcolor(GRID, cmap="cividis")
    ax.set_title("pcolor")
    fig.savefig("art_pcolor.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    mesh = ax.pcolormesh(GRID, cmap="magma")
    fig.colorbar(mesh, ax=ax)
    ax.set_title("pcolormesh")
    fig.savefig("art_pcolormesh.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 2.8))
    ax.hexbin(XS, YS, gridsize=8, cmap="Greys")
    ax.set_title("hexbin")
    fig.savefig("art_hexbin.pdf")
