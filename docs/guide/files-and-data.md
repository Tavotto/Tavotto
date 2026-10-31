# Where your files live

> Part of the [Tavotto README](../../README.md). This page holds the detail the README links to.

## Where your files live

<details>
<summary>Data directories and what goes in them</summary>

The app uses four nouns. A **project** is your script folder. A **layout** is a Tavotto
file that can hold several canvases; one project can have several layouts. A **canvas**
is one figure page inside a layout. A **project package** is an exported single
`.tavotto` file for sharing.

| Action or list in the app | Where it goes | What it holds |
|---|---|---|
| **Save layout** (⌘S / Ctrl+S; autosave uses the same slot) | `layouts/_autosave/` in the data folder on this computer: macOS `~/Library/Application Support/Tavotto/` · Linux `~/.local/share/tavotto/` · Windows `%LOCALAPPDATA%\Tavotto\` | The layout you're editing, all canvases included. It lives on this computer, **not in the project folder** |
| **Save layout as…** (⇧⌘S / Ctrl+Shift+S) | `tavottofile/<layout name>.json` inside the project (`layouts/` in the data folder when no project is open) | A named copy of the layout as it is right now, backed up and synced with the project. Later ⌘S saves don't update it; save it again under the same name to do that |
| Top-bar menu "Layouts in this project" → Open layout… | The project's `tavottofile/` (files older versions wrote to the project's `canvases/` or the data folder's `layouts/` are listed too, read-only) | The layouts saved into the project |
| Top-bar menu "Recent layouts on this computer" | An index on this computer + `layouts/_autosave/` in the data folder | Layouts recently edited on this computer, across projects; ones from another project are marked with its name |
| Layout versions… | `tavottofile/versions/` inside the project | Version snapshots of each layout |
| Export (figures) | `tavottofile/export/` inside the project (configurable per project) | The exported figure files |
| Export project package | Same folder, `tavottofile/export/<name>_<time>.tavotto` | The current layout + the assets it uses + their source scripts + a checksum manifest |
| Import project package… | Nothing is written into the project | Opens the layout in the package as a new layout (saved on this computer like any other); assets are never installed into the project automatically |
| Your scripts and figures | Read-only, unless you explicitly choose "write back to the original file" — which can be locked off per project | |

</details>
