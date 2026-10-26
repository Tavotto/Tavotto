<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/hero-window.png" width="100%"
       alt="The Tavotto desktop window on macOS with the layout saved to the project, panel (a) selected, and its position, size and scale in the properties column.">
</p>

<p align="center">
  <b>English</b> · <a href="https://github.com/Tavotto/Tavotto/blob/main/README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Tavotto/Tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://pypi.org/project/tavotto/"><img alt="PyPI" src="https://img.shields.io/pypi/v/tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Tavotto/Tavotto/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-1b1b18?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.14-1b1b18?style=flat-square&labelColor=1b1b18">
  <a href="https://ko-fi.com/tavotto"><img alt="Support on Ko-fi" src="https://img.shields.io/badge/Ko--fi-support-FF5E5B?style=flat-square&logo=kofi&logoColor=white&labelColor=1b1b18"></a>
  <a href="https://afdian.com/a/Tavotto"><img alt="在爱发电支持" src="https://img.shields.io/badge/%E7%88%B1%E5%8F%91%E7%94%B5-%E6%94%AF%E6%8C%81-946CE6?style=flat-square&labelColor=1b1b18"></a>
</p>

# Tavotto: make AI-drawn research figures publication-ready

**AI drew your research figure. Tavotto™ makes it publication-ready.** Tavotto is a free, open-source tool for finishing scientific figures: it opens the figure that an AI assistant or your own Python script already drew (matplotlib today), and you change titles, legends, curves and the page layout on a canvas. The script stays untouched, every edit is saved separately, and before export the figure is checked against a publication profile (font floor, column width, dpi).

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/demo.gif" width="100%"
       alt="A 12-second walkthrough: select a panel, drag it, set its width to 56 mm, then export.">
</p>

<p align="center">
  <a href="https://www.tavotto.com/#video"><img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/launch-film-cover-en.webp" width="60%"
       alt="The cover of the Tavotto launch film: the Tavotto mark on a dark ground, with the line From Matplotlib to publication-ready figures. Opens the 33-second film on tavotto.com."></a>
</p>

## Download

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases/latest/download/Tavotto-macOS.dmg"><img alt="Download for macOS, Apple Silicon" src="https://img.shields.io/badge/macOS-Apple%20Silicon-2868b7?style=for-the-badge&logo=apple&logoColor=white&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/releases/latest/download/Tavotto-macOS-Intel.dmg"><img alt="Download for macOS, Intel" src="https://img.shields.io/badge/macOS-Intel-2868b7?style=for-the-badge&logo=apple&logoColor=white&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/releases/latest/download/Tavotto-Windows-Setup.exe"><img alt="Download for Windows, x64" src="https://img.shields.io/badge/Windows-x64-2868b7?style=for-the-badge&logo=windows&logoColor=white&labelColor=1b1b18"></a>
  <br>
  <a href="#pip-linux-and-servers"><img alt="Install from PyPI" src="https://img.shields.io/badge/PyPI-pipx%20install-2868b7?style=for-the-badge&logo=pypi&logoColor=white&labelColor=1b1b18"></a>
  <a href="#using-tavotto-with-codex-for-the-first-time"><img alt="Install the Codex plugin" src="https://img.shields.io/badge/Codex-plugin-2868b7?style=for-the-badge&logo=openai&logoColor=white&labelColor=1b1b18"></a>
  <a href="#using-tavotto-with-claude-code-beta"><img alt="Install the Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin%20(Beta)-2868b7?style=for-the-badge&logo=claude&logoColor=white&labelColor=1b1b18"></a>
</p>

<p align="center"><sub>Needs <b>macOS 14+</b> on Apple Silicon, <b>macOS 15+</b> on Intel, or Windows x64 (Windows on ARM is neither built nor verified). Linux runs from PyPI (beta). What is supported is decided in one place, <a href="docs/support-matrix.json"><code>docs/support-matrix.json</code></a>.</sub></p>

## Get started

### Desktop app in three steps

1. **Install it.** Open the `.dmg` or run the `.exe` from the buttons above. The installer carries its own Python with numpy, matplotlib, pandas, scipy, seaborn and Pillow, so you do not install Python. The download is about 195 MB on macOS and 89 MB on Windows.
2. **Open your figures.** Drag a matplotlib script (`.py`) onto the window, or open the folder that holds your figure scripts. The home page also offers a guided example.
3. **Edit and export.** Double-click a panel to edit inside it, arrange panels on the page, then press Export. The preflight runs before anything is written.

On Windows, an unsigned installer makes SmartScreen show a prompt on first run: choose *More info → Run anyway*, or check the file against `SHA256SUMS.txt` on the release page first. More: [installation details](docs/guide/install-advanced.md).

### Use it from an AI coding assistant

The same engine and canvas are available as tools inside coding agents, so an agent can draw a figure, open it in Tavotto, run the preflight and export.

| Assistant | Status | How to install |
| --- | --- | --- |
| Codex | Plugin | [Using Tavotto with Codex for the first time](#using-tavotto-with-codex-for-the-first-time) |
| Claude Code | Beta | [Using Tavotto with Claude Code](#using-tavotto-with-claude-code-beta) |
| DeepSeek Harness | Beta | [Using Tavotto with DeepSeek Harness](#using-tavotto-with-deepseek-harness-beta) |
| WorkBuddy | Beta | [Using Tavotto with WorkBuddy](#using-tavotto-with-workbuddy-beta) |
| Cursor, VS Code, Trae, ZCode, MiniMax Code, Claude Desktop | Experimental | [Config generator](#using-tavotto-from-other-ai-editors-and-clients-experimental) |

### pip, Linux and servers

```sh
pipx install "tavotto[worker]"
tavotto
```

Your browser opens at `http://127.0.0.1:5089`. It needs Python 3.10 or later; on an older Python, `uv tool install --python 3.12 "tavotto[worker]"` fetches one. `tavotto --figures <dir>` opens a figure folder directly. More (uv, SSH to a remote server, your own Python environment): [installation details](docs/guide/install-advanced.md).

## What it does

### Edit inside the figure

Double-click a panel and Tavotto runs your script once and keeps the matplotlib `Figure` in memory. Pick the title, a tick, a curve, the legend or an arrow from the tree or on the canvas, then change its size, colour, dash pattern or position; dragging follows the cursor, and matplotlib runs once when you let go. [What can be edited](docs/guide/editing-reference.md).

![Editing inside figure (a): the element tree on the left, the whole page in view with the title selected and its size, bold and italic controls above it, and font, size and colour on the right.](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/canvas-edit.png)

### Lay out several figures on one page

Place panels on a page measured in millimetres at the size their script drew them, snap and align them, bind them into rows and grids, add (a)(b)(c) labels, and draw arrows, scale bars and zoom boxes on top.

![Layout mode: figures (a), (b) and (c) arranged on a 150 × 125 mm page, with (a) selected and its X, Y, W, H in millimetres on the right.](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/layout.png)

### Check and export for submission

Export writes PDF with real vector text, plus PNG, TIFF and SVG (PNG and TIFF at 300 to 1200 ppi; EPS too when you export a single figure that has a script). Alongside, the figure is checked against a publication profile at its final physical size: text under the font floor, the wrong column width, low-dpi rasters. Findings are sorted into to fix, to review, not verifiable and suggestions. They never lock the Export button; anything still unresolved is recorded in a style-check report written with the files. [Formats, levels and profile](docs/guide/export-and-preflight.md).

![The export dialog: output as whole page or each figure, PDF and PNG at 600 ppi, and the pre-export checks against the default spec showing 1 to review and 3 not verifiable.](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/preflight.png)

### Connect your own AI to change the figure

The assistant panel edits the script for you ("move the legend to the top left, 7 pt"). Connect a ChatGPT (Codex) or Claude subscription, an API key from DeepSeek, Kimi, Zhipu GLM, MiniMax, Alibaba Bailian, OpenAI or Anthropic and others, or any compatible custom endpoint. Your script is snapshotted first, you see the diff, and one click reverts it. This is the only path that touches your script, and you have to start it.

![The Connect an AI dialog: DeepSeek, Kimi, Zhipu GLM, MiniMax and Alibaba Bailian, each with a Connect button, and More AI… below.](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/connect-ai.png)

### Open a script in the environment it already runs in

Tavotto looks for the conda, venv or system Python your script was written for and runs it there. A package it cannot find is named, and your own environments are never modified. From a terminal, `tavotto run -- python figure.py` attaches to figures made by the command you already use ([Beta](docs/compatibility/tavotto-run.md)).

![After running a linked script: the asset library shows fig1_kinetics.py as Captured 1 figure, and the figure it produced sits on the canvas, with its position, size and original size on the right.](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/run-script.png)

### Go back to any earlier layout

The layout timeline keeps a node automatically after you pause editing (at most one every two minutes) and on export, write-back, save, and project open or close. Name the ones you want to keep, compare a node with the current canvas, and restore it; the state before the restore is saved too.

![The Timeline tab in the right column listing two named moments, "Three panels" and "Bottom row 52 mm", above "Project opened".](https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/timeline.png)

## Why use it

|  | Edit the script and re-run | Finish in a vector editor | Tavotto |
|---|---|---|---|
| Move a legend by 3 mm | Change a number, run, look, repeat | Drag it | Drag it |
| Knows it is a legend, not paths | In code | No | Yes: title, legend, ticks, series, colourbar |
| Edits survive the next data update | Yes | No, the hand-made layout is lost | Yes, replayed onto a fresh run |
| Several panels on a page in mm | By hand in code | Yes | Yes |
| Journal rules checked before export | No | No | Yes |

A vector editor is the more powerful drawing tool. It does not know that the thing you clicked is a legend, and it cuts the figure off from the code that drew it.

## Common questions

**Can I edit a PDF that matplotlib generated?** Yes, when the script that produced it is next to it: `tavotto open figures/Fig1.pdf` matches it to the script in the same project, re-runs it and lets you edit the figure itself, not a flattened picture. A PDF with no script in its project folder can still be placed on a page, arranged, annotated and exported, but not edited inside.

**Does Tavotto change my script?** No. Edits are stored as overrides in the layout and replayed onto a fresh run. "Write back to the original file" is an explicit action, and it can be locked off per project. The optional AI assistant is the other way your script can change, and only when you ask.

**Is my data uploaded?** No. Rendering, layout and export run on your machine. Tavotto makes two requests on its own: a daily update check, and anonymous usage statistics that are off until you opt in. If you connect an AI, your request goes to that AI, through its command-line tool or straight to its API, and nowhere else. See [Privacy](#privacy).

**Does it work with figures written by Claude, Codex or ChatGPT?** Yes. Any matplotlib script works, including seaborn and pandas plots. For agents, keep the script and its output in one folder and save the output as vector PDF; the plugin teaches the agent this shape.

**Do I need to install Python?** Not for the desktop app, which carries its own. The PyPI install needs Python 3.10 to 3.14.

**What about plotly or Pillow figures?** They are not matplotlib objects, so they cannot be edited inside. Tavotto says so and can ask your connected AI for an editable matplotlib version as a new script, leaving the original alone.

## Install in an AI coding assistant

Step-by-step installs for each assistant in the table above. Everything beyond the commands is in [the full reference](docs/guide/ai-hosts.md).

### Using Tavotto with Codex for the first time

> **Regular users: do not clone or build this repository.** Installing from source is only for people working on Tavotto itself.

| What you want to do | What to install |
| --- | --- |
| Codex draws the figure; you keep dragging and tweaking it in the Tavotto desktop window | The Tavotto desktop app + the Codex plugin (no Python engine needed) |
| Use Tavotto's canvas, preflight, editing and export tools directly inside Codex | The Codex plugin + the Tavotto Python engine |
| Change Tavotto itself | See "Contributors: developing from source" below |

#### The full Codex integration

Run these in a terminal, one at a time:

```sh
codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins
codex plugin add tavotto@tavotto
pipx install "tavotto[worker]"
```

Then **close your current Codex session and start a new one.** The plugin's skill and MCP tools do not hot-reload into a session that is already open. If you installed the engine with `pip`/`pipx`, `tavotto codex install` does the two `codex plugin` steps for you and skips what is already healthy; `tavotto codex doctor` only diagnoses.

<details>
<summary><b>Windows: plugin enabled but no tools, or only Codex Desktop (no Git, no <code>codex</code> on PATH)</b></summary>

If the plugin shows up enabled but with no tools, run `tavotto codex install`. If you only have Codex Desktop, skip the two `codex plugin` lines and let Tavotto do them. It finds the CLI bundled in `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe`, and when Codex cannot run `git` it downloads the plugin's release branch as a zip and checks every file against the build manifest:

```powershell
pipx install "tavotto[worker]"
tavotto codex install
```

Without the Python engine, do the same by hand in PowerShell:

```powershell
$codex = (Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\bin\*\codex.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$dir = "$env:LOCALAPPDATA\Tavotto\codex-marketplace"
Invoke-WebRequest https://github.com/Tavotto/Tavotto/archive/refs/heads/plugin-stable.zip -OutFile "$env:TEMP\tavotto-plugin-stable.zip"
Expand-Archive "$env:TEMP\tavotto-plugin-stable.zip" $dir -Force
& $codex plugin marketplace add "$dir\Tavotto-plugin-stable"
& $codex plugin add tavotto@tavotto
```

A local marketplace has no `codex plugin marketplace upgrade`: upgrade with `tavotto codex upgrade`, or delete `$dir\Tavotto-plugin-stable` and repeat the download. Background and symptoms: [the full reference](docs/guide/ai-hosts.md) and [`codex-plugin/README.md`](codex-plugin/README.md).

</details>

In the new session you can say:

> Draw this figure with Tavotto. Run the Tavotto health check first; only draw once it is healthy, and open the result in Tavotto at the end. Do not install or upgrade any component that is already working.

The plugin installs into your local `~/.codex` configuration, so it loads only in Codex surfaces that read local plugins: the Codex CLI and the Codex desktop app. A surface that does not load local plugins (a cloud-hosted session, an IDE integration that ignores `~/.codex/plugins`) will never show the Tavotto tools; check in a terminal `codex` session first.

#### Handing off to the desktop app only

Install the desktop app plus the plugin (the two `codex plugin` commands above; the `pipx` line is not needed). When you ask Codex to "open it in Tavotto", the plugin's skill hands the figure over with its own script, which finds the CLI inside the desktop app:

```sh
python3 <plugin-dir>/skills/tavotto-figure/scripts/handoff.py path/to/figure.py
```

Do not tell Codex to run a bare `tavotto open` on this route: the desktop installers deliberately leave your `PATH` untouched, so that command only exists after a PyPI install. Keep the script and its output in the same directory, and prefer vector PDF.

#### Let Codex do the install

Send Codex this message, in full:

> Follow the "Using Tavotto with Codex for the first time" section of the README exactly, as a regular-user install. Do not clone or build the source; do not run pnpm, npm, cargo, Tauri, tests, or an editable install. Install only the Codex plugin and the Tavotto engine it needs, then run the health check; when a new session is required, tell me so explicitly and stop.

### Using Tavotto with Claude Code (Beta)

The same plugin installs into Claude Code's terminal and IDE extensions, in Local or SSH sessions (Cloud and WSL sessions do not load local plugins). Run these one at a time in that environment's terminal:

```sh
claude plugin marketplace add Tavotto/Tavotto --sparse .claude-plugin
claude plugin install tavotto@tavotto
pipx install "tavotto[worker]"
```

Then start a new session or run `/reload-plugins`; `/mcp` should list `plugin:tavotto:tavotto`. Claude Code has no embedded canvas, so you work through the tools and hand the figure to the desktop app to keep editing by hand. Update with `claude plugin update tavotto@tavotto`.

### Using Tavotto with DeepSeek Harness (Beta)

Needs `pnpm` on `PATH`, which DSH's plugin manager uses. Add the bundle to the profile you use (`web` is the one `dsh web` starts):

```sh
dsh plugin --profile web add "git+https://github.com/Tavotto/Tavotto.git#plugin-stable&path:/codex-plugin"
pipx install "tavotto[worker]"
```

Start a new session with `dsh web`, in your project folder rather than your home folder. Update with `dsh plugin --profile web update tavotto-dsh`. Without pnpm, use the [config generator](#using-tavotto-from-other-ai-editors-and-clients-experimental) with `--host dsh`.

### Using Tavotto with WorkBuddy (Beta)

WorkBuddy installs the same plugin as Claude Code. In its plugin marketplace choose **Add marketplace** and enter:

```text
Tavotto/Tavotto
```

Install `tavotto@tavotto`, run `pipx install "tavotto[worker]"` in a terminal, then start a new conversation and ask WorkBuddy to call `tavotto_health`. Work in your project folder, not your home folder. Updating and uninstalling are done from the plugin's card in WorkBuddy.

The Beta label covers these install channels, backed by local host smoke tests; it does not claim `host_verified` acceptance of a released artifact. What is and is not verified per host is in [the full reference](docs/guide/ai-hosts.md).

### Using Tavotto from other AI editors and clients (experimental)

Cursor, VS Code (GitHub Copilot agent), Trae, ZCode, MiniMax Code, Claude Desktop (local chat) and the hosts above use the same MCP server and skill. Download `codex-plugin-<version>.zip` from [Releases](https://github.com/Tavotto/Tavotto/releases) (the name is historical; the contents are the same for every host), unzip it somewhere permanent, and generate the config. The generator only prints; it writes no files:

```sh
python3 <package>/integrations/configure.py --host vscode --project-root /absolute/path/to/project
```

```powershell
py -3 '<package>\integrations\configure.py' --host vscode --project-root 'D:\path\to\project'
```

Then call `tavotto_health` in a chat and check that `server.package_dir` is the folder you unzipped. Engine setup, the list of `--host` values, upgrading and the evidence level of each host: [the full reference](docs/guide/ai-hosts.md).

## Privacy

Rendering, composition and export are local; Tavotto does not upload your figures, scripts, projects or data. It makes two requests on its own: a once-a-day check of GitHub Releases for a new version (or the project's China mirror, `dl.tavotto.cn`, depending on your time zone; switch it off under **Settings → Check for updates**), and anonymous usage statistics that stay **off until you say yes** and can never contain figures, scripts, paths or text. Diagnostics bundles are saved locally, redacted, and sent only if you confirm. Details: [docs/privacy.md](docs/privacy.md) and the [event contract](docs/analytics/telemetry-events.md). Where Tavotto keeps its files: [docs/guide/files-and-data.md](docs/guide/files-and-data.md).

Free code signing is provided by SignPath.io, certificate by SignPath Foundation; see the [code signing policy](docs/code-signing-policy.md). Report security issues through [private reporting](https://github.com/Tavotto/Tavotto/security/advisories/new), not a public issue.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers how to verify a change and which boundaries the codebase keeps. When reporting a bug, **Settings → Privacy, diagnostics and About → Download diagnostics bundle** collects what is usually needed, with keys and personal paths redacted.

**Contributors: developing from source.** This is for changing Tavotto itself, never a fallback for a failed regular install (needs node and pnpm to build the interface):

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
.venv/bin/python scripts/fetch_fonts.py   # approved fonts (not in git)
python scripts/build_frontend.py
.venv/bin/tavotto
```

## Support the project

Tavotto is free and open source, and maintained by one person. If it saves you time, you can support its development: one-off or monthly on **Ko-fi**, or with Alipay / WeChat Pay on **爱发电** (afdian). Support is voluntary and unlocks no features; what it pays for, and the supporters wall, are on [tavotto.com/support](https://www.tavotto.com/support/).

<p>
  <a href="https://ko-fi.com/tavotto"><img alt="Support Tavotto on Ko-fi" src="https://img.shields.io/badge/Support%20on%20Ko--fi-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white"></a>
  <a href="https://afdian.com/a/Tavotto"><img alt="在爱发电支持 Tavotto" src="https://img.shields.io/badge/%E5%9C%A8%E7%88%B1%E5%8F%91%E7%94%B5%E6%94%AF%E6%8C%81-946CE6?style=for-the-badge"></a>
</p>

### Sponsors

Supporters on the $50/month **Sponsor** tier on [Ko-fi](https://ko-fi.com/tavotto/tiers) (or the 赞助人 plan on [爱发电](https://afdian.com/a/tavotto)) appear here with their avatar, for as long as they sponsor. The image updates on its own.

<a href="https://www.tavotto.com/support/"><img src="https://www.tavotto.com/api/supporters/sponsors" alt="Tavotto sponsors" width="720"></a>

## License

[AGPL-3.0-only](LICENSE).

Using Tavotto, modifying it and running it inside your lab are all unrestricted, and **the figures and PDFs you produce with it are entirely yours** — the licence does not reach your work. The obligations apply to distribution: if you give a modified Tavotto to others, or run it as a network service for them, the corresponding source has to be available to those users.

### Contributing

Contributions are accepted under the Tavotto Contributor License Agreement, which lets you **keep the copyright in your contribution** while allowing the project to distribute it under the community licence and, where applicable, under separate commercial terms. The Tavotto copyright holder(s) may offer separately licensed editions.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/legal/](docs/legal/).

### Trademark

**Tavotto™** is an unregistered trademark of the Tavotto project. An open-source copyright licence is not a trademark licence: forks are welcome and may say they are based on Tavotto, but shouldn't present themselves as the official release. See [TRADEMARKS.md](TRADEMARKS.md).

---

If Tavotto saves you an afternoon on your next figure, consider starring the repository.
