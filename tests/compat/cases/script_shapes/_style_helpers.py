"""被同目录脚本 import 的样式工具。它自己不出图，不该被当成绘图脚本。"""


def style_axes(ax, title):
    ax.set_title(title)
    ax.set_xlabel("x (a.u.)")
    ax.set_ylabel("y (a.u.)")
    ax.grid(True, linewidth=0.4, alpha=0.5)
    return ax
