"""入口叫 main()——注册表里最常见的一档。"""
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.scatter([1, 2, 3, 4], [1, 4, 2, 3])
    ax.set_title("entry=main")
    fig.savefig("shape_entry_main.pdf")
