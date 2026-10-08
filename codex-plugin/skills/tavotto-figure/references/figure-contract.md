# 图文件契约（写脚本前读一遍）

违反任何一条，图在 Tavotto 里都只能当死图排版，双击进不去图内编辑。

## 1. 脚本与产物同目录，而且必须先落成文件

```
figures/
  fig_removal_rate.py       ← 脚本
  Fig1_removal_rate.pdf     ← 它产出的图
```

**绝不用 `python -c`、`python - <<EOF` 或临时目录出图。** Tavotto 靠「产物 stem ↔
产出它的脚本」这条映射把一张图变成可参数化面板：没有脚本文件，用户拿到的就是一张
改不了的死图；脚本躺在别的目录，映射同样建立不起来。

落点：用户当前工作目录下的 `figures/`。那儿已经有一个图库（目录里有
`tavotto_registry.json`）就沿用它，别另起炉灶。

## 2. 入口是一个无参函数

```python
def main():
    ...

if __name__ == "__main__":
    main()
```

Tavotto 的渲染 worker 就是 `import 这个模块` 再 `getattr(module, "main")()`，
所以：

* **import 期不许有副作用**——顶层不要跑计算、读大文件、画图；
* `main()` 不能有必填参数。

## 3. 产物名写成字面量，不要来自运行期

```python
OUT = Path(__file__).resolve().parent
fig.savefig(OUT / "Fig1_removal_rate.pdf")
```

模块级常量、f-string 拼常量、`OUT / "..."`、`.with_suffix()` 这些都能被静态解析。
**不能**来自 `sys.argv`、时间戳、随机串或「遍历数据目录得到的名字」——那样注册表
登记不了，你会在自检里看到 `parameterizable: false`。

一个 stem 只属于一张图；一个脚本出多张图就用多个不同的 stem。

## 4. 存矢量 PDF

用 `fig.savefig(OUT / "<Stem>.pdf")`。不要拿 300 dpi 的 PNG 当交付物，也不要
`rasterized=True`（`imshow` 的位图除外）——Tavotto 导出的是真矢量 PDF，投稿要的正是它。

## 5. 可复现

随机数固定种子（`rng = np.random.default_rng(20260818)`）；**不要 `plt.show()`**。

## 6. 脚本是「被 Tavotto 导入的」，不是独立程序

用户常让你「写个脚本画图」，默认写出来的是独立运行版（弹窗选文件、Pillow 画图、
`plt.show()`），放进 Tavotto 全跑不起来。按下面写：

* **只用 Matplotlib 画图**。Tavotto 只捕获 Matplotlib figure；Pillow / OpenCV 直接画出的
  图片只能当位图导入排版，不能逐元素编辑。
* **不弹窗、不等输入**：不用 tkinter / Qt 文件对话框、`input()`、argparse 必填参数。数据文件
  放项目里用相对路径（`Path(__file__).resolve().parent / "data" / "x.csv"`），或写成脚本
  顶部的常量；路径别从运行期交互得来。
* **不 `plt.show()`**（会阻塞渲染 worker），只 `savefig`。
* **依赖分两档**：处处可用的只有 numpy / matplotlib / pillow（插件/worker 便携依赖集：基础依赖
  `pillow` 加 `worker` extra，出处 `pyproject.toml`；pillow 只用来读图、处理数据，出图仍须 Matplotlib）；
  scipy / pandas / seaborn 仅桌面版内置环境有（出处 `packaging/runtime-lock.json` 的 `top_level`），
  其他环境可能要用户授权安装。`tests/test_codex_plugin.py` 看护两处与出处一致。
* **默认环境里没有的包**（如 ROOT）：用户在 Tavotto 里选了装有它的 Python，就照常 import 做计算、
  Matplotlib 出图，别删用户环境里能用的计算；否则把重计算拆成**单独脚本**先导出 CSV，画图脚本只读 CSV。
* **改造用户已有脚本**：保留原有计算与配色，只换掉上面这些独立运行的部分，不顺手重写。

## 模板

```python
"""Fig1: 温度对去除率的影响（数据来自 data/runs.csv）。"""
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent
COL_1, COL_2 = 8 / 2.54, 15 / 2.54          # 单栏 / 双栏（英寸）

mpl.rcParams.update({
    # 字体按开工问题的答案；Arial 用 "font.family": "sans-serif" + "font.sans-serif"
    "font.family": "serif",
    # 有中文就把中文字体也加进来，否则导出 PDF 里是方框
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    # 全部 > 8pt：正好 8pt 的图例/刻度会被预检当阻断项拦下
    "font.size": 9, "axes.labelsize": 9, "axes.titlesize": 9,
    "legend.fontsize": 9, "xtick.labelsize": 9, "ytick.labelsize": 9,
    # 轴标题默认加粗（用户特殊要求才改）
    "axes.labelweight": "bold",
    # 外框 0.75pt（规范档位之一），刻度朝内、只留主刻度
    "axes.linewidth": 0.75, "xtick.direction": "in", "ytick.direction": "in",
    "xtick.minor.visible": False, "ytick.minor.visible": False,
    # 图例加不加框按开工问题的答案（默认无框）
    "legend.frameon": False,
})


def main():
    t = np.array([1000, 1500, 2000, 2500, 3000])
    rate = np.array([9.8, 13.1, 15.4, 18.2, 20.1])
    err = np.array([0.6, 0.5, 0.7, 0.6, 0.9])

    fig, ax = plt.subplots(figsize=(COL_1, COL_1 * 0.72))
    # 线宽取规范档位（0.5/0.75/1.0/1.5）
    ax.errorbar(t, rate, yerr=err, marker="o", ms=3.5, lw=1.0, capsize=2.5,
                color="#1b3a6b", label="Sample A")
    ax.set_xlabel("Temperature (K)")            # 轴标题写成 Title (unit)
    ax.set_ylabel(r"Removal rate ($\mathrm{mg\,h^{-1}}$)")
    ax.legend(loc="lower right")
    fig.tight_layout(pad=0.4)
    fig.savefig(OUT / "Fig1_removal_rate.pdf")


if __name__ == "__main__":
    main()
```
