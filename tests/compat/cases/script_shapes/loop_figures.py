"""常量 for 循环里逐张出图——静态扫描要能把循环展开。"""
import matplotlib.pyplot as plt

PANELS = ["shape_loop_1", "shape_loop_2", "shape_loop_3"]


def main():
    for i, name in enumerate(PANELS):
        fig, ax = plt.subplots(figsize=(3.0, 2.0))
        ax.plot([i, i + 1, i + 2])
        ax.set_title(name)
        fig.savefig(name + ".pdf")
