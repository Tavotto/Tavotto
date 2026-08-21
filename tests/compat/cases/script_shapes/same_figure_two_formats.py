"""同一张 Figure 存两种格式。必须只算**一张**图，不是两个面板。"""
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([1, 4, 9, 16])
    ax.set_title("Two formats, one figure")
    fig.savefig("shape_two_formats.pdf")
    fig.savefig("shape_two_formats.png", dpi=150)
