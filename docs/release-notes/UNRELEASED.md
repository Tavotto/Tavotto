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

## Changed

**Legend entries unlinked in an older document look different after
reopening.** Trigger: a document saved with 0.15.0 or earlier in which a
legend entry was unlinked from its plot object ("Unlink" on the legend
card, or `binding = custom` written by hand), and whose plot object was
edited *after* the unlink. Symptom: the handle kept the look it had at the
moment of unlinking, but only for that session. The document recorded just
"custom", so reopening it derived the handle from the plot object's
*current* state again, and a live session and a replay of the same
document drew two different figures (#414). Now the document decides:
"Unlink" writes the handle's colour, line width, line style, marker and
marker size into the document together with the binding (bars, fills and
scatter handles have only the colour), and an unlinked handle always draws
as *the script's original handle plus those overrides*. "Relink" removes
all six fields and is the exact inverse. Entries unlinked before this
version carry no such styles, so they now show the script's original
handle instead of the look they had when they were unlinked; that look was
never stored and cannot be recovered. To freeze the current look, press
"Unlink" again. (#414, ADR 0034)

**Colour fields in the manifest report `none` for a colour that does not
draw.** Trigger: any colour field (`color`, `facecolor`, `edgecolor`,
`markerfacecolor`, `markeredgecolor`, `handle_color`, `grid_color`, …)
whose value has alpha 0, for example a patch drawn with `edgecolor="none"`
or a line whose markers have `markerfacecolor="none"`. Symptom: the field
read `#000000`, so the inspector showed a black swatch for an edge that was
not there, and a script or agent reading the manifest could not tell
"black" from "none".
The field now reads the string `none`; it is accepted back as a value in
overrides, and the inspector draws it as an empty swatch labelled "None".
A filled shape's `facecolor` keeps reporting the colour that would draw
while `fill` is off, as it did before. Anything that parses these fields
as a hex string must accept `none` as well. (#427)

**A render process that dies now says how it died, instead of "crashed
(unresponsive)".** Trigger: the rendering worker exits during the first
build or a later render — most reports came from people rendering with
their own Conda or venv interpreter on Windows. Symptom: every attempt ended
in the same sentence, "渲染进程崩溃（无响应），会话需要重建", and the
diagnostics report listed bare `Traceback (most recent call last):` lines
with nothing after them (#435). Now the message states the exit status
(`sys.exit()`, a Python fatal error, an access violation, a missing DLL, a
signal, or "closed the pipe but kept running") and says explicitly when the
worker left no output at all; the worker runs with `faulthandler` enabled,
so a hard crash leaves its Python stack in `worker.log`; and the
diagnostics report carries the last lines of evidence from recent
`worker.log` files (error and frame lines only — nothing your script
printed and no source lines) plus the exception line of each traceback.
Two related changes: a script that calls `sys.exit()` no longer kills the
worker — `sys.exit(0)` / `exit()` at the end is a normal ending, a script
that parses command-line arguments with argparse (or click, typer, docopt)
and exits because Tavotto passed none is reported as "the script requires
command-line arguments" together with its `usage:` text (give the arguments
defaults, or use `tavotto run -- python script.py args…`), and any other
non-zero exit is reported as the script's own exit; and non-UTF-8 bytes on
the worker's protocol pipe are reported as garbage on the pipe rather than
as a crash. The `SyntaxWarning: invalid escape sequence '\o'` line that a
full Python printed at the top of `worker.log` (from `manifest.py`) is gone.

**Choosing a rendering interpreter in Settings runs the full health check.**
Trigger: Settings → Rendering environment → pointing Tavotto at your own
Python. Symptom: the setting was accepted as long as `import matplotlib`
worked, so an interpreter with an unsupported Python version, or one whose
Pillow / numpy DLLs are broken, was stored — and the first render failed
with "crashed" (#435). The check is now the same one used for project
environments (Python version range, matplotlib, and Tavotto's own worker
imports), and refuses with a specific reason: unsupported Python version,
no matplotlib, worker cannot start (with the failing import), or the
interpreter cannot start. An environment already stored before this
version is not re-checked until you pick it again.
