<!--
待发条目：已经合进 main、但还没有任何一版告诉用户的行为变更与迁移提示。

写在这里而不是留在 PR 正文里，因为发行说明是发版那天写的，写的人不会回头
翻每一个 PR 的「遗留」段——issue #244 就是这么漏掉的。

发版时（RELEASING.md 第 2 步）把下面的 `## ` 段落搬进
`docs/release-notes/vX.Y.Z.md` 并从这里删掉；带着没搬走的段落打 tag，
release.yml 的「拼 release body」当场红（scripts/check_pending_release_notes.py）。
这段注释留在原处。


v0.18.0 的五条用户可见说明已迁入 v0.18.0.md。渠道启用在 v0.18.0 的 plugin-stable promote
成功并核验后走独立文档 PR；技能源码的安装说明只随后续插件产物分发，不得重打包或替换
已验证的 v0.18.0 发行 zip，也不得绕过正式发布器改写 plugin-stable。

英文写，与 release notes 一致：**按症状和触发条件写，不要按提交写**。
-->

## Claude Code can be installed from inside Tavotto

If Claude Code is not installed, signing in with a Claude subscription under
"Connect AI" now offers "Install and sign in" instead of a greyed-out card.
Tavotto downloads the official Claude Code binary only after you click it
(from the npmmirror registry, falling back to Anthropic's own download
server), checks it against a pinned size and SHA-256, and keeps it in
Tavotto's data folder without modifying it; a Claude Code you installed
yourself is left alone. The installed copy does not update itself; Tavotto
moves it to a new version in a later release. Sign-in runs Claude Code's own
`claude auth login` in your browser and returns to Tavotto when it finishes;
Tavotto never sees or stores your credentials. Install errors (offline,
checksum mismatch, disk full…) now name the tool being installed. (#1491)

## "Connect AI editors" adds Google Antigravity

Settings → Connect AI editors lists Antigravity. Connecting writes a
`tavotto` entry under `mcpServers` in both `~/.gemini/config/mcp_config.json`
and `~/.gemini/antigravity/mcp_config.json` (merged, with a backup, never
overwriting other servers); disconnecting removes it from both. The row
shows "not installed" unless one of those Antigravity folders exists — a
`~/.gemini` folder from Gemini CLI alone does not count. Start a new
Antigravity session after connecting. (#1490)
