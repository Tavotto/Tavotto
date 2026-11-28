# 排版标注与矢量编辑工具（ADR 0208 第 2 期、ADR 0212 第 5 期）

> 2026-11-25 新写（ADR 0208 第 2 期 ②），速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**。
> 2026-11-26 补上改已有矢量对象的两个工具（ADR 0212 第 5 期 ①）；同日补路径锚点逐点编辑（第 4 期，细则在 `references/layout-annotate.md`）。
> 看护用例：`tests/test_mcp_layout_annotate.py`（真 stdio + 真 worker：读场景 → 方案 → 写入 → 撤销、快照子图不量、摘要不对拒写）、
> `tests/test_mcp_layout_edit.py`（同上，编辑补丁）、`tests/test_layoutannotate.py` / `tests/test_vectoredit.py`（引擎层）、
> `tests/test_mcp_server.py::test_tools_list_shape`。

<!-- agents-route
sheet: codex-plugin/AGENTS.md
order: 135
改到: `tavotto_layout_*`、`bridge.layout_*()`
主题: 排版标注 → `layout-annotate.md`；建议待办 → `suggest-todos.md`
必守要点: 量子图与成品同一判定；写入带 echo_digest
-->

- **只翻译**：读排版、求解、核对、复述、写入、撤销全在 `engine/layoutannotate.py`（加）与 `engine/vectoredit.py`（改）。桥只做三件 MCP 才有的事：授权范围
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
- **编辑**（`tavotto_layout_edit_plan` / `_apply`）：补丁只点名对象 id（取场景的 `vector_objects`，`select` 筛；
  `anchored_to` 收子图短号，桥不翻译、引擎按 `panels_by_ref` 换成对象 id）。选择条件写错整次拒（`bad_select`），不当成全选。
  方案回包**不带**改后的整张对象表（`objects`），只带动到的；局部图（`vectoredit.preview_edit`）画改后的整张画布、框住改前与改后的位置。
  撤销与标注共用 `tavotto_layout_annotate_undo`（同一份按文件留存）。拆组 / 删除后成员不足的布局组约束在写入层去掉并写进复述（#2244）。
  几何内核没装上时四个几何操作回 `kernel_unavailable`，工具文案说明其余操作照常。
  路径锚点操作（`move_anchors` … `release_compound`）不用内核；锚点地址 `{sp, i}` 取场景 `vector_objects[].anchors`（有上限，超出只给 `subpaths` 计数），
  含义是**该条操作执行时**的状态，复述末尾的「现为」是后续地址的依据；桥与 server 不翻译、不校验地址（校验在 `vectoredit`）。
  核对的锚定端（尖端余量）取对象上落盘的 0208 锚点与本批 `move` 的锚点，**只认补丁跑完仍成立的**（端点离锚点解出的位置
  ≤ 锚点残差限；数据锚点为此现量）。只改了颜色 / 名字 / 锁定这类不改占地的字段（`vectoredit.PAINT_ONLY`）时，改之前就失败的
  同一项标 `preexisting`、不挡写入、照实说；挪了、改了形就按改后完整核对。
- 六个工具同 `tavotto_share_review` 处置：**不进** `usage.TOOLS`（走 `other` 桶，不升同意版本）；引擎没有
  `layoutannotate`（旧引擎）时不出现在 tools/list，调用回 `layout_annotate_unavailable`；有它没有 `vectoredit` 时
  只缺两个编辑工具，`select` 参数同样回这个码。
