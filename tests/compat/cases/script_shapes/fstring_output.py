"""文件名由 f-string 从模块级常量拼出来。"""
import matplotlib.pyplot as plt

PREFIX = "shape_fstring"
PANEL = "panel"


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([4, 2, 5, 1])
    ax.set_title("f-string output")
    fig.savefig(f"{PREFIX}_{PANEL}.pdf")
