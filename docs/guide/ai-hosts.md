# Using Tavotto from AI coding assistants (full reference)

> Part of the [Tavotto README](../../README.md). This page holds the detail the README links to.

## The full Codex integration

Verification status: the embedded canvas inside a real Codex Desktop task was accepted once, on 2026-08-24 with engine and plugin 0.9.2 and Codex Desktop 26.818 ([the record](../acceptance/codex-desktop-canvas.md)). It has not been re-run for the current release, and handing a figure over to the desktop window is not that canvas. Hosts that do not load local plugins never show the tools at all.

Run these in a terminal, one at a time:

```sh
codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins
codex plugin add tavotto@tavotto
pipx install "tavotto[worker]"
```

Then **close your current Codex session and start a new one.** The plugin's skill and
MCP tools do not hot-reload into a session that is already open.

**If you installed the engine with `pip`/`pipx`**, one command does the two
`codex plugin` steps for you, and tells you what it skipped:

```sh
tavotto codex install     # idempotent: fixes only what is missing
tavotto codex doctor      # diagnose only, changes nothing
```

It never installs or upgrades the Codex CLI itself, and it never reinstalls a
component that is already healthy. `tavotto codex uninstall` removes the plugin and
the marketplace entry (it leaves the engine alone).

**On Windows, if the plugin shows up enabled but with no tools**, run
`tavotto codex install` (again after upgrading the plugin, if the tools disappear).
The plugin starts its MCP server through a bundled launcher that looks for a Python
that really runs and skips the Microsoft Store `python3` alias; when it finds none,
this command pins a verified interpreter into the installed copy. Plugin 0.17.0 had a
launcher bug on Windows that left every user with no tools. Run
`codex plugin marketplace upgrade tavotto` (or `tavotto codex upgrade` if you installed
without Git, see below), then start a new session. The mechanism and
the symptoms are in
[`codex-plugin/README.md`](../../codex-plugin/README.md).

**Windows with only Codex Desktop (no `codex` on `PATH`, no Git).** The two
`codex plugin` commands need the Codex CLI on your `PATH` and Git for Windows:
`codex plugin marketplace add` clones the marketplace with `git` and fails with
`program not found` without it, and the CLI that ships inside Codex Desktop
(`%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe`) is not on `PATH`. Skip those two
lines and let Tavotto do them:

```powershell
pipx install "tavotto[worker]"
tavotto codex install
```

`tavotto codex install` finds the CLI bundled with Codex Desktop by itself. When Codex
reports that it cannot run `git`, it downloads the plugin's release branch as a zip
(<https://github.com/Tavotto/Tavotto/archive/refs/heads/plugin-stable.zip>), checks every
file against the plugin's build manifest and against the build manifest attached to the
same GitHub release, and registers it as a local marketplace under
`%LOCALAPPDATA%\Tavotto\codex-marketplace`. A local marketplace has no
`codex plugin marketplace upgrade`; upgrade with `tavotto codex upgrade`. Then start a
new Codex session.

Without the Python engine (the desktop-app handoff route below), do the same by hand in
PowerShell:

```powershell
$codex = (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$dir = "$env:LOCALAPPDATA\Tavotto\codex-marketplace"
Invoke-WebRequest https://github.com/Tavotto/Tavotto/archive/refs/heads/plugin-stable.zip -OutFile "$env:TEMP\tavotto-plugin-stable.zip"
Expand-Archive "$env:TEMP\tavotto-plugin-stable.zip" $dir -Force
& $codex plugin marketplace add "$dir\Tavotto-plugin-stable"
& $codex plugin add tavotto@tavotto
```

To upgrade by hand, delete `$dir\Tavotto-plugin-stable`, repeat the download and
`Expand-Archive` lines, then run the last line again. The manual route does not check
the files; `tavotto codex doctor` does, once the engine is installed.

Desktop-app-only users: the desktop installer deliberately does not touch your `PATH`,
so a bare `tavotto` is not available — run the two `codex plugin` commands above
instead. (A settings-page button that runs the same installer is tracked in
[#170](https://github.com/Tavotto/Tavotto/issues/170).)

In the new session you can say:

> Draw this figure with Tavotto. Run the Tavotto health check first; only draw once it
> is healthy, and open the result in Tavotto at the end. Do not install or upgrade any
> component that is already working.

When Codex later edits, adds or renames a plotting script, it calls the plugin's
`tavotto_refresh_project` tool: Tavotto re-reads the project (static analysis only, no
script is run) and the open Tavotto window updates by itself — you never refresh or
restart it by hand. The tool reports which figures are now editable, which still need
a trial run you trigger in Tavotto, and which have a source conflict for you to settle.

When a figure already exists and only needs a new width, font, or font-size floor, just
say "make this figure 8 cm wide, Times New Roman, no text below 8 pt": Codex calls
`tavotto_normalize_figure`, which changes only what you named and leaves content,
colours, data and subplot structure alone. If text no longer fits after shrinking it
makes bounded margin adjustments first, then verifies the delivered file itself (PDF
page size and embedded fonts, PNG pixels and dpi). When the request cannot be met —
nothing fits, the font is not installed, the structure would have to change — it stops
and tells you which constraint to relax instead of lowering the bar or leaving behind a
file that only looks finished.

The first time a project-directory approval appears, what you are confirming is the
local figure directory Tavotto may access. Figures, scripts and data are still
processed on your machine.

The plugin installs into your local `~/.codex` configuration, so it loads only in
Codex surfaces that read local plugins — the Codex CLI in a terminal and the Codex
desktop app. A surface that does not load local plugins (a purely cloud-hosted
session, an IDE integration that ignores `~/.codex/plugins`) will never show the
Tavotto tools; verify in a terminal `codex` session first instead of debugging there.

## Handing off to the desktop app only

Install the desktop app plus the plugin (the two `codex plugin` commands above — the
`pipx` line is not needed on this route). When you ask Codex to "open it in Tavotto",
the plugin's skill hands the figure over with its own handoff script, which locates
the CLI bundled inside the desktop app by itself:

```sh
python3 <plugin-dir>/skills/tavotto-figure/scripts/handoff.py path/to/figure.py
```

Do not tell Codex to run a bare `tavotto open` on this route: the desktop installers
deliberately leave your `PATH` untouched, so that command only exists after a PyPI
install. This path does not require the MCP canvas or the Python engine inside
Codex. Keep the script and its output in the same directory, and prefer vector PDF
for the output.

## Let Codex do the install

Send Codex this message, in full:

> Follow the "Using Tavotto with Codex for the first time" section of the README
> exactly, as a regular-user install. Do not clone or build the source; do not run
> pnpm, npm, cargo, Tauri, tests, or an editable install. Install only the Codex
> plugin and the Tavotto engine it needs, then run the health check; when a new

## Using Tavotto with Claude Code (Beta)

The same plugin installs into Claude Code's terminal and IDE extensions. In the desktop app's Code tab,
the plugin route is limited to **Local or SSH sessions**: Cloud sessions do not inherit installed local
plugins, and WSL sessions do not support plugins ([Claude's environment limits](https://code.claude.com/docs/en/desktop#install-plugins)).
For Cloud or WSL, switch to a Local Code session to use this route; installing on your computer does not
make the plugin available in those sessions. For SSH, run the setup on the remote machine where the
session executes. Run these in that environment's terminal, one at a time:

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

Claude Desktop's chat and claude.ai don't start local MCP servers from plugins. For Claude Desktop's
local chat, use the config generator below; that route does not enable the plugin in Cloud or WSL Code sessions.

## Using Tavotto with DeepSeek Harness (Beta)

The same plugin is also a DeepSeek Harness bundle. This optional route needs `pnpm` already on `PATH`:
[DSH's plugin manager](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md#plugin-management)
uses it for installation and updates. If `pnpm` is unavailable, or DSH reports that it cannot find it,
use the [config-generator route below](#using-tavotto-from-other-ai-editors-and-clients-experimental)
with `--host dsh` instead. That existing route remains experimental and needs no additional Node.js or
pnpm installation for Tavotto.

With pnpm available, add the bundle to the profile you use (`web` here, the one `dsh web` starts):

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

## Using Tavotto with WorkBuddy (Beta)

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

To update an existing marketplace installation, open the installed Tavotto plugin card in WorkBuddy's
plugin marketplace and select **Update** ([official plugin management](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Plug-In)).
Then start a new conversation and ask WorkBuddy to call `tavotto_health`. These steps also apply to the
published v0.18.0 plugin; downloading a ZIP and regenerating MCP configuration does not update a marketplace plugin.
To uninstall the plugin, open the installed Tavotto entry's **Manage** page and use its uninstall action
([WorkBuddy plugin management](https://www.workbuddy.cn/docs/workbuddy/Plugins)).

These Beta labels cover the installation channels above, backed by local host smoke tests; they do not
claim `host_verified` acceptance of a released artifact. Windows host flows, Claude Code's IDE extensions
and desktop Code tab, DSH's desktop app and real-model flow, and WorkBuddy's GUI/canvas remain unverified.
Per-host skill and desktop-handoff gaps remain recorded in `docs/implementation/multi-host-mcp/acceptance.md`.

## Using Tavotto from other AI editors and clients (experimental)

Cursor, Claude Code, Claude Desktop (local chat), VS Code (GitHub Copilot agent), Trae, DeepSeek Harness,
WorkBuddy, ZCode and MiniMax Code use **the same** MCP server and skill as Codex. You don't need Codex, a clone of this repository,
or a frontend build. This config-generator route remains **experimental**, including for hosts with a Beta
plugin or bundle above. Config generation passes its tests and every host except DSH has protocol-level tests;
none of these routes has release-artifact `host_verified` acceptance (the claim lives in `mcp_hosts` in
`docs/support-matrix.json`; the evidence is in `docs/implementation/multi-host-mcp/acceptance.md`).

1. Download `codex-plugin-<version>.zip` from [Releases](https://github.com/Tavotto/Tavotto/releases) and unzip it into
   a folder you will keep. The name says codex for historical reasons; the contents are the same for every host.
2. Engine: skip this if you already ran `pipx install "tavotto[worker]"`. With only the desktop app, or nothing
   installed, run `python3 <package>/mcp/server.py --provision` once. It creates a separate environment in Tavotto's
   config folder and leaves your system Python alone. On Windows, use `py -3` wherever these steps say `python3`:
   there `python3` is often missing or opens the Microsoft Store instead of running Python.
3. Generate the config. The generator **only prints; it writes no files**:

   ```sh
   python3 <package>/integrations/configure.py --host vscode --project-root /absolute/path/to/project
   ```

   Windows (PowerShell):

   ```powershell
   py -3 '<package>\integrations\configure.py' --host vscode --project-root 'D:\path\to\project'
   ```

   `--host` is one of `cursor` `zcode` `dsh` `workbuddy` `claude-code` `claude-desktop` `trae` `vscode`
   `minimax-code`. For Trae, stderr also prints one-click install links (`trae://` and, for the China edition,
   `trae-cn://`). The config to
   merge goes to stdout. stderr says which file or settings screen to merge it into, which folder is authorized,
   whether the engine is ready, how to confirm the host loaded it, and where to put the skill.
4. In a chat, call `tavotto_health` and check that `server.package_dir` is the folder you just unzipped.

Only the `--project-root` folder is authorized; your whole home folder or a drive root is refused. To upgrade, unzip
the new version into a new folder, regenerate the config, and refresh the skill: copy the new `tavotto-figure/`
over the old copy, or rerun `--emit instructions` and replace the text you pasted before. To roll back, point the config at the old folder again.
Per-host sources and differences are in `docs/implementation/multi-host-mcp/hosts.md`.
ZCode can also install the Claude Code plugin above from its own plugin marketplace; that route hasn't been run in a signed-in ZCode session yet.
