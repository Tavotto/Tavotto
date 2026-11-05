# 非 Codex 宿主

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「非 Codex 宿主（2026-09-24，全文 `docs/implementation/multi-host-mcp/README.md`）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例（`codex-plugin/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_mcp_configure.py`、`tests/test_mcp_host_profiles.py`、`tests/test_plugin_candidate.py`、`tests/test_claude_plugin.py`、`tests/test_dsh_bundle.py`
> `codex-plugin/AGENTS.md`「必守要点」压缩前原文（2026-10-23，与正文同等有效）：同一份包、同一个启动器、同一份 Skill；不写任何文件；授权只来自 `--project-root`；文档证明不了的字段不加；Claude 插件的 server 名与 Codex 配置同名、超时按毫秒换算且 `timeout` / `timeoutMs` 同值（ADR 0103 / 0109）；插件目录里不许有别家自动读的 `.mcp.json` / `mcp/*.json`（ADR 0109）；DSH bundle 的包目录只由胶水插件提供（ADR 0104）
> `codex-plugin/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：同一份包 / 启动器 / Skill；不写任何文件；授权只来自 `--project-root`；server 名与超时与 Codex 配置同值；不放别家自动读的 MCP 配置

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 80
改到: `integrations/configure.py`、`HOSTS` 表、`RELOAD_HINT` / `RESTART_HOST`、`.claude-plugin/plugin.json` 与仓库根 `.claude-plugin/marketplace.json`、`package.json` 与 `dsh/`（DSH bundle）
主题: 非 Codex 宿主 → `non-codex-hosts.md`
必守要点: 同一份包 / 启动器 / Skill；不写任何文件；授权只来自 `--project-root`；不放别家自动读的 MCP 配置
-->

- **同一份完整包、同一个启动器、同一份 Skill**：`integrations/configure.py`（纯标准库，进
  `pluginmanifest.STAGE_REQUIRED`——只卡新 staging；**不进** `REQUIRED`，否则体检会把没有它的旧版已装插件报成损坏）从自己的位置找 `mcp/server.py`，按 `--host` 打印那一家的配置片段，
  **不写任何文件**。宿主差异只在它的 `HOSTS` 表（顶层 key / 额外字段 / 超时单位 / 落点 / Skill 入口）；
  字段依据与查证日期在 `docs/implementation/multi-host-mcp/hosts.md`，文档证明不了的字段不加。
- **会写文件的是另一条命令：`tavotto hosts`**（2026-10-27，「接入 AI 编辑器」，`engine/hostconnect.py`）。`configure.py`
  仍然只打印；桌面设置里的「接入」按钮 spawn 的是 `tavotto hosts connect <宿主> --json`。它写**用户级**配置
  （Cursor `~/.cursor/mcp.json`、VS Code 用户目录的 `mcp.json`（`servers`）、TRAE / TRAE CN 用户目录的 `mcp.json`、
  ZCode `~/.zcode` 下 `cli/` 与 `workspace/default/.zcode/` 两处的 `mcp.servers`、WorkBuddy `~/.workbuddy/mcp.json`、
  Claude 桌面版 `claude_desktop_config.json`；Claude Code 用它自己的 `claude mcp add --scope user`；Codex 委托
  `codexinstall`），条目是这一份 Tavotto 的 `tavotto-cli mcp`（`mcp_launch()`）。**点一下就接、不选文件夹**（2026-11-04，ADR 0009
  §1b）：会自己报项目的宿主（`HOSTS_WITH_OWN_WORKSPACE` = Claude Code / VS Code / WorkBuddy）`env` 为空；其余只写
  `TAVOTTO_MCP_SCOPE=local`（服务端兜底 = 本机任意位置，只拦凭据类文件）；命令行 `--project-root` 仍写
  `TAVOTTO_MCP_ROOTS`（根 / HOME / HOME 的上级拒绝）。旧按钮写的文件夹在 `hosts list` 时一次性迁移。三条纪律：写之前
  备份、只动 tavotto 一条、读不懂（JSONC 等）就不写并报 `config_unreadable`；断开只删 tavotto；WorkBuddy 额外在
  `mcp-approvals.json` 只信任 tavotto 这一条（用户点「接入」即同意，方案决策）。看护 `tests/test_hosts_connect.py`。
- **技能随接入一起复制（2026-11-06，`engine/hostskills.py`，纯标准库）**：`hosts connect` 在 MCP 写成之后把桌面版自带的
  `tavotto-figure`（冻结 = `_MEIPASS/skills/tavotto-figure`，`packaging/tavotto.spec` 整棵收进、缺了拒绝打包；源码树 =
  `codex-plugin/skills/tavotto-figure`；pip 安装没有 → `skill_source_missing`）复制进宿主**用户级**技能目录，作为 `steps`
  里的一步 `skill`；`list` 每行带 `skill`（current / stale / modified / missing / builtin）。目录表：Claude Code
  `~/.claude/skills`、Cursor `~/.cursor/skills`、VS Code `~/.copilot/skills`、ZCode `~/.zcode/skills`、Antigravity
  `~/.gemini/config/skills`、Trae `~/.trae-cn/skills` 与 `~/.trae/skills`（只写已存在的编辑器目录）、WorkBuddy
  `~/.workbuddy/skills`（官方未写明，待验）；查证与待验项在 `hosts.md`。写法：先写同级临时目录再换名；目录里放校验记录
  `.tavotto-skill.json`（schema / version / sha256 / written_by），sha256 覆盖整目录（相对路径 + 字节，排序、`/` 分隔，不含记录与
  `__pycache__`）。**用户改过的不覆盖**：目标已存在而无记录（不是我们写的）或内容与记录不符 → `modified`，原样保留、
  `disconnect` 也不删；同内容 → `skipped`；旧版且没被改 → 替换。技能写失败**不回滚 MCP**，整次结果如实 `ok=false`
  （`skill_write_failed` / `skill_source_missing`），并带 `mcp_connected=true`。Codex 不走这里（技能随插件）；Claude Code
  已装 Tavotto 插件（`register` 一步 skipped）时不再复制；Claude 桌面版没有技能目录（`builtin`）。没有技能的宿主靠 MCP
  工具 **`tavotto_guide`**（可选 `section` = `references/` 里真实存在的文件名，白名单、路径穿越回 `guide_section_unknown`，
  读不到回 `guide_unavailable`）读全文；`initialize` 的 instructions 让它开工前先调。工具名同步在 `server.HANDLERS`、启动器 `NORMAL_TOOLS`；**不进** `usage.TOOLS` 与遥测闭集（ADR 0170：进了要升 CONSENT_VERSION，等维护者），走 `other` 桶。
  **技能脚本在桌面版里怎么跑**：冻结版的 `sys.executable` 是 `tavotto-cli`，不是解释器，所以 `tavotto_health` 的
  `server.python` 在冻结时为 null，并给 `skill_script_command: [<tavotto-cli>, "skill-script"]`；`tavotto skill-script
  <prefs|handoff|update_check> [args…]`（`hostskills.run_script_cli`，闭集名字、不收路径）用 runpy 在进程内跑技能脚本、
  透传 argv 与退出码。`handoff.py` 冻结时没有默认解释器：要跑用户脚本必须显式 `--python`，否则回 `python_required`。
  技能目录里的符号链接（文件、目录都算，不跟随）计入 sha256，加了链接即 `modified`。
  已知未处理：Cursor / VS Code 也读 `~/.claude/skills`，
  与 Claude Code 同时接入可能看到同名技能两份（待验）。看护 `tests/test_host_skills.py`、`tests/test_hosts_connect.py`。
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
  `workspace_root_too_broad`（支持确认框的宿主在 cwd 兜底被拒时仍报「请确认」；但调用方**明确给了**主目录的
  绝对路径时，确认状态记成 `too_broad`、报「太宽」——让它「改传绝对路径重试」是错的）。看护 `tests/test_mcp_roots.py` 末节。
- 看护：`tests/test_mcp_configure.py`（解包到树外按生成配置真起 server）、`tests/test_mcp_host_profiles.py`
  （九个 profile 的独立期望）、`tests/test_plugin_candidate.py` 末条（真实候选）。
