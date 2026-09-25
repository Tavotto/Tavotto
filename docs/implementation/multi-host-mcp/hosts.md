# 各宿主的官方依据与生成的配置形状

查证日期 **2026-09-24**。证据等级（`HOSTS[...]["evidence"]` 与本表一致，测试对拍）：

- `official_source`：读到了官方文档全文（直接抓取官方站点页面，或官方文档仓库的源文件）；
- `search_snippet`：本次执行环境的出网代理拦了该站点（HTTP 403），只拿到同一官方域名下的
  搜索摘要——**不是逐字引用**，用之前要在能访问的网络上重核；
- 两者都只证明「接入机制存在」，**不证明 Tavotto 已在该宿主实测通过**（那是 `acceptance.md`）。

文档证明不了的字段一个都不加：没有经核实的超时字段就不写超时，没有经核实的 Skill 目录就走
`instruction_fallback`。所有 profile 共用同一份启动描述（绝对路径的 `command` + `args`，
`env.TAVOTTO_MCP_ROOTS`），不用任何宿主变量语法（`${workspaceFolder}`、`${CLAUDE_PLUGIN_ROOT}`、
`!!js` …）——路径已经是绝对的，变量只会引入「这个宿主认不认」的新问题。

| profile | 证据 | 配置落点（stdout 合并到哪） | 顶层 / 条目字段 | 超时 | Skill |
| --- | --- | --- | --- | --- | --- |
| `cursor` | search_snippet | `<项目>/.cursor/mcp.json`（或 `~/.cursor/mcp.json`） | `mcpServers` / `command` `args` `env` | 未核实，不写 | native：`.cursor/skills/` 或 `.agents/skills/` |
| `zcode` | search_snippet | `<项目>/.agents/mcp.json`，或 MCP 设置界面 | `mcpServers` / `command` `args` `env` | 未核实，不写 | instruction_fallback |
| `dsh` | official_source | Cordis patch：`dsh web --patch <文件>`，或 `$DSH_HOME/profiles/<名>/cordis.patch.yml` | `insert` → `@deepseek-ai/dsh-mcp-client` → `serverName` `transport: stdio` `command` `args` `env` | `toolCallTimeoutMs`（毫秒） | native：`.dsh/skills/` 或 `.agents/skills/` |
| `workbuddy` | search_snippet | WorkBuddy：插件 → MCP Server → 配置 MCP | `mcpServers` / `command` `args` `env` | 未核实，不写 | instruction_fallback |
| `claude-code` | official_source | `<项目>/.mcp.json`（project 作用域） | `mcpServers` / `type: stdio` `command` `args` `env` | `timeout`（毫秒，≥1000） | native：`.claude/skills/` 或 `~/.claude/skills/` |
| `claude-desktop` | official_source | `claude_desktop_config.json`（macOS `~/Library/Application Support/Claude/`，Windows `%APPDATA%\Claude\`） | `mcpServers` / `command` `args` `env`（Windows 另加展开后的 `APPDATA`） | 无该字段 | instruction_fallback |
| `trae` | search_snippet | MCP 窗口 → 添加 → 手动添加；或 `<项目>/.trae/mcp.json` | `mcpServers` / `command` `args` `env` | 未核实，不写 | instruction_fallback |
| `vscode` | official_source | `<项目>/.vscode/mcp.json` | **`servers`** / `type: stdio` `command` `args` `env` | 无该字段 | native：`.github/skills/` 或 `.agents/skills/` |

工具超时的唯一出处是包里 Codex `.mcp.json` 的 `tool_timeout_sec`（1800 秒）；有经核实字段的
宿主按单位换算（DSH / Claude Code 都是毫秒 → 1 800 000）。Codex 的字段名（`tool_timeout_sec`、
`startup_timeout_sec`、`env_vars`、`cwd`）不抄给任何别的宿主。

## 逐家说明

### Cursor（search_snippet）

- 来源：<https://cursor.com/docs/mcp>、<https://cursor.com/docs/skills>（摘要）。VS Code 官方文档的
  「MCP 配置发现」表也独立列出 Cursor 的 `.cursor/mcp.json` / `~/.cursor/mcp.json`。
- stdio 字段 `type` `command` `args` `env` `envFile`；插值 `${env:NAME}` `${workspaceFolder}` 等（我们不用）。
- Skill：项目 `.agents/skills/`、`.cursor/skills/`，兼容读取 `.claude/skills/`、`.codex/skills/`。
- MCP Apps：未核实，画布一栏 `not_run`。

### ZCode（search_snippet）

- 来源：<https://zcode.z.ai/en/docs/plugin>、`/mcp-services`、`/skill`（摘要）。
- 项目 MCP 配置 `.zcode/config.json`（形状未核实，本工具不生成它）；`.agents/mcp.json` 用
  `mcpServers`，**只在同作用域的 `.zcode` 配置没有定义任何 MCP 服务时才读**（不合并）。
- 插件 manifest `.zcode-plugin/plugin.json`（也认 `.claude-plugin/plugin.json`），插件根变量
  `ZCODE_PLUGIN_ROOT`；MCP 超时字段未核实。**本轮不做 ZCode 插件 manifest**：包里的 `.mcp.json` 是
  Codex 形状（相对 `python3`、Codex 专有字段），不能直接给 ZCode 用；另写一份就是第二份清单。
- 打开项目会连接项目配置里的全部 MCP 服务——不可信仓库里先看 `.zcode/config.json`。

### DeepSeek Harness（official_source）

- 来源：deepseek-ai/deepseek-harness `packages/mcp/mcp-client/README.md`、`docs/config-catalog.md`、
  `docs/user/guide/mcp-memory.md`、`packages/skill/skill-filesystem/README.md`（2026-09-23 的提交）。
- 官方 client 包 `@deepseek-ai/dsh-mcp-client`，**直接 stdio**，不绕经 Codex。`args` 不过 shell；
  `env` 叠在被清洗过的环境上（凭据样式变量与 `DSH_*` 会被剥掉——`TAVOTTO_MCP_ROOTS` 不受影响）。
- `toolCallTimeoutMs` 默认 60 000 ms，导出大图不够，生成时换算成 1 800 000。
- `serverName` 须匹配 `[A-Za-z0-9_-]{1,32}`；工具暴露为 `mcp__tavotto__<原名>`——Skill 按语义找工具，
  不硬编码暴露名。
- 生成的 YAML 不含 `!!js` 标签（**不生成要宿主执行的代码**；本工具也从不读取 / 执行用户的 YAML）。
- Skill 根：`<项目>/.dsh/skills`、`<项目>/.agents/skills`、`$DSH_HOME/skills`、`~/.agents/skills`；
  frontmatter 要 kebab-case `name` + `description`（`tavotto-figure` 满足）。

### WorkBuddy（search_snippet，有冲突）

- 来源：<https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide>（摘要）。
- 摘要只给出 CodeBuddy 的路径（`~/.codebuddy/mcp.json`、项目 `.mcp.json`），**没有找到 `.workbuddy/`
  路径**——所以本工具只指向 WorkBuddy 自己的界面（插件 → MCP Server → 配置 MCP），不写
  `.codebuddy` 或猜一个 `.workbuddy` 路径。形状 `mcpServers` / `type`（可省，按 `command` 推断 stdio）。
- 已知宿主行为：WorkBuddy 5.6.2 一轮后回收 CLI 进程（ADR 0078 已处理会话接力）。
- 连接器商店审核与完整 Buddy 应用不在范围内。

### Claude Code（official_source）

- 来源：<https://code.claude.com/docs/en/mcp>、`/skills`、`/plugins`、`/desktop`。
- 作用域：local（默认，`~/.claude.json` 按项目）/ project（`<项目>/.mcp.json`）/ user（`~/.claude.json`）。
  本工具生成 **project** 片段；交互会话第一次用会请用户批准。
- 等价 CLI（**只打印、不执行**）用 `claude mcp add-json`，参数就是 `.mcp.json` 里那一条的 JSON：

  ```sh
  claude mcp add-json --scope project tavotto '{"type":"stdio","command":"<python>","args":["<包>/mcp/server.py"],"env":{"TAVOTTO_MCP_ROOTS":"<项目>"},"timeout":1800000}'
  ```

  不用 `claude mcp add --transport stdio … -- <命令>`：它没有按服务器设超时的选项，会丢掉 `timeout`。

  二选一：用了 `.mcp.json` 就不要再用 `claude mcp add` 在 local / user 作用域登记同名 `tavotto`，
  否则会遮蔽项目那份。核对用 `/mcp` 或 `claude mcp get tavotto`，再调 `tavotto_health` 看
  `server.package_dir`。
- `.mcp.json` 里无 `type` 视为 stdio，写 `type: "stdio"` 是完整形式；`timeout` 是毫秒、下限 1000。
- Skill：`.claude/skills/<名>/SKILL.md`（项目）或 `~/.claude/skills/`（个人）——复制**整个**
  `tavotto-figure/` 目录。插件形态（`.claude-plugin/plugin.json` + `${CLAUDE_PLUGIN_ROOT}`）本轮不做。
- 桌面 **Code 标签页**读 `claude_desktop_config.json` + `~/.claude.json` + `.mcp.json`，同名时桌面
  配置优先、stdio 的 user 作用域优先于 `.mcp.json`（与 CLI 的优先级不同）；独立 CLI **不读**
  `claude_desktop_config.json`。所以 CLI 通过 ≠ Code 标签页通过 ≠ 桌面聊天通过，各记各的。

### Claude Desktop 本地聊天（official_source）

- 来源：<https://modelcontextprotocol.io/docs/develop/connect-local-servers>（2026-07-28 版源文件）、
  <https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop>。
- `claude_desktop_config.json` 的 `mcpServers`，`command` 与路径须为绝对路径；Settings → Developer →
  Edit Config 打开的就是它。改完**完全退出**再开；「+」→ Connectors 里看工具；日志
  macOS `~/Library/Logs/Claude/mcp*.log`、Windows `%APPDATA%\Claude\logs`。
- 官方排障：Windows 上 server 环境里可能没有 `APPDATA`，要在 `env` 里写展开后的值——Tavotto 的
  配置目录在 Windows 上正是 `%APPDATA%\Tavotto`，所以 Windows 上生成时带上 `APPDATA`。
- 没有项目 cwd / Roots：授权完全来自 `TAVOTTO_MCP_ROOTS`（用户选的目录），不用桌面启动目录或 HOME 兜底。
- Linux 没有官方 Claude Desktop；`.mcpb` 扩展是可选包装，本轮不做。
- 宿主内置的 Node / 云端代码执行环境不是本机 Python，路径也不是本机文件系统——不能传给本地 Tavotto。

### Trae / TraeCode（search_snippet）

- 来源：<https://docs.trae.cn/ide_model-context-protocol>、<https://docs.trae.cn/ide_tutorial-mcp-amap>、
  <https://docs.trae.ai/ide/model-context-protocol>（摘要；CN 与国际版分别查过，内容一致的部分才用）。
- 手动添加：MCP 窗口 → 添加 → 手动添加，粘贴 `mcpServers` JSON；项目级 `.trae/mcp.json`。
- **登记 ≠ 智能体能调用**：要把 tavotto 加进所用的自定义智能体（MCP 一栏），或用 Builder with MCP。
- Skill：官方有 Skills 功能，但 Skill 目录路径未核实 → `instruction_fallback`（`--emit instructions`
  的输出放进 `.trae/rules/` 或自定义智能体提示词）。**不照抄** `.cursor/skills` / `.claude/skills`。
- CN / 国际版、IDE / SOLO 不互相推定。

### VS Code（GitHub Copilot Agent，official_source）

- 来源：microsoft/vscode-docs 源文件（`docs/agents/reference/mcp-configuration.md` DateApproved
  2026-09-16、`docs/agent-customization/{mcp-servers,agent-skills,agent-plugins}.md`）。
- `.vscode/mcp.json` 顶层是 **`servers`**；stdio 条目 `type`（必需）`command`（必需）`args` `env`
  `envFile` `cwd`（默认工作区目录）。**工作区根的 `.mcp.json` 是另一种可移植格式（`mcpServers`）**，
  也是 Claude Code 的项目文件——本工具不往那里写，两个宿主各用各的入口，互不覆盖。
- Agent Host 直接读可移植格式，VS Code 会把 `.vscode/mcp.json` 的服务转发给它（用了 `${input:…}` 的除外；
  我们不用）。
- 核对：MCP: List Servers → Show Output；Chat 的 Configure Tools 勾选工具；MCP: Reset Cached Tools。
  受限模式（未信任工作区）不会启动工作区服务。
- Skill：项目 `.github/skills/`、`.claude/skills/`、`.agents/skills/`；个人 `~/.copilot/skills/` 等。
- MCP Apps：支持，受设置 `chat.mcp.apps.enabled` 控制；组织策略禁用时如实告知，**测试与生成器都不替用户改设置**。
- 远程窗口（SSH / WSL / Dev Containers）不在首版承诺内。

## 失败分档（宿主里「没工具」不是一件事）

| 现象 | 分档 | 怎么分辨 | 下一步 |
| --- | --- | --- | --- |
| 宿主 MCP 列表里 tavotto 红 / 起不来 | 服务器没启动 | 宿主日志；`<python> <包>/mcp/server.py --health` 在终端跑不跑得起来 | 重新生成配置（`--python` 指一个真能跑的解释器）；包目录挪过就重新生成 |
| 列表里是绿的，但对话里没有工具 | 工具没发现 / 当前智能体未启用 | VS Code Configure Tools、Trae 智能体 MCP 栏、DSH 等 `mcp__tavotto__*` 出现 | 在所用智能体里启用；重开对话 |
| 只有 `tavotto_health` 一个工具 | 引擎不可用（降级 server） | health 的 `code`：`desktop_only` / `tavotto_missing` / `engine_too_old` / `engine_unavailable` | 按 code 只修那一项（provision / pipx / 升级引擎） |
| 工具在，但打开图报 `path_out_of_scope` / `no_workspace_root` | 项目未授权 | health 的 `roots` / `root_authority.source` | 用正确的 `--project-root` 重新生成；不要放宽到 HOME |
| 工具正常、没有画布 | UI 没显示 | `checks.canvas_resource.ok` 为真但宿主不渲染 MCP Apps | 这是宿主能力 / 设置；工具流程照常走完，不要把外部窗口叫「内嵌画布」 |
| 宿主提示被管理员 / 策略禁止 | 组织策略 | 宿主的策略提示 | 找管理员；**不要**把「全部工具自动批准 / 关闭安全策略」当通用修复 |
