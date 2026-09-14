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

## Python 3.14

`pip install tavotto` (and `pipx install "tavotto[worker]"`) on Python 3.14 used
to install **0.8.0** silently: every release after 0.8.0 declared
`requires-python <3.14`, so a 3.14 interpreter — now the default `python3` from
Homebrew and the default for pipx on those machines — could only resolve the one
old version without that upper bound. The Codex plugin then reported
`engine_too_old`, and `pipx upgrade tavotto` resolved to 0.8.0 again.

This release accepts Python 3.14 (`requires-python >=3.10,<3.15`), so 3.14
installs the current version. The CI backend suite now runs on 3.14 alongside
3.10 and 3.13, and the wheel is installed into a clean 3.14 environment and
started as part of the package smoke. The desktop builds still bundle their own
3.13 runtime; nothing changes there. Project environments on 3.14 are now
accepted by the environment check instead of being refused as unsupported.


## Desktop installers and the GitHub Release now carry the project LICENSE

The Windows installer and the macOS app now ship the project licence (AGPL-3.0
full text) alongside the application: on Windows it lands in the installation
directory next to `Tavotto.exe`, on macOS in `Tavotto.app/Contents/Resources/`.
Every GitHub Release also attaches `LICENSE` as a separate asset next to
`SHA256SUMS.txt`, so the licence can be read without opening the source
archive. Nothing changes in the installer flow: there is still no licence page
to click through.
