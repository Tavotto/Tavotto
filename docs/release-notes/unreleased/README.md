# 待发说明片段

每个用户可见的 PR 在这里**新增一个文件** `<短横线主题>.md`（例如 `canvas-edge-resize.md`），
内容以一个 `## ` 标题开头，正文按症状和触发条件写（英文，写法同 RELEASING.md）。
不同 PR 用不同文件名，所以永远不会互相冲突。

发版时（RELEASING.md「发一个新版本」第 3 步）把所有片段（以及 UNRELEASED.md 里残留的 `## ` 段落）
搬进 `docs/release-notes/vX.Y.Z.md`，并删掉这些片段文件；本 README 留在原处。
带着没搬走的片段打 tag，发布闸（`scripts/check_pending_release_notes.py`）当场红。
