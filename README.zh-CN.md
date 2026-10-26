<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/hero-window.zh.png" width="100%"
       alt="macOS 上的 Tavotto 桌面窗口，排版已保存到项目，已选中图 (a)，右栏显示它的位置、尺寸和缩放。">
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/blob/main/README.md">English</a> · <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Tavotto/Tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://pypi.org/project/tavotto/"><img alt="PyPI" src="https://img.shields.io/pypi/v/tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Tavotto/Tavotto/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-1b1b18?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.14-1b1b18?style=flat-square&labelColor=1b1b18">
  <a href="https://ko-fi.com/tavotto"><img alt="Support on Ko-fi" src="https://img.shields.io/badge/Ko--fi-support-FF5E5B?style=flat-square&logo=kofi&logoColor=white&labelColor=1b1b18"></a>
  <a href="https://afdian.com/a/Tavotto"><img alt="在爱发电支持" src="https://img.shields.io/badge/%E7%88%B1%E5%8F%91%E7%94%B5-%E6%94%AF%E6%8C%81-946CE6?style=flat-square&labelColor=1b1b18"></a>
</p>

# Tavotto：matplotlib 图的可视化编辑器

**Tavotto™ 是免费开源的 matplotlib 图形编辑器，AI 写的绘图脚本也能接手。** 它打开脚本已经画好的图，标题、图例、曲线和版面都在画布上改。脚本一行不动，每处改动单独保存；导出之前，再按期刊规范（字号下限、栏宽、dpi）检查一遍。

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/demo.zh.gif" width="100%"
       alt="12 秒演示：选中一幅图，拖动它，把宽度改成 56 毫米，然后导出。">
</p>

<p align="center">
  <a href="https://www.tavotto.com/zh/#video"><img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/launch-film-cover.webp" width="60%"
       alt="Tavotto 发布短片的封面：深色底上的 Tavotto 标志。点开在 tavotto.com 播放 33 秒短片，有配乐。"></a>
</p>

## 下载

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases/latest/download/Tavotto-macOS.dmg"><img alt="下载 macOS Apple Silicon 版" src="https://img.shields.io/badge/macOS-Apple%20Silicon-2868b7?style=for-the-badge&logo=apple&logoColor=white&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/releases/latest/download/Tavotto-macOS-Intel.dmg"><img alt="下载 macOS Intel 版" src="https://img.shields.io/badge/macOS-Intel-2868b7?style=for-the-badge&logo=apple&logoColor=white&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/releases/latest/download/Tavotto-Windows-Setup.exe"><img alt="下载 Windows x64 版" src="https://img.shields.io/badge/Windows-x64-2868b7?style=for-the-badge&logo=windows&logoColor=white&labelColor=1b1b18"></a>
  <br>
  <a href="#pypi-安装linux-与服务器"><img alt="用 PyPI 安装" src="https://img.shields.io/badge/PyPI-pipx%20install-2868b7?style=for-the-badge&logo=pypi&logoColor=white&labelColor=1b1b18"></a>
  <a href="#在-codex-中第一次使用-tavotto"><img alt="安装 Codex 插件" src="https://img.shields.io/badge/Codex-plugin-2868b7?style=for-the-badge&logo=openai&logoColor=white&labelColor=1b1b18"></a>
  <a href="#在-claude-code-中使用-tavottobeta"><img alt="安装 Claude Code 插件" src="https://img.shields.io/badge/Claude%20Code-plugin%20(Beta)-2868b7?style=for-the-badge&logo=claude&logoColor=white&labelColor=1b1b18"></a>
</p>

<p align="center"><sub>系统要求：Apple Silicon 需 <b>macOS 14</b> 及以上，Intel 需 <b>macOS 15</b> 及以上，或 Windows x64（Windows ARM 没有构建、也没有验证过）。Linux 走 PyPI（beta）。什么算支持，唯一出处是 <a href="docs/support-matrix.json"><code>docs/support-matrix.json</code></a>。</sub></p>

## 如何开始使用

### 桌面版，三步

1. **安装。** 点上面的按钮下载，打开 `.dmg` 或运行 `.exe`。安装包自带 Python 和 numpy、matplotlib、pandas、scipy、seaborn、Pillow，不用自己装 Python。下载约 195 MB（macOS）/ 89 MB（Windows）。
2. **打开你的图。** 把 matplotlib 脚本（`.py`）拖进窗口，或打开存放绘图脚本的文件夹。主页上还有一个带引导的示例。
3. **改图并导出。** 双击一张图进入图内编辑，在版面上排好，再点导出。写出任何文件之前都会先跑一遍投稿前检查。

Windows 安装包未签名时，首次运行会弹 SmartScreen 提示：点「更多信息 → 仍要运行」，或先对照 Release 页的 `SHA256SUMS.txt` 核验下载。更多见[安装详解](docs/guide/install-advanced.zh-CN.md)。

### 在 AI 编程助手里用

同一套引擎和画布也以工具的形式提供给编程助手，让它画图、在 Tavotto 里打开、跑预检并导出。

| 助手 | 状态 | 怎么装 |
| --- | --- | --- |
| Codex | 插件 | [在 Codex 中第一次使用 Tavotto](#在-codex-中第一次使用-tavotto) |
| Claude Code | Beta | [在 Claude Code 中使用 Tavotto](#在-claude-code-中使用-tavottobeta) |
| DeepSeek Harness | Beta | [在 DeepSeek Harness 中使用 Tavotto](#在-deepseek-harness-中使用-tavottobeta) |
| WorkBuddy | Beta | [在 WorkBuddy 中使用 Tavotto](#在-workbuddy-中使用-tavottobeta) |
| Cursor、VS Code、Trae、ZCode、MiniMax Code、Claude Desktop | 实验 | [配置生成器](#在其他-ai-编辑器--客户端中使用实验) |

### PyPI 安装（Linux 与服务器）

```sh
pipx install "tavotto[worker]"
tavotto
```

浏览器会打开 `http://127.0.0.1:5089`。需要 Python 3.10 及以上；Python 太旧时，`uv tool install --python 3.12 "tavotto[worker]"` 会自带一份。`tavotto --figures <目录>` 直接打开某个图库。更多（uv、SSH 连远程服务器、用你自己的 Python 环境）见[安装详解](docs/guide/install-advanced.zh-CN.md)。

## 能做什么

### 在图里改

双击一张图，Tavotto 把你的脚本跑一遍，并把那个 matplotlib `Figure` 留在内存里。从左边的树里选，或直接在画布上点标题、刻度、曲线、图例、箭头，改字号、颜色、线型或位置；拖动时图跟着光标走，松手时 matplotlib 才跑一次。[能改什么](docs/guide/editing-reference.zh-CN.md)。

![在图 (a) 内部编辑：左边是元素树，整个页面都在视野里，选中标题，上方是字号、粗体、斜体工具条，右栏是字体、字号和颜色。](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/canvas-edit.zh.png)

### 把多张图排进一页

图按脚本画出来的尺寸放在以毫米计的版面上，可吸附、对齐，编成行、列或网格，一键加 (a)(b)(c) 标注，再在上面画箭头、比例尺和放大框。

![多图排版：三幅图排在 150 × 125 毫米的页面上，选中 (a)，右栏以毫米显示它的 X、Y、宽、高。](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/layout.zh.png)

### 投稿前检查并导出

导出 PDF 时文字保持为真正的矢量文字，另可导出 PNG、TIFF 和 SVG（PNG、TIFF 可选 300 到 1200 ppi；单独导出一张有脚本的图时还有 EPS）。同时，图会按**最终物理尺寸**对照出版规范检查：字号低于下限、栏宽不对、位图分辨率不够。结果分为要改、留意、无法核验和建议四类，不会锁住导出按钮；导出时仍未处理的项会记进随成图写出的样式检查报告。[格式、等级与规范](docs/guide/export-and-preflight.zh-CN.md)。

![导出弹窗：整页或逐图导出，PDF 与 PNG 600 ppi，按默认规范的导出前检查显示 1 项留意、3 项无法核验。](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/preflight.zh.png)

### 连接你自己的 AI 来改图

助手面板可以替你改脚本（「把图例移到左上角，7 pt」）。可以用 ChatGPT（Codex）或 Claude 的订阅登录，也可以用 DeepSeek、Kimi、智谱 GLM、MiniMax、阿里云百炼、OpenAI、Anthropic 等的 API Key，或任何兼容的自定义地址。改动前先给脚本存快照，你能看到 diff，一键回滚。这是**唯一**会碰到你脚本的路径，而且要由你发起。

![「连接 AI」弹窗：DeepSeek、Kimi、智谱 GLM、MiniMax、百炼，每家一个「连接」按钮，下方是「更多 AI…」。](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/connect-ai.zh.png)

### 在脚本原本能跑的环境里打开

Tavotto 会找到你写这个脚本时用的 conda、venv 或系统 Python，并在那里运行。找不到的包会点名告诉你，你自己的环境从不被修改。在终端里，`tavotto run -- python figure.py` 会接管你平时那条命令画出的图（[Beta](docs/compatibility/tavotto-run.md)）。

![运行关联脚本之后：素材库里 fig1_kinetics.py 显示「已捕获 1 张图」，产出的图已放在画布上，右栏显示它的位置、尺寸和原始尺寸。](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/run-script.zh.png)

### 回到任何一个早先的版面

排版时间线会在你停下编辑后（最多每两分钟一个）、以及导出、写回、保存、打开和离开项目时自动留一个节点。想留住的可以起名，可以和当前画布对比，再恢复；恢复之前的状态也会先存下来。

![右栏的时间线页签，列出两个命名时刻「三幅图」「下排 52 mm」，下面是「打开项目」。](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/timeline.zh.png)

## 为什么用它

|  | 改脚本再重跑 | 导进矢量编辑器收尾 | Tavotto |
|---|---|---|---|
| 把图例挪 3 毫米 | 改数字、运行、看、再改 | 拖一下 | 拖一下 |
| 知道点中的是图例而不是路径 | 在代码里 | 不知道 | 知道：标题、图例、刻度、数据系列、色条 |
| 数据更新后改动还在 | 在 | 不在，手工排版作废 | 在，回放到重新跑出的图上 |
| 以毫米排多图版面 | 在代码里手排 | 可以 | 可以 |
| 导出前按期刊规范检查 | 没有 | 没有 | 有 |

矢量编辑器是更强的绘图工具。它只是不知道你点中的那个东西是图例，也让图和画出它的代码分了家。

## 常见问题

**能直接改 matplotlib 生成的 PDF 吗？** 能，前提是画出它的脚本就在旁边：`tavotto open figures/Fig1.pdf` 会对应到同一项目里的脚本、重新运行，让你改图本身，而不是一张压平的图片。项目里找不到脚本的 PDF 仍然可以放进版面、排版、加标注和导出，但不能在图内编辑。

**会改我的脚本吗？** 不会。改动以 override 的形式存在排版里，回放到重新跑出的图上。「写回原文件」是一个明确的操作，还可以按项目锁死。可选的 AI 助手是另一个会改脚本的入口，且只在你主动发起时才改。

**数据会上传吗？** 不会。渲染、排版和导出都在你的电脑上完成。Tavotto 自己只发两类请求：每天一次的更新检查，以及默认关闭、由你同意才开启的匿名使用统计。你连接了 AI 时，请求只发给那个 AI，经它的命令行工具或直接发到它的 API。见[隐私](#隐私)。

**Claude、Codex、ChatGPT 写的绘图脚本能用吗？** 能。任何 matplotlib 脚本都行，包括 seaborn、pandas 画的图。让智能体来画时，脚本和产物放在同一个文件夹、产物存成矢量 PDF；插件会教智能体这个约定。

**要自己装 Python 吗？** 桌面版不用，它自带。PyPI 安装需要 Python 3.10 到 3.14。

**plotly、Pillow 画的图呢？** 它们不是 matplotlib 对象，不能在图内编辑。Tavotto 会如实说明，并可以请你连接的 AI 另存一份可编辑的 matplotlib 版新脚本，原脚本不动。

## 在 AI 编程助手里安装

上表里每个助手的逐步安装。命令以外的细节都在[完整说明](docs/guide/ai-hosts.zh-CN.md)里。

### 在 Codex 中第一次使用 Tavotto

> **普通用户不要克隆或构建这个仓库。** 源码安装只用于参与 Tavotto 开发。

| 你要做什么 | 需要安装什么 |
| --- | --- |
| Codex 画完图后，在 Tavotto 桌面窗口里继续拖拽修改 | Tavotto 桌面版 + Codex 插件（不需要 Python 引擎） |
| 在 Codex 里直接使用 Tavotto 画布、预检、修改与导出工具 | Codex 插件 + Tavotto Python 引擎 |
| 修改 Tavotto 本身 | 见下方「贡献者：从源码开发」 |

#### 完整的 Codex 集成

在终端依次运行：

```sh
codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins
codex plugin add tavotto@tavotto
pipx install "tavotto[worker]"
```

然后**关闭当前 Codex 会话并新开一个会话**。插件的 skill 与 MCP 工具不会在已经打开的会话里热重载。引擎是用 `pip`/`pipx` 装的话，`tavotto codex install` 会替你做那两条 `codex plugin` 步骤，并跳过已经正常的部分；`tavotto codex doctor` 只诊断、不改动。

<details>
<summary><b>Windows：插件已启用但没有工具，或只装了 Codex 桌面版（没有 Git，<code>PATH</code> 上没有 <code>codex</code>）</b></summary>

插件显示已启用却一个工具都没有时，运行 `tavotto codex install`。只装了 Codex 桌面版的话，跳过那两条 `codex plugin` 命令，交给 Tavotto：它会自己找到 `%LOCALAPPDATA%\OpenAI\Codex\bin\<一串哈希>\codex.exe` 这个自带的 CLI；Codex 起不来 `git` 时，改从 GitHub 下载插件发行分支的压缩包，并逐个文件对照构建清单：

```powershell
pipx install "tavotto[worker]"
tavotto codex install
```

没有 Python 引擎时，在 PowerShell 里手动做同样的事：

```powershell
$codex = (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$dir = "$env:LOCALAPPDATA\Tavotto\codex-marketplace"
Invoke-WebRequest https://github.com/Tavotto/Tavotto/archive/refs/heads/plugin-stable.zip -OutFile "$env:TEMP\tavotto-plugin-stable.zip"
Expand-Archive "$env:TEMP\tavotto-plugin-stable.zip" $dir -Force
& $codex plugin marketplace add "$dir\Tavotto-plugin-stable"
& $codex plugin add tavotto@tavotto
```

本地市场没有 `codex plugin marketplace upgrade`：升级用 `tavotto codex upgrade`，或删掉 `$dir\Tavotto-plugin-stable` 后重新下载。成因与症状见[完整说明](docs/guide/ai-hosts.zh-CN.md)和 [`codex-plugin/README.md`](codex-plugin/README.md)。

</details>

新会话里可以直接说：

> 用 Tavotto 画这张图。先运行 Tavotto 健康检查；健康后再画，最后在 Tavotto 里打开。不要安装或升级任何已经可用的组件。

插件装在你本机的 `~/.codex` 配置里，因此只有会读取本机插件的 Codex 界面才能加载它：终端里的 Codex CLI 与 Codex 桌面应用。不读取本机插件的界面（纯云端会话、不认 `~/.codex/plugins` 的 IDE 集成）永远不会出现 Tavotto 工具，先在终端的 `codex` 会话里验证。

#### 只交给桌面版收尾

装桌面版 + 插件（上面的两条 `codex plugin` 命令；这条路不需要 `pipx` 那行）。让 Codex「在 Tavotto 里打开」时，插件的 skill 会用自带的交接脚本完成交接，它会自己找到桌面版内置的命令行：

```sh
python3 <插件目录>/skills/tavotto-figure/scripts/handoff.py path/to/figure.py
```

别让 Codex 直接跑裸的 `tavotto open`：桌面安装包**刻意不改你的 PATH**，那条命令只在 PyPI 安装之后才存在。脚本与产物应放在同一目录，产物优先保存为矢量 PDF。

#### 让 Codex 代你安装

把下面一句完整发给 Codex：

> 请严格按照 README 的「在 Codex 中第一次使用 Tavotto」执行普通用户安装。不要 clone 或构建源码，不要运行 pnpm、npm、cargo、Tauri、测试或 editable install。只安装 Codex 插件和所需的 Tavotto 引擎，运行健康检查；需要新会话时明确告诉我并停止。

### 在 Claude Code 中使用 Tavotto（Beta）

同一份插件可装进 Claude Code 的终端和 IDE 扩展，限 Local 或 SSH 会话（Cloud 和 WSL 会话不加载本地插件）。在对应环境的终端逐条运行：

```sh
claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin
claude plugin install tavotto@tavotto
pipx install "tavotto[worker]"
```

然后新开 Claude Code 会话，或运行 `/reload-plugins`；`/mcp` 里应该列出 `plugin:tavotto:tavotto`。Claude Code 没有内嵌画布，照常用工具打开、修改、预检和导出，要手动编辑就交接桌面版。更新用 `claude plugin update tavotto@tavotto`。

### 在 DeepSeek Harness 中使用 Tavotto（Beta）

要求 `PATH` 上已有 `pnpm`，DSH 的插件管理器会调用它。把 bundle 加进实际使用的 profile（这里的 `web` 是 `dsh web` 启动的那一份）：

```sh
dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"
pipx install "tavotto[worker]"
```

装好后用 `dsh web` 新开会话，请在项目目录里启动，不要在主目录启动。更新用 `dsh plugin --profile web update tavotto-dsh`。没有 pnpm 就用[配置生成器](#在其他-ai-编辑器--客户端中使用实验)，选 `--host dsh`。

### 在 WorkBuddy 中使用 Tavotto（Beta）

WorkBuddy 装的是同一份 Claude Code 插件。在它的插件市场选择「添加市场」，填入：

```text
Tavotto/Tavotto
```

安装 `tavotto@tavotto`，在终端运行 `pipx install "tavotto[worker]"`，再新开对话，让 WorkBuddy 调用 `tavotto_health`。请在项目目录使用，不要在主目录使用。更新和卸载都在 WorkBuddy 里该插件的卡片上操作。

以上 Beta 只描述安装渠道，依据是本地宿主冒烟，不是发行产物的 `host_verified` 验收。各宿主哪些验证过、哪些没有，见[完整说明](docs/guide/ai-hosts.zh-CN.md)。

### 在其他 AI 编辑器 / 客户端中使用（实验）

Cursor、VS Code（GitHub Copilot Agent）、Trae、ZCode、MiniMax Code、Claude Desktop（本地聊天）和上面这些宿主用的是同一份 MCP 服务和技能。从 [Releases](https://github.com/Tavotto/Tavotto/releases) 下载 `codex-plugin-<版本>.zip`（名字带 codex 是历史原因，内容对所有宿主一样），解压到会长期保留的目录，再生成配置。生成器**只打印，不写任何文件**：

```sh
python3 <完整包>/integrations/configure.py --host vscode --project-root /绝对路径/你的项目
```

```powershell
py -3 '<完整包>\integrations\configure.py' --host vscode --project-root 'D:\你的项目'
```

然后在对话里调用 `tavotto_health`，确认 `server.package_dir` 就是刚解压的那份包。引擎准备、`--host` 的可选值、升级方法和各宿主的验证程度见[完整说明](docs/guide/ai-hosts.zh-CN.md)。

## 隐私

渲染、排版和导出都在本机进行，Tavotto 不会上传你的图、脚本、项目或数据。它自己只发两类请求：每天一次检查 GitHub Releases 有没有新版本（按时区和语言，也可能先问项目的国内镜像 `dl.tavotto.cn`；在**设置 → 检查更新**里可以关），以及默认**关闭**、由你同意才开启的匿名使用统计，其中绝不会出现图、脚本、路径或文字内容。诊断包只保存在本地、已脱敏，确认后才会发送。详见 [docs/privacy.md](docs/privacy.md) 与[事件契约](docs/analytics/telemetry-events.md)。Tavotto 把文件放在哪：[docs/guide/files-and-data.zh-CN.md](docs/guide/files-and-data.zh-CN.md)。

Free code signing provided by SignPath.io, certificate by SignPath Foundation。完整内容见[签名政策](docs/code-signing-policy.md)。安全问题请走[私密报告](https://github.com/Tavotto/Tavotto/security/advisories/new)，不要开公开 issue。

## 参与开发

欢迎提 issue 与 PR，改动怎么验、代码库刻意守着哪些边界，见 [CONTRIBUTING.md](CONTRIBUTING.md)。报 bug 时，**设置 → 隐私、诊断与 About → 下载诊断包**会把通常需要的信息一次收齐（密钥与个人路径已脱敏）。

**贡献者：从源码开发。** 这条路只用于修改 Tavotto 本身，不是普通安装失败后的退路（构建界面需要 node 和 pnpm）：

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
.venv/bin/python scripts/fetch_fonts.py   # 批准的字体（不在 git 里）
python scripts/build_frontend.py
.venv/bin/tavotto
```

## 支持项目

Tavotto 免费开源，由一个人维护。如果它帮你省了时间，可以支持它的持续开发：在 **爱发电** 用支付宝或微信支付，或在 **Ko-fi** 一次性或按月支持。支持完全自愿，不解锁任何功能；钱用在哪里、支持者墙，见 [tavotto.com/zh/support](https://www.tavotto.com/zh/support/)。

<p>
  <a href="https://afdian.com/a/Tavotto"><img alt="在爱发电支持 Tavotto" src="https://img.shields.io/badge/%E5%9C%A8%E7%88%B1%E5%8F%91%E7%94%B5%E6%94%AF%E6%8C%81-946CE6?style=for-the-badge"></a>
  <a href="https://ko-fi.com/tavotto"><img alt="Support Tavotto on Ko-fi" src="https://img.shields.io/badge/Support%20on%20Ko--fi-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white"></a>
</p>

### 赞助者

在 [爱发电](https://afdian.com/a/tavotto) 选择**赞助人**方案、或在 [Ko-fi](https://ko-fi.com/tavotto/tiers) 选择每月 $50 **Sponsor** 档的支持者，会带头像列在这里，赞助期间一直保留。图片自动更新。

<a href="https://www.tavotto.com/zh/support/"><img src="https://www.tavotto.com/api/supporters/sponsors" alt="Tavotto 赞助者" width="720"></a>

## 许可证

[AGPL-3.0-only](LICENSE)。

自己使用、修改、在实验室内部部署都不受限制，**用它排出来的图和导出的 PDF 完全属于你**——许可证不影响你的作品。受约束的是分发：如果你把改过的 Tavotto 分发给别人，或架成别人能通过网络访问的服务，需要向这些用户提供对应的源码。

### 贡献

贡献按 Tavotto 贡献者许可协议（CLA）接受：**你保留自己贡献的著作权**，同时授权项目在社区版许可证下继续分发它，并在适用时按另行约定的商业条款分发。Tavotto 的著作权人可以提供另行授权的版本。

见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [docs/legal/](docs/legal/)。

### 商标

**Tavotto™** 是 Tavotto 项目的未注册商标。开源的著作权许可不等于商标许可：欢迎 fork，也可以说明自己基于 Tavotto，但不应让人误以为是官方发行版。见 [TRADEMARKS.md](TRADEMARKS.md)。

---

如果 Tavotto 帮你省了一个下午，欢迎给仓库点个 star。
