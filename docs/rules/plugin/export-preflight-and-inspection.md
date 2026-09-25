# 导出：先预检与产物核验（ADR 0068）

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「导出产物核验（2026-09-21，统一实施包 U08，ADR 0068）/ MCP server 与内嵌画布」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

## 导出先预检（原「MCP server 与内嵌画布」一节）

- **导出先预检**：有 error **或 `not_verifiable`** 且没有 `explicit_confirm` 时
  一张图都不出（`needs_confirm`，与导出对话框同一判据；`blocking` 仍只表示
  error）。PNG 的 dpi 与 profile 的 `min_raster_dpi` 比一次，复用同一个
  `raster-dpi` id 与同一张 severity 表。默认格式取**这次调用**的 profile，
  默认导出目录也要过 `check_scope`。强制导出与确认项都记进 proof。

## 导出产物核验（原「导出产物核验」一节）

- **`bridge.export` 与 HTTP 导出接的是同一份检查器接线** `engine/artifactinspect.inspect_produced`
  （`engine_exportjob.run(..., inspect=_inspect)`，`backend="worker"`）：每个封口的临时文件在提交点之前
  重新打开量事实，不合格的那一项以 `artifact_rejected` 进 `partial`、**不发布**；合格的 `files[].manifest`
  原样带出（`verdict / checks 四值 / notes / sha256 / px / size_pt…`）。旧键（`path / bytes / vector / dpi /
  status / error`）一个不动。位图 `Produced` 带期望像素（图幅 × dpi，与 `artifactcheck` 同一换算），
  `size` 那一维才量得到。契约层 `probe_asset` 按需 import（`_probe_asset`），不进桥的常驻 import 闭包。
- **来源与四身份随 manifest 走（U09，ADR 0070）**：这条路是 worker 直接序列化、没有 RenderPlan，`_produce` 每个格式经
  `engine_artifactinspect.execution_provenance()` 用与 HTTP 候选路同一份算法补齐——回执从**这条会话**的账本装配
  （`report_origin=build` + pid 核过，体检结果冒充不了）、源是这次执行的 Figure（`kind=figure`，semantic 身份对格式
  不变）、`identity{semantic, render, artifact, run}` 与 `provenance{sources, receipts, nodes}` 进 `files[].manifest`；
  装配失败不影响导出，但要进 `warnings` 说「产物身份未核验」。桥**不**新增 import（回执 / 绑定都在 `artifactinspect` 里算）。
- **给模型看的文字里「未核验」永远不是「已核验」**：`server._inspection_summary` 三组各自点名
  （未通过 / 已核验 / 未核验），一组都不省；没有 manifest = 整份未核验。这条入口只有 standard 政策
  （必需 = 完整性 + 核心尺寸）；严格政策走 HTTP 的 `inspection` 段。
- 桥新增 import `artifactinspect` → **三处同源**一起改：`scripts/make_plugin_manifest.BRIDGE_IMPORTS_AT_MIN`、
  `codex-plugin/mcp/server.py` 的 `_BRIDGE_IMPORT` 探测串（resolver 用它判老引擎够不够用，漏了它 = 交棒后桥
  ImportError 崩死；`test_mcp_resolver.py::test_bridge_import_probe_matches_the_bridge` 对拍）、桥本身；它晚于
  v0.15.0，所以 `MIN_TAVOTTO_VERSION` 在 v0.16.0 发版时抬到 0.16.0（发那一版的 PR 里才能写这个号）。
- 替身 worker 写出的文件也要过得了检查：`tests/support/artifactbytes.py`（stdlib 最小合法 PDF / PNG）。
  看护：`tests/test_mcp_export_inspection.py`（独立读取器 + 坏文件负例 + unknown 不说已核验 + 同一份接线）。
