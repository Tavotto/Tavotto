# RenderCore：Render IR、RenderPlan、字体政策与可检索文字（统一实施包 U06，ADR 0059；字体政策的 ADR 随第二个 PR）

> 2026-09-20 随 U06 新增；速查行在 `src/tavotto/AGENTS.md`「按改动路径找细则」表里
> （`rendercore/` 那一行）。这里是这一主题规则的**唯一全文**；速查表只留一行。
> 改规则改这里，并同步那一行。

- **分层是硬边界**（`tests/support/importgraph.py` 的 `rendercore_model` / `rendercore_native`
  两层 + 层规则，`tests/test_rendercore_model.py` 钉外部名字）：纯模型（`ir` / `geometry` /
  `typography` / `sources` / `plan`，字体注册表 `fonts` 随字体政策 ADR 加入）**只许标准库**与仓库里
  同样纯标准库的模块；候选包（pikepdf / fontTools / uharfbuzz / pypdfium2）只在 native 适配层
  （`hbshaper` / `pdfwriter`）的函数 / 类里 import；**整包零 `import pymupdf`**，也没有边进
  `pdfbackend` / worker 侧，反方向同样不许（D03：新核心不借旧库，旧后端不认识新核心，U08 之前
  两边不接）。往纯模型里加一个第三方 import 的正确做法是把那段挪进适配层，不是给守卫开口子。
- **IR 就是 PDF 空间**：pt、左下原点、y 向上；矩阵行向量与 `cm` 同形；`Group` 先 `transform`
  再 `clip` 再 children；paint order = 列表顺序、hidden 不进 IR、**绝不按 id 排序**。画布毫米 /
  顶原点 → IR 的换算**只在 `plan.compile_page()`**，写入器原样落笔、不再翻 y。测试里的几何期望
  **手算**，不调 `plan` 的换算函数反推（RC-095：写入 / 读取不同源）。
- **`ir.validate()` 是写入器的门**：NaN / Inf / 非正尺寸 / 不可逆矩阵 / 越界 alpha / 悬空资源 /
  不合闭集的 fill rule / cap / join，全部在进写入器之前以 `IRError(code)` 拒绝；`IR_ERROR_CODES`
  是闭集，加一条 code 同时补 `tests/test_rendercore_ir.py` 的负例（那里有一条用例要求每个 code 至少
  一条负例）。
- **Capabilities 是写入器的合同**（`ir.CAPABILITIES[格式][操作]`，三档 `native / rasterized /
  unsupported` + 理由）：声明 `unsupported` 的操作写入器必须以结构化错误拒绝，不静默降级、不整页
  位图冒充矢量；实现了新操作先改表再改写入器，写入器用例（U06 第二个 PR）交叉核对两边。
  `plan.RenderPlan.unsupported` 在编译期就按格式列出这一页给不出的操作。
- **文字只有一条路**：`typography` 用同一份 shaped plan 量宽与落笔（RC-030）；分层只剩
  primary / cjk / missing——**没有 fallback 脸**，画不出的字进问题系统（`CompiledPage.problems`
  的 `glyph_missing`），不暗中回退系统脸；合成上下标（`⁵` → 上标 `5`）的用户原文随
  `GlyphRun.actual_text` 进 IR，写入器据此包 ActualText（RC-036）。换行 / 对齐 / 行高 / 上下标 /
  下划线的用户合同逐句来自旧 `_draw_text`，改这些先改前端 TextView 那一侧的同一语义。
- **源产物冻结一次、核两次**：`sources.StaticSourceResolver` 只认「磁盘原件 + 无 override」，
  不跑脚本（RC-019）；带 override / runtime 素材抛 `source_needs_execution`（U08 接 worker + 回执，
  RC-017：绝不拿 materialized cache 冒充）；`read_frozen()` 在读字节那一刻核 sha256，不符就是
  `source_changed`（RC-014）。同一张图两套 override = 两条资源（key 含字节 hash + 语义身份），
  不按 stem 串用（RC-015）。不复制脚本 / 实验数据到 staging。
- **身份不另造**：`plan.compile_plan()` 的 `plan_identity` 就是 U01 的 `exportreq.render_plan_ref()`
  （04 §3 三种身份不混）；`FileResource.semantic_identity` 是 `SourceArtifact.semantic_identity()`。
- **Arrow / Shape 编译成 `Path`**（`geometry`，框空间 y 向下 + 一个 `Group.transform`）；
  `polygon_points` / `dash_pattern` 与 `web/src/lib/shapeGeometry.ts` 是严格同源对
  （`docs/rules/repo/same-origin-pairs.md`），`tests/test_rendercore_geometry.py` 拿旧 facade 当 oracle
  对拍，两边都改的时候先改前端。
- **不切默认**：PyMuPDF 仍是默认后端，本包在 U06 不接任何用户可见入口；facade 19 项与 Canvas 面
  的迁移在 U08（`docs/implementation/tavotto-foundation/U00_FACADE_LEDGER.md` 逐项）。
