"""非笛卡尔投影：polar（含极坐标柱状）。"""
import matplotlib.pyplot as plt
import numpy as np

THETA = np.linspace(0.0, 2 * np.pi, 24, endpoint=False)
R = 1.0 + 0.4 * np.cos(3 * THETA)


def main():
    fig, ax = plt.subplots(figsize=(3.2, 3.0), subplot_kw={"projection": "polar"})
    ax.plot(THETA, R, color="#2F6FB2")
    ax.set_title("Polar line")
    fig.savefig("art_polar.pdf")

    fig, ax = plt.subplots(figsize=(3.2, 3.0), subplot_kw={"projection": "polar"})
    ax.bar(THETA, R, width=0.2, bottom=0.2, alpha=0.7)
    ax.set_title("Polar bar")
    fig.savefig("art_polar_bar.pdf")
