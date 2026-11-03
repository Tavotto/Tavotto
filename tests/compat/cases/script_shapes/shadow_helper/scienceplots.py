"""同目录 helper，名字与 PyPI 上的 SciencePlots（import 名 scienceplots）相同。"""
import matplotlib.pyplot as plt

PALETTE = ["#1b6ca8", "#e07b39", "#3a9e5f", "#8e5ba8"]


def apply():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7, "axes.linewidth": 0.8})
