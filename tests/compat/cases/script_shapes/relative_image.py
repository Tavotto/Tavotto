"""相对路径读同目录的 PNG（plt.imread）。"""
import matplotlib.pyplot as plt


def main():
    img = plt.imread("sample.png")
    fig, ax = plt.subplots(figsize=(3.0, 3.0))
    ax.imshow(img)
    ax.set_title("Relative image")
    fig.savefig("shape_relative_image.pdf")
