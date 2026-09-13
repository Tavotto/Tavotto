"""教程项目的出版样式（自包含）。

这个文件与真实项目里的 `paper_style.py` 扮演同一个角色：统一字号、线宽、
配色，并提供 `save(fig, stem)`。Tavotto 会拦截 `save()` 里的 `fig.savefig`
把 Figure 留在内存里，所以在编辑器里改元素**不会**重写磁盘上的 PDF。

只用 matplotlib 自带的 STIX 字体：教程必须在 macOS / Windows / pip 环境里
画出同一张图，且不依赖任何联网字体或系统字体。STIX 是 Times 一族的衬线体，
在默认出版规范的字体白名单里——教程图本身不该带着「字体不合规范」的提示。
"""

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt

mpl.rcParams.update(
    {
        "font.family": "STIXGeneral",
        "mathtext.fontset": "stix",
        "font.size": 9,
        "axes.labelsize": 9,
        "axes.labelweight": "bold",
        "axes.titlesize": 9,
        "legend.fontsize": 9,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "axes.linewidth": 0.75,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "legend.frameon": False,
        "figure.dpi": 120,
    }
)

# 双栏 15 cm 的版面上并排两张图，每张 6.5 cm（教程画布就是这样摆的）
COL_1 = 6.5 / 2.54

PALETTE = ["#1b3a6b", "#c0562a", "#4a7c59", "#8c6d31"]


def save(fig, stem: str, outdir: str | Path | None = None) -> Path:
    """存成矢量 PDF，放在脚本同级目录。"""
    out = Path(outdir) if outdir else Path(__file__).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}.pdf"
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return path
