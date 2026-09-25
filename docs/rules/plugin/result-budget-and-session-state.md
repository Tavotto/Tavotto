# 工具结果的体积预算与画布取件（ADR 0069）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「工具结果的体积预算与画布取件（2026-09-21，ADR 0069，issue #457）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **Codex 把 MCP 工具结果送给桌面 UI 的事件副本封顶在 1 MiB**，超过就把 `structuredContent`
  / `_meta` 置空——模型那份与画布自己发的 `tools/call` 不受影响，症状是「模型正常、画布永远
  等待」（422 元素 ≈ 1.3 MB）。**顺带**：`structuredContent` 非空时 codex 只把它给模型，
  `content` 文本整段丢弃。全文在 ADR 0069。
- **只有单图 open 守预算**（`CANVAS_INLINE_BUDGET_BYTES` 768 KiB，量整个 `CallToolResult`
  的紧凑 UTF-8 字节，别用默认 ensure_ascii），按 `INLINE_ELISION_STEPS` 省 svg → manifest →
  位图 → 预检清单，写 `structuredContent.elided`；说明加完再量一次，还超先退到只剩把手
  （`HANDLE_ONLY_KEYS`）再截 `content` 文字；只在单图 + 有画布时跑（批量 / 无画布没有
  iframe）；**apply 不守**（画布靠它拿新 manifest）；`_meta` 不再复制 `widgetData`。
- **`tavotto_session_state` 是画布的取件通道**：只读、不重渲染，全部来自 `Session` 上最近一次
  `_render` 留下的字段（加字段先加到 `Session`），预检复用 `Session.preflight_cache`（`_render` 必清）。降级
  `NORMAL_TOOLS` 由 `test_degraded_normal_tool_names_mirror_the_real_server` 钉成镜像。
- **画布启动三路**（`web/src/mcp/boot.ts`）：完整结果直接种（`elided` 在就不算完整，只省 svg
  会种出空画布；矢量图必须带 svg 字符串）；只有把手就取件、回来的
  `patches` 原样种进账本；空壳当场报形状（`data-boot-state` / `data-boot-detail`），30 秒没
  结果也说出口但继续收。都不自己发起 open。真宿主验收加大图一条（acceptance 文档 D 节）。
- 看护：`tests/test_mcp_server.py` 末节、`tests/test_mcp_resolver.py`、`web/src/mcp/boot.test.ts`、
  `web/e2e/mcp-canvas.spec.ts`。**跑变异一律 `-B` 并清 `__pycache__`**：等长改动一秒内还原，
  pyc 头不变，跑的是变异版。
