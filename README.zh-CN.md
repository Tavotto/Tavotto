<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/hero.svg" width="100%"
       alt="Tavotto —— matplotlib 与 AI 生成科研图的可视化编辑器。直接编辑图表，不必修改代码。">
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/blob/main/README.md">English</a> · <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Tavotto/Tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://pypi.org/project/tavotto/"><img alt="PyPI" src="https://img.shields.io/pypi/v/tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Tavotto/Tavotto/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-1b1b18?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.13-1b1b18?style=flat-square&labelColor=1b1b18">
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases/latest"><b>下载</b></a> ·
  <a href="#上手">上手</a> ·
  <a href="#图内能改什么">图内能改什么</a> ·
  <a href="#导出与投稿前检查">投稿前检查</a>
</p>

图早就画完了，但它还不是 **Figure 1**。Tavotto™ 直接打开 matplotlib 已经产出的图，
让你点中标题、图例、某一条曲线——就地改。

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/workbench.zh.png" width="100%"
       alt="Tavotto 工作台：左栏是这张图里所有元素的树，中间是 150 × 112.5 mm 的版面上排好的 (a)(b)(c) 三张面板，右栏是选中标题的属性——字体、9 pt、文字内容，以及源文件 fig1_kinetics.py。">
</p>

<p align="center"><sub>选中的是面板 (a) 的标题。右边是它的字体与字号——也是画出它的脚本 <code>fig1_kinetics.py</code>，一个字节没动。</sub></p>

## 在 Codex 中第一次使用 Tavotto

> **普通用户不要克隆或构建这个仓库。** 源码安装只用于参与 Tavotto 开发。

先选你需要的方式：

| 你要做什么 | 需要安装什么 |
| --- | --- |
| Codex 画完图后，在 Tavotto 桌面窗口里继续拖拽修改 | Tavotto 桌面版 + Codex 插件（不需要 Python 引擎） |
| 在 Codex 里直接使用 Tavotto 画布、预检、修改与导出工具 | Codex 插件 + Tavotto Python 引擎 |
| 修改 Tavotto 本身 | 见下方「贡献者：从源码开发」 |

### 完整的 Codex 集成

在终端依次运行：

```sh
codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins --sparse codex-plugin
codex plugin add tavotto@tavotto
pipx install "tavotto[worker]"
```

然后**关闭当前 Codex 会话并新开一个会话**。插件的 skill 与 MCP 工具不会在已经
打开的会话里热重载。

新会话里可以直接说：

> 用 Tavotto 画这张图。先运行 Tavotto 健康检查；健康后再画，最后在 Tavotto 里
> 打开。不要安装或升级任何已经可用的组件。

第一次出现项目目录授权时，确认的是 Tavotto 可以访问的本地图库目录。图、脚本和
数据仍在本机处理。

插件装在你本机的 `~/.codex` 配置里，因此只有会读取本机插件的 Codex 界面才能
加载它——终端里的 Codex CLI 与 Codex 桌面应用。不读取本机插件的界面（纯云端
会话、不认 `~/.codex/plugins` 的 IDE 集成）永远不会出现 Tavotto 工具——先在
终端的 `codex` 会话里验证，不要在那些界面里反复排障。

### 只交给桌面版收尾

装桌面版 + 插件（上面的两条 `codex plugin` 命令；这条路**不需要** `pipx` 那行）。
让 Codex「在 Tavotto 里打开」时，插件的 skill 会用自带的交接脚本完成交接——它会
自己找到桌面版内置的命令行：

```sh
python3 <插件目录>/skills/tavotto-figure/scripts/handoff.py path/to/figure.py
```

别让 Codex 直接跑裸的 `tavotto open`：桌面安装包**刻意不改你的 PATH**，那条命令
只在 PyPI 安装之后才存在。这条路径不要求 Codex 内嵌画布，也不需要 Python 引擎。
脚本与产物应放在同一目录，产物优先保存为矢量 PDF。

### 让 Codex 代你安装

把下面一句完整发给 Codex：

> 请严格按照 README 的「在 Codex 中第一次使用 Tavotto」执行普通用户安装。
> 不要 clone 或构建源码，不要运行 pnpm、npm、cargo、Tauri、测试或 editable
> install。只安装 Codex 插件和所需的 Tavotto 引擎，运行健康检查；需要新会话时
> 明确告诉我并停止。

## 别再为了挪一次图例重跑脚本

投稿前的最后一段路通常是这样：改一行、重跑、看一眼、再改。来回二十遍，
而要改的都是些看得见却不好用语言描述的东西——图例往左三毫米、刻度标签小一号、
面板 (b) 和面板 (a) 对齐。

另一条路是把 PDF 拖进 Illustrator 手工收尾，然后接受一件事：从此这张图和画出它的
代码再无关系。

Tavotto 是第三条路。打开图、改你看得见的东西、导出。脚本原地不动，每一步都可撤销。

## 在图里改

双击一张面板，Tavotto 把你的脚本跑一遍，并把那个 matplotlib `Figure` 留在内存里。
从这一刻起你改的就是这张图本身：标题、坐标轴标签、某条刻度、某条曲线、图例、
脚本画的箭头——从左边的树里选，或者直接在画布上点——改字号、颜色、字重、线型，
或者干脆拖到别处去。

**拖和调都是即时的**：图跟着光标一帧一帧地动，matplotlib 只在你松手时跑一次定稿。
命中判据跟着**真实画出来的路径**走，而不是外接矩形——点曲线选中的就是曲线，
不是它周围那一大片空白。

## 排出版面

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/layout.zh.png" width="100%"
       alt="同一个窗口的排版态：左栏素材库列出三个源 PDF 与它们的物理尺寸，中间是排好的版面、其中一张面板处于选中状态，右栏是它的位置、毫米尺寸与缩放比例。">
</p>

面板落在以毫米计的版面上，尺寸就是脚本画出来的尺寸。拖动、互相吸附、多选对齐与分布、
成组，或者把几张面板绑成行 / 列 / 网格约束——尺寸一变自动重排。(a)(b)(c) 面板标签
一条命令生成。文字、箭头、形状标注可任意角度叠在上层，并带一组科研常用预设：
可逆反应箭头、比例尺、放大框。

## 脚本仍然是源头

改图这条路径一个字节都不碰你的 `.py`。每一次修改都以 override 的形式存在文档旁边，
下次打开这张图时重新跑一遍脚本再回放上去——撤销、版本历史、导出时按全质量重渲染，
靠的都是同一套机制。（唯一的例外是下面那个改图助手，而它必须由你点名调用。）

如果你**确实**想把改动落进磁盘上的图文件，「写回原始文件」是一个显式动作：
它会从零重跑一遍脚本，证明结果与你眼前看到的一致，并且可以按项目整个锁死。

## 导出与投稿前检查

导出 PDF 时，每张原始矢量面板按原样整块嵌入，**文字仍然是可选中、可搜索的真文字**；
PNG 由同一份版面栅格化，两者绝不会不一致。两处明示的例外：面板设了小于 1 的透明度、
或做了翻转时，该面板按导出 DPI 转成位图嵌入——PDF 的矢量内容不支持这两种效果。

在写出任何文件之前，Tavotto 会按出版规范 profile 检查一遍，把审稿人三周后才会告诉你的
事情先说出来：

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/preflight.zh.png" width="82%"
       alt="导出对话框里的预检清单：两条阻断项指出文字的最终有效字号低于规范的 8.5 pt 与 8 pt 下限，两条警告分别关于边框线宽与图例字号，三条建议关于图例字重、坐标轴标签格式与没有 marker 的曲线，另有一条标为无法核验。">
</p>

字号一律按**最终物理尺寸**判——面板摆成 60% 时比的是 `fontsize × 0.6`，
不是脚本里写的那个数。结论分四档：**阻断**在你明确确认之前不让导出；**警告**一定展示；
**无法核验**是真的查不了的那些（比如外部位图内部的文字），需要人来看；
**建议**绝不替你做决定。这些内容连同你的确认，都会写进随成图产出的 proof report。

## 收尾 AI 画的图

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/workflow.zh.svg" width="100%"
       alt="工作流：你、Claude 或 Codex 写的 Python 脚本跑 matplotlib 产出矢量 PDF；Tavotto 负责图内编辑、排版与投稿前检查；结果是 PDF 或 PNG。脚本全程是源头。">
</p>

编程助手写的第一版 matplotlib 已经相当好，剩下的都是视觉活儿——图例高了两行、
这张面板想再小一点、标签压住了一个数据点。把这些用提示词描述一遍，比自己动手还慢。

一条命令把图交接过来，终端里或 agent 里都行：

```sh
tavotto open figures/Fig1_kinetics.pdf   # 给产物
tavotto open figures/fig1_kinetics.py    # 给脚本也行，产出名自动解析
tavotto open figures/                    # 或者整个图库
```

它会把这张图所在的图库当项目打开、把缺的条目补进注册表，然后把图送进桌面应用——
已经开着窗口就直接送进那一个，不会另起一套。没装桌面版则退回浏览器模式。

**用 Codex 的话**可以装插件。它让 Codex 知道「Tavotto 能接手的图」该长什么样
（脚本与产物同目录、矢量 PDF、产出名可静态解析），并把编辑器搬进 Codex 里——
安装命令与第一个会话该说什么，见上面的
[在 Codex 中第一次使用 Tavotto](#在-codex-中第一次使用-tavotto)。

插件带一个 skill、一个本地 MCP server（六个工具：打开图、应用 override、跑预检、
按指定 DPI 导出真矢量 PDF/SVG 或 PNG、校验重放、关闭会话——在完全没有界面的宿主里
也能用），以及一块内嵌画布，用的是**和桌面应用同一份**前端代码，拖拽、吸附、撤销
没有第二套实现。详见 [`codex-plugin/README.md`](codex-plugin/README.md)，
其中也写清了哪些部分**还没有在真实的 Codex Desktop 里验证过**。

模型建议的路径永远不等于权限。零配置第一次打开时，支持这项能力的 Codex 会把
规范化后的本地目录展示给你确认；批准只在当前 Tavotto MCP 连接内有效。

## 把现成的项目带进来 · `tavotto run`（Beta）

`tavotto open` 假设的是 Tavotto 喜欢的形状：脚本挨着它的产物，都在一个图库目录
里。真实的论文项目常常不是这样——一个 conda 环境、一个包、几个命令行参数：

```sh
conda activate paper
python -m figures.fig3 --dataset run7
```

这一类，在你本来就在敲的那条命令前面加 `tavotto run --`：

```sh
tavotto run -- python figure.py
tavotto run -- python figure.py --sample A --temperature 800
tavotto run -- /path/to/paper/.venv/bin/python figure.py
tavotto run -- python -m paper.figures.xps --sample A
```

Tavotto 用**你给的那条 Python 命令**去跑，只接管那个进程里创建的 Matplotlib
Figure。解释器、工作目录、命令行参数、环境变量、`stdout` / `stdin`——一样都不
重建、不接管。`savefig` 照常写它本来就会写的文件；Ctrl+C 照常打断你的脚本；
`tavotto run` 返回的是**你脚本自己的退出码**。

在 Tavotto 里编辑**不会改变你的脚本看到的东西**：

```python
ax.set_title("Script")
plt.show()                          # 你在 Tavotto 里把标题改掉
assert ax.get_title() == "Script"   # 通过——你的代码是执行权威
```

脚本启动**之前**，桌面上会问一次，写明解释器路径、工作目录和目标。
**你确认之前，一行代码都不会跑。**

> 这个模式**不是沙盒**：脚本拥有与你自己运行它时完全相同的权限。只运行你信任
> 的代码。

Beta，边界是明确的：只支持 Python 脚本或 `-m` 模块、只接管那一个进程里的图、
不支持任意 shell 包装、不支持 Jupyter、不写回你的源码、不写回你脚本自己存出来
的产物、需要桌面应用。完整契约、错误码与排障见
[`docs/compatibility/tavotto-run.md`](docs/compatibility/tavotto-run.md)。

## 全部在本机运行

渲染、合成、导出都是本地进程。Tavotto 不会把你的图、脚本、项目文件或数据上传到任何地方，
未发表的结果不出这台机器。它自己发起的对外请求只有两条：

- **每天一次**去 GitHub Releases 看有没有新版本。在**设置 → 检查更新**里关掉
  （或设 `TAVOTTO_NO_UPDATE_CHECK=1`）。
- **匿名用量统计——默认不发，问过你、你同意了才开始。** 首启询问一次。开了之后发的是
  粗粒度的功能事件（启动、打开图、一次编辑、导出成功）加上版本号、操作系统与架构，
  标识是本机随机生成的一串 UUID。**绝不发送**你的图、脚本、文件名、路径、科研数据、
  图内文字与改图助手的提示词——事件结构上就装不下它们。在
  **设置 → 隐私、诊断与 About** 里关掉（或设 `TAVOTTO_NO_TELEMETRY=1`）。

两个开关互不代管。细节见[隐私政策](docs/privacy.md)与[事件契约](docs/analytics/telemetry-events.md)。

## 上手

### 桌面版

到[最新发行版](https://github.com/Tavotto/Tavotto/releases/latest)下载 macOS 的
`.dmg`（Apple Silicon）或 Windows 的 `.exe`。装完双击即用——Tavotto 在自己的窗口里
打开，之后的升级也都在软件内完成。

**你不需要自己装 Python。** 两个安装包都自带一套 Tavotto 专用的 Python 运行环境，
常用科学栈已经装好——numpy、matplotlib、pandas、scipy、seaborn、Pillow，
两个平台锁的是同一组版本，同一个脚本在两边画出同一张图。装完立刻就能渲染：
不联网、不需要 Homebrew / Conda / Xcode，也不碰你已有的任何 Python。

这套内置环境也是安装包偏大的原因：**macOS 下载约 195 MB，Windows 约 89 MB，
装完约半个 GB**。只付一次，而且完全离线。

> macOS 版**只发 Apple Silicon（arm64）**。Intel Mac 没有构建、也没有验证过，
> 请走下面的 PyPI 安装。没有 Linux 安装包；Linux 走 PyPI（浏览器模式，beta）。
> Windows 安装包是否经过代码签名，以各版 Release 页的说明为准；未签名的安装包
> 首次运行会弹 **SmartScreen** 提示（点「更多信息 → 仍要运行」，或先对照
> Release 页的 `SHA256SUMS.txt` 核验下载）。支持 / beta / 不支持的唯一权威
> 清单是 [`docs/support-matrix.json`](docs/support-matrix.json)——发行页、
> 官网与应用内文案都以它为准（可机器核对的部分有测试看护）。

### PyPI

三个平台命令相同：

```sh
pipx install "tavotto[worker]"
tavotto
```

浏览器会打开 `http://127.0.0.1:5089`。`--figures <目录>` 直接打开某个图库，
`--port` 换端口，`--no-browser` 不自动开浏览器。

### 30 秒跑通一遍

```sh
pipx install "tavotto[worker]"
git clone --depth 1 https://github.com/Tavotto/Tavotto.git
tavotto --figures Tavotto/examples/figures
```

素材栏里会出现三张面板。拖一张到版面上、双击它、在元素树里点标题、
把 9 pt 改成 11、导出。`examples/figures/` 里就是两个普通的 matplotlib 脚本——
Tavotto 不要求你按任何特殊方式写它们。

<details>
<summary><b>进阶安装与渲染环境</b></summary>

**pip**（装进当前环境）：

```sh
pip install "tavotto[worker]"
tavotto
```

**复用画图时用的那套环境**：去掉 `[worker]` 只装本体，再指定你自己的解释器——
这样渲染用的就是脚本当初依赖的那一套。（这是轻量 CLI 形态：没有 `[worker]` 就
没有随装的渲染栈，渲染——包括 Codex 的 MCP 集成——完全依赖你指定的那个解释器）：

```sh
pipx install tavotto
export TAVOTTO_WORKER_PYTHON=/path/to/your/env/bin/python   # Windows: setx TAVOTTO_WORKER_PYTHON "..."
tavotto
```

**贡献者：从源码开发。** 这条路只用于修改 Tavotto 本身，绝不是普通安装失败后的
退路（需要 node + pnpm 构建界面）：

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
python scripts/build_frontend.py
.venv/bin/tavotto
```

**用哪个解释器渲染**。选择顺序：`TAVOTTO_WORKER_PYTHON` → 你在设置里指定的 →
内置环境 → Tavotto 自身的解释器 → 机器上探测到的 Python / Conda。
**你显式指定的永远优先**；Tavotto 只是**启动**你指定的环境来渲染，
绝不往里面装任何东西，也绝不修改你已有的 Python / Conda。内置环境同样只读——
字节码与 matplotlib 字体缓存都改道到 Tavotto 自己的数据目录，安装目录一个字节都不写
（macOS 上往签过名的 `.app` 里写东西会直接破坏代码签名）。

内置环境覆盖的是**常用**科学栈，**不承诺覆盖你脚本可能 import 的任意包**。
脚本要用它没有的包（rdkit、astropy、自家实验室的库）时，Tavotto 会直接说出缺的是
哪个包，并给出「换成你自己的环境」的入口（**设置 → 渲染环境**）；它不会替你把那个包
装进内置环境，也不会装进你的环境。一个可用解释器都没有时，排版、标注、导出照常，
只有图内编辑用不了。

**设置 → 隐私、诊断与 About** 始终显示当前用的是哪个解释器、来自哪里
（`bundled` / `configured` / `system` …）；用内置环境时还会显示每个包的精确版本。
诊断包里也有同一份。

**第一次打开某张图时会跑一遍你的脚本**。轻量图秒级，重的该多久就多久；
之后每次修改都是亚秒级。

</details>

## Tavotto 站在哪一段

|  | 只用 matplotlib | 矢量编辑器 | Tavotto |
|---|---|---|---|
| 画出这张图 | ✓ | — | 用你已经画好的图 |
| 直接可视化编辑 | 有限 | ✓ | ✓ |
| 知道自己在改什么 | 代码里的对象 | 一堆路径和字形 | 标题 / 图例 / 刻度 / 数据系列 / 色条 |
| 以毫米排多面板版面 | 在代码里手排 | ✓ | ✓ |
| 导出 PDF 里是矢量文字 | ✓ | ✓ | ✓ |
| 改动仍与脚本相连 | ✓ | 从此是另一个文件 | ✓ |
| 导出前按期刊规范检查 | — | — | ✓ |

矢量编辑器永远是更强的绘图工具。它只是不知道你点中的那个东西是个图例。

## 图内能改什么

| | |
|---|---|
| **文字** | 标题、坐标轴标签、刻度标签、图例条目、图内注释——内容 / 字号 / 颜色 / 字重 / 字形 / 旋转 / 透明度 / 显隐，可直接拖动 |
| **数据系列** | 线宽、线型、颜色、marker（散点可整体换形状）、图例条目顺序 |
| **箭头** | 脚本画的箭头（`FancyArrowPatch`）：整体拖动、拖单个端点，改箭头样式 / 线型 / 线宽 / 帽大小 / 颜色。`annotate()` 的箭头保持数据锚点——只开放样式 |
| **坐标轴** | 刻度定位与格式（几个刻度、落在哪、写成什么）、刻度线、网格、四条边框（统一或逐条）、范围、尺度、纵横比。拖动子图时属于它的东西一起走——你摆过的标签、它的色条、孪生轴 |
| **色条** | 方向、两端延伸三角、色图、范围、刻度与标签样式——就地重建，撤销、写回、重新导出全链路一致 |
| **3D 坐标轴** | 视角（elev / azim / roll）、投影方式、轴线、背景面板、网格、按轴的刻度组、可选的轴箭头 |
| **图幅** | 整张图的毫米尺寸（版面会重排）、背景 |

Tavotto **不做**的是凭空生成图的内容。它改的是脚本已经画出来的那些东西的属性；
新曲线、新面板、换数据仍然来自脚本——这正是重点。

## 出版规范与预检

规则收在一个带版本的 JSON 文件里
（[`src/tavotto/profiles/publication.json`](src/tavotto/profiles/publication.json)），
Python 引擎与 TypeScript 前端读的是同一份，不存在第二份会漂移的副本。

默认的 `lab-publication-v1` 规定：单栏 80 mm / 双栏 150 mm；16:9、4:3、1:1 三种比例；
正文 9 pt，最终有效字号的下限只有一个数——必须严格大于 8 pt；位图 ≥ 300 dpi；
Times New Roman 加一份显式的中日韩字体回退；线宽 0.5 / 0.75 / 1.0 / 1.5 pt；
刻度朝内、四边封闭、图例无边框；坐标轴标签写成 `Title (unit)`；
以及按语义类型选用的 Scientific colour maps。

期刊有自己的栏宽时用覆盖而不是分叉：`{"widths_mm": {"double": 178}}`
——其余全部继承，而且这次覆盖会记进 proof report。

## 改图助手（可选）

助手面板可以把需求交给你本机的 **Codex / Claude CLI** 去直接改脚本，
比如「把图例移到左上角并缩小到 7pt」。改动前自动快照，完成后显示 diff 并重新渲染，
一键可回滚。这是**唯一**会碰到你源码的路径，而且必须由你主动发起。
其余所有功能都不需要装这些 CLI。

## 文件都放在哪

<details>
<summary>数据目录与各自装什么</summary>

| | |
|---|---|
| 文档与自动保存 | macOS `~/Library/Application Support/Tavotto/` · Linux `~/.local/share/tavotto/` · Windows `%LOCALAPPDATA%\Tavotto\` |
| 导出成图、画布文件与版本历史 | 都收在项目内的一个 `tavottofile/` 里：导出在 `tavottofile/export/`，命名画布就在旁边，版本历史在 `tavottofile/versions/`——找得到、好备份、跟着图一起同步。旧版本写在老位置的文件仍可读 |
| 你的脚本与图 | 只读——除非你明确选择「写回原始文件」，且该权限可按项目锁死 |

</details>

## 安全与代码签名

Free code signing provided by SignPath.io, certificate by SignPath Foundation。
Windows 安装包由本仓库的 GitHub Actions 构建，并在被称为已签名发行版之前经过人工
签名审批。完整内容见[签名政策](docs/code-signing-policy.md)。

安全问题请走[私密报告](https://github.com/Tavotto/Tavotto/security/advisories/new)，
不要开公开 issue。

## 参与开发

欢迎提 issue 与 PR——改动怎么验、代码库刻意守着哪些边界，见
[CONTRIBUTING.md](CONTRIBUTING.md)。报 bug 时，
**设置 → 隐私、诊断与 About → 下载诊断包**会把通常需要的信息一次收齐
（密钥与个人路径已脱敏）。

```sh
.venv/bin/python -m pytest        # 后端
cd web && pnpm test               # 前端
cd web && pnpm build              # 类型检查（tsc -b）+ 打包
```

## 许可证

[AGPL-3.0-only](LICENSE)。

自己使用、修改、在实验室内部部署都不受限制，**用它排出来的图和导出的 PDF 完全属于你**
——许可证不影响你的作品。受约束的是分发：如果你把改过的 Tavotto 分发给别人，
或架成别人能通过网络访问的服务，需要向这些用户提供对应的源码。

### 贡献

贡献按 Tavotto 贡献者许可协议（CLA）接受：**你保留自己贡献的著作权**，同时授权
项目在社区版许可证下继续分发它，并在适用时按另行约定的商业条款分发。
Tavotto 的著作权人可以提供另行授权的版本。

见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [docs/legal/](docs/legal/)。

### 商标

**Tavotto™** 是 Tavotto 项目的未注册商标。开源的著作权许可不等于商标许可：
欢迎 fork，也可以说明自己基于 Tavotto，但不应让人误以为是官方发行版。
见 [TRADEMARKS.md](TRADEMARKS.md)。

---

如果 Tavotto 帮你在下一张图上省了一个下午，欢迎给仓库点个 star。
