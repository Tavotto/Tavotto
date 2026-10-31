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

## Interface icons now respond with small motions

Icons in buttons, the left rail, the toolbar, menus and the command palette play a short one-shot motion when you hover, click or move the highlight to them with the keyboard (the export arrow drops into its tray, undo swings back, the folder opens, the layout grid tiles pulse…). Status icons in notices and toasts animate once when they appear, and copy buttons draw their check mark after copying. Every motion is under 0.75 s and returns to the static icon; nothing loops except the loading spinner. With the system's "Reduce motion" setting on, icons do not move.
