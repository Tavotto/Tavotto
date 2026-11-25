# 排版标注工具（ADR 0208 第 2 期）

> 2026-11-25 新写（ADR 0208 第 2 期 ②），速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**。
> 看护用例：`tests/test_mcp_layout_annotate.py`（真 stdio + 真 worker：读场景 → 方案 → 写入 → 撤销、快照子图不量、摘要不对拒写）、
> `tests/test_layoutannotate.py`（引擎层）、`tests/test_mcp_server.py::test_tools_list_shape`。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 135
改到: `tavotto_layout_*` 四个工具、`bridge.layout_*()`
主题: 排版标注工具 → `layout-annotate.md`
必守要点: 只翻译；量子图与成品合成同一判定；写入带 echo_digest
-->

- **只翻译**：读排版、求解、核对、复述、写入、撤销全在 `engine/layoutannotate.py`。桥只做三件 MCP 才有的事：授权范围
  （`check_scope`）、每张子图的「量」、把结果压成工具结果。不许在桥里求解或改坐标。
- **量子图与成品合成同一个判定**（`bridge._layout_measure`）：带它在排版里的改动（`panel.overrides`）现跑一次导出、
  `data_points` 实测、拿这次绘制的 manifest；排版里按冻结快照合成的（`figureSnapshot` / `figureOrigin`）、运行时素材、
  没有登记脚本的子图**在成品里不是现画的，这里也不量**（`composed_from_snapshot` / `runtime_panel` / `no_script`），只给外框。
  拿现跑的结果冒充快照里的那张图 = 对齐到一张用户看不到的图。
- 量用的是 `open_figure` + `Session.acquire()`（唯一 worker 入口不变）；量时打开的会话 id 回在 `opened_sessions` 里，
  受会话预算约束，Agent 用完可以关。
- **写入必须带方案的 `echo_digest`**：服务端重新求解、重新核对，描述变了（哪怕复述不变）就 `echo_mismatch`。
  工具说明要求 Agent 先拿复述对照用户原话、写完把复述念给用户；认不准指哪个元素时列候选给用户选。
- 局部图（方案里的 PNG）走真合成（`layoutannotate.preview`），子图源是这次量时导出的 PDF；画不出来不让方案失败。
- 四个工具同 `tavotto_share_review` 处置：**不进** `usage.TOOLS`（走 `other` 桶，不升同意版本）；引擎没有
  `layoutannotate`（旧引擎）时不出现在 tools/list，调用回 `layout_annotate_unavailable`。
