<p align="center">
  <img src="https://raw.githubusercontent.com/erwanjun/magplot/main/assets/readme/hero.svg" width="100%"
       alt="Magplot — 把 matplotlib 面板拖到画布上排版，双击进图内改元素，导出真矢量 PDF">
</p>

<p align="center">
  <a href="https://github.com/erwanjun/magplot/blob/main/README.md">English</a> · <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/erwanjun/magplot/releases"><img alt="Release" src="https://img.shields.io/github/v/release/erwanjun/magplot?style=flat-square&color=4a63d8&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/erwanjun/magplot/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-4a63d8?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.13-1b1b18?style=flat-square&labelColor=1b1b18">
  <img alt="Platform" src="https://img.shields.io/badge/macOS%20·%20Windows%20·%20Linux-1b1b18?style=flat-square&labelColor=1b1b18">
</p>

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
取 macOS 的 `.dmg` 或 Windows 的 `.exe`，装完双击即用，Magplot 会在浏览器里打开。

安装包里刻意不含 matplotlib：Magplot 渲染的是**你自己的脚本**，它们要 import
你自己那套依赖，所以它用的是你已有的那个 Python——就是你画这些图时用的那个。
见[使用须知](#使用须知)。

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
- **渲染需要一个装了 matplotlib 的 Python**——Magplot 跑的是你的脚本，
  解释器得能 import 它们 import 的东西。从 PyPI 装时 `[worker]` 会带一个；
  `.dmg`/`.exe` 安装包则去找你已有的那个。两种情况都可以用 `MM_WORKER_PYTHON`
  指定，「设置 → 隐私、诊断与 About」能看到当前用的是哪一个。
  一个都没有时，排版、标注、导出照常，只有 ⚡ 图内编辑用不了。

## 开发

```sh
.venv/bin/python -m pytest        # 后端
cd web && pnpm test               # 前端
cd web && pnpm tsc --noEmit && pnpm build
```

欢迎提 issue 与 PR。

## 许可证

[AGPL-3.0-only](https://github.com/erwanjun/magplot/blob/main/LICENSE)。

自己使用、修改、在实验室内部部署都不受限制，**用它排出来的图和导出的 PDF 完全属于你**
——许可证不影响你的作品。受约束的是分发：如果你把改过的 Magplot 分发给别人，
或架成别人能通过网络访问的服务，需要向这些用户提供对应的源码。
