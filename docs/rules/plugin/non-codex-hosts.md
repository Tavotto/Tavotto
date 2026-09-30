# 非 Codex 宿主

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「非 Codex 宿主（2026-09-24，全文 `docs/implementation/multi-host-mcp/README.md`）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **同一份完整包、同一个启动器、同一份 Skill**：`integrations/configure.py`（纯标准库，进
  `pluginmanifest.STAGE_REQUIRED`——只卡新 staging；**不进** `REQUIRED`，否则体检会把没有它的旧版已装插件报成损坏）从自己的位置找 `mcp/server.py`，按 `--host` 打印那一家的配置片段，
  **不写任何文件**。宿主差异只在它的 `HOSTS` 表（顶层 key / 额外字段 / 超时单位 / 落点 / Skill 入口）；
  字段依据与查证日期在 `docs/implementation/multi-host-mcp/hosts.md`，文档证明不了的字段不加。
- 授权只来自 `--project-root`（写进 `TAVOTTO_MCP_ROOTS`）；根 / HOME / 包目录拒绝。启动器探针与
  `launcher_starts()` 同一把尺（跑 `--health` 要体检 JSON）；引擎只在当前 shell 环境里找得到时才钉
  `TAVOTTO_MCP_PYTHON`，钉完在最小环境里再验。超时唯一出处是 Codex 配置 `codex.mcp.json` 的 `tool_timeout_sec`（启动超时是 `startup_timeout_sec`），按单位换算。
- 启动器 / roots 的恢复话术不再只说「新开 Codex 会话」（`RELOAD_HINT` / `RESTART_HOST`），恢复命令写
  真实绝对路径。`tavotto_health` 多了 `server`（实际包目录 / 版本）与分层 `checks`——
  `host_ui_rendered` 永远是 `unknown_to_server`，不从资源在推断画布已显示。
- **Claude Code 插件形态（ADR 0103）**：同一份插件目录多一份 `.claude-plugin/plugin.json`，仓库根
  `.claude-plugin/marketplace.json` 走 `git-subdir → plugin-stable`，发行链不改。plugin.json 的
  `mcpServers` 与 Codex 配置**同名**（ADR 0109 前 Codex 配置就是插件根 `.mcp.json`，Claude Code 先读它再按名替换，名字不同 = Codex 那条也被
  起一遍并失败），条目是同一个启动器的 `${CLAUDE_PLUGIN_ROOT}/` 写法、`timeout` 由 `tool_timeout_sec`
  换算成毫秒、不带 Codex 字段；不写 `skills` 键（会替换默认位置）；授权走 `roots/list`。进
  `STAGE_REQUIRED`、不进 `REQUIRED`。安装命令唯一出处 `brand.CLAUDE_*`。看护 `tests/test_claude_plugin.py`。
- **DSH bundle（ADR 0104）**：同一份目录兼作 npm 包 `tavotto-dsh`（`package.json` + `dsh/`），`dsh plugin add`
  经 pnpm 的 git 子目录规格从发行分支装。补丁没有包目录变量 → 胶水插件按 `import.meta.url` 提供服务
  `tavotto`，mcp-client / 技能提供者两行 `inject` 它；**补丁里的 `!!js` 只读 `ctx.tavotto` 与
  `process.cwd()`**。启动器 POSIX 经 `/bin/sh mcp/launch`（不靠执行位）、Windows 直接给 `mcp/launch.cmd` 由 cross-spawn 拼
  `cmd /d /s /c`（给 `cmd.exe` 加参数的形态路径带空格就起不来）；serverName 与 Codex 配置同名；超时按毫秒换算；
  技能提供者 `includeDefaultRoots: false` 且不与 base 的 `filesystem` 同名。`package.json` 的 `files` 覆盖
  胶水读的全部路径；三份文件进 `STAGE_REQUIRED`。安装规格唯一出处 `brand.DSH_*`。看护 `tests/test_dsh_bundle.py`。
- 两种安装命令（`claude plugin …` 与 `dsh plugin …` 的规格 / 包名）凡在进版本库的 Markdown 里出现（ADR 与已发行的发行说明除外），
  都要与 `brand.CLAUDE_*` / `brand.DSH_*` 拼出的整段相等：看护 `tests/test_install_commands_from_brand.py`，不按文件点名。
- **更多宿主装同一份 Claude 插件（ADR 0109）**：Claude Code / ZCode / WorkBuddy / MiniMax Code 都会自动读插件根
  `.mcp.json`，而 WorkBuddy 让它**覆盖**清单同名条目（Codex 形状的相对启动器在那边 ENOENT）——所以 Codex 的配置
  改名 `codex.mcp.json`、由 Codex 清单 `mcpServers` 指向，**插件目录里不许有 `.mcp.json` 与 `mcp/*.json`**
  （`pluginmanifest.STAGE_FORBIDDEN`，stage 拒绝；已装的旧版仍是 `.mcp.json`，体检 / 钉 command 按清单指向找
  `mcp_config_rel`）。Claude 条目同时写 `timeout` 与 `timeoutMs`（ZCode 只认后者，默认 30 s）、同值；MiniMax Code
  的插件兼容模式会因 `timeoutMs` 整条丢弃，所以它只走项目 `.mcp.json` 生成器。WorkBuddy 的安装入口（市场
  `brand.WORKBUDDY_MARKETPLACE` + `brand.CLAUDE_PLUGIN_REF`）唯一出处在 brand。Trae 生成器给一键链接
  （`install_links`，国际版 / 国内版两个前缀）、超时写 env、command 含空格拒绝。
- **授权根不收主目录（ADR 0109）**：`RootAuthority` 的五个入口（显式变量、`roots/list`、用户确认、宿主工作区
  变量、cwd 兜底）与生成器 `validate_project_root` 同一判据——主目录本身及其上级一律拒，子目录照收；
  主目录集合 `roots.home_dirs()` 与生成器 `_home_dirs()` 对拍。被拒而没有别的根时是独立一档
  `workspace_root_too_broad`（支持确认框的宿主在 cwd 兜底被拒时仍报「请确认」）。看护 `tests/test_mcp_roots.py` 末节。
- 看护：`tests/test_mcp_configure.py`（解包到树外按生成配置真起 server）、`tests/test_mcp_host_profiles.py`
  （九个 profile 的独立期望）、`tests/test_plugin_candidate.py` 末条（真实候选）。
