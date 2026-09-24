# 在 Codex 以外的宿主里使用 Tavotto

Tavotto 在所有宿主里是**同一份完整包、同一个 MCP 服务、同一份技能**。差别只在：
配置写在哪、Skill 装在哪、装完之后怎样让宿主重新加载。

本技能被复制到别的目录时（例如 `.claude/skills/`），**不要**按相对路径去找包里的其他
文件。完整包的位置看 `tavotto_health` 回报的 `server.package_dir`；工具还不存在时，
问用户把 `codex-plugin-<版本>.zip` 解压到了哪里。

## 工具缺失时（本会话里没有 `tavotto_health`）

按用户**当前所在的宿主**只给那一家的步骤，给完就停，不在旧会话里假装工具可用：

1. 下载 GitHub Releases 里的 `codex-plugin-<版本>.zip`，解压到一个会长期保留的目录
   （名字带 codex 是历史原因，内容对所有宿主都一样）。
2. 在终端运行（路径换成真实的绝对路径）：

   ```text
   python3 <完整包>/integrations/configure.py --host <宿主> --project-root <项目绝对路径>
   ```

   `<宿主>` 是 `cursor` / `zcode` / `dsh` / `workbuddy` / `claude-code` / `claude-desktop` /
   `trae` / `vscode` 之一。它**只打印**配置（stdout）以及说明（stderr）：合并到哪个文件或界面、
   授权的是哪个目录、引擎是否就绪、怎样确认宿主真的加载了、Skill 怎么装。它不写任何文件。
3. 按说明合并配置，再按下表让宿主重新加载。

认不出宿主，就只给第 1–2 步，并请用户对照他的客户端文档确认 MCP 配置的位置。**不要**让
非 Codex 用户执行 `codex plugin add`。

| 宿主 | 让已开的会话拿到工具 | Skill 入口 |
| --- | --- | --- |
| Cursor | 在 MCP 设置里确认已连接后，新开 Agent 对话 | 复制整个 `tavotto-figure/` 到 `.cursor/skills/` |
| Claude Code（CLI） | 重开会话，用 `/mcp` 确认 connected（项目 `.mcp.json` 第一次要批准） | 复制到 `.claude/skills/` |
| Claude Desktop（聊天） | **完全退出**再打开 | 没有原生入口：`--emit instructions` 放进项目说明 |
| VS Code（Copilot Agent） | MCP: List Servers → 启动 tavotto；在 Configure Tools 里勾选 | 复制到 `.github/skills/` |
| Trae | MCP 列表确认已连接，**并把 tavotto 加进所用智能体** | `--emit instructions` 放进规则或智能体提示词 |
| DSH | 新开会话，等 `mcp__tavotto__*` 工具出现 | 复制到 `.dsh/skills/` 或 `.agents/skills/` |
| WorkBuddy / ZCode | 在 MCP 设置里确认已连接，重开对话 | `--emit instructions` |

## 引擎不可用（只有 `tavotto_health`，或它回 `ok: false`）

处理方法与 Codex 相同，见 `first-run-and-recovery.md`：按 `code` 只修那一项。**恢复命令直接用
health 结果里 `recovery` 给的原文**，那里是这台机器上的真实路径。修完按上表重新加载。
`desktop_only` 的意思是装了桌面版：交接可用，MCP 工具还需要一个 Python 环境。

## 能力差异（按实际情况说，不夸大）

- **画布**：只有支持 MCP Apps 的宿主才会显示内嵌画布（例如 VS Code 需要开启
  `chat.mcp.apps.enabled`，是否开启由用户或组织决定）。没有画布时，同一组工具照样能走完
  打开 → 修改 → 预检 → 导出。`tavotto_health` 里的 `checks.host_ui_rendered` 永远是
  `unknown_to_server`：服务器无法知道画布有没有显示出来，要问用户或看界面。
- **授权**：只有配置里的项目目录。用户要处理别的目录，就重新生成配置；不要让用户把 HOME
  或磁盘根加进来。
- **没有本机终端或文件写入能力的宿主**（Claude Desktop 聊天）：不能运行 `scripts/*.py`，也
  不能把新脚本保存到本机。这种情况下直接问偏好；已有的本地图照常用 MCP 工具打开和修改；
  新的出图脚本交给用户保存并运行。
- **复制出去的技能**里，`scripts/update_check.py` 找不到包的版本信息，更新检查会显示
  「未知」，这不是故障。升级的方法是解压新版完整包，然后重新生成配置。
- 多个宿主可以同时配置 Tavotto，各自起自己的 MCP 进程。会话会记录在 Tavotto 的数据目录里，
  另一个进程拿到同一个 `session_id` 时，**只有在它自己的授权也覆盖那个项目时**才能接着用；
  授权的是别的目录就拿不到（`workspace_root_changed`）。所以隔离靠的是授权目录，不是宿主本身：
  两个宿主授权同一个项目时，它们可以互相接续会话。
