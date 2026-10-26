# 在 AI 编程助手里使用 Tavotto（完整说明）

> 属于 [Tavotto 自述](../../README.zh-CN.md)，这里放 README 指过来的细节。

## 完整的 Codex 集成

验收状态：画布在真实 Codex Desktop 任务里只验收过一次，时间是 2026-08-24，引擎与插件 0.9.2，Codex Desktop 26.818（[验收记录](../acceptance/codex-desktop-canvas.md)）；当前发行版没有重做，而「把图交接到桌面窗口」不算这块画布。不读取本机插件的宿主永远看不到这些工具。

在终端依次运行：

```sh
codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins
codex plugin add tavotto@tavotto
pipx install "tavotto[worker]"
```

然后**关闭当前 Codex 会话并新开一个会话**。插件的 skill 与 MCP 工具不会在已经
打开的会话里热重载。

**Windows 上若「插件已启用，工具一个都没有」**（升级插件后又出现也一样），再跑一条：

```sh
tavotto codex install
```

插件用自带的启动器起 MCP server：它会找一个真能跑的 Python，并跳过微软商店的
`python3` 别名；一个都找不到时，这条命令把已装副本的启动命令钉到一个验证过的
解释器（`tavotto codex doctor` 只诊断不改）。插件 0.17.0 在 Windows 上有一个启动器
缺陷，会让所有人都一个工具都没有：跑 `codex plugin marketplace upgrade tavotto`
（没有 Git、按下面的办法装的，跑 `tavotto codex upgrade`），再新开会话。成因与症状见
[`codex-plugin/README.md`](../../codex-plugin/README.md)。

**Windows 上只装了 Codex 桌面版（`PATH` 上没有 `codex`，也没有 Git）。** 那两条
`codex plugin` 命令需要 `PATH` 上有 Codex CLI 和 Git for Windows：
`codex plugin marketplace add` 用 `git` 克隆市场，没有 Git 时报 `program not found`；
Codex 桌面版自带的 CLI（`%LOCALAPPDATA%\OpenAI\Codex\bin\<一串哈希>\codex.exe`）
不在 `PATH` 上。跳过那两行，交给 Tavotto：

```powershell
pipx install "tavotto[worker]"
tavotto codex install
```

`tavotto codex install` 会自己找到桌面版自带的 CLI。Codex 报「起不来 `git`」时，它改从
GitHub 下载插件发行分支的压缩包
（<https://github.com/Tavotto/Tavotto/archive/refs/heads/plugin-stable.zip>），逐个文件
对照插件的构建清单、并与同一次 GitHub release 附带的构建清单核对，再登记成本地市场
（放在 `%LOCALAPPDATA%\Tavotto\codex-marketplace`）。本地市场没有
`codex plugin marketplace upgrade`，升级用 `tavotto codex upgrade`。装好后新开 Codex 会话。

没有 Python 引擎（下面「只交给桌面版收尾」那条路）时，在 PowerShell 里手动做同样的事：

```powershell
$codex = (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$dir = "$env:LOCALAPPDATA\Tavotto\codex-marketplace"
Invoke-WebRequest https://github.com/Tavotto/Tavotto/archive/refs/heads/plugin-stable.zip -OutFile "$env:TEMP\tavotto-plugin-stable.zip"
Expand-Archive "$env:TEMP\tavotto-plugin-stable.zip" $dir -Force
& $codex plugin marketplace add "$dir\Tavotto-plugin-stable"
& $codex plugin add tavotto@tavotto
```

手动升级：删掉 `$dir\Tavotto-plugin-stable`，重跑下载与 `Expand-Archive` 两行，再跑最后
一行。手动这条路不核对文件；装了引擎之后 `tavotto codex doctor` 会核对。

新会话里可以直接说：

> 用 Tavotto 画这张图。先运行 Tavotto 健康检查；健康后再画，最后在 Tavotto 里
> 打开。不要安装或升级任何已经可用的组件。

之后 Codex 修改、新建或重命名绘图脚本时，会调用插件的 `tavotto_refresh_project`
工具：Tavotto 重新读一遍项目（只做静态分析，不运行脚本），开着的 Tavotto 窗口
自己更新——你不需要手动刷新或重启。工具会报告哪些图现在可编辑、哪些还需要你在
Tavotto 里点一次「试运行并连接」、哪些有源脚本冲突要你来裁决。

已经画好的图只要改宽度、字体或字号下限时，直接说「把这张图改成 8 cm 宽、
Times New Roman、字号不小于 8 pt」：Codex 会调用 `tavotto_normalize_figure`——只改你
点名的那几项，其余内容、配色、数据、子图结构一律不动；缩小后文字装不下时先做有上限
的边距调整，再检查最终导出的文件本身（PDF 页面尺寸与嵌入字体、PNG 像素与 dpi）。
装不下、字体没装或需要改结构时它会停下来告诉你需要放宽哪一条，不会替你改标准，也
不会留下一个看起来成功的文件。

第一次出现项目目录授权时，确认的是 Tavotto 可以访问的本地图库目录。图、脚本和
数据仍在本机处理。

插件装在你本机的 `~/.codex` 配置里，因此只有会读取本机插件的 Codex 界面才能
加载它——终端里的 Codex CLI 与 Codex 桌面应用。不读取本机插件的界面（纯云端
会话、不认 `~/.codex/plugins` 的 IDE 集成）永远不会出现 Tavotto 工具——先在
终端的 `codex` 会话里验证，不要在那些界面里反复排障。

## 只交给桌面版收尾

装桌面版 + 插件（上面的两条 `codex plugin` 命令；这条路**不需要** `pipx` 那行）。
让 Codex「在 Tavotto 里打开」时，插件的 skill 会用自带的交接脚本完成交接——它会
自己找到桌面版内置的命令行：

```sh
python3 <插件目录>/skills/tavotto-figure/scripts/handoff.py path/to/figure.py
```

别让 Codex 直接跑裸的 `tavotto open`：桌面安装包**刻意不改你的 PATH**，那条命令
只在 PyPI 安装之后才存在。这条路径不要求 Codex 内嵌画布，也不需要 Python 引擎。
脚本与产物应放在同一目录，产物优先保存为矢量 PDF。

## 让 Codex 代你安装

把下面一句完整发给 Codex：

> 请严格按照 README 的「在 Codex 中第一次使用 Tavotto」执行普通用户安装。
> 不要 clone 或构建源码，不要运行 pnpm、npm、cargo、Tauri、测试或 editable
> install。只安装 Codex 插件和所需的 Tavotto 引擎，运行健康检查；需要新会话时
> 明确告诉我并停止。

## 在 Claude Code 中使用 Tavotto（Beta）

同一份插件可装进 Claude Code 的终端和 IDE 扩展。桌面应用的 Code 标签页仅限 **Local 或 SSH
会话**使用插件；Cloud 会话不继承本地安装的插件，WSL 会话不支持插件
（[Claude 的环境限制](https://code.claude.com/docs/en/desktop#install-plugins)）。
Cloud 或 WSL 用户请切换到 Local Code 会话使用这条路径；在本机安装不会让这两个环境获得插件。
SSH 会话要在会话实际执行的远程机器上安装。在对应环境的终端逐条运行：

```sh
claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin
claude plugin install tavotto@tavotto
pipx install "tavotto[worker]"
```

然后新开 Claude Code 会话，或在当前会话运行 `/reload-plugins`。用 `/mcp` 确认
`plugin:tavotto:tavotto` 已连接，再让 Claude 调用 `tavotto_health` 确认找到了引擎。
授权范围是 Claude Code 的启动目录（通过 MCP roots 报告）；`/add-dir` 可添加项目目录。
Claude Code 没有内嵌画布，照常用工具打开、修改、预检和导出；要手动编辑就交接桌面版。
更新用 `claude plugin update tavotto@tavotto`。

Claude Desktop 的聊天界面和 claude.ai 不会从插件启动本地 MCP 服务。Claude Desktop 的本地聊天
请用下面的配置生成器；这条路径不会给 Cloud 或 WSL Code 会话启用插件。

## 在 DeepSeek Harness 中使用 Tavotto（Beta）

同一份插件也是 DeepSeek Harness bundle。这条可选路径要求 PATH 上已有 `pnpm`：
[DSH 的插件管理器](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md#plugin-management)
在安装和更新时会调用它。没有 pnpm，或 DSH 提示找不到它，就使用
[下面的配置生成器路径](#在其他-ai-编辑器--客户端中使用实验)，选择 `--host dsh`。
这条既有路径仍属实验，无需为 Tavotto 另装 Node.js 或 pnpm。

已有 pnpm 时，把 bundle 加进实际使用的 profile（这里的 `web` 是 `dsh web` 启动的那一份）：

```sh
dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"
pipx install "tavotto[worker]"
```

装好后新开会话（`dsh web`）；发现完成后会出现 `mcp__tavotto__*` 工具，技能目录中会有
`tavotto-figure`。授权范围是启动 `dsh` 的目录，所以请在项目目录里启动，不要在主目录启动。
DSH 没有内嵌画布，走同一组工具。更新用 `dsh plugin --profile web update tavotto-dsh`。
bundle 和下面生成器提供的 `cordis.patch.yml` 二选一；不要同时配置，否则两行会抢同一个
`tavotto` 服务名。

## 在 WorkBuddy 中使用 Tavotto（Beta）

WorkBuddy 装的是同一份 Claude Code 插件。在插件市场选择「添加市场」，填入：

```text
Tavotto/Tavotto
```

从新市场安装 `tavotto@tavotto`，再在终端运行 `pipx install "tavotto[worker]"`。
新开对话，让 WorkBuddy 调用 `tavotto_health` 确认找到了引擎。授权范围是对话的工作目录
（WorkBuddy 不报告 MCP roots），请在项目目录使用，不要在主目录使用。
WorkBuddy 的内嵌画布还没有验收；工具可独立使用。若之前用配置生成器手动添加过 Tavotto，
请移除重复项，避免出现两个 Tavotto 服务。

更新已有的市场插件时，在 WorkBuddy 插件市场的已安装列表中打开 Tavotto 插件卡片，选择
**「更新」**（[官方插件管理](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Plug-In)），
然后新开对话并调用 `tavotto_health`。这些步骤也适用于已发布的 v0.18.0 插件；下载 ZIP 并
重新生成 MCP 配置不会更新市场插件。
卸载插件时，打开已安装 Tavotto 条目的**「管理」**页面，再执行卸载
（[WorkBuddy 插件管理](https://www.workbuddy.cn/docs/workbuddy/Plugins)）。

以上三条 Beta 只描述安装渠道，依据是本地宿主冒烟（`local_smoke`），不是发行产物的
`host_verified` 验收。Windows 宿主流程、Claude Code 的 IDE 扩展和桌面 Code 标签页、
DSH 桌面版和真实模型流程、WorkBuddy GUI / 画布仍未验收。
各宿主的技能与桌面交接缺口仍记录在 `docs/implementation/multi-host-mcp/acceptance.md`。

## 在其他 AI 编辑器 / 客户端中使用（实验）

Cursor、Claude Code、Claude Desktop（本地聊天）、VS Code（GitHub Copilot Agent）、Trae、
DeepSeek Harness、WorkBuddy、ZCode 用的是**同一份** MCP 服务和技能，不需要装 Codex，也不需要 clone
仓库或构建前端。这条配置生成器路径仍是**实验**，包括上面已有 Beta 插件 / bundle 的宿主。
配置生成已通过测试，除 DSH 外都做了协议级测试；这些路径尚无发行产物的 `host_verified` 验收
（口径见 `docs/support-matrix.json` 的 `mcp_hosts`，证据见
`docs/implementation/multi-host-mcp/acceptance.md`）。

1. 从 [Releases](https://github.com/Tavotto/Tavotto/releases) 下载 `codex-plugin-<版本>.zip`，解压到一个会长期保留的目录
   （文件名带 codex 是历史原因，内容对所有宿主都一样）。
2. 引擎：已经 `pipx install "tavotto[worker]"` 的可以跳过；只装了桌面版或什么都没装，就显式运行一次
   `python3 <完整包>/mcp/server.py --provision`（在 Tavotto 配置目录下建一个独立环境，不改动系统 Python）。
   Windows 上凡是写 `python3` 的地方都换成 `py -3`：那里的 `python3` 常常不存在，或者会打开 Microsoft Store 而不是运行 Python。
3. 生成配置（**只打印，不写任何文件**）：

   ```sh
   python3 <完整包>/integrations/configure.py --host vscode --project-root /绝对路径/你的项目
   ```

   Windows（PowerShell）：

   ```powershell
   py -3 '<完整包>\integrations\configure.py' --host vscode --project-root 'D:\你的项目'
   ```

   `--host` 可选 `cursor` `zcode` `dsh` `workbuddy` `claude-code` `claude-desktop` `trae` `vscode`。stdout 是要合并的配置，
   stderr 写明合并到哪个文件或界面、授权的是哪个目录、引擎是否就绪、如何确认宿主已经加载、技能怎么装。
4. 在对话里调用 `tavotto_health`，确认它回报的 `server.package_dir` 就是刚解压的那份包。

授权范围只有 `--project-root` 指定的目录，不接受整个主目录或磁盘根目录。升级时解压新版到新目录，重新生成配置，并刷新技能：把新的 `tavotto-figure/` 覆盖复制到原来的位置，
或者重新运行 `--emit instructions`、替换之前粘贴的说明；
回退就是把配置改回指向旧目录。各宿主的依据与差异见 `docs/implementation/multi-host-mcp/hosts.md`。
ZCode 也能从自己的插件市场安装同一份 Claude Code 插件，但还没有在登录后的真实 ZCode 会话里验收这条路径。
