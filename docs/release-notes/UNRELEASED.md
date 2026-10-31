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

## Tavotto launched from Codex Desktop (a Windows MSIX app) no longer keeps a separate copy of your data

On Windows, when Tavotto was started from the Codex desktop app, recent projects, settings, managed environments and caches could end up in a private per-package folder (`%LOCALAPPDATA%\Packages\...\LocalCache`), so the app showed a different state than when started from the Start menu. Tavotto now starts itself outside that package environment, when the Codex plugin launches it (the `tavotto open` command now re-runs itself outside the package before probing a script or writing anything) and, as a fallback, when the app notices it was started inside one (the launcher now sees whether the replacement process actually came up, instead of treating the hand-over as success). Data that was already written into the package folder is not moved automatically. Diagnostic bundles now say whether the app was running with a package identity and whether its data folders are under `Packages`.

## Missing helper modules are no longer offered as packages to install

If a script stores its own folder in a variable and then adds a subfolder to `sys.path` (for example `ROOT = Path(__file__).resolve().parent` followed by `sys.path.insert(0, str(ROOT / "source"))`), and that subfolder is missing from the project, Tavotto used to treat the script's own helper modules (such as `config`) as unknown packages and pre-filled a same-named PyPI package for you to install. These names are now reported as missing local modules from that folder: nothing is pre-filled and nothing is sent to pip. Put the missing files back into the folder the script expects and run again.

## Single-figure export now includes canvas annotations (#1551)

When the canvas held a single figure, text, arrows and shapes you added on the canvas were missing from both the export preview and the exported files. They are now drawn into PDF, PNG, TIFF, SVG and EPS, clipped to the figure frame, with their stacking relative to the figure preserved (annotations moved below the figure in the layer tree stay below it). The export still follows the original-figure rules: PNG / TIFF keep the source image's pixel size and DPI, and exports without annotations are byte-for-byte unchanged. EPS keeps matplotlib's original EPS and lays the annotations over it as vector outlines; if an annotation uses transparency, which EPS cannot express, only the EPS item fails with a stated reason and the other formats are delivered.

## Scripts that load a private package folder built for another Python version

If a script puts its own `pip install --target` folder at the front of `sys.path` (for example a `.kegg_python` folder whose matplotlib was built for Python 3.12) and Tavotto runs it with a different Python, the run used to fail with an `ImportError` and the fix assistant kept trying to switch Python versions. Tavotto now recognises this case and offers **Skip this folder and rerun**: for that project, imports no longer come from that folder. Your script and the folder are left untouched, and the fix assistant uses the same remedy instead of changing Python.

## "More" menu is grouped, command palette and settings wording cleaned up

The top-bar "..." menu is split into File / View / Help groups with small headings, and a disabled write-back item now explains why in its tooltip instead of a second line. The command palette shows an icon for every command and draws its shortcut as one plain grey key label, and "Add (a)(b)(c) labels" is renamed "Add (a)(b)(c) numbering". In Settings, "Interface looks off?" is now "Reset interface layout", the "Open canvas settings" row is gone, and the appearance note explains the canvas paper stays white in dark mode to match exports. The welcome page drops the smiley and its clickable-looking feature row, and the tutorial shows progress only at the top, labels the first step's back button "Back to welcome", and says what is left to do on a disabled "Next" (for example "Drag in the script to continue"). (#1556)

## Overwrite confirmation always matches the file actually overwritten (#1549)

If an export stopped at the "file already exists" confirmation and you then changed the file name, format, scope or export folder, clicking Overwrite replaced a different existing file than the one the confirmation listed. Changing any of these now cancels the confirmation and the export starts over with a fresh conflict check. Batch exports behave the same way when the export folder changes.

## A rewritten script no longer fails with a missing entry function

When a project was opened while a script still had a plotting function, Tavotto registered that function as the script's entry. If you later rewrote the script to draw at the top level (no function at all), every run failed with `AttributeError: module has no attribute ...` and the AI repair could not change the entry. Before a registered script runs, Tavotto now checks the registered entry against the script as it is now; when the function is gone it re-detects the entry (usually running the whole script) and updates only the `entry` field of that registry line. If an entry still cannot be found, the card says so with its own code (`script_entry_missing`), and the AI repair has a `fix_entry` tool that does the same re-detection.

## Cold canvas and export preview show the state you saved after an undo (#1547)

For script-run figures, undoing an edit (for example moving a legend back) and then saving could make the reopened canvas and the export preview show the state from before the undo, until you entered figure editing. The exported files were already correct. Cached previews are now looked up by the figure's current edits and are discarded when the script or its input data change. Projects saved by earlier versions may show the existing "approximate preview" badge on such figures until they are rendered once.

## "Connect with an API key" AI never ran in the desktop app

In the 0.19.0–0.19.2 desktop installers, the built-in AI used when you connect a provider with an API key (instead of a Codex / Claude Code subscription) failed about a tenth of a second after every request, without contacting the provider. The installer was missing the module that starts it. Connections through Codex or Claude Code were not affected. This is fixed, and the desktop smoke test now starts the built-in AI from the installed app.

## Text typed on the canvas is no longer lost when clicking empty space (#1546)

Typing into a text box on the canvas and then clicking empty canvas space, without pressing Enter, discarded the typed text. Typing in the inspector's text field and then clicking empty space left the edit open, so the next action (such as drawing an arrow) was merged into it and a single undo also reverted the text. The text is now committed whenever the editor closes, and the next action is undone on its own.

## AI edits that failed instantly now leave a reason in the diagnostic bundle

When an AI edit or conversion ended in failure, the session log only said "status=failed", so a run that failed within a tenth of a second (or a conversion that finished without producing the new script) could not be explained from a diagnostic bundle. The end-of-session log line now also records the exit code, the built-in runner's closed failure reason, whether the runner exited without reporting one, and which verification checks were unmet. Only fixed codes are written, never paths, prompts or keys.

## "Rebuild as editable figure" stays on the current canvas (#1545)

Rebuilding a PNG as an editable figure could jump to another canvas and open an older, already-edited figure from the same script with the same file name, while reporting that the figure had been redrawn. The rebuilt figure is now added next to the PNG on the canvas you started from, and the success message only appears when that happens.

## First run on a fresh machine no longer fails with "session s-1 does not exist"

When Tavotto ran a script for the very first time on a machine (matplotlib builds its font cache, so the render process takes about ten seconds to start) and a second try-run of the same script started meanwhile, for example from the AI repair button, the second one closed the session the first one had just opened. The first run then failed with "session s-1 does not exist (workerd may have restarted), please reopen" even though the script was fine. A try-run now shares a session that another run has just opened and not finished building yet, instead of closing it.

## Timeline: version strip, content-centred thumbnails, safer restores

- The Timeline tab now has a strip of ticks at the top, one per saved version. Hovering shows that version's stored thumbnail (nothing is re-rendered, so it keeps up with the mouse); clicking opens the existing compare card. ←/→/Home/End and Enter work from the keyboard.
- Thumbnails are now taken of the area that has content, not the whole page, and every thumbnail in the timeline (rows, hover card, compare card) is centred on that content. Versions saved by earlier builds are cropped from their sketch the same way.
- The scope filter and the "Named" toggle are now one row. When a single figure is open the scope reads "This figure / All figures"; in a multi-figure layout it reads "This layout / All figures".
- Restoring a version that would change which figures are on the canvas (swap one for another, one → a layout, a layout → one) now always asks first and says how many figures it will become. Restoring from the compare card shows only that question; the restore starts after Continue.

## AI repair can now see and run in the environment your project is using

The repair assistant's environment list only showed external Python installs and left out the environment the project was actually running in (including Tavotto's built-in Python), so its "run the script to verify" step was refused with "select that environment first". The list now starts with the environment currently in use, marked as current, and verification runs in it.

## Interface icons now respond with small motions

Icons in buttons, the left rail, the toolbar, menus and the command palette play a short one-shot motion when you hover, click or move the highlight to them with the keyboard (the export arrow drops into its tray, undo swings back, the folder opens, the layout grid tiles pulse…). Status icons in notices and toasts animate once when they appear, and copy buttons draw their check mark after copying. Every motion is under 0.75 s and returns to the static icon; nothing loops except the loading spinner. With the system's "Reduce motion" setting on, icons do not move.
