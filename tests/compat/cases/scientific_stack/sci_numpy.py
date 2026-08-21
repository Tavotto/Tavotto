"""纯 NumPy ndarray 输入——最基础的一档。"""
import matplotlib.pyplot as plt
import numpy as np

X = np.linspace(0.0, 2 * np.pi, 64)


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(X, np.sin(X), label="sin")
    ax.plot(X, np.cos(X), label="cos")
    ax.fill_between(X, np.sin(X), np.cos(X), alpha=0.2)
    ax.set_title("NumPy arrays")
    ax.legend()
    fig.savefig("sci_numpy.pdf")
