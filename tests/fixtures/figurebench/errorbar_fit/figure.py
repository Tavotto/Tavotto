"""FigureBench 夹具：带误差棒的测量点 + 最小二乘拟合线（拟合在脚本里用纯 Python 算，数据写死）。"""

import matplotlib.pyplot as plt

X = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
Y = [2.2, 4.1, 6.3, 7.9, 10.2, 14.8]
YERR = [0.3, 0.3, 0.4, 0.4, 0.5, 0.6]


def _least_squares(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return slope, my - slope * mx


def main():
    fit_x, fit_y = X, Y
    slope, intercept = _least_squares(fit_x, fit_y)
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    ax.errorbar(
        X, Y, yerr=YERR, fmt="o", color="#1f77b4", elinewidth=1.2, capsize=2, label="measured"
    )
    ax.plot(X, [slope * x + intercept for x in X], color="#d62728", linestyle="-", label="fit")
    ax.set_title(f"slope = {slope:.3f}", fontsize=9)
    ax.set_xlabel("Concentration (mM)", fontsize=9)
    ax.set_ylabel("Absorbance (a.u.)", fontsize=9)
    ax.legend(fontsize=8)
    fig.savefig("errorbar_fit.pdf")


if __name__ == "__main__":
    main()
