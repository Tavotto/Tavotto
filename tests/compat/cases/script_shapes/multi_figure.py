"""一个脚本三张图，各存各的。"""
import matplotlib.pyplot as plt


def main():
    for name, ys in (("shape_multi_a", [1, 2, 3]),
                     ("shape_multi_b", [3, 2, 1]),
                     ("shape_multi_c", [2, 3, 1])):
        fig, ax = plt.subplots(figsize=(3.2, 2.2))
        ax.plot(ys)
        ax.set_title(name)
        fig.savefig(name + ".pdf")
