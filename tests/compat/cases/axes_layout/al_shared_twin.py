"""共享轴与孪生轴：sharex / sharey / twinx / twiny。"""
import matplotlib.pyplot as plt
import numpy as np

X = np.linspace(0.0, 5.0, 30)


def main():
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(3.8, 3.0), sharex=True)
    a1.plot(X, np.sin(X))
    a2.plot(X, np.cos(X))
    a2.set_xlabel("shared x")
    fig.suptitle("sharex")
    fig.savefig("ax_sharex.pdf")

    fig, (b1, b2) = plt.subplots(1, 2, figsize=(4.6, 2.4), sharey=True)
    b1.plot(X, np.sin(X))
    b2.plot(X, np.cos(X))
    b1.set_ylabel("shared y")
    fig.suptitle("sharey")
    fig.savefig("ax_sharey.pdf")

    fig, c1 = plt.subplots(figsize=(3.8, 2.4))
    c1.plot(X, np.sin(X), color="#2F6FB2")
    c1.set_ylabel("sin", color="#2F6FB2")
    c2 = c1.twinx()
    c2.plot(X, np.exp(X / 3), color="#B4473C")
    c2.set_ylabel("exp", color="#B4473C")
    c1.set_title("twinx")
    fig.savefig("ax_twinx.pdf")

    fig, d1 = plt.subplots(figsize=(3.8, 2.4))
    d1.plot(X, np.sin(X))
    d1.set_xlabel("x (a.u.)")
    d2 = d1.twiny()
    d2.set_xlim(0.0, 10.0)
    d2.set_xlabel("x (doubled)")
    d1.set_title("twiny")
    fig.savefig("ax_twiny.pdf")
