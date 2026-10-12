"""#956：花纹色在 `rc_context` 里建的**文本框 / 图例框 / 线集合**——alpha 往返的重放夹具。

#950 / #874 只覆盖了 Patch / Collection 里的柱、矩形、fill_between、scatter；还有三个消费方同样直接调
`set_alpha`、在 matplotlib ≥3.11 上按环境 rc 重解析花纹色：

  文本背景框   `("text", "bbox_alpha")`              FancyBboxPatch  rc = blue
  图例框       `("legend", "framealpha")`             FancyBboxPatch  rc = red（框的花纹用 `set_hatch` 补上）
  线集合       `("linecoll", "alpha")`                LineCollection  rc = green

误差线 / 茎线的 LineCollection 成员也走 `Collection.set_alpha`，但它们是两点线段、没有可填充的面积，花纹画不出来
（3.11.2 / 3.10.9 实测 `set_alpha` 前后像素逐字节相同），所以没有可观测的分叉，也就没有可看护的行为：不钉、不进本夹具。

`build()` 返回 fig（worker 探针直接 exec 本文本取它），`main()` 在末尾存图；环境 rc 在 `build()` 末尾改成
`magenta`（与上面每一个创建时的值都不同）。本模块不 import matplotlib：夹具只是一段脚本文本。
"""

from __future__ import annotations

SCRIPT_NAME = "fig_rc_hatch_text_legend.py"
ENTRY = "main"
STEM = "RcHatchTextLegend"

LIBRARY = """\
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection


def build():
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    with plt.rc_context({"hatch.color": "blue"}):
        ax.text(0.5, 0.5, "hi", transform=ax.transAxes, fontsize=18,
                bbox=dict(hatch="//", facecolor="#cccccc"))
    with plt.rc_context({"hatch.color": "red"}):
        ax.plot([0, 1], [0, 1], label="a")
        leg = ax.legend(loc="upper left")
        leg.get_frame().set_hatch("//")
    with plt.rc_context({"hatch.color": "green"}):
        ax.add_collection(
            LineCollection([[(0.1, 0.1), (0.4, 0.5), (0.4, 0.1), (0.1, 0.1)]],
                           hatch="//", facecolor="#cccccc", edgecolor="k")
        )
    plt.rcParams["hatch.color"] = "magenta"  # 环境值：与上面每一个创建时的值都不同
    ax.set_xlim(-0.1, 1.1)
    ax.set_ylim(-0.1, 1.1)
    return fig


def main():
    fig = build()
    fig.savefig("RcHatchTextLegend.png")
"""

#: 名字 → (gid, prop)。`bbox_alpha` / `framealpha` 是 #956 点名的两个；`linecoll` 是 sweep 出来的同族消费方。
TARGETS = {
    "text_bbox": ("axes_0.texts_0", "bbox_alpha"),
    "legend_frame": ("axes_0.legend", "framealpha"),
    "linecoll": ("axes_0.linecoll_0", "alpha"),
}
