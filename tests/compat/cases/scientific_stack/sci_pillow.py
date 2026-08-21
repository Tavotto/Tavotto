"""Pillow 读图 + imshow。"""
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


def main():
    with Image.open("sample.png") as im:
        arr = np.asarray(im.convert("RGB"))
    fig, ax = plt.subplots(figsize=(3.0, 3.0))
    ax.imshow(arr)
    ax.set_axis_off()
    ax.set_title("Pillow image")
    fig.savefig("sci_pillow.pdf")
