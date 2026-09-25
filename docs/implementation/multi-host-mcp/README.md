# 多宿主本地 MCP 接入（Cursor / ZCode / DSH / WorkBuddy / Claude Code / Claude Desktop / Trae / VS Code）

> 这是一项**兼容性**改造，不是按宿主数新建产品：同一份版本化完整包、同一个 MCP
> 启动器与 server、同一套引擎、同一份核心 Skill。宿主之间的差异只在配置 schema、
> Skill 入口、说明与验收。Codex 插件那条路一字未改（回归基线）。

本目录三份文件：

| 文件 | 内容 |
| --- | --- |
| `README.md`（本文） | 实施基线、接口、用户路径、两组改动的边界、限制与回退 |
| `hosts.md` | 每个宿主的官方依据（链接 + 查证日期 + 证据等级）、生成的配置形状、Skill 入口、确认加载的办法 |
| `acceptance.md` | 分层验收矩阵（配置 / 工具流程 / Skill / 画布 / 交接），初值 `not_run`，逐行可追溯 |

## 实施基线（2026-09-24，main@3b9bc66）

已有、直接复用：

- **发行物**：ADR 0043 的完整插件 staging / 确定性 zip。release.yml 把
  `codex-plugin-<版本>.zip` 作为 GitHub Release 资产发出，内容摘要、最低引擎版本、
  `plugin-build.json` 清单都已有唯一出处。它按 `git ls-files codex-plugin` + 显式画布组装，
  所以新加进 `codex-plugin/` 的文件**自动**进包、进摘要、被 `verify` 逐字节核对。
  wheel / sdist 显式排除 `codex-plugin/`——`pip install tavotto` 之后**没有** `tavotto_mcp`，
  这正是其他宿主也要从完整包取 MCP 代码的原因。
- **启动器** `mcp/server.py`：纯标准库解析器（当前解释器 → `TAVOTTO_MCP_PYTHON` → worker →
  自管 venv → CLI 反推 → PATH）、`--health` 一行体检 JSON、`--provision` 显式建自管环境、
  找不到引擎时的降级 server。**不新增第二套 resolver。**
- **授权**：`RootAuthority`，显式 `TAVOTTO_MCP_ROOTS` 是最高权威（roots/list 与确认框都不能扩宽它）。
- **画布**：`widget.py` + `web/src/mcp`，标准 MCP Apps 元数据（`_meta.ui.resourceUri`、
  `text/html;profile=mcp-app`）与 OpenAI 兼容字段并存；ADR 0069 的体积预算、`elided`、
  `tavotto_session_state` 取件、完整 patches 账本。本轮**没有改画布**。
- **跨进程会话**（ADR 0078）：WorkBuddy 5.6.2 实测一轮后回收 CLI 进程，已由会话记录接力。

修改（两组，可独立审查）：

- **PR 1（宿主无关的基础）**：`integrations/configure.py`（进 `STAGE_REQUIRED`）、启动器 / roots
  恢复话术中立化且写真实路径、`tavotto_health` 的 `server` 身份与分层 `checks`、server
  instructions 补「先 health / 授权 / elided / 无 UI 也能走完」、树外启动测试与候选测试。
- **PR 2（全部宿主的薄适配与 Skill）**：逐家核对后的 `HOSTS` 表字段与独立期望测试、
  Skill 的最小中立化（宿主无关的工具发现 / 安装说明 / 提问 / 偏好 / 脚本解释器）、
  `references/other-hosts.md`、本目录的宿主依据与验收矩阵、README 中英入口、支持矩阵的宿主子表、
  由（已中立化的）SKILL.md 生成的等价说明。

主要风险：宿主文档变化快（字段以查证日期为准）；四个宿主（Cursor / ZCode / WorkBuddy /
Trae）的官方站点在本次执行环境里只能拿到搜索摘要，证据等级标为 `search_snippet`；
没有任何真实宿主客户端在本环境里跑过——验收矩阵如实全是 `not_run` 或 `protocol_tested`。

## 接口：`integrations/configure.py`

```text
python3 <完整包>/integrations/configure.py --host <profile> --project-root <绝对路径>
        [--python <启动器解释器>] [--engine-python <引擎解释器>]
        [--diagnose | --emit config|instructions]
```

- `--host`：`cursor` `zcode` `dsh` `workbuddy` `claude-code` `claude-desktop` `trae` `vscode`。
  profile 名只是本工具的选择参数，不是各家 CLI 的原生命令。
- stdout：那一家可合并的配置片段（DSH 是 Cordis YAML patch，其余 JSON）；stderr：合并到哪、
  授权目录、引擎状态与恢复步骤、怎样确认宿主真的加载了、Skill 怎么装。失败非零、stdout 为空。
- `--diagnose`：改为输出一份机器可读 JSON（包 / 启动器探针 / 引擎 / 授权 / Skill），**不含配置**。
- `--emit instructions`：没有经核实的原生 Skill 入口的宿主用的等价说明——就是包里（已中立化的）
  SKILL.md 的正文，相对引用改写成包内绝对路径（`instruction_fallback`，不是第二份手写规则）。
- `--engine-python`：显式的引擎解释器**直接作为启动命令**（与 `--python` 二选一），并验证体检报的就是它。
- 退出码：0 片段已打印（引擎没就绪也是 0，配置本身是对的）；2 参数错；3 启动器起不来 / 包不完整 /
  显式引擎解释器不可用 / 引擎可用但按这份配置起的 server 握手失败。
- 引擎可用时，打印前会按生成的启动描述真起一次 server 做 `initialize`（`--health` 不 import
  `tavotto_mcp`，只有握手才证明整条启动路径通）。
- **不写任何文件**、不联网、不 provision、不改宿主设置。

启动描述只有一份：`command` = 启动器解释器绝对路径，`args` = [包内 `mcp/server.py` 绝对路径]，
`env` = `{TAVOTTO_MCP_ROOTS: <项目>}`（+ 必要时 `TAVOTTO_MCP_PYTHON`，+ Windows 上
Claude Desktop 的 `APPDATA`）。不依赖 shell、`~` 展开、宿主变量语法或 cwd。

三个解释器分开：**启动器**（配置里的 command，只需能跑纯标准库）、**引擎**（启动器自己找；
只在当前 shell 环境里找得到时才钉，钉完在最小环境里再验）、**渲染**（归 Tavotto 设置，不碰）。

## 用户路径（从发行包开始）

1. 从 GitHub Releases 下载 `codex-plugin-<版本>.zip`（名字带 codex 是历史原因，内容对所有宿主一样）。
2. 解压到一个你打算长期保留的目录（配置里写的是绝对路径；挪目录就重新生成）。
3. 引擎：已装 `pipx install "tavotto[worker]"` 的直接下一步；只有桌面版或什么都没有时，显式跑
   `<python> <包>/mcp/server.py --provision`（在 Tavotto 配置目录下建自管环境，不碰系统 Python）。
   `--health` 随时自检。
4. `python3 <包>/integrations/configure.py --host <宿主> --project-root <项目绝对路径>`，
   把 stdout 合并进 stderr 指明的那个文件 / 设置界面。
5. Skill：原生入口的宿主把整个 `skills/tavotto-figure/` 目录复制到它的 Skill 目录；其余用
   `--emit instructions` 放进规则 / 智能体提示词。
6. 按 stderr 的「确认加载」步骤核对，再在对话里调用 `tavotto_health`——它回报的
   `server.package_dir` 应是这份包（同名 tavotto 被多处登记时靠它分辨）。

恢复分三种，不混：**只有桌面版**（`desktop_only`：交接能用，MCP 要一个 Python 环境 → provision
或 pipx）；**引擎未就绪 / 太旧**（按 `tavotto_health` / `--health` 的 code 只修那一项）；**宿主没加载
工具**（配置位置 / 宿主 MCP 列表 / 智能体未启用工具 / 组织策略——见 `hosts.md` 的失败分档）。

## 已知限制、升级影响与回退

- 包目录是配置里的绝对路径：**升级 = 解压新版到新目录 + 重新生成配置**（旧目录可留作回退，
  回退就是把配置指回旧目录）。本轮没有自动更新链——不另起第三条更新通道。
- 远程 SSH / WSL / Dev Containers / 云端 Agent：不在首版承诺内，本机绝对路径不能直接给远程会话用。
- 共享的自管 runtime（`--provision`）没有跨宿主的锁：两个宿主正在用时不要重跑 provision。
  本轮没有新增任何自动重建入口。
- 不做 VSIX、`.mcpb`、ZCode / Claude 插件 manifest、市场上架；本地 MCP 接入不依赖这些包装。
- Claude Desktop 聊天没有本地终端 / 文件写入：偏好脚本与出图脚本落盘不可用（见 Skill 的说明），
  已有本地图可以走 MCP 工具。
