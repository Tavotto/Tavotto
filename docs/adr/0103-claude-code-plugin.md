# ADR 0103：同一份插件目录兼作 Claude Code 插件

日期：2026-09-28 · 状态：**Accepted**（用户 2026-09-28 要求「开一个 worktree 做出来」；实现随本 PR）
相关：[0005 外部交接与 Codex 插件](0005-external-handoff-and-codex-plugin.md)、
[0043 插件发行分支](0043-plugin-stable-channel.md)、
`docs/implementation/multi-host-mcp/`（非 Codex 宿主的配置生成器）。

## 问题

Claude Code 早已能用 Tavotto：`integrations/configure.py --host claude-code` 打印一段项目
`.mcp.json`，技能复制到 `.claude/skills/`。但这要用户先下载 zip、解压、生成配置、手工合并、
再复制技能——比 Codex 的两条 `plugin` 命令长得多，`hosts.md` 也记着「插件形态本轮不做」。
2026-09-23 Anthropic 上线 Claude Marketplace，官方说明本地 MCP server **只能装在插件里**分发
（`.mcpb` 桌面扩展的上架已废弃），插件形态成了 Claude 这一侧的正路。

## 裁决

1. **不另起目录、不另起发行分支。** `codex-plugin/` 里加 `.claude-plugin/plugin.json`；仓库根加
   `.claude-plugin/marketplace.json`，条目来源是 `git-subdir → plugin-stable` 上的
   `codex-plugin`，与 Codex 市场指向同一个提交。发行链（`plugin_stage.py` / `plugin_publish.py`）
   一行不改：它按 `git ls-files` 取插件目录，新清单自然进 staging；`STAGE_REQUIRED` 多记它一条
   （不进 `REQUIRED`，理由同 #559：`REQUIRED` 也体检已装的旧版 Codex 插件）。
2. **MCP 条目写在 plugin.json 里，名字必须与 `.mcp.json` 相同。**（2026-09-28 由 [ADR 0106](0106-more-hosts-one-plugin.md)
   接着改：Codex 的配置改名 `codex.mcp.json`，插件根不再有 `.mcp.json`；同名仍保留，理由换成技能与恢复话术按名找工具。） Claude Code 先读插件根的
   `.mcp.json`，再按名字合并 plugin.json 的 `mcpServers`，同名整条替换。`.mcp.json` 是 Codex
   形状（`./` 相对 command、`cwd`、`tool_timeout_sec`），Claude Code 不认 `cwd`，照原样起会
   ENOENT——2.1.283 实测，名字对不上时两条都在、Codex 那条失败。所以 Claude 条目是同一个启动器
   换成 `${CLAUDE_PLUGIN_ROOT}/` 绝对路径、超时由 `tool_timeout_sec` 换算成毫秒，不带任何
   Codex 专有字段。**一份启动器、一个 server.py、一份技能**，两个宿主只差清单。
   #720 把启动器拆成 `mcp/launch`（sh）+ `mcp/launch.cmd`（纯批处理）后，Claude 条目随 Codex 写成
   `${CLAUDE_PLUGIN_ROOT}/mcp/launch`：macOS / Linux 直接执行它（100755）；Windows 上 Claude Code
   经 cross-spawn 起 stdio server，按 PATHEXT 找到同目录的 `launch.cmd` 再交给 `cmd /d /s /c`——与
   Codex 在 Windows 上的解析是同一条路。
3. **授权走 MCP roots。** Claude Code 回 `roots/list`（启动目录 + `/add-dir` 加的目录）并发
   `list_changed`，`RootAuthority` 原样接住，不需要 `TAVOTTO_MCP_ROOTS`。实测 `source: mcp_roots`。
4. **不做**：远程 HTTPS 服务 / connector 上架（桌面聊天与 claude.ai 会忽略插件里的本地 MCP，内嵌画布
   只在那两处有机会显示——要做就是远程渲染，另立项）；`bin/`（有它 claude.ai / Cowork 拒装整个插件）；
   `userConfig`。Claude Desktop 聊天仍走配置生成器。

## 对外口径（2026-09-28 用户决定）

README 的 Claude Code 章节标「(Beta)」，由 `docs/support-matrix.json` 的 `mcp_hosts` 派生：`claude-code` 为
`status: beta`、`channel: claude-plugin`。beta 这一档要求验收矩阵里有该渠道的子行且「工具完整流程」至少
`local_smoke`；`tests/test_mcp_host_profiles.py::test_beta_label_follows_the_matrix_and_its_evidence` 三向对拍。
配置生成器那条路仍是实验。

**2026-09-30 用户改为先合代码、README 暂不给安装方式**：plugin-stable 在下一次 promote 之前没有
`.claude-plugin/`，main 上的 README 若先给出命令，照着装会坏。所以合入时 README 不含这一节、矩阵里
`claude-code` 仍是 `experimental`（beta 档说的是「这条渠道能装」，此刻它还不能）；两条命令完整留在
`docs/release-notes/UNRELEASED.md` 那一段，发版时随段落搬走的同时把 README 一节加回、矩阵改回 `beta` +
`channel: claude-plugin`。`tests/test_claude_plugin.py::test_install_lines_are_published_where_the_matrix_says`
按矩阵这一档判命令该在 README 还是在待发说明，两边都钉。技能（`references/other-hosts.md`、`SKILL.md` 的
升级提示）同理先不教装法，同一条用例钉住插件目录里没有 `claude plugin …` 命令；配置生成器的提示不指向未上
README 的章节（`tests/test_readme_section_references.py`）。发版时要加回的清单在待发说明那一段的注释里。

## 看护

`tests/test_claude_plugin.py`：身份与版本随 Codex 清单与产品；server 名覆盖 `.mcp.json`；条目是
启动器的 Claude 写法（毫秒超时、无 Codex 字段、文件真在）；技能用默认位置；市场指向发行分支；
`STAGE_REQUIRED`；两条安装命令由 `brand.CLAUDE_*` 拼出（矩阵 beta 时在 README，此前只在待发说明）；本机有 `claude` 时跑
`claude plugin validate --strict`。九条变异（改名、秒当毫秒、加 `cwd`、相对 command、版本漂、
摘掉 staging 要求、ref 改 main、README 少 `--sparse`、写 `skills` 键）各自打红。

## 验收证据（2026-09-28，Claude Code 2.1.283，macOS）

- `claude --plugin-dir codex-plugin` + 无头会话：`tavotto_health` → `ok: true`、`source: mcp_roots`、
  10 个工具；打开 → 改图例与线宽（applied 13）→ 预检（2 阻断）→ 导出被拦；确认后强制导出，
  PDF 落在项目 `tavottofile/export/`，`verdict: accepted`。
- 隔离的 `CLAUDE_CONFIG_DIR`：本地 git 仓库模拟 `plugin-stable`，`marketplace add` →
  `install tavotto@tavotto` 成功，缓存里 `launch.cmd` 保留 755，`mcp list` 为 Connected。
- #720 之后（2026-09-30，Claude Code 2.1.285，macOS）：隔离 `HOME` / `CLAUDE_CONFIG_DIR`，
  `claude --plugin-dir codex-plugin mcp list` → `plugin:tavotto:tavotto: <插件>/mcp/launch <插件>/mcp/server.py - ✔ Connected`。
- 尚未做：真正从 GitHub 的 `plugin-stable` 安装（要等本 PR 合入、下一次 promote 把清单带上去）、
  Windows、IDE 扩展与桌面 Code 标签页。
