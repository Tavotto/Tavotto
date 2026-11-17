# 批量打开与会话账本

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「MCP server 与内嵌画布」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。
> 看护用例（`codex-plugin/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_mcp_server.py`
> `codex-plugin/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：一张失败不回滚整批、「没尝试」是第三个桶；open 之后每一步都不许把已登记会话带走；会话不抱 worker 引用、渲染成功后才登记

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 120
改到: `stems` / `discover_stems`、`_safe_preflight()`、`_live_session_for()`、`Session.acquire()`
主题: 批量打开与会话账本 → `sessions-and-batch-open.md`
必守要点: 一张失败不回滚整批、「没尝试」是第三个桶；open 之后每一步都不许把已登记会话带走；渲染成功后才登记
-->

- **批量打开（issue #174）**：`tavotto_open_figure` 的 `stems` / `discover_stems`
  一次开 N 张独立图，每张仍走 `open_figure` 那条路（`_resolve_project` 是单图与
  批量共用的那一段解析，范围校验顺序只有一份）。四条不许破坏：**一张失败不回滚
  整批**（失败那张带稳定 code + 自己的 stem 名）；结局是 `done`/`partial`/`failed`
  三档（词汇同 `engine/exportjob.py`），**「没尝试」是第三个桶**，会话预算在开
  之前问而不是靠 `_evict_if_needed()` 事后淘汰（同一批里先开的正好最久没用，
  事后淘汰的表现是「返回了 N 个 session_id，前几个已被自己这批挤掉」）；
  `discover_stems` 只认注册表里已登记**且产物在磁盘上**的 stem，不 probe 不猜；
  **批量结果不挂内嵌画布并把这件事说出口**——一次 `tools/call` 只带得出一块
  iframe，而画布只认完整的单图 open 结果（`web/src/mcp/main.tsx` 的
  `isOpenResult`），挂上去的表现是 iframe 永远停在「等待 tavotto_open_figure」。
  预检按 #102 第 4 条只回合计 + 阻断项点名，「没跑出结论」不并进「通过」。
- **open 之后的每一步都不许把已经登记的会话带走**（#271 评审）：预检经
  `server._safe_preflight()`，`BridgeError`（预期内、有稳定 code）与其余异常
  （`preflight_crashed`，没人诊断过）**分两档**，两条路都照常回 session_id
  ——异常逃出去时 `tools/call` 回的是一条错误结果，里头没有 id，用户手上就是
  开着却关不掉的会话（批量那一路会连同**同一次调用里已开好的其余几个**一起丢）。
- **同一张图不开第二个会话**：`_live_session_for()` 沿用**还没改过**的会话
  （`patches` 为空），因为画布 seed 的是 `overrides: []`（`web/src/mcp/session.ts`），
  沿用带 patch 的会话会让画布账本与引擎状态对不上。这条同时堵掉「批量填满预算 →
  照指引再单独开一张看画布 → 静默挤掉这批里先开的那个」。真发生淘汰时
  `_evict_if_needed()` 返回被淘汰的 id，open 的文字里**必须说出口**。
- **safe 被自身守卫拦下 → 同一次渲染里换 native 一次**（ADR 0127 修订 2026-11-17）：`_render` 里 safe 的
  `override` 以 `safe_file_operation_blocked` 收场时，`_native_fallback()` 经 `nativefallback.decide`（`execpolicy.decide_fallback(mcp_initiated=True)`，
  开关 / `safe_only` / 原因 / 账本一道不少）判定，再由 `nativefallback.launch`（`desktop_native_spec` + `desktopproc.start`，stdin 为 `null`，
  解释器沿用 safe worker 的 `.python`）起真实 Python、等屏障、build；会话记 `Session.native`（`NativeWorker`，`RunError` 翻成 `WorkerError`）。
  之后 `acquire()` 直接给它，**不进池、不被池 LRU 杀**；进程不在了报 `native_session_ended`，不悄悄换回 safe。回包（open / apply / session_state）
  带 `execution: "native"`、`fallback`、一句 `execution_note`（写明不受授权目录限制）；不许回退时 BridgeError 带 `native_fallback_declined`。
  关会话 / 淘汰 / `shutdown_all` 都经 `_end_native()` 放行到脚本跑完（放不了就终止）；`_retire_session_worker` 对 native 不作废（回 False），
  `verify_replay` 明确拒绝（`native_session_unsupported`）；`Session.native` 不落盘，进程换了之后恢复走 `_render`，按同一条证据重新回退。
  看护 `tests/test_mcp_native_fallback.py`（真用户 Python：脚本执行次数、`fig.png` 真落盘、`safe_only` / kill switch / 脚本异常不回退、起进程失败收进程）。
- **会话不抱 worker 引用**：池的上限（`pool.max_alive()`）与桥的 `MAX_SESSIONS` 是两个数，
  必然打架——每次操作前 `pool.get()` 重新取（`Session.acquire()`）。
  会话**渲染成功之后**才登记，否则失败的 open 会堆满账本并挤掉在用的会话。
- **草稿带回（ADR 0192 第 2 条，全文在 `session-journal.md`）**：`open_figure` 新建会话时按（项目、stem、运行配置）带回没导出的草稿；
  `_live_session_for` 的沿用规则不变（只沿用没改过的会话）。批量 `open_figures` 逐张走 `open_figure`，每张各自带回，
  `_batch_entry` 带 `restored_draft`；`discard_draft` 批量同样认。淘汰（`_evict_if_needed`）经 `_retire` 把没导出的改动存成草稿。
