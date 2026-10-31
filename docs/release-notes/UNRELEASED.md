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

## "More" menu is grouped, command palette and settings wording cleaned up

The top-bar "..." menu is split into File / View / Help groups with small headings, and a disabled write-back item now explains why in its tooltip instead of a second line. The command palette shows an icon for every command and draws its shortcut as one plain grey key label, and "Add (a)(b)(c) labels" is renamed "Add (a)(b)(c) numbering". In Settings, "Interface looks off?" is now "Reset interface layout", the "Open canvas settings" row is gone, and the appearance note explains the canvas paper stays white in dark mode to match exports. The welcome page drops the smiley and its clickable-looking feature row, and the tutorial shows progress only at the top, labels the first step's back button "Back to welcome", and says what is left to do on a disabled "Next" (for example "Drag in the script to continue"). (#1556)

## Single-figure export now includes canvas annotations (#1551)

When the canvas held a single figure, text, arrows and shapes you added on the canvas were missing from both the export preview and the exported files. They are now drawn into PDF, PNG, TIFF, SVG and EPS, clipped to the figure frame, with their stacking relative to the figure preserved (annotations moved below the figure in the layer tree stay below it). The export still follows the original-figure rules: PNG / TIFF keep the source image's pixel size and DPI, and exports without annotations are byte-for-byte unchanged. EPS keeps matplotlib's original EPS and lays the annotations over it as vector outlines; if an annotation uses transparency, which EPS cannot express, only the EPS item fails with a stated reason and the other formats are delivered.

## Scripts that load a private package folder built for another Python version

If a script puts its own `pip install --target` folder at the front of `sys.path` (for example a `.kegg_python` folder whose matplotlib was built for Python 3.12) and Tavotto runs it with a different Python, the run used to fail with an `ImportError` and the fix assistant kept trying to switch Python versions. Tavotto now recognises this case and offers **Skip this folder and rerun**: for that project, imports no longer come from that folder. Your script and the folder are left untouched, and the fix assistant uses the same remedy instead of changing Python.

## Overwrite confirmation always matches the file actually overwritten (#1549)

If an export stopped at the "file already exists" confirmation and you then changed the file name, format, scope or export folder, clicking Overwrite replaced a different existing file than the one the confirmation listed. Changing any of these now cancels the confirmation and the export starts over with a fresh conflict check. Batch exports behave the same way when the export folder changes.

## Cold canvas and export preview show the state you saved after an undo (#1547)

For script-run figures, undoing an edit (for example moving a legend back) and then saving could make the reopened canvas and the export preview show the state from before the undo, until you entered figure editing. The exported files were already correct. Cached previews are now looked up by the figure's current edits and are discarded when the script or its input data change. Projects saved by earlier versions may show the existing "approximate preview" badge on such figures until they are rendered once.

## "Connect with an API key" AI never ran in the desktop app

In the 0.19.0–0.19.2 desktop installers, the built-in AI used when you connect a provider with an API key (instead of a Codex / Claude Code subscription) failed about a tenth of a second after every request, without contacting the provider. The installer was missing the module that starts it. Connections through Codex or Claude Code were not affected. This is fixed, and the desktop smoke test now starts the built-in AI from the installed app.

## Text typed on the canvas is no longer lost when clicking empty space (#1546)

Typing into a text box on the canvas and then clicking empty canvas space, without pressing Enter, discarded the typed text. Typing in the inspector's text field and then clicking empty space left the edit open, so the next action (such as drawing an arrow) was merged into it and a single undo also reverted the text. The text is now committed whenever the editor closes, and the next action is undone on its own.

## "Rebuild as editable figure" stays on the current canvas (#1545)

Rebuilding a PNG as an editable figure could jump to another canvas and open an older, already-edited figure from the same script with the same file name, while reporting that the figure had been redrawn. The rebuilt figure is now added next to the PNG on the canvas you started from, and the success message only appears when that happens.

## First run on a fresh machine no longer fails with "session s-1 does not exist"

When Tavotto ran a script for the very first time on a machine (matplotlib builds its font cache, so the render process takes about ten seconds to start) and a second try-run of the same script started meanwhile, for example from the AI repair button, the second one closed the session the first one had just opened. The first run then failed with "session s-1 does not exist (workerd may have restarted), please reopen" even though the script was fine. A try-run now shares a session that another run has just opened and not finished building yet, instead of closing it.

## AI repair can now see and run in the environment your project is using

The repair assistant's environment list only showed external Python installs and left out the environment the project was actually running in (including Tavotto's built-in Python), so its "run the script to verify" step was refused with "select that environment first". The list now starts with the environment currently in use, marked as current, and verification runs in it.
