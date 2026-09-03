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

## Notes

**Rotated text, shapes and arrows now export in the direction the canvas
shows them — check documents where you worked around the old behaviour.**
Symptom: an annotation you rotated to 90° came out of the PDF export
mirrored, at 270°, so the way to get the angle you wanted was to type the
opposite one. That is fixed — the exported PDF now matches the canvas.
Trigger: any document saved before this release that contains a rotated
text box, shape or arrow whose angle was chosen to compensate; re-exporting
it now produces the mirrored angle. Tavotto does not correct those angles
for you: a 270° that was a workaround and a 270° you actually wanted are
identical in the file, and guessing would silently rewrite your work.
What to do: open the document and look at the rotated annotations on the
canvas — the canvas has always shown the angle you typed, so an angle that
looks wrong there is the old workaround. Set each one to the angle you
actually want. What you see on the canvas is now what the export produces.
(#244, follows #215)
