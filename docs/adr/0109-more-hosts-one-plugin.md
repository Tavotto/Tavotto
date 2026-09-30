# ADR 0109：ZCode / WorkBuddy / MiniMax Code / Trae 与同一份插件目录

日期：2026-09-28 · 状态：**Accepted**（用户 2026-09-28 要求「顺带再适配 Zcode、workbuddy、minimax code、Trae」，
并对下面三处取舍当场拍板）
相关：[0103 Claude Code 插件](0103-claude-code-plugin.md)、[0104 DSH bundle](0104-dsh-bundle.md)、
[0009 工作区根权威](0009-codex-workspace-root-authority.md)、`docs/implementation/multi-host-mcp/`。

## 问题

ADR 0103 / 0104 给了 Claude Code 与 DSH 一键安装的渠道。另外四个宿主在 `integrations/configure.py` 里只有
「生成配置、手工合并」这一档，而且 ZCode / WorkBuddy / Trae 三家的字段只拿到过搜索摘要。2026-09-28 逐家读到
官方全文或客户端源码（ZCode 3.14.3 的 CLI、WorkBuddy 5.6.2 自带的 CodeBuddy CLI 2.147.0 与 GUI 主进程、
MiniMax Code 开源仓库 `c593d3d5`、Trae CN / 国际版文档全文），结论各不相同：

| 宿主 | 插件机制 | 能不能装我们的目录 | 卡在哪 |
| --- | --- | --- | --- |
| ZCode | 认 `.claude-plugin/plugin.json`、读 `.claude-plugin/marketplace.json`、支持 `git-subdir` | 能；插件根 `.mcp.json` 被清单同名条目整条替换（与 Claude Code 同向） | 超时只认 `timeoutMs`，`timeout` 被忽略、默认 30 s |
| WorkBuddy | 同上一族（CodeBuddy），清单 `.codebuddy-plugin` → `.workbuddy-plugin` → `.claude-plugin` | 装得上，**起不来**：合并方向相反，插件根 `.mcp.json` 与 `mcp/*.json` 覆盖清单，Codex 形状的 `./mcp/launch.cmd` 在会话目录里 ENOENT；加 `.codebuddy-plugin` 清单也盖不掉（隔离实测） | 插件根的 `.mcp.json` |
| MiniMax Code | 认 `.claude-plugin/plugin.json` 与 `${CLAUDE_PLUGIN_ROOT}`；**不读任何 marketplace**，CLI 只能手放目录 | 兼容模式遇未知字段整条丢弃；插件进程的 cwd 与 `roots/list` 都是**用户主目录** | 没有安装入口；授权会变成整个 home |
| Trae | IDE 没有可自发布的插件（国内版「插件市场」是官方货架）；企业版 CLI 未验证 | 否 | 最高一档是 `trae://` 一键添加 MCP + `.trae/skills/` |

## 裁决

1. **Codex 的 MCP 配置改名 `codex.mcp.json`**（用户裁决一：改名、本 PR 做全）。Codex 按清单 `mcpServers` 的
   路径读配置（0.157.1 隔离实测：指向改名后的文件 → `codex mcp list` 有 tavotto；指向不存在的文件 → 为空），
   名字随我们定；Claude Code / ZCode / WorkBuddy / MiniMax Code 都会自动读插件根 `.mcp.json`，改名后它们只看到
   Claude 清单那一条。**不另写 `.codebuddy-plugin/` / `.zcode-plugin/` / `.minimax-plugin/` 清单**——那是第二、
   三份 MCP 清单，而且对 WorkBuddy 无效。
   - 插件目录里**不许有** `.mcp.json` 与 `mcp/*.json`（WorkBuddy 连后者也扫）：`pluginmanifest.STAGE_FORBIDDEN`，
     `plugin_stage` 组装时拒绝；源码树由 `tests/test_claude_plugin.py` 看护。
   - 已装的旧版副本仍是 `.mcp.json`：`pluginmanifest.mcp_config_rel()` 按 Codex 清单的指向决定这份插件的配置
     叫什么，`REQUIRED` 不再写死名字（新名进 `STAGE_REQUIRED`）；体检、钉 command（`codexinstall`）、配置生成器
     的超时、DSH 胶水都按清单指向找。旧包照样体检得过、钉得上（`tests/test_plugin_stage.py`）。
2. **Claude 条目同时写 `timeout` 与 `timeoutMs`**（同值，由 `tool_timeout_sec` 换算）：Claude Code 忽略后者
   （`plugin validate --strict` 通过，`mcp list` 实测 Connected），ZCode 只认后者。代价：MiniMax Code 的兼容模式
   因这个未知字段丢掉整条——它本来就没有安装入口，改走项目 `.mcp.json`（下条）。
3. **授权根不收主目录**（用户裁决二：协议 roots 也拒绝 HOME）。`RootAuthority` 的五个入口——显式
   `TAVOTTO_MCP_ROOTS`、`roots/list`、用户确认、宿主工作区变量、cwd 兜底——与生成器 `validate_project_root`
   同一判据：主目录本身及其上级拒，子目录照收；两侧的主目录集合对拍。被拒又没有别的根时是独立一档
   `workspace_root_too_broad`（处置 `configure_roots`：换个目录启动宿主，或设 `TAVOTTO_MCP_ROOTS`）；支持确认框的
   宿主在 cwd 兜底被拒时下一步仍是「请确认」。影响所有宿主：在 `~` 里启动 Claude Code 不再授权整个 home。
4. **口径**（用户裁决三：能 Beta 的 Beta，其余升级生成器）：
   - **WorkBuddy → beta**，渠道与 Claude Code 相同（`claude-plugin`）：隔离 `CODEBUDDY_CONFIG_DIR` 从本地市场装、
     假的 OpenAI 兼容模型让 headless CLI 经 ToolSearch → DeferExecuteTool 真调 `tavotto_health`（引擎就绪、根来源
     cwd），技能进目录。README 章节标 (Beta)，入口唯一出处 `brand.WORKBUDDY_MARKETPLACE` + `brand.CLAUDE_PLUGIN_REF`。
   - **ZCode 仍 experimental**：同一份插件装得上、ZCode 自己的 MCP 客户端报 connected + 10 个工具、技能在目录里，
     但 `-p` 与会话都要 Z.AI 登录（凭据加密存放，不伪造），工具调用没跑——够不到 beta 的「工具流程 local_smoke」。
     生成器的 zcode profile 加 `timeoutMs`；README 只在 experimental 段提一句插件路线。
   - **MiniMax Code**：新 profile `minimax-code`（项目 `.mcp.json`，与 Claude Code 同形，`timeout` 毫秒，
     `TAVOTTO_MCP_ROOTS`），experimental。`mcode exec` 配了自定义 provider 仍要求登录，真宿主没跑。
   - **Trae**：一个 profile 覆盖国际版与国内版——stderr 给两条一键链接（`trae://` / `trae-cn://`，单条目 JSON →
     Base64 → URL 编码，由生成器在本机算出）；超时写进 `env`（`START_MCP_TIMEOUT_MS` ← `startup_timeout_sec`，
     `RUN_MCP_TIMEOUT_MS` ← `tool_timeout_sec`）；`command` 含空格拒绝（官方：解析出错）；技能改为原生
     `.trae/skills/`（全局 `~/.trae` / `~/.trae-cn`）。experimental。

## 不做

- 各家专属清单（理由见裁决 1）；MiniMax 的插件路线；Trae 企业版 CLI（要企业账号）。
- 在市场条目里写 `version`：Claude Code 要求版本只在 plugin.json（`validate` 警告），ZCode 因此不会提示有新版，
  更新靠手动 `plugins update`——写在 `hosts.md`。
- 伪造任何宿主的登录态来凑证据。

## 看护

`tests/test_claude_plugin.py`（插件目录没有自动读取的配置、Codex 清单指向新名、`timeout` / `timeoutMs` 同值、
WorkBuddy 章节来自 brand）、`tests/test_plugin_stage.py`（stage 拒 `.mcp.json` / `mcp/*.json`、清单指向决定
配置名、旧版已装副本体检与钉 command、清单指向的配置是必需文件）、`tests/test_codex_plugin.py`（真 Codex CLI
装完 `codex mcp list` 有 tavotto）、`tests/test_mcp_roots.py` 末节（五个入口 × 主目录 / 上级 / 子目录、太宽一档、
确认框宿主不被截走、与生成器同一判据）、`tests/test_mcp_host_profiles.py`（九个 profile、ZCode `timeoutMs`、
Trae env 超时 / 一键链接往返 / 空格拒绝、WorkBuddy beta 三向对拍）。每条都做过摘掉实现必红的变异。

## 验收证据（2026-09-28，macOS）

- Codex：codex-cli 0.157.1，隔离 `CODEX_HOME`，本地市场装改名后的插件 → `codex mcp list` 有
  `tavotto ./mcp/launch.cmd`；把清单指向不存在的文件 → 列表为空。
- Claude Code 2.1.283：`claude --plugin-dir codex-plugin mcp list` 只剩 `plugin:tavotto:tavotto` 一条、Connected；
  `plugin validate --strict` 对插件与市场都通过。
- WorkBuddy：见裁决 4；改名前的布局同法 ENOENT。
- ZCode 3.14.3（CLI 0.16.9，`ELECTRON_RUN_AS_NODE` + 隔离 `ZCODE_STORAGE_DIR` / `HOME`）：本地市场 git-subdir 安装，
  `plugins list` 选中 `.claude-plugin/plugin.json`、诊断为空；app-server `mcp/list` → `plugin:tavotto:tavotto`
  connected、`toolCount: 10`；`skills list` 有 `tavotto:tavotto-figure`。
- 都**不是发行件**、不是 GUI 窗口，不算 host_verified；真正从 GitHub 的 `plugin-stable` 安装要等本 PR 与
  #692 / #694 一起合入并 promote。
