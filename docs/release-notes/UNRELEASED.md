<!--
待发条目：已经合进 main、但还没有任何一版告诉用户的行为变更与迁移提示。

写在这里而不是留在 PR 正文里，因为发行说明是发版那天写的，写的人不会回头
翻每一个 PR 的「遗留」段——issue #244 就是这么漏掉的。

发版时（RELEASING.md 第 2 步）把下面的 `## ` 段落搬进
`docs/release-notes/vX.Y.Z.md` 并从这里删掉；带着没搬走的段落打 tag，
release.yml 的「拼 release body」当场红（scripts/check_pending_release_notes.py）。
这段注释留在原处。

英文写，与 release notes 一致：**按症状和触发条件写，不要按提交写**。
-->


## Codex plugin on Windows: MCP tools now appear

On Windows, plugin 0.17.0 loaded as enabled, with its skill, but with no Tavotto tools at all, even with the engine installed. Codex gave no error. The bundled launcher was one file serving as both a shell script and a batch file, and `cmd.exe` echoed its first line (`#!/bin/sh`) to stdout ahead of the MCP handshake, so Codex dropped the server. The launcher is now two files: `mcp/launch` for macOS and Linux, and `mcp/launch.cmd` for Windows, whose first line is `@echo off`. `.mcp.json` points at `./mcp/launch`, and on Windows Codex resolves that to `launch.cmd`. macOS and Linux behave exactly as before. After upgrading the plugin, start a new Codex session. Diagnostic lines the server writes to stderr are now UTF-8, so on Windows systems whose code page is not UTF-8 they show up in the Codex logs instead of being dropped. If you are still on plugin 0.17.0 on Windows, run `codex plugin marketplace upgrade tavotto`. With this engine, `tavotto codex install` also treats any stdout output besides the health JSON as a launcher that does not start, and pins a verified interpreter into the installed copy.

## Claude Code plugin (Beta)

<!-- 发版时（2026-09-30 用户决定）：README 在 plugin-stable 带上 `.claude-plugin/` 之前不给安装方式。
搬这一段的同时把「### Using Tavotto with Claude Code (Beta)」一节加回 README（两条命令见下，整行由
`brand.CLAUDE_*` 拼出），并把 docs/support-matrix.json 的 claude-code 改回 `status: beta` +
`channel: claude-plugin`；tests/test_claude_plugin.py 与 test_mcp_host_profiles.py 按矩阵这一档判
命令该在 README 还是在这里。同时把指向这一节的提示加回：
codex-plugin/integrations/configure.py 里 claude-code 的 target 那句改回「更省事的是装插件（README「Using Tavotto
with Claude Code」的两条命令），就不需要这段配置；」（tests/test_readme_section_references.py 要求被引用的章节
真的在）。技能里也要加回（插件随发版带着技能走，Codex 在 #692 评审里要求 promote 之前技能也不给装法）：
codex-plugin/skills/tavotto-figure/references/other-hosts.md 在「工具缺失时」首段之后加回
「**Claude Code（终端 / IDE 扩展 / 桌面 Code 标签页）优先走插件**」一段（同样两条命令 + `/reload-plugins`、
`/mcp` 确认 `plugin:tavotto:tavotto`、授权目录 = 启动目录与 `/add-dir`），其后「其余宿主（以及不想装插件的
Claude Code 用户）：」；Skill 表里 Claude Code 拆成「插件版（新开会话或 `/reload-plugins`；插件自带技能）」与
「配置版」两行；SKILL.md 第 6 条的升级提示加「Claude Code 插件版：`claude plugin update tavotto@tavotto`」。原文见
PR #692。ADR 0103「对外口径」。README 一节的原文（放回「### Using Tavotto from other AI
editors and clients (experimental)」之前）：

### Using Tavotto with Claude Code (Beta)

The same plugin installs into Claude Code (terminal, IDE extensions and the desktop app's Code tab).
Run these in a terminal, one at a time:

```sh
claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin
claude plugin install tavotto@tavotto
pipx install "tavotto[worker]"
```

Then start a new Claude Code session, or run `/reload-plugins` in the one you have open. `/mcp` should list
`plugin:tavotto:tavotto` as connected; ask Claude to call `tavotto_health` to confirm the engine is found.
Tavotto may only open and write inside the folder Claude Code was started in (Claude Code reports it
through MCP roots; `/add-dir` adds more). Claude Code shows no embedded canvas, so you work through the
same tools: open, adjust, preflight, export. To keep editing by hand, hand the figure off to the desktop
app. Update with `claude plugin update tavotto@tavotto`.

Claude Desktop's chat and claude.ai don't start local MCP servers from plugins. For Claude Desktop, use the
config generator below.
-->

Tavotto now installs into Claude Code as a plugin:
`claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin`, then
`claude plugin install tavotto@tavotto`. It is the same plugin as for Codex (same MCP server and
skill); Claude Code has no embedded canvas, so you edit through the tools. The folder Claude Code
was started in is the authorized project. If you had set Tavotto up in Claude Code with the config
generator, remove that `.mcp.json` entry after installing the plugin, or you will have two servers.
The install path only works once this version has been promoted to the `plugin-stable` branch.

## DeepSeek Harness bundle (Beta)

<!-- 发版时（2026-09-30 用户决定）：README 在 plugin-stable 带上 `package.json` / `dsh/` 之前不给安装方式。
搬这一段的同时把「### Using Tavotto with DeepSeek Harness (Beta)」一节加回 README（放在 Claude Code 那节之后、
「### Using Tavotto from other AI editors and clients (experimental)」之前；命令整行由 `brand.DSH_*` 拼出），
并把 docs/support-matrix.json 的 dsh 改回 `status: beta` + `channel: dsh-bundle`；tests/test_dsh_bundle.py、
test_install_commands_from_brand.py 与 test_mcp_host_profiles.py 按矩阵这一档判命令该在 README 还是在这里。
同时把指向这一节的提示加回：codex-plugin/integrations/configure.py 里 dsh 的 target 那句改回「更省事的是装 bundle
（README「Using Tavotto with DeepSeek Harness」那条 dsh plugin 命令），就不需要这段 YAML；」
（tests/test_readme_section_references.py 要求被引用的章节真的在）。技能也加回：
codex-plugin/skills/tavotto-figure/references/other-hosts.md 在 Claude Code 那段之后加「**DeepSeek Harness 优先走
bundle**」一段（上面那条规格、`web` 换成所用 profile；装完新开 DSH 会话等 `mcp__tavotto__*`；授权目录 = 启动 `dsh`
的目录，别在 HOME 里启动），「其余宿主」那句改成「（以及不想装插件 / bundle 的 Claude Code、DSH 用户）」；SKILL.md
第 6 条的升级提示加「DSH bundle：`dsh plugin --profile <名> update tavotto-dsh`」。原文见 PR #694。
ADR 0104「对外口径」。README 一节的原文：

### Using Tavotto with DeepSeek Harness (Beta)

The same plugin is also a DeepSeek Harness bundle. Add it to the profile you use (`web` here, the one
`dsh web` starts):

```sh
dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"
pipx install "tavotto[worker]"
```

Start a new session (`dsh web`). The tools show up as `mcp__tavotto__*` once discovery finishes, and the
`tavotto-figure` skill is in the skill catalog. Tavotto may only open and write inside the folder you started
`dsh` in, so start it in your project, not in your home folder. DSH shows no embedded canvas; you work through the
tools. To update, run `dsh plugin --profile web update tavotto-dsh`. The bundle replaces the hand-merged
`cordis.patch.yml` from the config generator below: use one or the other, not both (two rows would claim the
same `tavotto` server name).
-->

Tavotto now installs into DeepSeek Harness as a profile bundle:
`dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"`.
The MCP tools (`mcp__tavotto__*`) and the `tavotto-figure` skill come with it; no YAML to merge. The folder
you start `dsh` in is the authorized project. If you had merged the config generator's `cordis.patch.yml`
row for Tavotto, remove it after installing the bundle: two rows with the same `tavotto` server name make the
second one fail to load. Like the Claude Code plugin, this works once this version is on `plugin-stable`.

## Codex plugin: an old pip/pipx engine is no longer reported as "desktop app only"

If the `tavotto` engine installed with pip or pipx was too old for the plugin, the plugin's health check could report `desktop_only` ("only the desktop app is installed") and suggest steps that did not fix it. This happened when the plugin could not state a minimum engine version (a plugin installed from a local marketplace or source checkout) or when the engine's CLI could not report its version. A common trigger is a pip mirror that has not synced the new release yet, so `pipx install "tavotto[worker]"` installs an older version. The health check now asks the engine's own Python environment for its version. It reports `engine_too_old` with both versions when it knows the minimum, and a new code, `engine_incompatible`, when it doesn't. Both give upgrade commands such as `pipx install --force "tavotto[worker]==<version>"`. If pip's `index-url` points to a mirror (from `PIP_INDEX_URL` or a pip config file, read only), the message says the mirror may lag and adds `--index-url https://pypi.org/simple` to each command. `tavotto codex doctor` reports the same code and text.
