"""刻度：分类轴 / 日期轴 / 科学计数 / 自定义标签 / 次刻度 / 自定义
Formatter 与 Locator。

日期用**写死的日期**，绝不用 datetime.now()——当前日期会让 manifest 与像素
基线每天都变，那条门禁第二天就自己红了。
"""
import datetime as dt

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FixedLocator, FuncFormatter, MultipleLocator

X = np.linspace(0.0, 10.0, 40)
DAYS = [dt.date(2024, 1, 1) + dt.timedelta(days=7 * i) for i in range(8)]


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.bar(["alpha", "beta", "gamma", "delta"], [3, 5, 2, 4])
    ax.set_title("categorical axis")
    fig.savefig("ax_categorical.pdf")

    fig, ax = plt.subplots(figsize=(4.0, 2.4))
    ax.plot(DAYS, [1, 3, 2, 5, 4, 6, 5, 7])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate()
    ax.set_title("datetime axis")
    fig.savefig("ax_datetime.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(X, X * 1.0e6)
    ax.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
    ax.set_title("scientific notation")
    fig.savefig("ax_scinotation.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(X, np.sin(X))
    ax.set_xticks([0, 2.5, 5.0, 7.5, 10.0])
    ax.set_xticklabels(["zero", "quarter", "half", "third", "full"])
    ax.set_title("custom tick labels")
    fig.savefig("ax_custom_ticklabels.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(X, np.cos(X))
    ax.xaxis.set_major_locator(MultipleLocator(2.0))
    ax.xaxis.set_minor_locator(MultipleLocator(0.5))
    ax.tick_params(which="minor", length=2)
    ax.set_title("minor ticks")
    fig.savefig("ax_minor_ticks.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(X, X ** 2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:.0f} u"))
    ax.set_title("custom Formatter")
    fig.savefig("ax_custom_formatter.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(X, np.sqrt(X))
    ax.xaxis.set_major_locator(FixedLocator([0.0, 1.0, 4.0, 9.0]))
    ax.set_title("custom Locator")
    fig.savefig("ax_custom_locator.pdf")
