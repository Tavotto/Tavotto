# 会话跨进程恢复（ADR 0078）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「会话跨进程恢复（2026-09-23，ADR 0078）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例（`codex-plugin/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_mcp_session_journal.py`、`tests/test_mcp_roundtrip.py`、`tests/test_mcp_draft_restore.py`（草稿，ADR 0192 第 2 条）

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 70
改到: `tavotto_mcp/sessionjournal.py`、`_restore_session`
主题: 会话跨进程恢复 → `session-journal.md`
必守要点: 记录不能自证权限、越界拒在渲染之前；明确释放的不复活（草稿只经 open 带回）；落盘失败不让工具调用失败
-->

- **会话不再只活在 server 进程内存里**：提交点（open 结束 / apply 结束 / 规范化收尾，含回退）
  经 `tavotto_mcp/sessionjournal.py` 落 `data_dir()/mcp-sessions/<id>.json`；内存未命中时
  `get_session` → `_restore_session` 按记录在本进程重建。起因是宿主会换进程（WorkBuddy 一轮后
  回收 CLI、Codex 改配置重启 server）。`_render` 本身不写盘，规范化中途的候选不落盘。
- **记录不能自证权限**：恢复先用**当前连接**的 `RootAuthority` 校验项目，越界 `workspace_root_changed`
  且**拒在渲染之前**；脚本 / 入口不存、从注册表重读，stem 不在了 `session_restore_failed`。
- **明确释放的不复活**：`close_session` 与 `_evict_if_needed` 连记录一起删。结果里的
  `restored: true` 只报一次（`_take_restored`），文字带 `server.RESTORED_NOTE`。
- **没导出的改动存成草稿（ADR 0192 第 2 条）**：导出出了文件时记下那一版的 `exported_hash`（也落盘）；
  `close_session` / `_evict_if_needed` 经 `_retire` 释放会话时，`unexported_changes` 为真就先 `_save_draft`、再删活会话记录
  （进程换过、只剩记录的会话关闭同理）。结果带 `unexported_changes: true`、`draft_saved` 与 `UNEXPORTED_NOTE`
  （存成草稿、重开自动带回）；草稿也存不下来（磁盘错误）就 `draft_saved: false` + `UNEXPORTED_LOST_NOTE` 如实说丢了。
  - **草稿放另一个目录** `data_dir()/mcp-drafts/<旧 id>.json`（`sessionjournal.DRAFTS_DIRNAME`），记录形状同会话记录：
    旧插件只扫 `mcp-sessions/`，读不到草稿；`get_session(旧 id)` 仍是 `unknown_session`——**草稿只经 `open_figure` 带回，
    永远不是按 id 复活的活会话**（这与「明确释放的不复活」一致，不是例外）。同一张图（项目、stem、运行配置）只留最新一份；
    保留期与记录同一个 `TTL_SECONDS` / `MAX_RECORDS`。
  - **带回**：`open_figure` 新建会话（没有同一张图的活会话，不管改没改过）时 `_find_draft`：先过 `_judge_path`（越界当作没有，
    拒在渲染之前）、再 `_render_draft`——patches 先过 `patchspec` 规范化，渲染后按 worker 自己的 warning
    （`_unapplied_patches`：元素不存在 / 属性不支持 / 应用失败 / identity 对不上）找出用不了的，从会话里摘掉并在**同一个会话**上
    再渲染一次，让画面与 `patches` 配对。回包 `restored_draft: {patches, from_session, dropped?, note}`，`dropped` 逐条列
    `{gid, prop, reason}`。先渲染成功再登记；没丢任何一条才带回规范化合同。带回的会话仍是「没导出」（`exported_hash` 空）。
  - **渲染不了不拖垮这张图**：带草稿渲染失败时按脚本原样开（回包 `draft_restore_failed: {code, error}`），草稿留着，关了重开会再试；
    连原样也渲染不了就抛那个错，草稿不动。
  - `discard_draft: true`：不带回并删掉这张图的草稿（回包 `discarded_draft: true`）；批量打开同样认。
  - **清草稿**：带回成功（已并进活会话记录）、`discard_draft`、导出成功（`_persist` 里 `exported_hash == patch_hash()`）。
  - **批量打开**逐张走 `open_figure`，每张各自带回，摘要里对应那条带 `restored_draft`；会话预算用光而没尝试的不动草稿。
  - **native 会话**：草稿只存 patches，不存进程；带回走正常 open，safe 被拦后同样回退 native（`execution: native`）。
  - **没经 close 就丢的会话**（进程退出 `shutdown_all` / 宿主断开）：它的活会话记录还在 `mcp-sessions/`。`_find_draft` 找不到草稿时，
    取同一（项目、stem、运行配置）里**不在本进程内存**、`_record_unexported` 为真的记录当草稿（`_orphan_records`，多份取最新），
    同样的越界校验、过期口径、先渲染成功再登记、`dropped`；带回成功后旧记录删掉（不会按旧 id 再复活出第二份）；
    `discard_draft` 也删这类记录（本进程内存里的活会话记录绝不动）。
  - **取舍：不存 pid 判别「别的 MCP 进程里还活着」**——跨平台判进程存活不可靠（Windows 的 `os.kill` 会真结束进程），所以别的进程正开着
    同一张图且改过没导出时，后开的会话会带上那份改动（拷贝，对方继续用），对方的记录被删、下次提交点（apply / 导出）重新落盘。
    可接受：与「同一张图开两个会话」同一个量级，且宁可多带回也不丢改动。
- 落盘失败不让工具调用失败，但写 stderr。`sessionjournal` 只用标准库——不动 `_BRIDGE_IMPORT` 三处同源。
- 看护：`tests/test_mcp_session_journal.py`、`tests/test_mcp_roundtrip.py` 的两进程接力；`tests/test_mcp_draft_restore.py`（真 matplotlib 真链路）。
