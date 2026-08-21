"""if __name__ == "__main__" 守卫下调用 main()。"""
import matplotlib.pyplot as plt


def build():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([1, 2, 3], [2, 1, 3], marker="o")
    ax.set_title("Guarded main")
    fig.savefig("shape_dunder.pdf")


if __name__ == "__main__":
    build()
