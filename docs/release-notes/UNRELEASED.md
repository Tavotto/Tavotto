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

## Codex: normalize an existing figure without redrawing it

When a figure already exists and only needs a new width, font, or a font-size
floor, the Codex skill now routes the request to a new MCP tool,
`tavotto_normalize_figure`. It records a baseline, changes only the named targets
(width alone keeps the original aspect ratio; `min_font_pt` lifts only text below
the floor), detects clipping, overlapping text, and legends covering data on the
real render, makes bounded margin / legend adjustments only when measurements
require them, exports, and verifies the delivered file itself (PDF page size and
embedded fonts, PNG pixels and dpi). If the constraints cannot be met, the
session returns to its baseline and no file is written; the response names the
conflict and the smallest constraint to relax. A requested font that is not
installed is reported as `font_unavailable` instead of being silently
substituted.

Tick labels gained a `fontfamily` override, and every text element in the
manifest now reports the face that actually draws it (`face`, and `math_face`
for mathtext such as log-axis `10^4`).

Release housekeeping: the plugin bridge now imports four engine modules that
first ship in this release (`artifactcheck`, `figcapture`, `interference`,
`normalize`), so `MIN_TAVOTTO_VERSION` in `scripts/make_plugin_manifest.py`
must be raised to this release's version when tagging.


## Clicking a spine band while a child element is selected now selects the axes

Clicking the inner or outer band of an axes spine toggles that side's inward /
outward ticks and moves the selection to the axes so the tick card appears. When
a scatter series, legend, or text belonging to that axes was selected, the ticks
toggled but the selection stayed on the child element: the tick card did not
appear and the child's outline remained on the overlay. The selection now stays
put only when the axes itself or that side's tick group is already selected
(issue #343, second item).

## Package job progress no longer bleeds across projects

Starting an install, update, or uninstall in one project and then switching to
another showed the first project's progress bar and cancel button on the second
project's Packages page (the backend rejects a cancel from another project, so
the button acted on a job that page could not see), and the job's completion
refreshed the second project's package list and environment state. Progress is
now stored per owning project: the other project shows nothing, switching back
shows the job again with a working cancel button, and completion side effects
run only when the owning project is the one open (issue #309).
