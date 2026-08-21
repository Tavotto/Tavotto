"""plt.savefig()（当前 figure），不是 Figure.savefig()。"""
import matplotlib.pyplot as plt


def main():
    plt.figure(figsize=(3.6, 2.4))
    plt.plot([5, 3, 4, 1])
    plt.title("plt.savefig")
    plt.savefig("shape_plt_savefig.pdf")
