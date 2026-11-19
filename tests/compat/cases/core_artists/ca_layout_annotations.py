"""子图框 / 图幅坐标里手画的标注：位置是排版，不是数据（ADR 0100 修订 2026-11-18）。

照用户来图的写法（世界地图 + 震中 → 标签的折线引线）：底图是一张位图，引线是
`Line2D(transform=ax.transAxes)` + 白色描边路径效果，标签是 transAxes 文字，震中是数据坐标的散点；
再加上同一类的手画图例点（transAxes 散点）、线组、贴在子图框里的图片、表格、整张图上的分隔线组。
这些元素在 CompatBench 的「拖动」阶段里必须能拖——普查与别的阶段看不见「登记了、却按所在列表
把能力判成数据」这一类。

数据是确定性的（不用 np.random），像素基线与元素数两次运行之间不漂。
"""

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D


def main():
    fig = plt.figure(figsize=(6.0, 3.6))
    ax = fig.add_axes([0.06, 0.16, 0.88, 0.78])
    yy, xx = np.mgrid[0:40, 0:80]
    ax.imshow(np.sin(xx / 9.0) + np.cos(yy / 7.0), extent=(0, 80, 0, 40), cmap="Greys", alpha=0.35)
    epicentres = [(18, 26), (44, 12), (63, 31)]
    labels = [(0.08, 0.86, "Alpha"), (0.40, 0.08, "Bravo"), (0.92, 0.86, "Charlie")]
    for (ex, ey), (lx, ly, name) in zip(epicentres, labels):
        px, py = ax.transAxes.inverted().transform(ax.transData.transform((ex, ey)))
        elbow = (px + lx) / 2
        line = Line2D(
            [px, elbow, elbow, lx],
            [py, py, ly, ly],
            transform=ax.transAxes,
            color="#C51E30",
            lw=1.2,
            linestyle=(0, (3.0, 1.8)),
        )
        line.set_path_effects([pe.Stroke(linewidth=2.2, foreground="white"), pe.Normal()])
        ax.add_line(line)
        ax.text(
            lx,
            ly,
            name,
            transform=ax.transAxes,
            ha="center",
            va="bottom",
            fontsize=8,
            color="#C51E30",
        )
    ax.scatter([e[0] for e in epicentres], [e[1] for e in epicentres], c="#C51E30", s=30, zorder=5)
    ax.scatter([0.05], [0.12], transform=ax.transAxes, marker="^", c="#123D8C", s=40)
    ax.text(0.08, 0.12, "Group B", transform=ax.transAxes, va="center", fontsize=7)
    ax.add_collection(
        LineCollection(
            [[(0.70, 0.08), (0.80, 0.08)], [(0.70, 0.11), (0.80, 0.11)]],
            transform=ax.transAxes,
            colors="black",
            linewidths=1.0,
        )
    )
    ax.imshow(
        np.arange(9.0).reshape(3, 3),
        extent=(0.82, 0.95, 0.40, 0.62),
        transform=ax.transAxes,
        cmap="viridis",
    )
    ax.set_xlim(0, 80)
    ax.set_ylim(0, 40)
    ax.table(cellText=[["n", "3"]], bbox=[0.30, 0.70, 0.18, 0.10])
    fig.add_artist(
        LineCollection(
            [[(0.06, 0.08), (0.94, 0.08)]],
            transform=fig.transFigure,
            colors="#888888",
            linewidths=0.8,
        )
    )
    fig.savefig("art_layout_annotations.pdf")
