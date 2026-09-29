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

## Codex plugin: an old pip/pipx engine is no longer reported as "desktop app only"

If the `tavotto` engine installed with pip or pipx was too old for the plugin, the plugin's health check could report `desktop_only` ("only the desktop app is installed") and suggest steps that did not fix it. This happened when the plugin could not state a minimum engine version (a plugin installed from a local marketplace or source checkout) or when the engine's CLI could not report its version. A common trigger is a pip mirror that has not synced the new release yet, so `pipx install "tavotto[worker]"` installs an older version. The health check now asks the engine's own Python environment for its version. It reports `engine_too_old` with both versions when it knows the minimum, and a new code, `engine_incompatible`, when it doesn't. Both give upgrade commands such as `pipx install --force "tavotto[worker]==<version>"`. If pip's `index-url` points to a mirror (from `PIP_INDEX_URL` or a pip config file, read only), the message says the mirror may lag and adds `--index-url https://pypi.org/simple` to each command. `tavotto codex doctor` reports the same code and text.

## Claude Code plugin (Beta)

Tavotto now installs into Claude Code as a plugin:
`claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin`, then
`claude plugin install tavotto@tavotto`. It is the same plugin as for Codex (same MCP server and
skill); Claude Code has no embedded canvas, so you edit through the tools. The folder Claude Code
was started in is the authorized project. If you had set Tavotto up in Claude Code with the config
generator, remove that `.mcp.json` entry after installing the plugin, or you will have two servers.
The install path only works once this version has been promoted to the `plugin-stable` branch.

## DeepSeek Harness bundle (Beta)

Tavotto now installs into DeepSeek Harness as a profile bundle:
`dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"`.
The MCP tools (`mcp__tavotto__*`) and the `tavotto-figure` skill come with it; no YAML to merge. The folder
you start `dsh` in is the authorized project. If you had merged the config generator's `cordis.patch.yml`
row for Tavotto, remove it after installing the bundle: two rows with the same `tavotto` server name make the
second one fail to load. Like the Claude Code plugin, this works once this version is on `plugin-stable`.

