"""SciPy 拟合 + 画拟合结果。验的是「科学依赖 + matplotlib 输出」这条链路。"""
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit

XD = np.linspace(0.0, 4.0, 20)
YD = np.array([0.05, 0.42, 0.79, 1.15, 1.42, 1.71, 1.94, 2.13, 2.31, 2.44,
               2.57, 2.66, 2.75, 2.82, 2.87, 2.92, 2.95, 2.97, 2.99, 3.00])


def model(x, a, b):
    return a * (1.0 - np.exp(-b * x))


def main():
    popt, _ = curve_fit(model, XD, YD, p0=[3.0, 1.0])
    fig, ax = plt.subplots(figsize=(3.8, 2.4))
    ax.scatter(XD, YD, s=16, label="data")
    ax.plot(XD, model(XD, *popt), color="#B4473C",
            label=f"fit a={popt[0]:.2f}")
    ax.set_title("SciPy curve_fit")
    ax.set_xlabel("t (s)")
    ax.set_ylabel("signal")
    ax.legend()
    fig.savefig("sci_scipy_fit.pdf")
