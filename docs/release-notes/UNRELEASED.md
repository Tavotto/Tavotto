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

## Installing a missing package no longer leaves the figure missing another one

When a script ran in Tavotto's built-in Python and stopped on a package it doesn't include (for example scikit-learn), installing it created a fresh environment for the project that could lack packages the script had been using all along from the built-in one, such as pandas. The next run then stopped again with "pandas is missing", one package per round. This happened mostly when the script imports its own helper module from a folder it adds to `sys.path` (including `Path(__file__).parents[1]` and folders above the project). Tavotto now reads helper modules in those folders when it plans the install, a few levels above the project at most and never your home folder or anything above it. When it still can't see all of the script's imports, the new environment also gets the built-in packages (pandas, SciPy, seaborn, Pillow). The confirmation lists every package before anything is installed.

## The first-run setup log is redacted, and a failed install says where it stopped

When Tavotto sets up its own rendering environment (creating it, then installing matplotlib), everything written to the setup log is now redacted before it is kept: credentials and query strings in package-index URLs, personal paths and other secrets are removed, and the data folder appears as `<data>`. That log can end up in a feedback bundle, and previously pip's output went in verbatim. If a step fails, the log now ends with one line naming the step (environment, install or import check) and whether it timed out or exited with an error. A timed-out install keeps what pip printed before it was stopped, and a failed import check keeps its error. Nothing changes when setup succeeds. Thanks to @mvanhorn for this contribution (#807).

## A figure-name clash no longer sends you to the AI fix

When a script ran fine but the figures it saved have the same names as figures already linked to another script in the project, the card offered "Use your AI to fix it", and the assistant could not change anything, because the clash is not in the script. The card now offers sending feedback instead, and its message still says where to choose which script a figure belongs to. Your scripts are never changed.

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

## Preparing the environment on Windows no longer fails once at random

On Windows, the first attempt to prepare the private Python for a single-package fix could stop with a "write failed" error when antivirus software or a search indexer briefly held the freshly unpacked files, and only a second click worked. Tavotto now retries that step for a few seconds before giving up, and when it still fails the log records which step failed and the Windows error code.

## Scripts that start another script which prints text no longer fail with a protocol error

If your script started a second Python script with `subprocess.run(...)` (or used a library that writes straight to the console) and that program printed anything, the run failed with `protocol_mismatch` and no figure was captured. That output now goes to Tavotto's render log like the rest of your script's output, and the run completes normally. Your scripts are not changed.

## The fix assistant waits for a running install before switching Python

When the in-app fix assistant tried to create a new Python environment while a package installation for the same project was still running, it failed immediately with "dependency install busy" and often gave up. It now waits for that installation to finish, however long it takes on a slow network or with large packages, and then continues; the preparation card says "Waiting for another installation to finish…" meanwhile, and Stop still works. Only if the installation shows no progress at all for 10 minutes is the assistant told the problem is temporary and can try again, instead of treating it as a dead end. A script that is still running in the environment is still reported as needing you to stop it first.

## The maker's reply to a problem report now shows up in the app

When you send a problem report and *Contact the maker* is on, the app now also keeps a small record so the maker's reply can appear under *Contact the maker > My letters*, tagged "Report" with the report ID. The dialog says so after sending. Nothing changes if you turn *Contact the maker* off (`TAVOTTO_NO_CONTACT=1`), and a report that gets no reply is forgotten locally after 30 days.

## Scripts that `import pymupdf` can install it in one click

PyMuPDF's current documentation imports it as `import pymupdf` instead of the older `import fitz`. Tavotto only recognised the older name, so a script written the new way that ran in an environment without PyMuPDF got a "missing pymupdf" card with no install button, and the "Specify package…" box was not even filled in. Both names now lead to the same install of PyMuPDF into an environment Tavotto manages, exactly as `import fitz` already did. Your own Python environments are not changed unless you choose them.
