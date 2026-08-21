"""from helpers import ...：脚本自己所在目录必须在 sys.path 里。"""
import matplotlib.pyplot as plt

from _style_helpers import style_axes


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([1, 3, 2, 5])
    style_axes(ax, "Local helper")
    fig.savefig("shape_local_helper.pdf")
