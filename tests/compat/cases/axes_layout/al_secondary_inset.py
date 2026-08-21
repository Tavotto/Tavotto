"""次坐标轴与插图：secondary_xaxis / secondary_yaxis / inset_axes。"""
import matplotlib.pyplot as plt
import numpy as np

X = np.linspace(1.0, 10.0, 40)


def main():
    fig, ax = plt.subplots(figsize=(3.8, 2.6))
    ax.plot(X, 1.0 / X)
    ax.set_xlabel("wavelength (nm)")
    ax.secondary_xaxis("top", functions=(lambda v: 1000.0 / v,
                                         lambda v: 1000.0 / v)).set_xlabel(
        "wavenumber")
    ax.set_title("secondary_xaxis")
    fig.savefig("ax_secondary_x.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.6))
    ax.plot(X, X ** 1.5)
    ax.set_ylabel("counts")
    ax.secondary_yaxis("right", functions=(lambda v: v / 10.0,
                                           lambda v: v * 10.0)).set_ylabel(
        "counts (×10)")
    ax.set_title("secondary_yaxis")
    fig.savefig("ax_secondary_y.pdf")

    fig, ax = plt.subplots(figsize=(3.8, 2.6))
    ax.plot(X, np.log(X))
    inset = ax.inset_axes([0.55, 0.14, 0.4, 0.36])
    inset.plot(X[:12], np.log(X[:12]), color="#B4473C")
    inset.tick_params(labelsize=6)
    ax.set_title("inset_axes")
    fig.savefig("ax_inset.pdf")
