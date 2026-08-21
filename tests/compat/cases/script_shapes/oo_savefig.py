"""官方推荐的 OO 写法：显式 Figure/Axes，显式 savefig。"""
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 2.6))
    ax.plot([0, 1, 2, 3], [0, 1, 4, 9], label="quadratic")
    ax.set_title("OO API")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.legend()
    fig.savefig("shape_oo.pdf")
