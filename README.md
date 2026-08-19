<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/hero.svg" width="100%"
       alt="Tavotto — arrange matplotlib panels on a page, edit the elements inside them, export a true-vector PDF">
</p>

<p align="center">
  <b>English</b> · <a href="https://github.com/Tavotto/Tavotto/blob/main/README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/Tavotto/Tavotto/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Tavotto/Tavotto?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Tavotto/Tavotto/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/Tavotto/Tavotto/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-2868b7?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.13-1b1b18?style=flat-square&labelColor=1b1b18">
  <img alt="Platform" src="https://img.shields.io/badge/macOS%20·%20Windows%20·%20Linux-1b1b18?style=flat-square&labelColor=1b1b18">
</p>

<p align="center"><i>Edit the figure. Keep the source.</i></p>

The last mile before submission usually goes like this: the plots are done, but now
they have to become Figure 1 — resize the fonts, move the legend, align everything.
So you go back to Python, change a line, re-run the script, look again. Twenty times.
Or you drag the PDFs into Illustrator and lose the connection to your code for good.

**Tavotto lets you edit the figure directly.** Drop your matplotlib panels onto a page
and arrange them freely. Double-click any panel and you can select the things inside
it — title, axis labels, curves, legend — then change the font size, the colour, or
just drag them. **Dragging and dialling are instant** — the figure follows your
cursor frame by frame, and matplotlib runs once, when you let go, to make it official.

Every change is **non-destructive**: your script is never modified, and anything can
be undone. On export the engine re-renders each panel at full quality and composes a
PDF whose text is still real, selectable vector text.

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/workbench.png" width="100%"
       alt="The Tavotto workbench: an element tree on the left listing the title and axis labels, three panels arranged as (a)(b)(c) on the page, and the properties of the selected title on the right">
</p>

<p align="center"><sub>Left: elements inside the figure · Middle: a 150 × 130 mm page · Right: properties of the selected title — the source file is still <code>fig1_kinetics.py</code></sub></p>

## Install

**Download an installer** from the [latest release](https://github.com/Tavotto/Tavotto/releases/latest)
— `.dmg` for macOS, `.exe` for Windows — install it, and double-click. Tavotto opens
in its own desktop window, and updates itself from then on — it checks, downloads,
installs and restarts without sending you back to this page.

**You do not need to install Python.** Both the macOS and the Windows installer ship a
private Python runtime with the usual scientific stack already in it — numpy, matplotlib,
pandas, scipy, seaborn and Pillow, at pinned versions, identical on both platforms so the
same script draws the same figure. Rendering works the moment the installer finishes, with
no download and no network, and without Homebrew, Conda or Xcode. Tavotto never touches a
Python or Conda you already have; if a figure of yours needs a package that is not in that
list, point Tavotto at your own environment under **Settings → Rendering environment**.
See [Good to know](#good-to-know).

The macOS build is **Apple Silicon (arm64) only**. Intel Macs are not currently built or
tested — use the PyPI install below.

**Or install from PyPI**, which works the same on all three platforms:

```sh
pipx install "tavotto[worker]"
tavotto
```

Your browser opens at `http://127.0.0.1:5089`.

<details>
<summary>Using pip · reusing your own scientific environment · running from source</summary>

**pip** (installs into the current environment):

```sh
pip install "tavotto[worker]"
tavotto
```

**Reuse the environment your figures were made in.** Drop the `[worker]` extra and
point Tavotto at your own interpreter, so figures render against exactly the
dependencies they were written for:

```sh
pipx install tavotto
export TAVOTTO_WORKER_PYTHON=/path/to/your/env/bin/python     # Windows: setx TAVOTTO_WORKER_PYTHON "..."
tavotto
```

**From source** (needs node + pnpm to build the interface):

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
python scripts/build_frontend.py
.venv/bin/tavotto
```

</details>

**Options**: `tavotto --figures <dir>` opens a figure directory straight away;
`--port 5089` changes the port; `--no-browser` skips opening a browser.

## Try it

A ready-to-open example project ships with the repository:

```sh
tavotto --figures examples/figures
```

Three panels appear in the asset browser. Drag them onto the page, then double-click
one — you get a tree of everything inside that figure, and clicking the title lets you
change its size. `examples/figures/` holds two perfectly ordinary matplotlib scripts;
Tavotto does not ask you to write them in any special way.

## What you can edit inside a figure

| | |
|---|---|
| **Text** | Title, axis labels, tick labels, legend, annotations — content, size, colour, weight, style, rotation, opacity, visibility. Draggable. |
| **Data series** | Line width, dash pattern, colour, markers (scatter markers can be swapped wholesale), legend entry order |
| **Arrows** | Arrows your script draws (`FancyArrowPatch`): drag the whole arrow or either endpoint, and change arrow style, line style, width, head size and colour. Arrows attached to `annotate()` keep their data anchors — style only. |
| **Axes** | Tick groups, axis lines, grid, 3D viewing angle (elev/azim/roll), 3D axis arrows and panes. Drag a subplot and what belongs to it travels along — a label you had moved, its colourbar, a twin axis. |
| **Figure** | Overall figure size (the layout reflows), background |
| **Not editable** | Data-space properties such as axis limits, scales and spines, and colourbar orientation. Change those in your script. |

Around the page there is a full layout toolset: snapping and alignment guides, multi-select
distribute, grouping, layout groups (row / column / grid constraints that reflow when sizes
change), text / arrow / shape annotations at any rotation, presets for research figures
(reversible-reaction arrows, scale bars, error markers, zoom boxes), multiple canvases in
tabs, a version timeline, and named styles you can apply across a whole document.

## Export

PDF export embeds each original vector panel as-is, so **the text stays selectable and
searchable**. PNG is rendered from that same PDF, so the two can never disagree. Before
exporting, Tavotto checks for panels off the page, overlaps, tiny fonts, low effective
DPI, stale renders and missing assets, and can write a proof report alongside the figure
for your submission records.

Two deliberate exceptions: a panel with `opacity < 1` or a flip applied is embedded as a
bitmap at your export DPI, because PDF vector content supports neither.

## Code signing policy

Free code signing provided by SignPath.io, certificate by SignPath Foundation.
Windows release installers are built from this repository by GitHub Actions and
are submitted for manual signing before they are described as signed releases.
See the complete [Code signing policy](docs/code-signing-policy.md) and
[Privacy policy](docs/privacy.md).

## AI assistant (optional)

The assistant panel can hand a request to the **Codex or Claude CLI** on your machine to
edit the script itself — for example "move the legend to the top left and make it 7 pt".
Your script is snapshotted first; afterwards you see the diff and the figure re-renders,
and one click reverts it. Everything else works without these tools installed.

## Sending a figure in from elsewhere

Just made a figure somewhere else — ran a script yourself, or had Codex / Claude write one?
One command hands it over:

```bash
tavotto open figures/Fig1_kinetics.pdf   # the output file
tavotto open figures/fig1_kinetics.py    # or the script — output name is resolved for you
tavotto open figures/                    # or the whole figure library
```

It opens the figure's library as a project, adds any missing entries to the script registry,
then launches the **desktop app** (if it's already running the figure goes straight into that
window — no second copy). Without the desktop app it falls back to browser mode.

### Codex plugin

Install it and the matplotlib figures Codex writes come out in a shape Tavotto can take over
(script next to its output, vector PDF, statically resolvable output name) — **and you can
finish them without leaving Codex**:

```bash
codex plugin marketplace add Tavotto/Tavotto && codex plugin add tavotto@tavotto
```

Start a new session afterwards. The CLI and the Codex desktop app share one plugin directory,
so **installing once covers both**; `codex plugin marketplace upgrade tavotto` pulls updates.

The plugin ships three layers with clear boundaries:

* a **skill** that teaches Codex the conventions a Tavotto-editable figure has to satisfy;
* a local **MCP server** exposing the engine — open a figure, apply canonical overrides,
  run a publication preflight, export true-vector PDF/SVG or PNG at an explicit DPI.
  All six tools work in hosts with no UI at all;
* an **MCP App canvas** rendered inside Codex, built from the *same* frontend code the
  desktop app uses — dragging, hit-testing, snapping and undo have no second implementation.

Every edit is an override; **your Python source is never rewritten**. Multi-panel layout,
canvas annotations and write-back still live in the Tavotto window, one `tavotto open` away.

See [`codex-plugin/README.md`](codex-plugin/README.md) — including which parts are *not yet
verified inside a real Codex Desktop*. Design notes are in
[ADR 0006](docs/adr/0006-codex-mcp-app-and-publication-profile.md); the distribution roadmap
(including the official directory submission checklist) is in
[`docs/codex-plugin-distribution.md`](docs/codex-plugin-distribution.md).

## Publication profile and preflight

Export runs a **profile-driven preflight** first. The rules live in one versioned JSON file
(`src/tavotto/profiles/publication.json`) that both the Python engine and the TypeScript
frontend read — so there is no second copy to drift.

The default `lab-publication-v1` encodes: 80 mm single / 150 mm double column, 16:9 · 4:3 · 1:1
aspect ratios, 9 pt body text with a hard floor of **more than 8 pt of final effective size**
(8.5 pt strict), ≥ 300 dpi rasters, Times New Roman plus an explicit CJK fallback, 0.5 / 0.75 /
1.0 / 1.5 pt line widths, ticks in, enclosed spines, frameless legends, `Title (unit)` axis
labels, and Scientific colour maps by semantic type.

Font sizes are checked at their **final physical size** — a panel scaled to 60 % is judged on
`fontsize × 0.6`, not on what the script asked for. Findings come in four levels: `error`
blocks export until you explicitly confirm, `warn` is always shown, `not_verifiable` is what
we honestly cannot check (text inside an external bitmap) and needs a human, and `suggestion`
never decides anything for you. Everything, including the confirmation, is written into the
proof report next to the exported files.

Journals with their own widths need an override, not a fork:
`{"widths_mm": {"double": 178}}` — the rest is inherited, and the override is recorded in the
proof report.

## Where your data lives

On your machine. Rendering, composition and export are all local processes; nothing about
your figures or data is uploaded.

| | |
|---|---|
| Documents and autosaves | `~/Library/Application Support/Tavotto/` (Linux `~/.local/share/tavotto/`, Windows `%LOCALAPPDATA%\Tavotto\`) |
| Exports, canvas files and version history | Inside your project, in one `tavottofile/` folder: exports in `tavottofile/export/`, named canvases alongside them, version history in `tavottofile/versions/`. Visible, backupable, and synced with your figures. Files written by older versions stay readable where they were. |
| Your scripts and figures | Read-only, unless you explicitly choose "write back to original file" — which can be locked off per project |
| The only outbound request | A once-a-day check for a new release — plus the download itself, if you accept an update in the desktop app. Both stop when you turn the check off in Settings → Check for updates. |

## Good to know

- **The first open of a figure runs your script.** Light figures take a second; heavy
  ones take as long as they normally do. Every edit after that is sub-second.
- **Rendering needs a Python that can import what your scripts import.** Where that
  Python comes from depends on how you installed Tavotto:

  | Install | Interpreter used for rendering |
  |---|---|
  | Windows `.exe` | The **bundled runtime** that ships inside the installer — CPython 3.13 with numpy, matplotlib, pandas, scipy, seaborn and Pillow at pinned versions. Nothing to install, nothing to download. |
  | macOS `.dmg` (arm64) | The same **bundled runtime**, same pinned versions. No Homebrew, Conda or Xcode needed. |
  | PyPI with the `[worker]` extra | The environment you installed it into. |

  Tavotto picks in this order: `TAVOTTO_WORKER_PYTHON` → the interpreter you chose in
  Settings → the bundled runtime → its own interpreter → a Python/Conda it finds on the
  machine. **Whatever you choose explicitly always wins**, and Tavotto only *launches*
  the environment you point it at — it never installs anything into it, and never
  modifies an existing Python or Conda. The bundled runtime is likewise never written
  to: bytecode and the Matplotlib font cache go to Tavotto's own data folder, so the
  installed app stays byte-identical (on macOS, writing into it would break the code
  signature).

  The bundled runtime covers the common scientific stack — **it is not a promise to
  cover whatever your scripts import**. If a script needs a package it does not have
  (rdkit, astropy, your lab's own library), Tavotto says which package is missing and
  offers to switch to your own environment under **Settings → Rendering environment**;
  it will not install that package for you, into its own runtime or into yours.
  Without any working interpreter, layout, annotation and export still work — only
  in-figure editing needs one.

  **Settings → Privacy, diagnostics and About** shows which interpreter is in use, where
  it came from (`bundled`, `configured`, `system`, …), and — for the bundled runtime —
  its Python version and the exact pinned version of every package, read from the
  `runtime-manifest.json` that ships beside it. The same information is in the
  diagnostics bundle.

- **Desktop installers are large: ~180 MB to download, ~490 MB installed** (measured
  on macOS arm64; v0.7.0, without the bundled runtime, was 62 MB / 131 MB). The
  difference is the runtime: CPython plus numpy/scipy/pandas/matplotlib and their
  compiled extensions. It is the price of "install and render", paid once, offline.
  The PyPI install stays a few MB because it reuses the Python you already have.

## Development

```sh
.venv/bin/python -m pytest        # backend
cd web && pnpm test               # frontend
cd web && pnpm build              # type-check (tsc -b) + bundle

# Desktop builds (macOS and Windows): build the bundled rendering runtime first.
# Versions are pinned per platform/arch in packaging/runtime-lock.json; the script
# verifies the CPython download's SHA-256, checks every installed version against
# the lock, then imports each package with the freshly built interpreter and draws
# a real PDF. Any step failing fails the build.
python scripts/build_worker_runtime.py              # picks the target for this host
python scripts/build_worker_runtime.py --list-targets
python scripts/build_desktop.py                     # full desktop chain (includes it)
```

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for
how to verify a change and which boundaries the codebase keeps deliberately. When
reporting a bug, **Settings → Privacy, diagnostics and About → Download diagnostics
bundle** collects everything usually needed, with keys and personal paths redacted.
Security issues go through
[private reporting](https://github.com/Tavotto/Tavotto/security/advisories/new),
not a public issue.

## License

[AGPL-3.0-only](https://github.com/Tavotto/Tavotto/blob/main/LICENSE).

Using Tavotto, modifying it, and running it inside your lab are all unrestricted, and
**the figures and PDFs you produce with it are entirely yours** — the licence does not
reach your work. The obligations apply to distribution: if you give a modified Tavotto
to others or run it as a network service for them, the corresponding source has to be
available to those users.

## Star history

<a href="https://www.star-history.com/?repos=Tavotto%2FTavotto&type=date&legend=bottom-right">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Tavotto/Tavotto&type=date&theme=dark&legend=bottom-right&sealed_token=N_HkVy3WXmZ-L-LdXjq8yjVIGq3O6NWzfAI0NxRWdgJomReAYwu9qlvk78IdfeG8loxZTvRLP_VjiVIrO3ZIrfe8yEzeeklvUfkoRjpWy1Zm5SazecpETgwnZyseVroitCM5lhCLnTU7dorXRnk3FnU34Auy9YsfWrfmlPEb0IP0Sjwaz_7q47jCFt4C" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Tavotto/Tavotto&type=date&legend=bottom-right&sealed_token=N_HkVy3WXmZ-L-LdXjq8yjVIGq3O6NWzfAI0NxRWdgJomReAYwu9qlvk78IdfeG8loxZTvRLP_VjiVIrO3ZIrfe8yEzeeklvUfkoRjpWy1Zm5SazecpETgwnZyseVroitCM5lhCLnTU7dorXRnk3FnU34Auy9YsfWrfmlPEb0IP0Sjwaz_7q47jCFt4C" />
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Tavotto/Tavotto&type=date&legend=bottom-right&sealed_token=N_HkVy3WXmZ-L-LdXjq8yjVIGq3O6NWzfAI0NxRWdgJomReAYwu9qlvk78IdfeG8loxZTvRLP_VjiVIrO3ZIrfe8yEzeeklvUfkoRjpWy1Zm5SazecpETgwnZyseVroitCM5lhCLnTU7dorXRnk3FnU34Auy9YsfWrfmlPEb0IP0Sjwaz_7q47jCFt4C" />
  </picture>
</a>
