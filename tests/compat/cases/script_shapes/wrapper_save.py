"""存图包在一个工具函数里——stem 要跨函数传播才解得出来。"""
from pathlib import Path

import matplotlib.pyplot as plt

OUTDIR = Path(".")


def save_plot(fig, name):
    fig.savefig(OUTDIR / f"{name}.pdf")


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([1, 3, 2, 4])
    ax.set_title("Wrapper save")
    save_plot(fig, "shape_wrapper")
