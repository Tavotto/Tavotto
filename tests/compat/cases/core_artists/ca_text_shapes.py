"""文字、标注箭头、独立箭头与几何形状。"""
import matplotlib.pyplot as plt
from matplotlib.patches import (Circle, Ellipse, FancyArrowPatch, PathPatch,
                                Polygon, Rectangle)
from matplotlib.path import Path as MplPath


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2, 3], [1, 3, 2, 4])
    ax.text(0.15, 0.82, "inline note", transform=ax.transAxes)
    ax.annotate("peak", xy=(1.0, 3.0), xytext=(1.8, 3.6),
                arrowprops=dict(arrowstyle="->", color="#2A6F3C"))
    ax.set_title("Text & annotate")
    fig.savefig("art_text_annotate.pdf")

    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([0, 1, 2, 3], [1, 3, 2, 4])
    ax.add_patch(FancyArrowPatch(posA=(0.4, 1.5), posB=(2.4, 3.4),
                                 transform=ax.transData, arrowstyle="-|>",
                                 mutation_scale=10, color="#76008A"))
    ax.set_title("Standalone arrow")
    fig.savefig("art_arrow_patch.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    ax.set_xlim(0, 4)
    ax.set_ylim(0, 4)
    ax.add_patch(Rectangle((0.3, 0.3), 1.0, 0.8, facecolor="#9BC4E2",
                           edgecolor="#2F6FB2"))
    ax.add_patch(Circle((2.0, 1.0), 0.5, facecolor="#E2C89B"))
    ax.add_patch(Ellipse((3.0, 2.4), 1.2, 0.6, facecolor="#C9A0DC"))
    ax.add_patch(Polygon([[0.5, 2.0], [1.5, 3.5], [0.2, 3.2]],
                         facecolor="#5B8C5A", alpha=0.8))
    ax.set_title("Shapes")
    fig.savefig("art_shapes.pdf")

    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 3)
    verts = [(0.5, 0.5), (0.5, 2.5), (2.5, 2.5), (2.5, 0.5), (0.5, 0.5)]
    codes = [MplPath.MOVETO, MplPath.CURVE3, MplPath.CURVE3,
             MplPath.LINETO, MplPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MplPath(verts, codes), facecolor="none",
                           edgecolor="#B4473C", linewidth=1.6))
    ax.set_title("PathPatch")
    fig.savefig("art_pathpatch.pdf")
