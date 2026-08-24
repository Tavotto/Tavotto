---
name: tavotto-figure
description: 画 matplotlib 论文级图表，并用 Tavotto 继续微调（拖图例、改字号线宽、调刻度、按出版规范预检、导出矢量 PDF）。用户要画图、出图、做 figure、画折线/柱状/散点/误差棒、做论文配图、scientific plot / publication figure，或提到 Tavotto 时使用。
---

# Tavotto Figure

一句话：**数据与结构归代码，版式微调归鼠标。**

你只负责把图做成「Tavotto 能接手」的形状。图例位置、字号、线宽、刻度朝向这些，
用 Tavotto 的 MCP 工具改（在 Codex 里就能改完）——**不要在对话里追问这类参数，
更不要为这种改动重跑脚本**。唯一的例外是下面「开工」一节的三个问题
（画幅宽度 / 字体 / 图例加不加框）：那三件事决定整张图的骨架，动手前先问清。

## 开工：先同步插件，再问三件事

### 同步插件

每次用户 @Tavotto（或隐式触发本技能）开始画图前，先跑一次：

```
codex plugin marketplace add Tavotto/Tavotto && codex plugin add tavotto@tavotto
```

这一步把插件对齐到最新版。**它是尽力而为的**：离线、命令失败、marketplace
不可达都不算事故——报一句就继续画图，绝不为此阻塞出图，也绝不重试纠缠。
装到新版后**这个会话里的工具不会自动更新**，需要新开会话才生效（顺口提一句
即可，手里的活照干）。

### 三个开工问题（用提问工具，不用自由文本追问）

先读已记录的偏好（脚本路径相对本技能目录）：

```
python3 scripts/prefs.py --json
```

三个键里**记录过的直接用，不再问**；没记录的用宿主的**向用户提问工具**
（ask user question / request_user_input，一次问卷问齐，别拆成三轮对话）问：

1. **画幅宽度**——选项：单栏 **8 cm**、双栏 **15 cm**。用户不清楚就按这次
   任务自己推荐并说明理由：单张简单曲线/单组对比 → 单栏 8 cm；曲线多、
   面板多、横向信息密 → 双栏 15 cm。推荐项放在第一位。
2. **字体**——选项**必须包括 Times New Roman 与 Arial**（默认推荐
   Times New Roman）；用户点名其它字体就用其它字体。
3. **图例加不加框**——加框 / 无框（默认推荐无框）。用户选了加框，之后调
   `tavotto_preflight` / `tavotto_export` 时带
   `{"journal": {"legend_policy": {"frame": true}}}`——那是用户自己点的头，
   别让预检把它当违规每次都报一条 warning。

用户在回答里表示「以后都这样 / 记住」时才写偏好（`width` 记 `single` /
`double`；用户明确说「宽度每次都问」记 `ask`）：

```
python3 scripts/prefs.py --set font="Times New Roman" --set legend_frame=off --json
```

用户改主意时用 `--set` 覆盖或 `--unset` 退回「下次再问」。偏好写不进去
（`saved: false`）不算错误——下次重新问就是了。

本插件同时带一套 **MCP 工具**（`tavotto_open_figure` / `tavotto_apply_overrides` /
`tavotto_preflight` / `tavotto_export` / `tavotto_verify_replay` /
`tavotto_close_session`）。图画完之后优先走它们；只有多图拼版、画布标注、写回原图
这类需要完整工作台的事才交接给 Tavotto 桌面窗口（见最后一节）。

## 硬性约定

违反任何一条，图在 Tavotto 里都只能当死图排版，双击进不去图内编辑。

### 1. 脚本与产物同目录，而且必须先落成文件

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

### 2. 入口是一个无参函数

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
`rasterized=True`（`imshow` 的位图除外）——Tavotto 导出的是真矢量 PDF，投稿要的正是它。

### 5. 可复现

随机数固定种子（`rng = np.random.default_rng(20260818)`）；**不要 `plt.show()`**。

### 6. 投稿默认值（就是 `lab-publication-v1` 规范）

* 宽度：单栏 **80 mm（8 cm）**、双栏 **150 mm（15 cm）**——按开工问题的答案选，
  **不出这两档之外的宽度**；比例取 16:9 / 4:3 / 1:1
* 字号：正文 **9 pt**；**最终有效字号必须大于 8 pt**，规范下限 8.5 pt
  （图例与刻度别再用 8 pt——预检会当阻断项拦下）
* 线宽：**0.5 / 0.75 / 1.0 / 1.5 pt** 四档里选
* 坐标轴：封闭（四条边都留）、**刻度朝内、只留主刻度线，次刻度线不画**；
  轴标题写成 `Title (unit)` 且**默认加粗**（`axes.labelweight: bold`，
  用户特殊要求才改回常规字重）
* 图例：按开工问题的答案加框或无框（默认无框）
* 字体：按开工问题的答案（默认 Times New Roman，另一个标准选项是 Arial）；
  有中文就显式加中文字体（否则导出 PDF 里是方框）
* 位图（`imshow` 等）≥ 300 dpi；交付物存矢量 PDF
* 色系：用 Scientific colour maps（batlow / vik / roma…），按 sequential /
  diverging / categorical 语义选；**不要用 jet / rainbow**
* 数学式用 mathtext（`$\mathrm{cm^{-1}}$`）

用户另有期刊要求就按用户的，并在调工具时带 `journal` 覆盖。
画完跑一次 `tavotto_preflight` 确认，别凭记忆打包票。

### 7. 克制：数据之外的效果，一个都不擅自加

图上只画用户要的数据。**下面这些没有用户明确要求就一律不加**：

* 用背景色块/色带标注「某段数据好或差」；
* 在图里画箭头指向某个数据点或区域；
* 「peak here」「注意这里」这类说明性文字标注；
* 高亮、阴影、星号显著性标记等一切装饰性效果。

你觉得某个效果确实能帮读者理解时，**用向用户提问工具先问**（说明加什么、
为什么），用户点头才画；用户没点头就保持素图。「先加上再让用户删」不是
省事，是把用户的图改成了你的图。

### 8. 多子图：在 matplotlib 里拼，不假手他人

任务涉及多个子图组成一张主图时：

* **主图宽度 150 mm（15 cm）**，在**一个脚本、一个 Figure** 里用
  `plt.subplots` / `GridSpec` 排版，一次 `savefig` 出整张主图；
* **绝不用别的软件拼**——不用 PIL/ImageMagick 拼贴位图、不用 LaTeX
  subfigure、不导出散件让用户自己拼。matplotlib 之外拼出来的不是矢量整图，
  Tavotto 也接不住；
* **每个子图的 x 轴与 y 轴都各自标全**：轴标题、刻度线、刻度标签一样不少
  ——同一行的子图哪怕 y 轴标题一字不差，也逐个 `set_ylabel` /
  `set_xlabel`，**不许只给最左（最下）那个留、其余留白**；
* **不共享坐标轴**：不用 `plt.subplots(..., sharex=…, sharey=…)`——它会把
  里侧子图的刻度标签藏掉。要让各子图坐标范围一致，就逐个
  `set_xlim` / `set_ylim` 对齐，刻度与轴标题仍然每个子图自己标；
* 子图各自的标题用 `ax.set_title(...)`（默认居中），**矩阵式各归各位**：
  每个标题落在自己那个子图的正上方，不挤在整图左上角，也不用
  `loc="left"`（用户点名要左对齐才用）；
* 子图另需单独交付时，每张也按 8 cm / 15 cm 两档出，同样一图一 stem；
* 子图编号 (a)(b)(c) 用户要了才加（这也算约定 7 里的标注）。

以上是默认值，用户特殊要求（比如「里侧子图不要重复轴标题」）就按用户的。

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
    # 全部 ≥ 8.5pt：8pt 的图例/刻度会被预检当阻断项拦下
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

## 图画完之后：先用 MCP 工具

**动手前先体检一次（会话里第一次用 Tavotto 时）：**

```
tavotto_health {}
```

几十毫秒就回来：引擎在不在、内嵌画布资源在不在、允许的项目根。要是工具列表里
**只有 `tavotto_health`**（或它报 `ok: false`），说明这台机器还没有可用的
Tavotto 引擎——**先按它给的 `recovery` 步骤引导用户**（一条
`python3 <插件目录>/mcp/server.py --provision`，或 `pipx install tavotto`，
装完**新开 Codex 会话**），**不要**先把图画出来再撞上 `desktop_only`，
那是白花几分钟。三条铁律：

* 引擎不可用 ≠ 可以拿桌面窗口或浏览器顶替内嵌画布——那是两条路，不许冒充；
* 插件 enabled ≠ 工具可用：装完插件/引擎必须**新开会话**才能拿到工具；
* 工具回了结构化错误就把 `code` + 恢复步骤转达给用户，绝不自己编一个成功。

体检通过后：

```
tavotto_open_figure { "project_path": "figures", "stem": "Fig1_removal_rate" }
```

回来的是 `session_id` + `manifest`（哪些元素可改、每个元素有哪些属性）+ 预览 SVG +
出版规范 + 一份预检结果。支持 UI 的 Codex 会同时开出一块交互画布，用户可以直接拖。

之后：

* **改图** `tavotto_apply_overrides { session_id, patches }`。
  `patches` 是 `{gid, prop, value}` 的**全量列表**——列表里没有的 `(gid, prop)` 会自动
  恢复成脚本原始值，所以**每次都要发完整的一份，不要发增量**。gid 与 prop 从 manifest
  的 `elements[].editable` 里取，别猜。
* **体检** `tavotto_preflight { session_id }`。四档结果：`errors`（默认阻止导出）、
  `warnings`、`not_verifiable`（查不了，需用户确认）、`suggestions`。
  把 `report` 那段念给用户听。
* **导出** `tavotto_export { session_id, formats: ["pdf","png"] }`。有 `errors` 时它会
  拒绝——**先去修，或者问用户**；用户明确说「就这样导出」才带
  `explicit_confirm: true`（这次会记进 proof report）。
* **收尾** `tavotto_close_session { session_id }`。

期刊有自己的尺寸就带 `journal`，只覆盖点名的键：
`{"journal": {"widths_mm": {"double": 178}}}`。

**这些工具不会动用户的 .py 源码。** 数据、坐标范围、加删曲线/子图、colorbar 方向
仍然只能回代码改（清单见 `references/compatibility.md`）。

## 交接给 Tavotto 桌面窗口

**只在用户明确要外部窗口、或需求超出 MCP 工具能力时才走这条**（多图拼版、
加画布标注、(a)(b) 编号、版本历史、把修改写回原始 PDF/PNG）。日常改图一律
用上面的 MCP 工具与内嵌画布——桌面窗口不是它的替代品，反过来也一样。
执行本技能自带的（路径相对本技能目录）：

```
python3 scripts/handoff.py <脚本路径>
```

它做三件事：产物缺失或比脚本旧就先跑一遍脚本 → 把产物登记进图库注册表 →
唤起 Tavotto（桌面应用优先，没装就用浏览器模式）。**退出码非零就是没做完**
（0 成功 / 1 脚本报错 / 2 路径或交接失败 / 3 这台机器上用不了 Tavotto /
4 图不可参数化）。

用户只装了 Tavotto 桌面程序也没关系——不必让他再装 Python，也不必让他配
`TAVOTTO_CLI`，脚本会自己找到桌面版自带的命令行。

输出是一行 JSON，**你必须读**：

* `"parameterizable": true` —— 成了，交接完成。
* `"parameterizable": false` —— **没成，要修**。多半是脚本没和产物放在同一个目录
  （约定 1），或产物名静态解不出（约定 3）。改完重跑重新交接，不要把它当噪音略过。
* `"error_code": "tavotto_missing"` —— 用户机器上没有 Tavotto。按输出里的 `hint`
  引导安装；图已经画好了，脚本和产物都在原处，装完再执行同一条命令即可。
* `"error_code": "desktop_found_cli_missing"` —— **他装了桌面版，只是版本旧**
  （那一版没带命令行）。让他去 Releases 更新一次，**不要**让他「先装 Tavotto」
  ——他会去装一个已经装着的东西，然后发现还是不行。
* `"error_code": "registry_write_failed"` —— 图库目录不可写。把图和脚本换到一个
  可写的目录，或让用户修好权限，然后重新交接。原文件一个字节都没动。
* `"error_code": "launch_failed"` —— 桌面应用**起来了但没活下来**（或起不来）。
  `ok: true` 是等出来的：CLI 会等桌面进程存在且就绪，崩了就带着
  `exit_code` / `signal` / `log_path` 回这条。把这三样念给用户
  （`signal: "SIGABRT"` 多半是安装损坏或从受限环境启动 GUI），指给他
  `log_path` 的 sidecar 日志与 `~/Library/Logs/DiagnosticReports/` 的崩溃
  报告；`retryable: false` 时**不要**自己重试一个已知会崩的程序。
* `"error_code": "launch_timeout"` —— 唤起后进程在限期内没出现。
  `retryable: true`，可以重试一次；再超时就把 `log_path` 给用户。
* 顺便留意 `conflicts`（两个脚本抢同一个 stem）和 `dynamic_names`（某些脚本的产出名
  静态解不出）——只报告不自动裁决，需要时告诉用户。

* `"update"` —— 插件自己有新版本时才出现（每 24 小时最多查一次）。
  `status` 为 `available` 时**顺口告诉用户一句**就行：让他执行
  `codex plugin marketplace upgrade tavotto` 再重载 Codex。**别为这件事停下手里
  的活**，也别反复提——图已经交接好了，这只是个提醒。里面还可能有
  `tavotto` 字段，那是说本机的 Tavotto 版本低于新版插件的要求，让他去
  Releases 更新 Tavotto（**跟插件是两码事，别混着说**）。

完整的错误码清单与排障步骤在 `../../../docs/handoff-protocol.md`。

## Tavotto 出了问题：帮用户提 issue

用户撞上 Tavotto 的报错、渲染结果不及预期、画布/预览显示不出来时，除了按
上面的错误码引导恢复，还要**把问题记录成一份能复现的 issue 草稿**：

* **标题**：一句话说清「做什么时出了什么」；
* **环境**：`tavotto_health` 的输出（引擎版本/来源）、插件版本
  （`python3 scripts/update_check.py --json` 里的 `current_version`）、
  操作系统；
* **复现步骤**：最小化的脚本（或指出无法脱敏时用形状等价的替身数据）、
  依次执行的命令/工具调用、期望看到什么、实际看到什么；
* **原始证据**：结构化错误的 `code` 与消息原文、相关日志（`log_path` 指到的
  那份）——**先脱敏**：用户名、绝对路径、密钥一律抹掉。

草稿先给用户看。**用户明确允许之后**才提交到
<https://github.com/Tavotto/Tavotto/issues>（有 `gh` 就
`gh issue create --repo Tavotto/Tavotto`，没有就把草稿给用户让他自己贴）。
用户不允许就把草稿留在对话里，到此为止——**绝不擅自外发**。

## 画完之后就收手

回一句话：这张图画了什么、落在哪个文件、预检怎么样。然后按下表分工，
**不要默认重写代码**：

| 用户想改 | 谁来做 |
| --- | --- |
| 图例挪位置 / 字号 / 线宽 / 颜色 / marker 样式 | `tavotto_apply_overrides`（或画布里拖） |
| 刻度朝内朝外、刻度标签字号 | `tavotto_apply_overrides` |
| 标题、轴标签的文字与位置 | `tavotto_apply_overrides` |
| 「这图合不合投稿规范」 | `tavotto_preflight` |
| 出图 | `tavotto_export`（PDF 是真矢量） |
| 多图拼版、加箭头标注、加 (a)(b) 编号、写回原图 | Tavotto 桌面窗口（`handoff.py`） |
| 数据本身、坐标范围、对数/线性、加一条新曲线 | 代码（回来改脚本） |
| colorbar 方向、子图数量与结构 | 代码 |

改代码之后**重开一次会话**（`tavotto_close_session` 再 `tavotto_open_figure`），
或者再交接一次给桌面窗口：Tavotto 会重扫产物并定位到这张图，用户已经排好的版和
已经调过的元素不会丢。

能改与不能改的完整清单见 `references/compatibility.md`。
