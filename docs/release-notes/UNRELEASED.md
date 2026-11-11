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

## SVG assets can be exported with a layout or at their original size

SVG assets can now be included in PDF, PNG, TIFF and SVG layout exports, and exported at their own size with margins or annotations. Formats exported together use the same source version, so a file changing during export cannot silently produce different pictures. Each format reports SVG features it cannot render while other successful outputs remain available.

## Batch figure exports keep each job and filename separate

Starting a batch after another export no longer lets the previous result skip figures or bypass filename conflicts. Figures with the same output name keep the normal rename, replace and cancel choices. Runtime figures without a saved display name receive a normal default filename while keeping their original source identity.

## Original-image exports can find files hidden by a same-name PDF

A PNG already placed on a layout remains available for original-size export when the asset list prefers its same-name PDF. Exports preserve the image's pixels and declared density. Raster exports of a static PDF report its actual original page size instead of an outdated document size.

## Install computational-chart project packages in a new folder

The project menu and project list can inspect a computational-chart project package and install its resources into a new folder. Opening the installed project preserves your previous draft and saves a working copy through the normal save flow. If you close the installation window while save confirmation is pending, reopening the installation record continues with your latest draft instead of replacing it with the package's original layout. Installation does not run the included scripts.

## The desktop app's AI repair can run protected commands again

On the desktop app, when the AI repaired a script it could not run commands in its protected mode and always answered that the sandbox was unavailable, and its trial runs silently lost their operating-system sandbox. The app tested the sandbox by asking itself to run a one-line Python command, which the packaged app does not understand. It now tests with the built-in Python (or a plain system program when that is not installed yet). Nothing changes when you run Tavotto from source.

## The assistant shows what it is doing with small animated dot orbs

In the figure assistant, the line that says what the AI is doing now starts with a small animated dot orb instead of a static icon. The orb changes with the step: one shape while it thinks, another while it runs your script, another while it reads or edits it, and another between sending your request and the first reply. Finished steps keep a dimmer, slower orb: a breathing ring when the AI only thought, a rolling sphere when it also did something. The empty assistant panel shows a larger orb above "How should this figure change?". Orbs stop drawing while they are scrolled out of view, and with "Reduce motion" turned on in your system settings they stay still. Nothing else about the assistant changes.

## Installing a missing package no longer leaves the figure missing another one

When a script ran in Tavotto's built-in Python and stopped on a package it doesn't include (for example scikit-learn), installing it created a fresh environment for the project that could lack packages the script had been using all along from the built-in one, such as pandas. The next run then stopped again with "pandas is missing", one package per round. This happened mostly when the script imports its own helper module from a folder it adds to `sys.path` (including `Path(__file__).parents[1]` and folders above the project). Tavotto now reads helper modules in those folders when it plans the install, a few levels above the project at most and never your home folder or anything above it. When it still can't see all of the script's imports, the new environment also gets the built-in packages (pandas, SciPy, seaborn, Pillow). The confirmation lists every package before anything is installed.

## The first-run setup log is redacted, and a failed install says where it stopped

When Tavotto sets up its own rendering environment (creating it, then installing matplotlib), everything written to the setup log is now redacted before it is kept: credentials and query strings in package-index URLs, personal paths and other secrets are removed, and the data folder appears as `<data>`. That log can end up in a feedback bundle, and previously pip's output went in verbatim. If a step fails, the log now ends with one line naming the step (environment, install or import check) and whether it timed out or exited with an error. A timed-out install keeps what pip printed before it was stopped, and a failed import check keeps its error. Nothing changes when setup succeeds. Thanks to @mvanhorn for this contribution (#807).

## A copied script that saves figures with the same names now gets its own figures

If you copied a script to rerun it on new data and it saves figures with the same file names as the original (for example into a different output folder), preparing the copy used to stop with "these figure names already belong to other scripts", and the only way out took the figures away from the original script. Tavotto now links the copy's figures separately, so both scripts' figures can be opened and edited on their own; the asset panel still shows the original file names. The first time you open the copy's figures, Tavotto runs it once more. Only when both scripts write the very same file in the project folder is it still reported as a clash. Your scripts are never changed.

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

## Rendering no longer gets slower as more figures are open

With four or more scripted figures on the canvas, editing one figure could re-render the others in the background one after another. Tavotto kept only three render sessions alive, so every one of those renders restarted a script from scratch (about 15 times slower than a warm render, and 20 to 40 seconds on some Windows machines). Tavotto now keeps up to eight sessions alive when your computer has the free memory (three otherwise), and background re-renders of neighbouring figures no longer push out the figure you are editing. A neighbouring figure that would need a fresh start while the sessions are full simply keeps its previous picture.

## The maker's reply to a problem report now shows up in the app

When you send a problem report and *Contact the maker* is on, the app now also keeps a small record so the maker's reply can appear under *Contact the maker > My letters*, tagged "Report" with the report ID. The dialog says so after sending. Nothing changes if you turn *Contact the maker* off (`TAVOTTO_NO_CONTACT=1`), and a report that gets no reply is forgotten locally after 30 days.

## Scripts that `import pymupdf` can install it in one click

PyMuPDF's current documentation imports it as `import pymupdf` instead of the older `import fitz`. Tavotto only recognised the older name, so a script written the new way that ran in an environment without PyMuPDF got a "missing pymupdf" card with no install button, and the "Specify package…" box was not even filled in. Both names now lead to the same install of PyMuPDF into an environment Tavotto manages, exactly as `import fitz` already did. Your own Python environments are not changed unless you choose them.

## Text boxes added over a figure keep their size when you export that figure

In single-figure editing, a text box, arrow or shape drawn over the figure came out much smaller relative to the figure when it was exported, whenever the figure was shown scaled down on the canvas (for example a large Matplotlib figure fitted into the page). A label set to match the figure's own text looked right on the canvas but ended up about half as large in the exported file, inside a box that was too big for it. Export now scales font size, line width, padding, border and corner radius by the same ratio as the figure, so the annotation looks the same relative to the figure as it did on the canvas. The figure's own text is unchanged, and nothing changes when the figure was shown at its original size.

## In a multi-figure layout, dragging a figure's edge crops it instead of stretching it

Dragging one of the four side handles of a figure in a multi-figure layout used to stretch the figure in one direction, which squashed its text and markers. It now crops that side: the figure stays where it is and at the same scale, the handle stops at the figure edge and again at the edge of the plotted content, and you hold ⌥ (Alt on Windows) to cut into the content. Corner handles still scale the whole figure proportionally; while you drag, a small capsule shows the scale and the resulting text size in pt, and turns amber when the text falls below your journal's minimum. Annotations that sit on the figure (panel letters, arrows, text boxes) now scale with it; turn this off with **With annotations** in the figure's toolbar. Crops already in your layouts are unchanged; **Reset crop** in the toolbar puts a figure back to its full size.

## The first click on a figure after opening a project no longer waits for a cold start

On a slow disk or a Microsoft Store Python, the first render after opening a project could take 20 to 40 seconds, because the render session had to start (and the interpreter had to be checked) at that moment. Tavotto now starts the session for the first script-backed figure quietly in the background once the project has settled, and checks each interpreter once instead of once per task running at the same time. Nothing is run, drawn or written in the background, a failure is ignored, and your scripts and files are never touched.

## The first picture after launch appears a little sooner

The first time you render after starting Tavotto, the app checks your Python environment with several small probes. Probes that don't depend on each other now run side by side instead of one after another, which trims a fraction of a second from that first render on a typical setup. Later renders are unchanged, and Tavotto still never picks a different Python on its own.

## In single-figure editing, drag the canvas edges to add margin without resizing the figure

Dragging an edge of the figure in single-figure editing used to change the figure size, so the plot area stretched or squeezed. Edges now change the canvas around the figure instead: drag outward to add white margin (for example to make room for labels, or to bring the canvas to a journal column width, which it snaps to), drag inward to remove it. Dragging inward stops at the figure edge and again at the edge of the plotted content ("tight"); hold ⌥ (Alt on Windows) to crop into the content. Drag a corner to resize the figure itself, proportionally (hold ⇧ for a free aspect ratio); text keeps its pt size and the figure re-renders when you let go. A capsule next to the canvas explains each drag, and **Fit to content** / **Remove margin** appear when the figure is selected. Exports use the figure plus its margin. Margins live in your layout only and are not written back to the script.

## The text box looks like any other input, and its formatting buttons appear only while you edit

In the Quick Edit card and the properties panel, the text box used to carry a permanent row of formatting buttons that crowded the narrow card. At rest it is now a plain input, with a small "TeX" tag at its right edge that switches between plain text and LaTeX source. While you are editing, a floating bar with bold, italic, superscript, subscript, line break and case appears above the box; it disappears when focus leaves. Press Tab in the box to reach the bar, Esc to come back. The Quick Edit header now shows only the kind of element (for example "Title") instead of repeating the text that is already in the box. Shortcuts and behavior are unchanged.

## Text annotations can be rotated to any angle

Select a text annotation and drag the round handle above its frame to rotate it; hold Shift to snap to 15 degree steps. You can also type an angle in the Properties panel. Alignment, snapping and the multi-selection frame now follow the rotated outline instead of the unrotated frame, so a rotated label lines up by what you see. Exports draw the rotated text exactly where the canvas shows it. Existing projects are unchanged until you rotate something.

## The Font row of a figure's text is now one row with two dropdowns

Editing the font of a text element in a figure used to take two rows, "Chinese font" and "English font". They are now a single "Font" row: the English font on the left and the Chinese font on the right. Each dropdown keeps its own name for screen readers, and hovering shows the full font name when it is cut short. When the English font you picked already draws Chinese characters, the Chinese dropdown is greyed out instead of showing an extra info button, and hovering it says why. Either font can be restored on its own: with only one changed, the restore button resets just that one; with both changed, it opens a small menu to restore the English font, the Chinese font, or both at once (one undo step). The Chinese dropdown is narrow and shows a short name (for example "宋体" instead of "宋体-简"); the list and the hover text still give the full name. Nothing about how fonts are saved has changed.

## The properties panel no longer shows the "Figure › Subplot" trail at the top

Selecting something inside a figure used to put a clickable trail of its parents (figure, subplot, group) above the name. It is gone; to go up a level, click the parent in the Figure elements tree on the left. The "n changes" button with its restore menu now sits on the name line, to the right of the name.

## Editing a figure stays on the run it came from

When you edit a figure produced by a script, Tavotto now remembers the exact run that drew it and keeps applying your edits to that run while you continue editing. Saving, reopening, preflight and export all keep the same change; a different script with the same file name can no longer be mistaken for the one that produced the figure. An output snapshot saved earlier is only treated as an archive of what you saw, not as permission to edit a run that is no longer there.

## A moved or deleted script says so instead of naming a package

Running a figure whose script file has been moved or deleted used to report that a package named after the script was missing, which sent you looking for something to install. It now says the script file itself is missing and asks you to put it back. Tavotto never substitutes another file with the same name.

## Saving is not blocked by an unreadable project-file binding

If the server cannot answer the project-file metadata request, the app now treats an unrecognised reply as an older server rather than as a temporary outage. Automatic saves and the last save before switching documents or projects keep working, so the last edit is not lost. A reply that claims the newer contract but cannot be read still fails closed.
