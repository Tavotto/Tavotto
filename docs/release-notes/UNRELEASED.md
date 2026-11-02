<!--
待发条目：已经合进 main、但还没有任何一版告诉用户的行为变更与迁移提示。

写在这里而不是留在 PR 正文里，因为发行说明是发版那天写的，写的人不会回头
翻每一个 PR 的「遗留」段——issue #244 就是这么漏掉的。

发版时（RELEASING.md 第 2 步）把下面的 `## ` 段落搬进
`docs/release-notes/vX.Y.Z.md` 并从这里删掉；带着没搬走的段落打 tag，
release.yml 的「拼 release body」当场红（scripts/check_pending_release_notes.py）。
这段注释留在原处。


v0.18.0 的五条用户可见说明已迁入 v0.18.0.md。渠道启用在 v0.18.0 的 plugin-stable promote
成功并核验后走独立文档 PR；技能源码的安装说明只随后续插件产物分发，不得重打包或替换
已验证的 v0.18.0 发行 zip，也不得绕过正式发布器改写 plugin-stable。

英文写，与 release notes 一致：**按症状和触发条件写，不要按提交写**。
-->

## Resolution is shown as dpi everywhere, matching journals and matplotlib

The canvas panel's export summary, the export and profile settings, the "Min resolution" value in the journal rules, and the notes in the export check used to mix "ppi" and "dpi" for the same quantity. They all say "dpi" now, as journal guidelines and matplotlib's `savefig` do. Only the wording changed: the checks, the saved settings and the exported files are the same.

## The fix assistant can correct a data file you linked by mistake

If a script could not find its data and you pointed Tavotto at the wrong file, the next run failed with a reading error (for example `JSONDecodeError`), and the fix assistant could find the right file but was not allowed to replace your earlier choice. It now sees which file each data link points to and can re-point a wrong link to the right file after you confirm. Choosing a different file for the same path in the missing-data dialog also always takes effect now. Your script and your data are never changed.

## Scripts that write their SVG by hand now get "Make editable version"

A script that uses no plotting library and builds the SVG text itself (for example joining `<svg>` / `<rect>` strings and writing them to a `.svg` file) used to end with "no figure was produced, the data file was probably not found", and the fix assistant had no way to hand it over for conversion. Tavotto now recognises this case: the card says the figure was drawn as SVG and offers **Make editable version**, which writes a separate Matplotlib script for you to compare and accept. Your original script is never changed.

## Icon animations no longer overlap themselves

Several interface icon animations had parts passing through each other while playing (the sheet in the folder icon poked out of the front panel, the two squares in the group icon merged, the envelope flap flipped through the top edge, the sun in the image icon crossed the mountain, the small sparkle touched the big one). Each animation now starts from the state before the action and ends at the resting icon without any parts crossing: lock drops its shackle in and unlock pops it out by the same distance, disconnect pulls the plug out of its socket, and distribute slides one block to even spacing. The disconnect, save and unlock icons were redrawn, and clicking "Disconnect" now plays the unplug motion.

## Converting a script in a project with very long file paths no longer fails on Windows

On Windows without long-path support, "Make editable version" for an R / MATLAB / Python script could fail straight away with a generic error when the project's data files sat in deeply nested or long-named folders: the working copy Tavotto prepares for the assistant went past the 260-character path limit. Tavotto now copes with long paths while preparing it. If a path still cannot be used, the card says a file path in the project is too long and the conversion did not start, instead of an unexplained error.

## Installing a missing package no longer leaves the figure missing another one

When a script ran in Tavotto's built-in Python and stopped on a package it doesn't include (for example scikit-learn), installing it created a fresh environment for the project that could lack packages the script had been using all along from the built-in one, such as pandas. The next run then stopped again with "pandas is missing", one package per round. This happened mostly when the script imports its own helper module from a folder it adds to `sys.path` (including `Path(__file__).parents[1]` and folders above the project). Tavotto now reads helper modules in those folders when it plans the install, a few levels above the project at most and never your home folder or anything above it. When it still can't see all of the script's imports, the new environment also gets the built-in packages (pandas, SciPy, seaborn, Pillow). The confirmation lists every package before anything is installed.
