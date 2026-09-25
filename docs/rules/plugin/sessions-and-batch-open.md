# 批量打开与会话账本

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「MCP server 与内嵌画布」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

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
- **会话不抱 worker 引用**：池的 `MAX_ALIVE` 与桥的 `MAX_SESSIONS` 是两个数，
  必然打架——每次操作前 `pool.get()` 重新取（`Session.acquire()`）。
  会话**渲染成功之后**才登记，否则失败的 open 会堆满账本并挤掉在用的会话。
