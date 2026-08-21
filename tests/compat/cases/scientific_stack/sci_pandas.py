"""pandas 的 DataFrame.plot()——它自己建 Figure/Axes，写法与手搓完全不同。"""
import matplotlib.pyplot as plt
import pandas as pd

DF = pd.DataFrame({"x": [1, 2, 3, 4, 5],
                   "alpha": [2.0, 4.0, 3.0, 5.0, 4.5],
                   "beta": [1.0, 2.5, 2.0, 3.5, 3.0]}).set_index("x")


def main():
    ax = DF.plot(figsize=(3.8, 2.4), title="pandas line")
    ax.figure.savefig("sci_pandas_line.pdf")

    ax = DF.plot.bar(figsize=(3.8, 2.4), title="pandas bar")
    ax.figure.savefig("sci_pandas_bar.pdf")

    ax = DF.reset_index().plot.scatter(x="x", y="alpha", figsize=(3.8, 2.4),
                                       title="pandas scatter")
    ax.figure.savefig("sci_pandas_scatter.pdf")

    plt.close("all")
