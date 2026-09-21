# ADR 0067：facade 候选切换开关、执行侧源解析器与 ExportJob 接线（无静默回退）

日期：2026-09-21 · 状态：**Accepted（U08 第一切片；默认仍是 PyMuPDF，候选只在显式选中时接管）**
相关：[0059 Render IR 与 RenderPlan](0059-render-ir-and-render-plan.md)、[0060 字体政策](0060-font-policy-and-allowlist.md)、
[0065 合成](0065-imported-page-composition.md)、[0066 render child 与 RasterBuffer](0066-render-child-and-raster-buffer.md)、
[0053 合同与准备](0053-foundation-contracts-and-preparation.md)、[0031 导出](0031-unified-export-pipeline.md)、
[0046 EPS / TIFF](0046-eps-and-tiff-export-formats.md)、[0049 写回像素门](0049-write-back-pixel-verification.md)；
实施包 `docs/implementation/tavotto-foundation/`（`phases/U08_facade_validation.md`、`01_SCOPE_AND_DECISIONS.md`
D03 / D07 / D08、`06_ENABLE_AND_RELEASE.md` §1、registry RC-001 ~ RC-006、RC-054 ~ RC-060、RC-083 ~ RC-090、RC-093）、
`U00_FACADE_LEDGER.json`（19 项 + Canvas 面 + `candidate_parity`）。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 开关放在哪 | **契约层** `pdfbackend/__init__.py`：19 个契约名保持不变，每个函数显式委托给 `_impl()`，四个常量经 PEP 562 `__getattr__`；实现模块两个——`pdfbackend/pymupdf_backend.py`（默认）与 `rendercore/facade.py`（候选）。`app.py` / `artifactcheck` / `tutorial` / MCP bridge / 两个生成脚本这八处调用点**一行不改**就同时切换（RC-001：八个 probe 调用点一个不漏） | `pdfbackend/__init__.py`；`tests/test_rendercore_facade.py::test_the_default_backend_is_pymupdf_and_the_contract_names_follow_the_selection` |
| 怎么选 | `TAVOTTO_RENDER_BACKEND` ∈ {`pymupdf`（默认，未设 / 空串同义）, `rendercore`}；每次调用读环境（测试可按用例切）；不认识的取值当场 `BackendSelectionError(code=backend_unknown)`，**不猜、不退默认** | `test_an_unknown_backend_name_is_an_error_not_a_default` |
| 无静默回退 | 06 §1：选定即定。候选被选中而候选包 / 批准字体缺席 → `CandidatePackagesMissing` / `FontsUnavailable` 原样抛出，**绝不**换回 PyMuPDF——「静默回退」会让「候选后端能用」在任何机器上恒真。反证：把 `_impl()` 改成 try 候选失败就 import 旧后端，用例红 | `test_the_candidate_never_falls_back_to_pymupdf_when_its_fonts_are_missing` |
| 层规则 | importgraph 的 `pdfbackend` 层拆成 `pdfbackend_impl`（只有 `pymupdf_backend.py`：与新核心两个方向都不许有边，D03 原样）与 `pdfbackend`（契约层：全仓库唯一同时认识两个实现的模块，两条边登记在 baseline 的 `extra_edges`）；新核心整包仍零 `import pymupdf`、零边进 `pdfbackend/` | `tests/support/importgraph.py`、`tests/import_architecture_baseline.json`、`tests/test_rendercore_model.py` |
| 候选实现 | `rendercore/facade.py`：19 项同签名同返回结构（表见模块头）；`Canvas` 面是 RC-002 合同与老调用形态的适配器（`compile_page` → `pdfwriter` Canonical PDF → child 栅格一次 → PNG / TIFF 同一 buffer），与 `job.produce` 同一套函数，不是第二套实现 | `tests/test_rendercore_facade.py`（18 条：对拍 + 候选合同） |
| 产品导出路 | `app._export_produce`：`scope=canvas` 在候选下走 `rendercore.job.produce`（作业生命周期 `exportjob.run` 一字不改：临时目录 / 原子发布 / partial / 取消提交点 / 命名预留 / 覆盖 / report 失败都是既有权威）；`scope=original` 仍走 `_export_produce_original` → 契约层 `original_pdf / png / tiff`（自动切换）；EPS 在候选下报旧路同一个稳定码 `eps_not_for_canvas`（RC-090） | `tests/test_rendercore_app.py`（同步 / 异步 / EPS / 原图 / child 失败 partial / 写入器失败 500） |
| 执行侧源 | `sources.ExecutionSourceResolver(static, execute)`：带 override / runtime 素材的面板交给 `execute`，其余 `StaticSourceResolver`（磁盘原件不跑脚本，RC-019）；回来的不是 `origin=execution` 就拒（RC-017 must_fail：materialized cache 冒充）。app 层的 `execute` = `_serialize_figure_with_worker`（「谁来渲染」那扇门的唯一导出调用点）+ `receipt.from_worker` / **`receipt.from_native_session`**（新：native 会话按描述符元数据重建 spec，argv 只记数量、`source_revision` 空串不猜）+ `receipt.source_artifact_for`（`receipt_id` / `receipt_identity` / `patch_hash` 随源进 RenderPlan） | `test_a_panel_with_overrides_is_rendered_by_the_worker_and_carries_a_receipt`（真 worker、真 override、独立读取器抽文字） |
| 预览 | `/api/render` 在候选下走 `rendercore.preview.PreviewCache`（`facade.preview_cache(CACHE_DIR, identity=source_sha1)`：键 = 内容身份 + 后端 build + PDFium 版本 + 字体政策 + 像素参数；`source_sha1` 的 (mtime, size) memo 经 `identity=` 注入；缓存目录变了就重建实例）；child 有界队列满 → **503 + Retry-After: 1**（背压不是故障），别的 child 错误 → 500 带稳定 code | `test_api_render_under_the_candidate_serves_a_png_of_the_bucket_width_from_the_cache`、`…reports_backpressure_as_503_and_other_failures_as_500`、`test_switching_the_backend_changes_the_preview_cache_key` |
| 关停 | `reset_projects(wait=True)` 末尾 `renderhost.shutdown_shared()`：child 收掉并 reap（ADR 0066） | `test_reset_projects_reaps_the_render_child` |
| 像素比较器 | 尺子只有一份：`tavotto/pixelmetrics.py`（逐字从旧后端搬出）；两个后端的 `compare_png` 只换解码器（PyMuPDF Pixmap / `rasterio.decode`），三指标逐值相同 | `test_compare_png_gives_the_same_metrics_as_the_old_backend`；`tests/test_pixel_compare.py` 原样 |
| 覆盖表 / 向量 | 候选各有一份生成物：`rendercore/canvas_coverage.json`（`gen_canvas_coverage.py --backend rendercore`，`primary` = **12 张 Liberation 脸的交集**——前端读的是与族 / 字重无关的表）与 `tests/golden/glyph_plan_vectors.rendercore.json`（`gen_glyph_plan_vectors.py --backend rendercore`）；与默认表的差异钉成闭集（7 条样例：⁵ / ₂ 进 primary，⁻ / 😀 / 𝛼 变 missing，D07）。**默认表仍是前端读的那张**，切表归 U10 | `tests/test_rendercore_glyph_vectors.py` |
| 对拍纪律 | 旧契约用例在 `TAVOTTO_RENDER_BACKEND=rendercore` 下逐字重跑（`scripts/dev/u08_parity.py`，清单在 ledger `candidate_parity`）：23 个套件；14 条 deselect 全是实现特定断言、逐条带替代证据，**运行器拒绝没有替代证据的 deselect**；用户合同一条不删（D07 / RC-093） | `evidence/u08/parity.json` |
| 批准差异 | 见 §2 表 | — |

## 1. 为什么开关在契约层而不是 app.py

`pdfbackend/__init__.py` 的 docstring 从第一天就承诺「换用其它 PDF 库只需新写一个实现模块，HTTP 层一行不用动」。
U00 清点证实 `app.py` 确实只认契约名，但还有 `engine/artifactcheck.py`（两处）、`engine/tutorial.py`、`codex-plugin/mcp/tavotto_mcp/bridge.py`、
`scripts/gen_canvas_coverage.py`、`scripts/gen_glyph_plan_vectors.py` 也经契约层。开关放在 app.py 只切得动一个消费者，
其余仍走旧后端——「同一 PDF 在预览里是候选、在 MCP 探测里是旧后端」正是 RC-001 要挡的形状。放在契约层，一处开关
八个调用点同时换，U10 切默认只改一行 `BACKEND_DEFAULT`、删一个实现模块。

代价是契约层要认识两个实现模块。D03 的原意是「新核心不借旧库、旧后端不认识新核心」，这两条在层规则里原样保留
（`pdfbackend_impl` ↔ `rendercore_*` 两个方向零边）；契约层认识候选是**反方向**（消费者认识实现），不是新核心借旧库。

## 2. 与旧后端的批准差异（对拍表）

| 项 | 旧后端 | 候选 | 性质 |
|---|---|---|---|
| `BACKEND_NAME` / `BACKEND_VERSION` | `pymupdf` / `1.28.2` | `rendercore` / `0.1` | 有意：`/api/render` 旧缓存键天然失效 |
| 文字层归属 | 四层（含隐式回退脸 Noto Serif） | 三层，`fallback` 恒空；⁵ / ₂ 进 primary（Liberation 自带），⁻ / 😀 / 𝛼 变 missing | D07 批准迁移（ADR 0060 §1 / §4） |
| 文字基线 | Times bbox ascender 1.053 | Liberation OS/2 typo ascender 0.693（12 pt 差 1.77 pt） | D07 批准（校准用例钉「恰好等于这个量」） |
| 面板 opacity < 1 / flip | 退位图 | 透明组 / 负缩放，保矢量 | 有意（ADR 0065） |
| 矢量源栅格化的 PNG | pHYs 写 96（PyMuPDF 默认） | pHYs 写真实 ppi | 有意：文件说的就是它是什么 |
| `probe_asset(kind="pdf")` 的 /UserUnit | `page.rect` **也乘**了（实测 540 = 2 × 270） | child 的 probe 乘一次 | **无差异**——U07 交接里那条「有意差异」实测不成立，划掉 |
| `annotate_asset` 的落盘 | `incremental=True` 追加 | pikepdf 整份重写（原内容流与资源不动） | 实现细节；产物语义相同（源文字层在、覆盖层是 Form） |
| 画布 EPS | `eps_not_for_canvas` | 同一个码（params 里多带结构化 `unsupported`） | 无差异 |
| PDFium 对 Type 3 字形的栅格 | — | `test_mcp_normalize.py::test_zero_edit_roundtrip…[two]` 的 2% 阈值在 PDFium 下是 3.3% | 03 §6「需要校准」：按 case 记，不全局放大；页面尺寸那一半两边都过 |

## 3. 反证（每条变异一次就红，`scratchpad/u08/mutate_a.py`）

| 变异 | 红在 |
|---|---|
| `_impl()` 候选失败时 import 旧后端（静默回退） | `test_the_candidate_never_falls_back_to_pymupdf_when_its_fonts_are_missing` |
| 不认识的取值退默认 | `test_an_unknown_backend_name_is_an_error_not_a_default` |
| `ExecutionSourceResolver` 不核 origin | `tests/test_rendercore_sources.py`（PR A 追加的负例） |
| `_export_produce` 候选下仍走旧 `compose` | `test_export_canvas_under_the_candidate_writes_a_searchable_pdf_and_a_png_from_it`（PNG 不再等于再栅格一次的 Canonical PDF） |
| `job.produce` EPS 报 `format_failed` | `test_export_canvas_eps_under_the_candidate_reports_the_old_stable_code` |
| `/api/render` 候选下 `render_queue_full` 回 500 | `…reports_backpressure_as_503…` |
| `reset_projects` 不收 child | `test_reset_projects_reaps_the_render_child` |
| `original_png` PNG 源走 decode + encode | `test_original_png_copies_a_png_source_byte_for_byte_and_transcodes_jpeg` |
| `original_pdf` 搬全部页 / 重画 | `test_original_pdf_moves_only_the_first_page_and_does_not_redraw_it` |
| `original_tiff` 位图源没 dpi_meta 也写 ppi | `test_original_tiff_writes_only_the_declared_density_and_keeps_the_pixel_grid` |
| `Canvas.save_png` 重新编译 / 另画一页 | `test_the_canvas_face_saves_png_and_tiff_from_one_canonical_pdf` |
| `annotate_asset` 静默丢面板 | `test_annotate_asset_refuses_panel_objects` |
| `pdf_fonts` 不进 Form / 没有预算 | `test_pdf_fonts_matches_the_old_backend_including_fonts_inside_form_xobjects`、`…has_a_budget_for_self_referencing_forms` |
| `coverage_ranges` 只取 serif-regular | `test_the_candidate_primary_layer_is_the_intersection_of_all_twelve_faces` |
| ledger 里 deselect 一条没有替代证据 | `scripts/dev/u08_parity.py --check` 退出 2；`tests/test_foundation_facade_ledger.py::test_candidate_parity_deselections_have_replacement_evidence` |

## 4. 没做 / 边界

* 默认后端不切（`BACKEND_DEFAULT = pymupdf`）；前端不读候选覆盖表；产品包不带候选包 / 字体（U10 / U11）。
* 候选下 `_write_render_cache` / `_publish_render_cache` / `source_sha1` 那三段在 `app.py` 原地保留给默认路；
  U10 切默认时删。
* `ExecutionSourceResolver` 只在 `scope=canvas` 用；`scope=original` 的执行侧仍是 `_resolve_panel_source`（旧权威，
  不复制），产物经契约层的 `original_*` 自动切换。
* 有限产物验证（ArtifactInspector / Manifest、D08 政策）归 U08 第二切片（ADR 0068）；入口审计（前端回执 / i18n /
  MCP 版本探针）归第三切片。
