"""stem 来自模块级常量 + 字符串拼接。"""
import matplotlib.pyplot as plt

STEM = "shape_module_const"
EXT = ".pdf"


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([1, 2, 1, 2])
    ax.set_title(STEM)
    fig.savefig(STEM + EXT)
