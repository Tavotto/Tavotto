---
name: magplot-figure
description: 画 matplotlib 论文级图表，并把成图交给 Magplot 继续用鼠标微调（拖图例、改字号线宽、调刻度、拼版导出矢量 PDF）。用户要画图、出图、做 figure、画折线/柱状/散点/误差棒、做论文配图、scientific plot / publication figure，或提到 Magplot 时使用。
---

# Magplot Figure

一句话：**数据与结构归代码，版式微调归鼠标。**

你只负责把图做成「Magplot 能接手」的形状，然后交出去。图例位置、字号、线宽、
刻度朝向这些，用户在 Magplot 里点几下就改完了——**不要在对话里追问这类参数，
更不要为这种改动重跑脚本**。

## 硬性约定

违反任何一条，图在 Magplot 里都只能当死图排版，双击进不去图内编辑。

### 1. 脚本与产物同目录，而且必须先落成文件

```
figures/
  fig_removal_rate.py       ← 脚本
  Fig1_removal_rate.pdf     ← 它产出的图
```

**绝不用 `python -c`、`python - <<EOF` 或临时目录出图。** Magplot 靠「产物 stem ↔
产出它的脚本」这条映射把一张图变成可参数化面板：没有脚本文件，用户拿到的就是一张
改不了的死图；脚本躺在别的目录，映射同样建立不起来。

落点：用户当前工作目录下的 `figures/`。那儿已经有一个图库（目录里有
`mm_registry.json`）就沿用它，别另起炉灶。

### 2. 入口是一个无参函数

```python
def main():
    ...

if __name__ == "__main__":
    main()
```

Magplot 的渲染 worker 就是 `import 这个模块` 再 `getattr(module, "main")()`，
所以：

* **import 期不许有副作用**——顶层不要跑计算、读大文件、画图；
* `main()` 不能有必填参数。

### 3. 产物名写成字面量，不要来自运行期

```python
OUT = Path(__file__).resolve().parent
fig.savefig(OUT / "Fig1_removal_rate.pdf")
```

模块级常量、f-string 拼常量、`OUT / "..."`、`.with_suffix()` 这些都能被静态解析。
**不能**来自 `sys.argv`、时间戳、随机串或「遍历数据目录得到的名字」——那样注册表
登记不了，你会在自检里看到 `parameterizable: false`。

一个 stem 只属于一张图；一个脚本出多张图就用多个不同的 stem。

### 4. 存矢量 PDF

用 `fig.savefig(OUT / "<Stem>.pdf")`。不要拿 300 dpi 的 PNG 当交付物，也不要
`rasterized=True`（`imshow` 的位图除外）——Magplot 导出的是真矢量 PDF，投稿要的正是它。

### 5. 可复现

随机数固定种子（`rng = np.random.default_rng(20260818)`）；**不要 `plt.show()`**。

### 6. 投稿默认值

单栏 8 cm、双栏 15 cm 宽；正文 9 pt、图例 8 pt；刻度朝内；图例无边框；
数学式用 mathtext（`$\mathrm{cm^{-1}}$`）。用户另有期刊要求就按用户的。

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
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9, "axes.labelsize": 9, "axes.titlesize": 9,
    "legend.fontsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.linewidth": 0.6, "xtick.direction": "in", "ytick.direction": "in",
    "legend.frameon": False,
})


def main():
    t = np.array([1000, 1500, 2000, 2500, 3000])
    rate = np.array([9.8, 13.1, 15.4, 18.2, 20.1])
    err = np.array([0.6, 0.5, 0.7, 0.6, 0.9])

    fig, ax = plt.subplots(figsize=(COL_1, COL_1 * 0.72))
    ax.errorbar(t, rate, yerr=err, marker="o", ms=3.5, lw=1.2, capsize=2.5,
                color="#1b3a6b", label="Sample A")
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel(r"Removal rate ($\mathrm{mg\,h^{-1}}$)")
    ax.legend(loc="lower right")
    fig.tight_layout(pad=0.4)
    fig.savefig(OUT / "Fig1_removal_rate.pdf")


if __name__ == "__main__":
    main()
```

## 交接给 Magplot

脚本写好后执行本技能自带的（路径相对本技能目录）：

```
python3 scripts/handoff.py <脚本路径>
```

它做三件事：产物缺失或比脚本旧就先跑一遍脚本 → 把产物登记进图库注册表 →
唤起 Magplot（桌面应用优先，没装就用浏览器模式）。**退出码非零就是没做完**
（0 成功 / 1 脚本报错 / 2 路径或交接失败 / 3 这台机器上用不了 Magplot /
4 图不可参数化）。

用户只装了 Magplot 桌面程序也没关系——不必让他再装 Python，也不必让他配
`MAGPLOT_CLI`，脚本会自己找到桌面版自带的命令行。

输出是一行 JSON，**你必须读**：

* `"parameterizable": true` —— 成了，交接完成。
* `"parameterizable": false` —— **没成，要修**。多半是脚本没和产物放在同一个目录
  （约定 1），或产物名静态解不出（约定 3）。改完重跑重新交接，不要把它当噪音略过。
* `"error_code": "magplot_missing"` —— 用户机器上没有 Magplot。按输出里的 `hint`
  引导安装；图已经画好了，脚本和产物都在原处，装完再执行同一条命令即可。
* `"error_code": "desktop_found_cli_missing"` —— **他装了桌面版，只是版本旧**
  （那一版没带命令行）。让他去 Releases 更新一次，**不要**让他「先装 Magplot」
  ——他会去装一个已经装着的东西，然后发现还是不行。
* `"error_code": "registry_write_failed"` —— 图库目录不可写。把图和脚本换到一个
  可写的目录，或让用户修好权限，然后重新交接。原文件一个字节都没动。
* 顺便留意 `conflicts`（两个脚本抢同一个 stem）和 `dynamic_names`（某些脚本的产出名
  静态解不出）——只报告不自动裁决，需要时告诉用户。

* `"update"` —— 插件自己有新版本时才出现（每 24 小时最多查一次）。
  `status` 为 `available` 时**顺口告诉用户一句**就行：让他执行
  `codex plugin marketplace upgrade magplot` 再重载 Codex。**别为这件事停下手里
  的活**，也别反复提——图已经交接好了，这只是个提醒。里面还可能有
  `magplot` 字段，那是说本机的 Magplot 版本低于新版插件的要求，让他去
  Releases 更新 Magplot（**跟插件是两码事，别混着说**）。

完整的错误码清单与排障步骤在 `../../../docs/handoff-protocol.md`。

## 交接之后就收手

回一句话：这张图画了什么、落在哪个文件，然后告诉用户——图例、字号、线宽、刻度、
元素位置直接在 Magplot 里拖或改，改完在那儿导出 PDF。

用户回来提要求时先分清归谁做，**不要默认重写代码**：

| 用户想改 | 谁来做 |
| --- | --- |
| 图例挪位置 / 字号 / 线宽 / 颜色 / marker 样式 | Magplot（拖、点） |
| 刻度朝内朝外、刻度标签字号 | Magplot |
| 标题、轴标签的文字与位置 | Magplot |
| 多图拼版、加箭头标注、加 (a)(b) 编号 | Magplot |
| 数据本身、坐标范围、对数/线性、加一条新曲线 | 代码（回来改脚本） |
| colorbar 方向、子图数量与结构 | 代码 |

改代码之后**再交接一次**（同一条命令）：Magplot 会重扫产物并定位到这张图，
用户已经排好的版和已经调过的元素不会丢。

能改与不能改的完整清单见 `references/compatibility.md`。
