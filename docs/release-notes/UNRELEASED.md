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

## Added

**TIFF files show up in the asset library.**
Trigger: a project folder contains `.tif` / `.tiff` figures (a common journal
submission format). Before: they were silently left out of the asset library —
and a script that saved only a TIFF made its figure disappear from the library
altogether. Now they are listed, previewed, placed on the canvas and exported
like PNG / JPEG. Supported: 8-bit and 16-bit (reduced to 8-bit, no automatic
contrast stretch) grayscale / RGB with or without alpha, 8-bit palette and
1-bit black-and-white images; uncompressed, LZW, Deflate, PackBits, JPEG and
CCITT G3/G4 compression. Multi-page TIFFs use the first page, as multi-page
PDFs do. Floating-point or signed pixels, CMYK / Lab, 32-bit samples, other
compressions (ZSTD, WebP, JPEG 2000), BigTIFF and planar TIFFs are not
supported: they are listed under "These files can’t be used" with the reason
and how to re-save them, instead of being drawn wrong. Writing edits back into
the original file still works only for PDF / PNG figures; for a JPEG or TIFF
figure it now says so instead of reporting success without changing the file.

**Settings → Diagnostics → Performance records where drag stutter comes from.**
Trigger: dragging figure sub-elements or canvas objects feels choppy on a
particular Mac. Before: there was no way to show *where* the time went on that
machine — the developer's Mac could not reproduce it. Now "Start" opens a small
probe panel on the canvas: drag as usual, optionally run the auto test (two
synthetic 3-second drags on an object you click, always cancelled, so the
document and undo history are untouched), then "Finish and save". The report
is a numbers-only JSON (frame times split into input handling / rendering /
unattributed, render counts, SVG size, Mac model, power and thermal state — no
file names, figure text or paths), saved to `perf-reports/` in the Tavotto data
folder and shown in Finder. Nothing is uploaded. Developers read it with
`python scripts/perf_report.py report.json --html out.html`. (ADR 0075)

**Export jobs carry a bounded phase trace that names the failing step.**
Trigger: any export (`POST /api/export`, or `tavotto_export` over MCP)
whose status you read back. Symptom before: a `partial` or `failed` job
told you *that* it failed and, at best, the error code of one output;
where in the pipeline it broke (source, compile, compose, raster,
inspect, publish, report) was not recorded. Now the job status has a
`trace`: one event per phase (phase, elapsed ms, outcome, error code),
capped at 64 events (the middle is dropped, head and tail are kept and
`truncated` says so), and `failed_phase` / `failed_code` point at the
*first* failure — later knock-on failures do not overwrite it. A job whose
figures all succeeded but whose style-check report could not be written is
`partial` with `failed_phase = "report"`. Existing keys are unchanged. (ADR
0071)

**Preparation results carry a complete execution receipt, and a plan that
no longer matches the world is refused with a named reason.** Trigger:
opening or re-preparing a figure through the preparation API (`POST
/api/engine/preparation`). Symptom before: the receipt described the
interpreter Tavotto *intended* to use; a health-check or probe result could
be mistaken for the report of the process that actually ran the script;
and a plan made before the working-directory grant, the interpreter choice
or the script's data changed was still executed. Now the receipt's runtime
section is accepted only from the process that ran the script
(`report_origin = build`, and its pid — or, on Windows where a virtual
environment's `python.exe` is a launcher, the launcher's child — matches
the one the control plane started; anything else is recorded as
`runtime_rejected` and the receipt is `partial`), it lists the project
files the script opened through Python (`inputs`, always `partial`: native
readers such as h5py are named as unobserved, never guessed), and
`binding_check` compares them with the data the plan was made against.
Before a session is started the plan is re-checked against the grant, the
interpreter decision and the data binding; any mismatch is
`preparation_plan_stale` with a `reason`, and nothing runs. A live session
whose data changed after it ran keeps showing the figure it made, marked
as an old snapshot (`binding_check.matched = false`) until you rebuild;
your edits are kept. (ADR 0070, 0071)

**Dragging inside a figure snaps, and legends resize from their corners.**
Trigger: dragging text, an axis label, a legend or a subplot inside a
figure. Before: nothing snapped — only canvas objects did — so lining up
"Vacuum" with "Superconductor" or two axis labels was done by eye, and a
legend could only be resized one font-size / spacing field at a time in the
inspector. Now in-figure drags snap to other elements' left / centre / right
and top / middle / bottom edges and to the figure's centre lines, with the
same guides as canvas objects; hold ⌘ or Ctrl to drag without snapping. A selected legend has four corner handles: drag one to scale the
whole legend with the opposite corner fixed and a live preview; one drag is
one undo step. Moving the main plot together with a colorbar (⇧-click to
multi-select) now carries labels you had moved, such as "(a)", the same way
dragging the plot alone already did. (#575)

## Changed

**A legend's font size now scales the whole legend box, as
`ax.legend(fontsize=…)` does in matplotlib.** Trigger: setting a legend's
font size in the inspector, dragging a legend corner, or applying a paper
style / publication profile that sets legend font size (the built-in style
does, for every legend). Before: only the entry text changed size; the
padding, row spacing, handle length, handle–text gap, column spacing and
minimum row height stayed at the script's size, so the box did not scale
with its text and corner-dragging overshot or undershot. Now all of those
follow the font size. Border padding, label spacing, handle length, handle
text padding and column spacing can still be set on their own in the legend's
layout details, on top of the new size. The legend title keeps its own size
(`title_fontsize`), as in matplotlib. **Migration:** documents saved with a
legend font size override — set by hand, or by applying a style or profile —
redraw that legend with a proportionally larger or smaller box when opened
(for example a 10 pt legend set to 7 pt: 97 × 42 px → 82 × 33 px at 100 dpi).
Text size is unchanged; the right and top edges of a dragged legend move.
Adjust the spacing fields if you need the old look. (#579, ADR 0034)

## Fixed

**A legend placed with a preset location no longer jumps the first time it
is dragged.** Trigger: a legend anchored with `loc="upper right"` and the
like, dragged for the first time. Before: it landed a few pixels away from
where you let go (about 6 px for a 6.9 pt legend), because Tavotto measured
text on a 100 dpi bitmap renderer while the canvas draws vector SVG. Now
text is measured the way the canvas draws it, so the legend stays where you
drop it and selection boxes, hit areas and snap guides of in-figure text
line up with the glyphs. Bitmap preview and PNG export still lay out for
pixels. (#576, #579)

**Letting go of a drag no longer flashes "Rendering…".** Trigger: releasing
a drag of a figure element. Before: the "Rendering…" badge lit up on every
release and the figure could blur for a moment while the new version's
embedded images decoded. Now the badge appears only when an ordinary
re-render takes longer than 700 ms (a cold start still says so at once), and
the previous picture — at the dragged position — stays until the new one's
images are decoded. (#575)
