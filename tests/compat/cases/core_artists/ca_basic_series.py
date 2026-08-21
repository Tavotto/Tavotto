"""最高频的几种数据系列。数据全部写死，没有随机数。"""
import matplotlib.pyplot as plt
import numpy as np

X = np.linspace(0.5, 5.0, 10)
Y = np.array([1.0, 2.5, 2.0, 3.5, 3.0, 4.5, 4.0, 5.5, 5.0, 6.5])


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(X, Y, label="signal", color="#2F6FB2", linewidth=1.6)
    ax.plot(X, Y * 0.6, label="baseline", linestyle="--")
    ax.set_title("Line plot")
    ax.set_xlabel("x (a.u.)")
    ax.set_ylabel("y (a.u.)")
    ax.legend()
    fig.savefig("art_plot.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.scatter(X, Y, s=28, c="#B4473C", label="points")
    ax.set_title("Scatter")
    ax.legend()
    fig.savefig("art_scatter.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.bar(["a", "b", "c", "d"], [3.0, 5.0, 2.0, 4.0], color="#5B8C5A")
    ax.set_title("Vertical bars")
    fig.savefig("art_bar.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.barh(["a", "b", "c", "d"], [3.0, 5.0, 2.0, 4.0])
    ax.set_title("Horizontal bars")
    fig.savefig("art_barh.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.errorbar(X, Y, yerr=0.35, xerr=0.15, capsize=3, label="measured")
    ax.set_title("Errorbar")
    ax.legend()
    fig.savefig("art_errorbar.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.step(X, Y, where="mid", label="steps")
    ax.set_title("Step")
    ax.legend()
    fig.savefig("art_step.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.stem(X, Y)
    ax.set_title("Stem")
    fig.savefig("art_stem.pdf")
