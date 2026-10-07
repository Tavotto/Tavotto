<!--
待发条目：已经合进 main、但还没有任何一版告诉用户的行为变更与迁移提示。

写在这里而不是留在 PR 正文里，因为发行说明是发版那天写的，写的人不会回头
翻每一个 PR 的「遗留」段——issue #244 就是这么漏掉的。

发版时（RELEASING.md 第 2 步）把下面的 `## ` 段落搬进
`docs/release-notes/vX.Y.Z.md` 并从这里删掉；带着没搬走的段落打 tag，
release.yml 的「拼 release body」当场红（scripts/check_pending_release_notes.py）。
这段注释留在原处。

发布后启用时核对的安装命令：
`claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin`
`claude plugin install tavotto@tavotto`
`dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"`

v0.18.0 的五条用户可见说明已迁入 v0.18.0.md。下方三个 HTML 注释是发布后的渠道启用清单，
不再是待发用户说明；先等 v0.18.0 的 plugin-stable promote 成功并核验，再用独立文档 PR 启用
README / 支持矩阵 / 生成器提示。技能源码的安装说明同步更新，随后续插件产物分发；不得为了文档
把 plugin-stable 从这次已验证的发行 zip 改成另一棵源码树。

英文写，与 release notes 一致：**按症状和触发条件写，不要按提交写**。
-->

<!-- v0.18.0 promote 成功后（2026-09-30 用户决定；#795 评审收口）：README 在 plugin-stable 带上 `.claude-plugin/` 之前不给安装方式。
核验稳定渠道已经带上新清单后把「### Using Tavotto with Claude Code (Beta)」一节加回 README（两条命令见下，整行由
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

<!-- v0.18.0 promote 成功后（2026-09-30 用户决定；#795 评审收口）：README 在 plugin-stable 带上 `package.json` / `dsh/` 之前不给安装方式。
核验稳定渠道已经带上新清单后把「### Using Tavotto with DeepSeek Harness (Beta)」一节加回 README（放在 Claude Code 那节之后、
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

<!-- v0.18.0 promote 成功后（2026-09-30 用户决定；#795 评审收口）：WorkBuddy 与 Claude Code / DSH 一样，README 在 plugin-stable 带上
`.claude-plugin/` 之前不给装法。09-28 用户定的「只有 WorkBuddy 标 Beta（ZCode 缺登录后的实测，仍是实验）」不推翻，
只是推迟到发版生效。核验稳定渠道已经带上新清单后：
1. 把下面「### Using Tavotto with WorkBuddy (Beta)」一节加回 README（Claude Code、DSH 两节之后，「### Using Tavotto
   from other AI editors and clients (experimental)」之前；「添加市场」那格整行是 `brand.WORKBUDDY_MARKETPLACE`）；
2. docs/support-matrix.json 的 workbuddy 改回 `status: beta` + `channel: claude-plugin`；
3. codex-plugin/integrations/configure.py 里 workbuddy 的 target 那句改回「更省事的是装插件（README「Using Tavotto
   with WorkBuddy」的两条命令），就不需要这段配置；」；
4. 技能 codex-plugin/skills/tavotto-figure/references/other-hosts.md 加回「**WorkBuddy 优先走插件**」一段（插件市场
   「添加市场」填 `Tavotto/Tavotto`，装 `tavotto@tavotto`，没装引擎再 `pipx install "tavotto[worker]"`，新开对话；
   授权目录是对话的工作目录，别在 HOME 里用）与「**ZCode** 也能从自己的插件市场装同一份插件……但还没在登录后的真
   会话里跑过；把这一点告诉用户」一段，「其余宿主」那句加上 WorkBuddy；Skill 表把「WorkBuddy / ZCode」拆成
   「WorkBuddy（插件版）| 新开对话，让它调用 `tavotto_health` | 插件自带，不用复制」与「WorkBuddy / ZCode（配置版）」，
   DSH 拆成「DSH（bundle）| … | bundle 自带，不用复制」与「DSH（YAML patch）」。原文见 PR #712；
5. README experimental 段末「Per-host sources … hosts.md`.」之后加回一句：「ZCode can also install the Claude Code
   plugin above from its own plugin marketplace; that route hasn't been run in a signed-in ZCode session yet.」
tests/test_claude_plugin.py::test_workbuddy_section_installs_the_same_plugin_from_brand 按矩阵这一档判这一节该在
README 还是在这里。README 一节的原文：

### Using Tavotto with WorkBuddy (Beta)

WorkBuddy installs the same plugin as Claude Code. In WorkBuddy open the plugin marketplace, choose **Add
marketplace**, and enter:

```text
Tavotto/Tavotto
```

Install `tavotto@tavotto` from the new marketplace, then run `pipx install "tavotto[worker]"` in a terminal. Start a
new conversation and ask WorkBuddy to call `tavotto_health` to confirm the engine is found. Tavotto may only open and
write inside the conversation's working folder (WorkBuddy doesn't report MCP roots), so work in your project folder,
not your home folder. The embedded canvas hasn't been checked in WorkBuddy yet; the tools work without it. If you
added Tavotto by hand with the config generator below, remove that entry, or you will have two Tavotto servers.
-->

## Codex on Windows: no more terminal window popping up again and again

After upgrading the Codex plugin, Tavotto rebuilds its own plugin environment
in the background. On Windows 11 that rebuild opened a terminal window that
failed straight away with error `0x800700e8`, so the environment was never
rebuilt and the window came back every time Codex started the Tavotto
server. The rebuild now runs without a window, and after one attempt Tavotto
waits 30 minutes before trying again in the background; the message in Codex
points to `provision.log` and gives the command to run it by hand. Setting
`TAVOTTO_MCP_NO_AUTO_PROVISION=1` now also reaches the server under Codex
(before, Codex did not pass it on, so the switch had no effect).
