<p align="center">
  <img src="https://raw.githubusercontent.com/erwanjun/magplot/main/assets/readme/hero.svg" width="100%"
       alt="Magplot — 把 matplotlib 面板拖到画布上排版，双击进图内改元素，导出真矢量 PDF">
</p>

<p align="center">
  <a href="https://github.com/erwanjun/magplot/blob/main/README.md">English</a> · <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/erwanjun/magplot/releases"><img alt="Release" src="https://img.shields.io/github/v/release/erwanjun/magplot?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/erwanjun/magplot/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-2868b7?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.13-1b1b18?style=flat-square&labelColor=1b1b18">
  <img alt="Platform" src="https://img.shields.io/badge/macOS%20·%20Windows%20·%20Linux-1b1b18?style=flat-square&labelColor=1b1b18">
</p>

<p align="center"><i>Edit the figure. Keep the source. —— 直接在图上改，脚本不动。</i></p>

投稿前的最后一公里通常是这样的：图早就画好了，但要拼成 Figure 1、调字号、
挪图例、对齐版面——于是回到 Python 里改一行、重跑脚本、再看一眼，来回二十遍。
或者把 PDF 拖进 Illustrator，从此和脚本失去联系。

**Magplot 让你直接在图上改。** matplotlib 输出的面板拖进画布自由排版，
双击任意一张图就能点中里面的标题、坐标轴、曲线、图例——改字号、换颜色、拖位置，
Python 在后台实时重渲染（热态约 40 ms）。

所有修改都是**非破坏性**的：你的脚本一个字节都不会被改动，随时可撤销。
导出时引擎按全质量重新出图，合成一份文字仍可选中的真矢量 PDF。

<p align="center">
  <img src="https://raw.githubusercontent.com/erwanjun/magplot/main/assets/readme/workbench.png" width="100%"
       alt="Magplot 工作台：左栏图内元素树列出标题与坐标轴，画布上三张面板排成 (a)(b)(c)，右栏正在编辑选中标题的属性">
</p>

<p align="center"><sub>左：图内元素 · 中：150×130 mm 的版面 · 右：选中标题后可改的属性（源文件仍是 <code>fig1_kinetics.py</code>）</sub></p>

## 安装

**下载安装包**：到 [最新发行版](https://github.com/erwanjun/magplot/releases/latest)
取 macOS 的 `.dmg` 或 Windows 的 `.exe`，装完双击即用，Magplot 在自己的桌面窗口中打开。

**Windows 用户不需要自己装 Python。** 安装包里自带一套 Magplot 专用的 Python
运行环境，常用科学栈已经装好——numpy、matplotlib、pandas、scipy、seaborn、Pillow，
版本都是固定的。装完立刻就能渲染，不用下载、不用联网。它**不会碰你已有的任何
Python 或 Conda**；如果某张图要用到清单之外的包，在「设置 →「渲染环境」」里
换成你自己那套环境即可。见[使用须知](#使用须知)。

macOS 版仍然用你已有的 Python（或让 Magplot 在自己的目录里建一个隔离环境）。

**或者从 PyPI 装**，三个平台命令相同：

```sh
pipx install "magplot[worker]"
magplot
```

浏览器会自动打开 `http://127.0.0.1:5089`。

<details>
<summary>用 pip 装 · 复用你自己的科学栈环境 · 从源码跑</summary>

**pip**（装进当前环境）：

```sh
pip install "magplot[worker]"
magplot
```

**复用画图时用的环境**：去掉 `[worker]` 只装本体，再指定你自己的解释器——
这样渲染用的就是脚本当初依赖的那一套，图长什么样完全一致：

```sh
pipx install magplot
export MM_WORKER_PYTHON=/path/to/your/env/bin/python     # Windows: setx MM_WORKER_PYTHON "..."
magplot
```

**从源码跑**（需要 node + pnpm 构建界面）：

```sh
git clone https://github.com/erwanjun/magplot.git && cd magplot
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
python scripts/build_frontend.py
.venv/bin/magplot
```

</details>

**命令行参数**：`magplot --figures <目录>` 直接打开某个图库；`--port 5089` 换端口；
`--no-browser` 不自动开浏览器。

## 先跑通一遍

仓库里带了一个可直接打开的示例项目：

```sh
magplot --figures examples/figures
```

三张面板会出现在左侧素材栏。拖到画布上，双击其中一张——你会看到这张图里所有元素的
树，点标题就能改字号。`examples/figures/` 里就是两个普通的 matplotlib 脚本，
Magplot 不要求你按任何特殊方式写它们。

## 图内能改什么

| | |
|---|---|
| **文字** | 标题、坐标轴标签、刻度标签、图例、图内注释——内容 / 字号 / 颜色 / 字重 / 字形 / 旋转 / 透明度 / 显隐，可直接拖动 |
| **数据系列** | 线宽、线型、颜色、marker（散点可整体换形状）、图例条目顺序 |
| **坐标轴** | 刻度组、轴线、网格、3D 视角（elev/azim/roll）、3D 轴箭头与背景面板 |
| **图幅** | 整张图的尺寸（会重排版）、背景 |
| **不开放** | 坐标轴范围、刻度尺度、spines 等数据空间属性，以及色条方向——这些请在脚本里改 |

画布上另有一整套排版能力：智能吸附对齐、多选分布、成组、布局组（行/列/网格约束，
尺寸变化自动重排）、任意角度旋转的文字/箭头/形状标注、科研预设（可逆反应箭头、
比例尺、误差标注、放大框）、多画布标签页、版本时间线、可跨全文档批量应用的命名样式。

## 导出

PDF 会把每张原始矢量面板整块嵌进去，**文字仍然可选中、可搜索**；PNG 由同一份 PDF
渲染，两者绝不会不一致。导出前自动预检越界、重叠、过小字号、过低等效 DPI、
过期渲染与缺失素材，并可随成图写一份 proof report 作为投稿留档。

两处明示的例外：面板设了 `opacity < 1` 或做了翻转时，该面板按导出 DPI 转成位图嵌入
——PDF 的矢量内容不支持这两种效果。

## 改图助手（可选）

助手面板可以把需求交给你本机的 **Codex / Claude CLI** 去直接改脚本，
例如「把图例移到左上角并缩小到 7pt」。改动前自动快照，完成后显示 diff 并重渲染，
一键可回滚。没装这些 CLI 不影响其余任何功能。

## 你的数据在哪

全部在本机。渲染、合成、导出都是本地进程，图和数据不会上传到任何地方。

| | |
|---|---|
| 文档与布局 | `~/Library/Application Support/Magplot/`（Linux `~/.local/share/magplot/`，Windows `%LOCALAPPDATA%\Magplot\`） |
| 你的脚本与图 | 只读——除非你明确选择「写回原始文件」，且该权限可按项目锁死 |
| 唯一的对外请求 | 每天一次检查有没有新版本，可在「设置 → 检查更新」关掉 |

## 使用须知

- **第一次打开某张图时会跑一遍你的脚本**。轻量图秒级，重的该多久就多久；
  之后每次修改都是亚秒级。
- **渲染需要一个能 import 你脚本所需依赖的 Python**。这个 Python 从哪来，
  取决于你怎么装的 Magplot：

  | 安装方式 | 渲染用的解释器 |
  |---|---|
  | Windows `.exe` | 安装包**自带的内置环境**：CPython 3.13 + numpy / matplotlib / pandas / scipy / seaborn / Pillow（版本固定）。不用装、不用下载。 |
  | macOS `.dmg` | 你自己的 Python；也可以让 Magplot 在它自己的数据目录里建一个隔离环境。 |
  | PyPI + `[worker]` | 你装它的那个环境。 |

  选择顺序：`MM_WORKER_PYTHON` → 你在设置里指定的 → 内置环境 → Magplot 自身的
  解释器 → 机器上探测到的 Python / Conda。**你显式指定的永远优先**；
  Magplot 只是**启动**你指定的环境来渲染，
  绝不往里面装任何东西，也绝不修改你已有的 Python / Conda。

  脚本要用内置环境里没有的包（rdkit、astropy、自家实验室的库）时，Magplot 会
  直接告诉你缺的是哪个包，并给出「换成你自己的环境」的入口
  （设置 →「渲染环境」）。一个可用解释器都没有时，排版、标注、导出照常，
  只有图内编辑用不了。「设置 → 隐私、诊断与 About」始终显示当前用的是
  哪个解释器、来自哪里。

## 开发

```sh
.venv/bin/python -m pytest        # 后端
cd web && pnpm test               # 前端
cd web && pnpm tsc --noEmit && pnpm build

# 只有打 Windows 桌面版才需要：构建内置渲染环境。
# 版本锁在 packaging/runtime-lock.json；脚本会校验 CPython 下载的 SHA-256，
# 并逐个 import 测试装进去的每个包。
python scripts/build_worker_runtime.py
```

欢迎提 issue 与 PR。

## 许可证

[AGPL-3.0-only](https://github.com/erwanjun/magplot/blob/main/LICENSE)。

自己使用、修改、在实验室内部部署都不受限制，**用它排出来的图和导出的 PDF 完全属于你**
——许可证不影响你的作品。受约束的是分发：如果你把改过的 Magplot 分发给别人，
或架成别人能通过网络访问的服务，需要向这些用户提供对应的源码。
