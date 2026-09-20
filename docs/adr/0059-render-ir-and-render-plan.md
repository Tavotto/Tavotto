# ADR 0059：Render IR 与 RenderPlan 编译——RenderCore 的纯模型层

日期：2026-09-20 · 状态：**Accepted（U06 第一切片；不切默认后端）**
相关：[0053 U01 合同](0053-foundation-contracts-and-preparation.md)、[0055 render_spike](0055-render-spike.md)、
[0031 导出](0031-unified-export-pipeline.md)、
[0033 字形回退](0033-scientific-text-and-font-fallback.md)；实施包 `docs/implementation/tavotto-foundation/`
（`phases/U06_render_model_text.md`、`04_ARCHITECTURE.md` §1–§3、`01_SCOPE_AND_DECISIONS.md` D03 / D07）。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| IR 放哪、长什么样 | 新包 `src/tavotto/rendercore/`，纯模型层 = `ir` / `geometry` / `typography` / `sources` / `plan`（`fonts` 随 ADR 0060 加入），**只许标准库**；节点闭集 `Page / Group / Path / Image / ImportedPage / ShapedText`，资源 `FontResource / FileResource` |
| 单位 / 原点 / 变换 / 顺序 | pt、左下原点、y 向上（= PDF 用户空间）；矩阵行向量 `(a b c d e f)` 与 `cm` 同形；`Group` 先 `transform` 再 `clip` 再 children；paint order = 列表顺序，hidden 不进 IR；画布毫米 / 顶原点 → IR 的换算**只在 `plan.compile_page()` 一处**，写入器不再翻 y（RC-008） |
| Arrow / Shape | 编译成 `Path`（`geometry`，框空间 y 向下 + 一个 `Group.transform`），不为图标建插件；`_polygon_points` / `_dash_pattern` 同源对换宿主、数字不变 |
| 文字 | `typography` 用同一份 shaped plan 量宽与落笔（RC-030）；四层只剩 primary / cjk / missing，**没有 fallback 脸**；合成上下标的用户原文随 `GlyphRun.actual_text` 进 IR（RC-036 的依据） |
| 校验 | `ir.validate()` 在进写入器之前拒绝 NaN / Inf / 非正尺寸 / 不可逆矩阵 / 越界 alpha / 悬空资源（RC-012），错误码闭集 `IR_ERROR_CODES` |
| Capabilities | `ir.CAPABILITIES[格式][操作]` 三档 `native / rasterized / unsupported` + 理由（RC-011）；写入器与它交叉核对，声明 `unsupported` 的以结构化错误拒绝、不静默降级 |
| RenderPlan | `plan.compile_plan(ExportRequest, SourceResolver, FaceProvider)`：身份**复用** U01 的 `exportreq.render_plan_ref()`，不造第二个身份函数；只编译 `scope=canvas`（`original` 归 U08） |
| 源产物 | `sources.StaticSourceResolver`：无 override 的磁盘原件冻结成 `SourceArtifact(origin=static)`，不跑脚本（RC-019）；带 override / runtime 素材抛 `source_needs_execution`，U08 接 worker + 回执；写入前 `read_frozen()` 再核 sha256（RC-014） |
| 边界守卫 | `tests/support/importgraph.py` 新增 `rendercore_model` / `rendercore_native` 两层与 9 条层规则；`tests/test_rendercore_model.py` 钉外部依赖 ⊆ 标准库、零 `pymupdf`、在屏蔽全部重包的子进程里 import + 编译 |
| 默认切换 | **不切**。本包在 U06 不接任何用户可见入口；PyMuPDF 仍是默认后端 |

## 1. 为什么是这种形状

* **纯模型层只许标准库**（RC-007）：U00 清点出 37 个测试文件直接 `import pymupdf`、7 个碰实现模块内部名——
  「换后端上层零改动」对测试不成立的根因是旧模型与旧库长在一起。IR 与编译器不认识任何 native 库，
  U07 / U08 换写入器 / 栅格器时模型层一个字不动；测试用一张合成脸（`tests/support/fakeface.py`）就能把
  编译跑完，不需要候选包。
* **换算只在一处**：旧 facade 在 `_draw_text` / `_draw_shape` / `_place_panel` 各自换算毫米与 y 向；RC-008 的
  must_fail_example「PDF 出口重复 y 翻转」正是多处换算的产物。现在 IR 就是 PDF 空间，写入器原样落笔。
* **hidden 不进 IR、顺序不排序**：旧 facade 在 `_export_produce_canvas` 里 `if o.get("hidden"): continue`，
  IR 把这一步固定在编译期并记账（`dropped_hidden`），写入器看不到「隐藏」这个概念，也就不可能画错。
* **ImportedPage 只是引用**（RC-013）：节点只有资源 key + 落位（rect / crop / 旋转 / 翻转 / opacity），
  `internal` 恒 `"unknown"`——源 PDF 里的文字 / axes 没有 gid，IR 不为它们编造身份。
* **Capabilities 是写入器的合同不是愿望**：`unsupported_for(page, fmt)` 在编译期就列出这一页在某格式下给不出
  的操作；U06 的表如实写着 PDF 里 `image` / `imported_page` / `flip` 尚未收编（U07）、PNG / TIFF / EPS 全
  `unsupported`。

## 2. 与旧 facade 的对应（U08 迁移时逐项对拍）

| 旧 facade | RenderCore |
|---|---|
| `mm2pt` / `hex2rgb` | `ir.mm2pt` / `ir.hex2rgb`（四条取值用例逐字保留） |
| `CANVAS_TEXT_FAMILIES` / `COVERAGE_MAX_CP` | `typography` 同名常量，同值 |
| `text_width(s, size, bold, italic, family)` | `typography.text_width(s, size, faces)`——族由 `faces_for(provider, family, bold, italic)` 决定 |
| `text_plan` / `missing_glyphs` / `coverage_ranges` | `typography` 同名函数（`fallback` 恒空） |
| `_draw_text` 的换行 / 对齐 / 行高 / 上下标 / 下划线 | `typography.layout_text` + `plan._text_node`，逐句搬 |
| `_draw_shape` / `_draw_arrow` | `geometry.shape_paths` / `arrow_paths` |
| `_place_panel` | `plan._panel_node` → `ImportedPage` / `Image`（写入归 U07） |
| `compose().place(...)` 的 hidden 跳过 | `compile_page` 的 `dropped_hidden` |

## 3. 反证（每条门禁变异一次就红）

| 变异 | 红在 |
|---|---|
| `validate()` 去掉 NaN 检查 | `test_rendercore_ir` 的 `non_finite` 负例 |
| `compile_page` 按 `object_id` 排序 children | `test_paint_order_is_the_object_list_order…` |
| `compile_page` 再翻一次 y（`page_h - rect.y`） | `test_a_panel_lands_at_mm_to_pt_with_y_flipped_exactly_once` |
| `typography.coverage` 的 fallback 改成 `cjk.covers` | `test_layers_are_primary_cjk_missing_and_never_fallback` |
| `ir.py` 顶部加 `import pymupdf` | `test_rendercore_model` 的三条 + `test_import_architecture` 层规则 |
| `read_frozen` 不核 hash | `test_frozen_bytes_are_verified_at_read_time` |

## 4. 没做 / 边界

* 写入器与字体的 native 适配（`hbshaper` / `pdfwriter`）、依赖 extra、字体分发：U06 的第二个 PR（ADR 0060）。
* `Image` / `ImportedPage` 的写入、透明组 / 导入页 / 栅格 / PNG / TIFF：U07；`scope=original`、facade 19 项迁移：U08。
* 前端画布文字的同源预览（`canvasFontStack` 仍是 CSS 系统字体栈）：未做，见 U06 交接。
