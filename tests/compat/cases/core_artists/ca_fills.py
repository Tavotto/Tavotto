"""填充类：fill / fill_between / fill_betweenx / stackplot。"""
import matplotlib.pyplot as plt
import numpy as np

T = np.linspace(0.0, 6.0, 40)
A = np.sin(T) + 1.5
B = np.cos(T) + 1.5


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.fill([0.5, 2.5, 3.5, 1.5], [0.5, 0.8, 2.2, 2.0], color="#9BC4E2",
            edgecolor="#2F6FB2")
    ax.set_title("Polygon fill")
    fig.savefig("art_fill.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(T, A, color="#2F6FB2")
    ax.fill_between(T, A - 0.3, A + 0.3, alpha=0.35, label="band")
    ax.set_title("fill_between")
    ax.legend()
    fig.savefig("art_fill_between.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.fill_betweenx(T, A - 0.3, A + 0.3, alpha=0.35)
    ax.set_title("fill_betweenx")
    fig.savefig("art_fill_betweenx.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.stackplot(T, A, B, labels=["A", "B"])
    ax.set_title("Stackplot")
    ax.legend(loc="upper right")
    fig.savefig("art_stackplot.pdf")
