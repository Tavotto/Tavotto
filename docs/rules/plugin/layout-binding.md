# 插件会话绑定排版子图（ADR 0217 第二、三节）

> 2026-11-28 新写（0217 第 2 期：绑定 + 桌面 → 插件）。速查行并在 `layout-annotate.md` 那一行里（`codex-plugin/AGENTS.md` 为守棘轮上限不单列）；这里是这一主题的**唯一全文**，改规则改这里，并同步那一行。
> 看护用例：`tests/test_mcp_layout_binding.py`（自动绑 / 歧义 / 显式 / 快照子图不绑 / 草稿被取代 / 桌面改了下次先读 / 子图没了解绑 / 锁住的元素 / 恢复时重新核）、`tests/test_mcp_error_next.py`。
> 第 3 期（第四节）已做：绑定会话写回排版 + 桌面按条并入（见文末「写回」）。还没做（后续 PR）：窗口里的待办卡（第五节）。
> 写回看护用例：`tests/test_mcp_layout_writeback.py`、前端 `web/src/lib/agentMerge.test.tsx`（`ADR 0217 §四` 一组）。

- **绑定 = `Session.binding`**：`{layout, canvas_id, panel_id, revision, ovr}`。`revision` 是最近读到的排版修订号，`ovr` 是那时子图 `overrides` 的 `patch_hash`（内部指纹，回包不带）。
  没绑定的会话一切照旧。绑定随会话记录落盘，但**记录证明不了权限**：恢复只当线索（形状不对当没有），有效性每次调用前由 `_sync_binding` 重新核，恢复路径不另写一份校验。
- **谁能绑**：与 `_layout_measure` 同一口径，共用 `bridge._panel_script`（冻结快照 / `figureOrigin`、运行时素材、没有登记脚本的子图不能绑；子图 `fileId` → 注册表 `for_artifact` 的 stem 必须等于会话 stem）。不许再抄一份判据。
- **`tavotto_open_figure` 的 `layout` + `panel`**（子图 id 或 `tavotto_layout_scene` 的短号 `P1`…，短号只认该排版当前画布）：显式给了对不上 → `layout_binding_mismatch`（`reason`：`panel_not_found` / `figure_not_in_layout` / `other_figure` / `no_script` / `composed_from_snapshot` / `runtime_panel`）；排版自己的错误码（`layout_not_found` 等）原样抛。
  不给：用 `layoutannotate.list_layouts` / `open_layout` 扫项目排版（所有画布），**恰好一个**能绑才自动绑；多个 → `binding.state: ambiguous`（`candidates` 最多 8 条 + `count`，Agent 问用户再带 `layout` / `panel` 重开）；没有 → `state: none`。批量打开不绑。
- **绑定那一刻子图赢**：会话 patches = 子图 `overrides`（`identity` 原样）。草稿与子图不同时不带回草稿（原样留在盘上）、回包 `draft_superseded: true`；一致就照常带回。`_live_session_for` 只沿用 patches 为空的会话，所以沿用的会话绑定时只是按子图重渲染。
- **每次调用前先读**（扩 `@_synced`，同一个入口，**先绑定后脚本**）：`_sync_binding` 读排版、核子图仍在且仍能绑、取 `lockedGids`；修订号变了**且子图 overrides 的指纹变了**才按新列表重渲染同一个会话，回包 `layout_sync: {reason: "desktop_changed", changed: [{gid, prop}…≤8], changed_count, patches}`（重渲染失败时挂在 `BridgeError.extra`，绑定的修订号不前进，下次再试）。
  修订号变了但子图的修改没变（桌面只挪了子图）不动会话。重渲染同时解除规范化合同、标验收 `stale`。
- **子图 / 排版没了或不再能绑** → 解绑，回包 `binding: {state: "lost", reason, layout, panel_id}`，之后照没绑定的会话用；排版暂时读不了（`layout_unreadable`）不解绑、这次不同步。
- **`lockedGids`**：绑定会话的 `apply_overrides` 里锁住元素的条目不改，进 `rejected`（`reason: "locked"`，带 `index`）；锁住元素已有的修改原样保留，原样重发不算拒绝。
- **有界**：`binding` / `layout_sync` 都在 `HANDLE_ONLY_KEYS`（预算退到只剩把手也保留），所以候选与 `changed` 必须定长。

## 写回（ADR 0217 第四节，第 3 期）

- **何时写**：绑定会话里改了 `session.patches` 的工具，在**提交点**写一次：`apply_overrides`（含 `suggest_apply`，它调前者）渲染通过后；`normalize_figure` 与 `fix_to_journal` 在事务收尾（`ok` 且 patches 变了）——事务中途的候选状态不写。没绑定的会话一概不碰排版；子图里已经是这份（指纹相同）不写、不留空撤销。脚本改后自动重跑（`_sync_script`）不写。
- **怎么写**：`_write_layout_back` → `layoutannotate.write_canvas`（文档锁、修订号核对、先存撤销副本、原子替换），只把整份**规范列表**写进这个子图的 `overrides`；`base_revision` = 绑定上记的修订号（本次调用前 `_sync_binding` 读到的）。没带 `identity` 的条，值与调用前一致时沿用旧 identity。成功后绑定的 `revision` / `ovr` 前进，回包 `layout_written: {layout, panel, undo_token, revision}`（在 `HANDLE_ONLY_KEYS`，定长）；撤销用 `tavotto_layout_annotate_undo`（共用 0208 留存，每份排版最近 20 步）。
- **写之前排版变了** → `layout_changed`：会话退回调用前的 patches 与画面（`_revert_after_failed_write`；normalize / fix 还原合同与导出标记），文件不动；Agent 重读（任一调用都会先对齐桌面）再改。normalize / fix 带导出时，已出的导出文件不撤回（写回在导出之后才核对）。
- **桌面赢**：桌面 `planAgentMerge` 对子图 `overrides` 按 `(gid, prop)` 三方合并（基线 = 账本 `before`），同一条两边都改 = 留桌面的、不挡并入；下一次自动保存把桌面的结果写回排版。绑定上记 `written`（上次**这次写改了**的键 → 那条的 `patch_hash`，去掉的键记空串，最多 64 条，随记录落盘当线索）；`_sync_binding` 读到子图修改变了、且 `written` 里有键与桌面现在的不同 → `layout_sync.reason: "desktop_won"`，`won`（≤8 条）+ `won_count`，说一次就清。插件自己撤销（`layout_undo`）会清掉该排版上绑定会话的 `written`，不当作桌面赢。
- 规范化合同：桌面把子图改到合同范围外时，下一次读到的变化照 §三 解除合同、验收标 `stale`（这一期没有新增处置）。
