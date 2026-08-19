"""示例图库的统一出版样式。

真实项目里这个文件通常长得多（期刊配色、字体嵌入、多种画幅规格），
但 Tavotto 只依赖两件事：

  1. 图由脚本里的 `main()` 生成；
  2. 出图走 `save(fig, stem)` 或 `fig.savefig(...)`。

Tavotto 的 worker 会拦截这两个调用把 Figure 留在内存里——所以在编辑器里
改元素时**不会**真的重写磁盘上的 PDF，你的脚本和产物都不被动。
"""
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt

# 投稿常见要求：9pt 正文、Times 系衬线、细线宽、刻度朝内
mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "legend.fontsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "axes.linewidth": 0.6,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "legend.frameon": False,
    "figure.dpi": 120,
})

# 单栏 8cm / 双栏 15cm 是最常见的两档投稿宽度
COL_1 = 8 / 2.54
COL_2 = 15 / 2.54

PALETTE = ["#1b3a6b", "#c0562a", "#4a7c59", "#8c6d31"]


def save(fig, stem: str, outdir: str | Path | None = None) -> Path:
    """存成矢量 PDF，放在脚本同级目录。"""
    out = Path(outdir) if outdir else Path(__file__).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}.pdf"
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return path
