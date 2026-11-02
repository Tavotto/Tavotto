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

## Scripts that write their SVG by hand now get "Make editable version"

A script that uses no plotting library and builds the SVG text itself (for example joining `<svg>` / `<rect>` strings and writing them to a `.svg` file) used to end with "no figure was produced, the data file was probably not found", and the fix assistant had no way to hand it over for conversion. Tavotto now recognises this case: the card says the figure was drawn as SVG and offers **Make editable version**, which writes a separate Matplotlib script for you to compare and accept. Your original script is never changed.

## Icon animations no longer overlap themselves

Several interface icon animations had parts passing through each other while playing (the sheet in the folder icon poked out of the front panel, the two squares in the group icon merged, the envelope flap flipped through the top edge, the sun in the image icon crossed the mountain, the small sparkle touched the big one). Each animation now starts from the state before the action and ends at the resting icon without any parts crossing: lock drops its shackle in and unlock pops it out by the same distance, disconnect pulls the plug out of its socket, and distribute slides one block to even spacing. The disconnect, save and unlock icons were redrawn, and clicking "Disconnect" now plays the unplug motion.
