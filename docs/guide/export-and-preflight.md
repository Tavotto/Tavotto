# Export formats, preflight levels and the publication profile

> Part of the [Tavotto README](../../README.md). This page holds the detail the README links to.

## Export for publication

PDF export embeds each original vector panel as it was drawn, so **the text stays
real, selectable, searchable text**. SVG is also offered (text stays editable by default). PNG and TIFF (lossless, Deflate-compressed, with
the DPI written into the file) are rasterised from that same PDF, so the raster shows
exactly what the PDF contains. Two deliberate exceptions: a panel with opacity below 1,
or with a flip applied, is embedded as a bitmap at your export DPI — PDF vector content
supports neither. EPS is written by matplotlib itself, so it is available when you
export a single figure that has a script, at its original size; the canvas
composition and script-less figures cannot produce EPS, and Tavotto says so instead
of wrapping a bitmap in PostScript.

Before anything is written, Tavotto checks the figure against a publication profile
and tells you what a reviewer would have told you three weeks later:

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/preflight.png" width="82%"
       alt="The export dialog: output as whole page or each figure, PDF and PNG at 600 ppi, and the pre-export checks against the default spec showing 1 to review and 3 not verifiable.">
</p>

Font sizes are judged at their **final physical size** — a panel placed at 60% is
checked against `fontsize × 0.6`, not against what the script asked for. Findings come
in four levels, shown as chips in the export dialog: **to fix** (blocking), **to review**
(warning), **not verifiable** and **suggestion**. Nothing locks the Export button. **Not verifiable** is what genuinely cannot be checked —
text inside an imported bitmap — and needs a human. **Suggestion** never decides
anything for you. Findings that are still unresolved when you export (blocking and not
verifiable ones, or a check that could not run) are recorded automatically in a style-check
report written next to the exported files; the report can also be switched on for clean runs.

## Publication profile and preflight

The rules live in one versioned JSON file
([`src/tavotto/profiles/publication.json`](../../src/tavotto/profiles/publication.json))
that both the Python engine and the TypeScript frontend read, so there is no second
copy to drift.

The default `lab-publication-v1` encodes 80 mm single / 150 mm double column; 16:9,
4:3 and 1:1 aspect ratios; 9 pt body text with a single hard floor of 8 pt (inclusive) on
final effective size; ≥ 300 dpi rasters; Times New Roman with an explicit
CJK fallback; 0.5 / 0.75 / 1.0 / 1.5 pt line widths; ticks in, enclosed spines,
frameless legends; `Title (unit)` axis labels; and Scientific colour maps by semantic
type.

A journal with its own widths needs an override, not a fork:
`{"widths_mm": {"double": 178}}` inherits the rest, and the override is recorded in
the proof report.
