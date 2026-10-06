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
  （`HANDLE_ONLY_KEYS`）再截 `content` 文字；open 只在单图 + 有画布时跑（批量 / 无画布没有
  iframe）；`_meta` 不再复制 `widgetData`。
- **apply 的 `summary=true` 是显式的模型回执选项**：不返回 manifest / SVG / 位图负载，
  保留会话、patch_hash、render_revision、worker_generation、应用与拒绝计数、合同解除与
  恢复标志，写 `elided.fetch_with=tavotto_session_state`。资源与无画布说明加完后量整个
  CallToolResult，守 `APPLY_SUMMARY_BUDGET_BYTES`（16 KiB），超限诊断可省但计数不可省。
  `Session.apply_receipt` 只保留最近成功 apply 的诊断（紧凑 UTF-8 JSON ≤ 64 KiB），不存
  manifest / 预览，也不重复保存既有的 `Session.warnings`；warnings 仍从取件顶层完整读取。
  超限的 rejected / timings / 预览错误依次不保留，用 `last_apply.diagnostics_unavailable`
  明示缺项，计数与同一 hash / revision 留着。这些极端诊断清单无法再完整取回，完整图态
  不受影响。下次成功 `_render` 清除旧回执；关闭 / 淘汰 / 出界 / 退出释放，进程重建
  不保留旧诊断。失败仍回 `isError=true`、`ok=false` 与原错误码；显式摘要的超大错误也守
  16 KiB，保留有界错误 / recovery 与诊断样本，列表带原计数，`elided` 明示完整错误诊断
  未保留（不能冒称 `tavotto_session_state` 能取回它）。预算内错误逐字段不变，不把失败压成成功。
  **省略 summary 或 false 保留旧 apply 完整响应**（画布靠同一次响应拿 manifest / 预览；
  另跳取件可能取到另一组 patches，不能代替原子响应）。工具说明与技能引导新模型传 true；
  旧客户端仍可能超过宿主事件上限，这是兼容性保留的边界。
- **`tavotto_session_state` 是画布的取件通道**：只读、不重渲染，全部来自 `Session` 上最近一次
  `_render` 留下的字段（加字段先加到 `Session`），预检复用 `Session.preflight_cache`（`_render` 必清）。降级
  `NORMAL_TOOLS` 由 `test_degraded_normal_tool_names_mirror_the_real_server` 钉成镜像。
- **画布启动三路**（`web/src/mcp/boot.ts`）：完整结果直接种（`elided` 在就不算完整，只省 svg
  会种出空画布；矢量图必须带 svg 字符串）；只有把手就取件、回来的
  `patches` 原样种进账本；空壳当场报形状（`data-boot-state` / `data-boot-detail`），30 秒没
  结果也说出口但继续收。都不自己发起 open。真宿主验收加大图一条（acceptance 文档 D 节）。
- **其余工具的失败结果守 `ERROR_RESULT_BUDGET_BYTES`（64 KiB，T10，ADR 0117 §五）**：`call_tool` 的 BridgeError 分支、apply 除外
  （显式摘要守 16 KiB、省略 summary 的旧 apply 保留完整失败）。量编码后的整个 CallToolResult；预算内逐字段不变；超了按
  `ERROR_ELISION_ORDER` 逐项截（traceback 先），`ERROR_PROTECTED_KEYS`（`ok` / `code` / `requirements` / `input` / `capability`）不截，
  `elided` 写明截了哪些、列表原来几条、完整诊断没有保留。看护 `tests/test_mcp_compat.py` 的有界错误两条。
- 看护：`tests/test_mcp_server.py` 末节、`tests/test_mcp_resolver.py`、`web/src/mcp/boot.test.ts`、
  `web/e2e/mcp-canvas.spec.ts`。**跑变异一律 `-B` 并清 `__pycache__`**：等长改动一秒内还原，
  pyc 头不变，跑的是变异版。
