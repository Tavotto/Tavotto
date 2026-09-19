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
