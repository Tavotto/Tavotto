<p align="center">
  <img src="https://raw.githubusercontent.com/erwanjun/magplot/main/assets/readme/hero.svg" width="100%"
       alt="Magplot — arrange matplotlib panels on a page, edit the elements inside them, export a true-vector PDF">
</p>

<p align="center">
  <b>English</b> · <a href="https://github.com/erwanjun/magplot/blob/main/README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/erwanjun/magplot/releases"><img alt="Release" src="https://img.shields.io/github/v/release/erwanjun/magplot?style=flat-square&color=2868b7&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/erwanjun/magplot/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-2868b7?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.13-1b1b18?style=flat-square&labelColor=1b1b18">
  <img alt="Platform" src="https://img.shields.io/badge/macOS%20·%20Windows%20·%20Linux-1b1b18?style=flat-square&labelColor=1b1b18">
</p>

<p align="center"><i>Edit the figure. Keep the source.</i></p>

The last mile before submission usually goes like this: the plots are done, but now
they have to become Figure 1 — resize the fonts, move the legend, align everything.
So you go back to Python, change a line, re-run the script, look again. Twenty times.
Or you drag the PDFs into Illustrator and lose the connection to your code for good.

**Magplot lets you edit the figure directly.** Drop your matplotlib panels onto a page
and arrange them freely. Double-click any panel and you can select the things inside
it — title, axis labels, curves, legend — then change the font size, the colour, or
just drag them. **Dragging and dialling are instant** — the figure follows your
cursor frame by frame, and matplotlib runs once, when you let go, to make it official.

Every change is **non-destructive**: your script is never modified, and anything can
be undone. On export the engine re-renders each panel at full quality and composes a
PDF whose text is still real, selectable vector text.

<p align="center">
  <img src="https://raw.githubusercontent.com/erwanjun/magplot/main/assets/readme/workbench.png" width="100%"
       alt="The Magplot workbench: an element tree on the left listing the title and axis labels, three panels arranged as (a)(b)(c) on the page, and the properties of the selected title on the right">
</p>

<p align="center"><sub>Left: elements inside the figure · Middle: a 150 × 130 mm page · Right: properties of the selected title — the source file is still <code>fig1_kinetics.py</code></sub></p>

## Install

**Download an installer** from the [latest release](https://github.com/erwanjun/magplot/releases/latest)
— `.dmg` for macOS, `.exe` for Windows — install it, and double-click. Magplot opens
in its own desktop window, and updates itself from then on — it checks, downloads,
installs and restarts without sending you back to this page.

**On Windows you do not need to install Python.** The installer ships a private Python
runtime with the usual scientific stack already in it — numpy, matplotlib, pandas, scipy,
seaborn and Pillow, at pinned versions. Rendering works the moment the installer finishes,
with no download and no network. Magplot never touches a Python or Conda you already have;
if a figure of yours needs a package that is not in that list, point Magplot at your own
environment under **Settings → Rendering environment**. See [Good to know](#good-to-know).

On macOS, rendering uses the Python you already have (or one Magplot sets up for you in
its own folder, on request).

**Or install from PyPI**, which works the same on all three platforms:

```sh
pipx install "magplot[worker]"
magplot
```

Your browser opens at `http://127.0.0.1:5089`.

<details>
<summary>Using pip · reusing your own scientific environment · running from source</summary>

**pip** (installs into the current environment):

```sh
pip install "magplot[worker]"
magplot
```

**Reuse the environment your figures were made in.** Drop the `[worker]` extra and
point Magplot at your own interpreter, so figures render against exactly the
dependencies they were written for:

```sh
pipx install magplot
export MM_WORKER_PYTHON=/path/to/your/env/bin/python     # Windows: setx MM_WORKER_PYTHON "..."
magplot
```

**From source** (needs node + pnpm to build the interface):

```sh
git clone https://github.com/erwanjun/magplot.git && cd magplot
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
python scripts/build_frontend.py
.venv/bin/magplot
```

</details>

**Options**: `magplot --figures <dir>` opens a figure directory straight away;
`--port 5089` changes the port; `--no-browser` skips opening a browser.

## Try it

A ready-to-open example project ships with the repository:

```sh
magplot --figures examples/figures
```

Three panels appear in the asset browser. Drag them onto the page, then double-click
one — you get a tree of everything inside that figure, and clicking the title lets you
change its size. `examples/figures/` holds two perfectly ordinary matplotlib scripts;
Magplot does not ask you to write them in any special way.

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
exporting, Magplot checks for panels off the page, overlaps, tiny fonts, low effective
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
magplot open figures/Fig1_kinetics.pdf   # the output file
magplot open figures/fig1_kinetics.py    # or the script — output name is resolved for you
magplot open figures/                    # or the whole figure library
```

It opens the figure's library as a project, adds any missing entries to the script registry,
then launches the **desktop app** (if it's already running the figure goes straight into that
window — no second copy). Without the desktop app it falls back to browser mode.

### Codex plugin

Install it and the matplotlib figures Codex writes come out in a shape Magplot can take over
(script next to its output, vector PDF, statically resolvable output name), and are handed
over automatically when they're done:

```bash
codex plugin marketplace add erwanjun/magplot && codex plugin add magplot@magplot
```

Start a new session afterwards. The CLI and the Codex desktop app share one plugin directory,
so **installing once covers both**; `codex plugin marketplace upgrade magplot` pulls updates.

Legend position, font sizes, line widths and ticks are then a drag or a click away in Magplot —
no need to describe them to an AI again. See [`codex-plugin/README.md`](codex-plugin/README.md);
the distribution roadmap (including the official directory submission checklist) is in
[`docs/codex-plugin-distribution.md`](docs/codex-plugin-distribution.md).

## Where your data lives

On your machine. Rendering, composition and export are all local processes; nothing about
your figures or data is uploaded.

| | |
|---|---|
| Documents and autosaves | `~/Library/Application Support/Magplot/` (Linux `~/.local/share/magplot/`, Windows `%LOCALAPPDATA%\Magplot\`) |
| Exports, canvas files and version history | Inside your project, in one `magplotfile/` folder: exports in `magplotfile/export/`, named canvases alongside them, version history in `magplotfile/versions/`. Visible, backupable, and synced with your figures. Files written by older versions stay readable where they were. |
| Your scripts and figures | Read-only, unless you explicitly choose "write back to original file" — which can be locked off per project |
| The only outbound request | A once-a-day check for a new release — plus the download itself, if you accept an update in the desktop app. Both stop when you turn the check off in Settings → Check for updates. |

## Good to know

- **The first open of a figure runs your script.** Light figures take a second; heavy
  ones take as long as they normally do. Every edit after that is sub-second.
- **Rendering needs a Python that can import what your scripts import.** Where that
  Python comes from depends on how you installed Magplot:

  | Install | Interpreter used for rendering |
  |---|---|
  | Windows `.exe` | The **bundled runtime** that ships inside the installer — CPython 3.13 with numpy, matplotlib, pandas, scipy, seaborn and Pillow at pinned versions. Nothing to install, nothing to download. |
  | macOS `.dmg` | Your own Python; Magplot can also build an isolated one for you inside its own data folder. |
  | PyPI with the `[worker]` extra | The environment you installed it into. |

  Magplot picks in this order: `MM_WORKER_PYTHON` → the interpreter you chose in
  Settings → the bundled runtime → its own interpreter → a Python/Conda it finds on the
  machine. **Whatever you choose explicitly always wins**, and Magplot only *launches*
  the environment you point it at — it never installs anything into it, and never
  modifies an existing Python or Conda.

  If a script needs a package the bundled runtime does not have (rdkit, astropy, your
  lab's own library), Magplot says which package is missing and offers to switch to your
  own environment under **Settings → Rendering environment**. Without any working
  interpreter, layout, annotation and export still work — only in-figure editing needs one.
  **Settings → Privacy, diagnostics and About** always shows which interpreter is in use
  and where it came from.

## Development

```sh
.venv/bin/python -m pytest        # backend
cd web && pnpm test               # frontend
cd web && pnpm build              # type-check (tsc -b) + bundle

# Windows desktop only: build the bundled rendering runtime before packaging.
# Versions are pinned in packaging/runtime-lock.json; the script verifies the
# CPython download's SHA-256 and import-tests every package it installs.
python scripts/build_worker_runtime.py
```

Issues and pull requests are welcome.

## License

[AGPL-3.0-only](https://github.com/erwanjun/magplot/blob/main/LICENSE).

Using Magplot, modifying it, and running it inside your lab are all unrestricted, and
**the figures and PDFs you produce with it are entirely yours** — the licence does not
reach your work. The obligations apply to distribution: if you give a modified Magplot
to others or run it as a network service for them, the corresponding source has to be
available to those users.

## Star history

<a href="https://www.star-history.com/#erwanjun/magplot&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=erwanjun/magplot&type=Date&theme=dark&legend=bottom-right">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=erwanjun/magplot&type=Date&legend=bottom-right">
    <img alt="Star history of erwanjun/magplot" src="https://api.star-history.com/svg?repos=erwanjun/magplot&type=Date&legend=bottom-right" width="100%">
  </picture>
</a>
