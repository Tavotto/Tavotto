"""导出修改后的 .py（ADR 0171）自己的扫描图库：`invariant_figures` 里没有的元素都加在这里。

`invariant_figures` 是全套不变式共用的那份，别的用例按编号写死了它的元素（`collections_6` …），
所以导出脚本要的图（多列图例、带框文字、箭头……）**只往这里追加**，一张图一个 stem，
加完把 stem 写进 `STEMS`。`tests/test_script_adjust.py` 的扫描与覆盖断言都读这里。
"""

from __future__ import annotations

SCRIPT_NAME = "fig_scriptadjust.py"
ENTRY = "main"
STEMS: tuple[str, ...] = ("AdjLegend",)

LIBRARY = """\\
import numpy as np
import matplotlib.pyplot as plt


def main():
    rng = np.random.RandomState(0)
    x = np.linspace(0.5, 6.0, 24)

    # ---- AdjLegend：带标题的两列图例（标题、列距、锚点；InvMix / InvCont 的图例没有标题） ----
    # 条目来自曲线、散点与柱：示意线三种类型都在。图例放在左下、离曲线远一点，改间距时框变大不出画布。
    fig, ax = plt.subplots(figsize=(4.4, 3.2))
    ax.plot(x, np.sin(x) + 2.0, marker="o", markersize=5, label="sin")
    ax.plot(x, np.cos(x) + 2.0, linestyle="--", label="cos")
    ax.scatter(x, rng.rand(len(x)) + 3.2, s=30, marker="^", label="noise")
    ax.bar([1.0, 2.0, 3.0], [0.5, 0.8, 0.6], width=0.4, label="bars")
    ax.set_ylim(-1.8, 4.6)
    ax.legend(loc="lower left", ncol=2, title="Series", title_fontsize=9, fontsize=8)
    fig.savefig("AdjLegend.pdf")
"""
