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

## Claude Code plugin (Beta)

Tavotto now installs into Claude Code as a plugin:
`claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin`, then
`claude plugin install tavotto@tavotto`. It is the same plugin as for Codex (same MCP server and
skill); Claude Code has no embedded canvas, so you edit through the tools. The folder Claude Code
was started in is the authorized project. If you had set Tavotto up in Claude Code with the config
generator, remove that `.mcp.json` entry after installing the plugin, or you will have two servers.
The install path only works once this version has been promoted to the `plugin-stable` branch.

## DeepSeek Harness bundle (experimental)

Tavotto now installs into DeepSeek Harness as a profile bundle:
`dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"`.
The MCP tools (`mcp__tavotto__*`) and the `tavotto-figure` skill come with it; no YAML to merge. The folder
you start `dsh` in is the authorized project. If you had merged the config generator's `cordis.patch.yml`
row for Tavotto, remove it after installing the bundle: two rows with the same `tavotto` server name make the
second one fail to load. Like the Claude Code plugin, this works once this version is on `plugin-stable`.

