"""逐段 / 逐点透明度的 Collection：同一色相、alpha 表示强弱（线组、散点）。"""
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

BLUE = (0.1, 0.3, 0.8)
BAND_Y = (0.88, 0.78, 0.68)
BAND_ALPHA = (0.25, 0.6, 1.0)
DOT_XY = np.array([(0.12, 0.25), (0.27, 0.25), (0.42, 0.25)])
DOT_ALPHA = (0.3, 0.7, 1.0)


class SwatchHandler(HandlerBase):
    def create_artists(self, legend, orig, xd, yd, width, height, fontsize, trans):
        return [Rectangle((xd, yd), width, height, facecolor=orig.get_color(), transform=trans)]


def main():
    fig = plt.figure(figsize=(4.0, 3.0))
    ax = fig.add_axes([0, 0, 1, 1])
    segs = [[(0.05, y), (0.45, y)] for y in BAND_Y]
    lc = LineCollection(segs, linewidths=10, label="band",
                        colors=[(*BLUE, a) for a in BAND_ALPHA])
    ax.add_collection(lc)
    # 与线组同色、同名的独立 marker、图例 proxy 与自定义 handler
    (dot,) = ax.plot([0.5], [BAND_Y[1]], "o", color=BLUE, ms=9, label="band")
    proxy = Line2D([], [], color=BLUE, lw=4)
    swatch = Line2D([], [], color=BLUE)
    ax.legend([proxy, lc, dot, swatch], ["band", "band", "band", "swatch"],
              handler_map={swatch: SwatchHandler()},
              loc="upper left", bbox_to_anchor=(0.58, 0.98), frameon=False)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()
    fig.savefig("art_linecoll_alpha.pdf")

    fig = plt.figure(figsize=(4.0, 3.0))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.scatter(DOT_XY[:, 0], DOT_XY[:, 1], s=500, edgecolors="none", label="pts",
               facecolors=[(*BLUE, a) for a in DOT_ALPHA])
    ax.legend(loc="upper left", frameon=False)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()
    fig.savefig("art_scatter_rgba.pdf")


if __name__ == "__main__":
    main()
