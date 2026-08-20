<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/hero.svg" width="100%"
       alt="Tavotto — 把 matplotlib 面板拖到画布上排版，双击进图内改元素，导出真矢量 PDF">
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/blob/main/README.md">English</a> · <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Tavotto/Tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Tavotto/Tavotto/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-2868b7?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.13-1b1b18?style=flat-square&labelColor=1b1b18">
  <img alt="Platform" src="https://img.shields.io/badge/macOS%20·%20Windows%20·%20Linux-1b1b18?style=flat-square&labelColor=1b1b18">
</p>

<p align="center"><i>Edit the figure. Keep the source. —— 直接在图上改，脚本不动。</i></p>

投稿前的最后一公里通常是这样的：图早就画好了，但要拼成 Figure 1、调字号、
挪图例、对齐版面——于是回到 Python 里改一行、重跑脚本、再看一眼，来回二十遍。
或者把 PDF 拖进 Illustrator，从此和脚本失去联系。

**Tavotto 让你直接在图上改。** matplotlib 输出的面板拖进画布自由排版，
双击任意一张图就能点中里面的标题、坐标轴、曲线、图例——改字号、换颜色、拖位置，
**拖和调都是即时的**：图跟着光标一帧一帧地动，matplotlib 只在你松手时跑一次定稿。

所有修改都是**非破坏性**的：你的脚本一个字节都不会被改动，随时可撤销。
导出时引擎按全质量重新出图，合成一份文字仍可选中的真矢量 PDF。

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/workbench.png" width="100%"
       alt="Tavotto 工作台：左栏图内元素树列出标题与坐标轴，画布上三张面板排成 (a)(b)(c)，右栏正在编辑选中标题的属性">
</p>

<p align="center"><sub>左：图内元素 · 中：150×130 mm 的版面 · 右：选中标题后可改的属性（源文件仍是 <code>fig1_kinetics.py</code>）</sub></p>

## 安装

**下载安装包**：到 [最新发行版](https://github.com/Tavotto/Tavotto/releases/latest)
取 macOS 的 `.dmg` 或 Windows 的 `.exe`，装完双击即用，Tavotto 在自己的桌面窗口中打开。
之后的升级都在软件里完成——自己检查、下载、安装、重启，不用再回发行页。

**你不需要自己装 Python。** macOS 与 Windows 的安装包**都**自带一套 Tavotto 专用的
Python 运行环境，常用科学栈已经装好——numpy、matplotlib、pandas、scipy、seaborn、
Pillow，版本都是固定的，而且**两个平台锁的是同一组版本**，同一个脚本在两边画出
同一张图。装完立刻就能渲染，不用下载、不用联网，也不需要 Homebrew、Conda 或
Xcode。它**不会碰你已有的任何 Python 或 Conda**；如果某张图要用到清单之外的包，
在「设置 →「渲染环境」」里换成你自己那套环境即可。见[使用须知](#使用须知)。

macOS 版**只发 Apple Silicon（arm64）**。Intel Mac 目前没有构建、也没有验证过，
请走下面的 PyPI 安装。

**或者从 PyPI 装**，三个平台命令相同：

```sh
pipx install "tavotto[worker]"
tavotto
```

浏览器会自动打开 `http://127.0.0.1:5089`。

<details>
<summary>用 pip 装 · 复用你自己的科学栈环境 · 从源码跑</summary>

**pip**（装进当前环境）：

```sh
pip install "tavotto[worker]"
tavotto
```

**复用画图时用的环境**：去掉 `[worker]` 只装本体，再指定你自己的解释器——
这样渲染用的就是脚本当初依赖的那一套，图长什么样完全一致：

```sh
pipx install tavotto
export TAVOTTO_WORKER_PYTHON=/path/to/your/env/bin/python     # Windows: setx TAVOTTO_WORKER_PYTHON "..."
tavotto
```

**从源码跑**（需要 node + pnpm 构建界面）：

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
python scripts/build_frontend.py
.venv/bin/tavotto
```

</details>

**命令行参数**：`tavotto --figures <目录>` 直接打开某个图库；`--port 5089` 换端口；
`--no-browser` 不自动开浏览器。

## 先跑通一遍

仓库里带了一个可直接打开的示例项目：

```sh
tavotto --figures examples/figures
```

三张面板会出现在左侧素材栏。拖到画布上，双击其中一张——你会看到这张图里所有元素的
树，点标题就能改字号。`examples/figures/` 里就是两个普通的 matplotlib 脚本，
Tavotto 不要求你按任何特殊方式写它们。

## 图内能改什么

| | |
|---|---|
| **文字** | 标题、坐标轴标签、刻度标签、图例、图内注释——内容 / 字号 / 颜色 / 字重 / 字形 / 旋转 / 透明度 / 显隐，可直接拖动 |
| **数据系列** | 线宽、线型、颜色、marker（散点可整体换形状）、图例条目顺序 |
| **箭头** | 脚本画的箭头（`FancyArrowPatch`）：整体拖动、拖单个端点，改箭头样式 / 线型 / 线宽 / 帽大小 / 颜色。`annotate()` 的箭头保持数据锚点——只开放样式 |
| **坐标轴** | 刻度组、轴线、网格、3D 视角（elev/azim/roll）、3D 轴箭头与背景面板。拖动子图时，属于它的东西一起走——你摆过的标签、它的色条、孪生轴 |
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

## Code signing policy

Free code signing provided by SignPath.io, certificate by SignPath Foundation。
Windows 安装包由本仓库的 GitHub Actions 构建，并在被称为已签名发行版之前经过人工签名审批。
完整内容见 [签名政策](docs/code-signing-policy.md) 和 [隐私政策](docs/privacy.md)。

## 改图助手（可选）

助手面板可以把需求交给你本机的 **Codex / Claude CLI** 去直接改脚本，
例如「把图例移到左上角并缩小到 7pt」。改动前自动快照，完成后显示 diff 并重渲染，
一键可回滚。没装这些 CLI 不影响其余任何功能。

## 从别处把图送进来

在别的地方刚画好一张图（自己跑脚本、或让 Codex / Claude 写的），一条命令送进 Tavotto：

```bash
tavotto open figures/Fig1_kinetics.pdf   # 给产物
tavotto open figures/fig1_kinetics.py    # 给脚本也行，产出名自动解析
tavotto open figures/                    # 或者整个图库
```

它会把这张图所在的图库当项目打开、把缺的条目补进脚本注册表，然后**优先唤起桌面
应用**（已经开着就直接送进那个窗口，不会另起一套），没装桌面版就退回浏览器模式。

### Codex 插件

装上它，Codex 生成的 matplotlib 图会**天然是 Tavotto 能接手的形状**（脚本与产物同
目录、矢量 PDF、产物名可静态解析），画完自动交接过来：

```bash
codex plugin marketplace add Tavotto/Tavotto && codex plugin add tavotto@tavotto
```

装完新开一个会话即可。CLI 与 Codex 桌面应用共用同一份插件目录，**装一次两边都有**；
`codex plugin marketplace upgrade tavotto` 拉新版。

之后图例位置、字号、线宽、刻度这些直接在 Tavotto 里拖/改，不用再回去跟 AI 描述一遍。
详见 [`codex-plugin/README.md`](codex-plugin/README.md)，
分发路线（含官方插件目录的提交清单）见
[`docs/codex-plugin-distribution.md`](docs/codex-plugin-distribution.md)。

## 你的数据在哪

全部在本机。渲染、合成、导出都是本地进程，图和数据不会上传到任何地方。

| | |
|---|---|
| 文档与自动保存 | `~/Library/Application Support/Tavotto/`（Linux `~/.local/share/tavotto/`，Windows `%LOCALAPPDATA%\Tavotto\`） |
| 导出成图、画布文件与版本历史 | 都收在项目内的一个 `tavottofile/` 里：导出在 `tavottofile/export/`，命名画布就在旁边，版本历史在 `tavottofile/versions/`——找得到、好备份、跟着图一起同步。旧版本写在老位置的文件仍可读 |
| 你的脚本与图 | 只读——除非你明确选择「写回原始文件」，且该权限可按项目锁死 |
| 对外请求 | 每天一次检查有没有新版本；桌面版里你接受更新时，还会下载那个安装包。关掉「设置 → 检查更新」后两者都不再发生 |
| 匿名用量统计 | **默认不发，首启问一次，你同意才开始**。开了之后发的是粗粒度的功能事件（启动、打开图、一次编辑、导出成功）加上版本号、操作系统与架构，标识是本机随机生成的一串 UUID。**绝不发送**你的图、脚本、文件名、路径、科研数据、图内文字与改图助手的提示词。随时可在「设置 → 隐私、诊断与 About」关掉，或设 `TAVOTTO_NO_TELEMETRY=1`。[发了什么](docs/analytics/telemetry-events.md) · [隐私政策](docs/privacy.md) |

## 使用须知

- **第一次打开某张图时会跑一遍你的脚本**。轻量图秒级，重的该多久就多久；
  之后每次修改都是亚秒级。
- **渲染需要一个能 import 你脚本所需依赖的 Python**。这个 Python 从哪来，
  取决于你怎么装的 Tavotto：

  | 安装方式 | 渲染用的解释器 |
  |---|---|
  | Windows `.exe` | 安装包**自带的内置环境**：CPython 3.13 + numpy / matplotlib / pandas / scipy / seaborn / Pillow（版本固定）。不用装、不用下载。 |
  | macOS `.dmg`（arm64） | **同一套内置环境**，版本完全相同。不需要 Homebrew / Conda / Xcode。 |
  | PyPI + `[worker]` | 你装它的那个环境。 |

  选择顺序：`TAVOTTO_WORKER_PYTHON` → 你在设置里指定的 → 内置环境 → Tavotto 自身的
  解释器 → 机器上探测到的 Python / Conda。**你显式指定的永远优先**；
  Tavotto 只是**启动**你指定的环境来渲染，
  绝不往里面装任何东西，也绝不修改你已有的 Python / Conda。
  内置环境同样**只读**：字节码与 Matplotlib 字体缓存都改道到 Tavotto 自己的
  数据目录，安装目录一个字节都不写（macOS 上往签过名的 `.app` 里写东西会直接
  破坏代码签名，下次启动就成了「应用已损坏」）。

  内置环境覆盖的是**常用**科学栈，**不承诺覆盖你脚本可能 import 的任意包**。
  脚本要用内置环境里没有的包（rdkit、astropy、自家实验室的库）时，Tavotto 会
  直接告诉你缺的是哪个包，并给出「换成你自己的环境」的入口
  （设置 →「渲染环境」）；它**不会**替你把那个包装进内置环境，也不会装进你的环境
  ——那样内置环境就不再可复现，「重装就能修」这条退路也没了。
  一个可用解释器都没有时，排版、标注、导出照常，只有图内编辑用不了。

  「设置 → 隐私、诊断与 About」始终显示当前用的是哪个解释器、来自哪里
  （`bundled` / `configured` / `system` …）；用内置环境时还会显示它的 Python
  版本与每个包的精确版本——那份数据读的是随包分发的 `runtime-manifest.json`，
  诊断包里也有同一份。

- **桌面安装包比较大：下载约 180 MB，装完约 490 MB**（macOS arm64 实测；
  不带内置环境的 v0.7.0 是 62 MB / 131 MB）。多出来的就是内置环境：CPython 加上
  numpy / scipy / pandas / matplotlib 及其编译扩展。这是「装完即可渲染」的代价，
  只付一次、且完全离线。PyPI 安装仍然只有几 MB，因为它复用你已有的 Python。

## 开发

```sh
.venv/bin/python -m pytest        # 后端
cd web && pnpm test               # 前端
cd web && pnpm build              # 类型检查（tsc -b）+ 打包

# 打桌面版（macOS / Windows 都要）：构建内置渲染环境。
# 版本锁在 packaging/runtime-lock.json（按平台/架构分层）；脚本会校验 CPython
# 下载的 SHA-256、按 .dist-info 核对每个包的版本，最后用刚装好的解释器逐个
# import 并真画一张 PDF——任何一步不过就当场失败。
python scripts/build_worker_runtime.py              # 按本机平台/架构挑目标
python scripts/build_worker_runtime.py --list-targets
python scripts/build_desktop.py                     # 完整桌面链路（含上面这步）
```

欢迎提 issue 与 PR——改动怎么验、代码库刻意守着哪些边界，见
[CONTRIBUTING.md](CONTRIBUTING.md)。报 bug 时，「设置 → 隐私、诊断与 About →
下载诊断包」会把通常需要的信息一次收齐（密钥与个人路径已脱敏）。安全问题请走
[私密报告](https://github.com/Tavotto/Tavotto/security/advisories/new)，
不要开公开 issue。

## 许可证

[AGPL-3.0-only](https://github.com/Tavotto/Tavotto/blob/main/LICENSE)。

自己使用、修改、在实验室内部部署都不受限制，**用它排出来的图和导出的 PDF 完全属于你**
——许可证不影响你的作品。受约束的是分发：如果你把改过的 Tavotto 分发给别人，
或架成别人能通过网络访问的服务，需要向这些用户提供对应的源码。

## Star history

<a href="https://www.star-history.com/?repos=Tavotto%2FTavotto&type=date&legend=bottom-right">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Tavotto/Tavotto&type=date&theme=dark&legend=bottom-right&sealed_token=N_HkVy3WXmZ-L-LdXjq8yjVIGq3O6NWzfAI0NxRWdgJomReAYwu9qlvk78IdfeG8loxZTvRLP_VjiVIrO3ZIrfe8yEzeeklvUfkoRjpWy1Zm5SazecpETgwnZyseVroitCM5lhCLnTU7dorXRnk3FnU34Auy9YsfWrfmlPEb0IP0Sjwaz_7q47jCFt4C" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Tavotto/Tavotto&type=date&legend=bottom-right&sealed_token=N_HkVy3WXmZ-L-LdXjq8yjVIGq3O6NWzfAI0NxRWdgJomReAYwu9qlvk78IdfeG8loxZTvRLP_VjiVIrO3ZIrfe8yEzeeklvUfkoRjpWy1Zm5SazecpETgwnZyseVroitCM5lhCLnTU7dorXRnk3FnU34Auy9YsfWrfmlPEb0IP0Sjwaz_7q47jCFt4C" />
    <img alt="Tavotto/Tavotto 的 star 增长" src="https://api.star-history.com/chart?repos=Tavotto/Tavotto&type=date&legend=bottom-right&sealed_token=N_HkVy3WXmZ-L-LdXjq8yjVIGq3O6NWzfAI0NxRWdgJomReAYwu9qlvk78IdfeG8loxZTvRLP_VjiVIrO3ZIrfe8yEzeeklvUfkoRjpWy1Zm5SazecpETgwnZyseVroitCM5lhCLnTU7dorXRnk3FnU34Auy9YsfWrfmlPEb0IP0Sjwaz_7q47jCFt4C" />
  </picture>
</a>
