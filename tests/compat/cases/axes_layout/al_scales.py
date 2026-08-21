"""坐标刻度类型：线性 / log / loglog / semilogx / semilogy / symlog / 反转。"""
import matplotlib.pyplot as plt
import numpy as np

X = np.linspace(1.0, 100.0, 60)
S = np.linspace(-50.0, 50.0, 60)


def main():
    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    ax.plot(X, X * 2)
    ax.set_title("linear")
    fig.savefig("ax_linear.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    ax.plot(X, X ** 2)
    ax.set_yscale("log")
    ax.set_title("log y")
    fig.savefig("ax_log.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    ax.loglog(X, X ** 2)
    ax.set_title("loglog")
    fig.savefig("ax_loglog.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    ax.semilogx(X, np.log(X))
    ax.set_title("semilogx")
    fig.savefig("ax_semilogx.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    ax.semilogy(X, X ** 1.5)
    ax.set_title("semilogy")
    fig.savefig("ax_semilogy.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    ax.plot(S, S ** 3)
    ax.set_yscale("symlog", linthresh=10.0)
    ax.set_title("symlog")
    fig.savefig("ax_symlog.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    ax.plot(X, np.sqrt(X))
    ax.invert_yaxis()
    ax.invert_xaxis()
    ax.set_title("inverted axes")
    fig.savefig("ax_inverted.pdf")
