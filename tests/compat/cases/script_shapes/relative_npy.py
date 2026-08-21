"""相对路径读 .npy。"""
import matplotlib.pyplot as plt
import numpy as np


def main():
    arr = np.load("series.npy")
    fig, ax = plt.subplots(figsize=(3.2, 2.6))
    ax.imshow(arr, cmap="viridis")
    ax.set_title("Relative npy")
    fig.savefig("shape_relative_npy.pdf")
