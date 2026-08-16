<p align="center">
  <img src="https://raw.githubusercontent.com/erwanjun/magplot/main/assets/readme/hero.svg" width="100%"
       alt="Magplot — arrange matplotlib panels on a page, edit the elements inside them, export a true-vector PDF">
</p>

<p align="center">
  <b>English</b> · <a href="https://github.com/erwanjun/magplot/blob/main/README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/erwanjun/magplot/releases"><img alt="Release" src="https://img.shields.io/github/v/release/erwanjun/magplot?style=flat-square&color=4a63d8&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/erwanjun/magplot/ci.yml?branch=main&style=flat-square&labelColor=1b1b18"></a>
  <a href="https://github.com/erwanjun/magplot/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0--only-4a63d8?style=flat-square&labelColor=1b1b18"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%20–%203.13-1b1b18?style=flat-square&labelColor=1b1b18">
  <img alt="Platform" src="https://img.shields.io/badge/macOS%20·%20Windows%20·%20Linux-1b1b18?style=flat-square&labelColor=1b1b18">
</p>

The last mile before submission usually goes like this: the plots are done, but now
they have to become Figure 1 — resize the fonts, move the legend, align everything.
So you go back to Python, change a line, re-run the script, look again. Twenty times.
Or you drag the PDFs into Illustrator and lose the connection to your code for good.

**Magplot lets you edit the figure directly.** Drop your matplotlib panels onto a page
and arrange them freely. Double-click any panel and you can select the things inside
it — title, axis labels, curves, legend — then change the font size, the colour, or
just drag them. Python re-renders in the background as you go (~40 ms once warm).

Every change is **non-destructive**: your script is never modified, and anything can
be undone. On export the engine re-renders each panel at full quality and composes a
PDF whose text is still real, selectable vector text.

<p align="center">
  <img src="https://raw.githubusercontent.com/erwanjun/magplot/main/assets/readme/workbench.png" width="100%"
       alt="The Magplot workbench: an element tree on the left listing the title and axis labels, three panels arranged as (a)(b)(c) on the page, and the properties of the selected title on the right">
</p>

<p align="center"><sub>Left: elements inside the figure · Middle: a 150 × 130 mm page · Right: properties of the selected title — the source file is still <code>fig1_kinetics.py</code></sub></p>

## Install

Same command on all three platforms. [pipx](https://pipx.pypa.io/) is recommended —
it keeps Magplot in its own environment, away from the one you do research in:

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
| **Axes** | Tick groups, axis lines, grid, 3D viewing angle (elev/azim/roll), 3D axis arrows and panes |
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

## AI assistant (optional)

The assistant panel can hand a request to the **Codex or Claude CLI** on your machine to
edit the script itself — for example "move the legend to the top left and make it 7 pt".
Your script is snapshotted first; afterwards you see the diff and the figure re-renders,
and one click reverts it. Everything else works without these tools installed.

## Where your data lives

On your machine. Rendering, composition and export are all local processes; nothing about
your figures or data is uploaded.

| | |
|---|---|
| Documents and layouts | `~/Library/Application Support/Magplot/` (Linux `~/.local/share/magplot/`, Windows `%LOCALAPPDATA%\Magplot\`) |
| Your scripts and figures | Read-only, unless you explicitly choose "write back to original file" — which can be locked off per project |
| The only outbound request | A once-a-day check for a new release, which you can turn off in Settings → Check for updates |

## Good to know

- **The first open of a figure runs your script.** Light figures take a second; heavy
  ones take as long as they normally do. Every edit after that is sub-second.
- **A Python with matplotlib is required** for rendering. The `[worker]` extra brings
  one along, or point `MM_WORKER_PYTHON` at your own.

## Development

```sh
.venv/bin/python -m pytest        # backend
cd web && pnpm test               # frontend
cd web && pnpm tsc --noEmit && pnpm build
```

Issues and pull requests are welcome.

## License

[AGPL-3.0-only](https://github.com/erwanjun/magplot/blob/main/LICENSE).

Using Magplot, modifying it, and running it inside your lab are all unrestricted, and
**the figures and PDFs you produce with it are entirely yours** — the licence does not
reach your work. The obligations apply to distribution: if you give a modified Magplot
to others or run it as a network service for them, the corresponding source has to be
available to those users.
