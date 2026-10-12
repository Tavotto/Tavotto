"""#874：花纹色在 `rc_context` 里建的图——alpha / fill 往返的重放夹具（两条用例共用一份）。

五类 artist 各在**不同的** `hatch.color` 下创建，创建完 `main()` 末尾把环境值改成与它们都不同的
`magenta`（3.11 默认 `'edge'`，所以必须显式改，否则 `'edge'` 那一块撞上环境值看不出分叉）：

  柱系列 / 单柱   rc = red                       （显式非 edge 颜色）
  独立 Rectangle  rc = 半透明 RGBA (0,.5,0,.25)   （内在 alpha）
  fill_between    rc = 'edge'（≥3.11；≤3.10 的 rc 不收 'edge'，换 blue）
  scatter         同 fill_between

本模块不 import matplotlib：夹具只是一段脚本文本，worker 在科学栈解释器里跑。
"""

from __future__ import annotations

SCRIPT_NAME = "fig_rc_hatch.py"
ENTRY = "main"
STEM = "RcHatch"

LIBRARY = """\
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    new = hasattr(Rectangle, "set_hatchcolor")
    with plt.rc_context({"hatch.color": "red"}):
        ax.bar([0, 1], [3, 5], 0.6, facecolor="#cccccc", hatch="//")
    with plt.rc_context({"hatch.color": (0.0, 0.5, 0.0, 0.25)}):
        ax.add_patch(Rectangle((2.2, 0.5), 0.8, 2.5, facecolor="#cccccc", hatch="xx"))
    with plt.rc_context({"hatch.color": "edge" if new else "blue"}):
        ax.fill_between([3.5, 4.5, 5.5], [1, 4, 2], facecolor="#cccccc", hatch="\\\\\\\\")
        ax.scatter([4, 5], [5, 6], s=300, facecolor="#cccccc", hatch="++")
    plt.rcParams["hatch.color"] = "magenta"  # 环境值：与上面每一个创建时的值都不同
    ax.set_xlim(-0.8, 6)
    ax.set_ylim(0, 7)
    fig.savefig("RcHatch.png")
"""

SERIES = "axes_0.barseries_0"
BAR = "axes_0.barseries_0.bar_0"
RECT = "axes_0.patches_2"
FILL = "axes_0.fill_0"
SCATTER = "axes_0.scatter_1"

#: (gid, 该 gid 上能编辑的会重解析花纹色的 prop 集合)
TARGETS = {
    "series": (SERIES, ("alpha",)),
    "bar": (BAR, ("alpha",)),
    "rect": (RECT, ("alpha", "fill")),
    "rect_fill_first": (RECT, ("fill",)),  # 第一次触碰是 fill：钉住不能只靠 alpha 那一路
    "fill_between": (FILL, ("alpha",)),
    "scatter": (SCATTER, ("alpha",)),
}
