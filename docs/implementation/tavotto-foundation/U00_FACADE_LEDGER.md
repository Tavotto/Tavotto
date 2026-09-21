# pdfbackend facade 迁移清单（生成物）

真值在 `U00_FACADE_LEDGER.json`（`tools/generate_facade_ledger.py` 派生本文件，手改无效）。
采样 SHA `319a506dff5a003d85f02ad7ac55504ec3a9d1f9`（2026-09-20）；facade `src/tavotto/pdfbackend/__init__.py`，实现 `src/tavotto/pdfbackend/pymupdf_backend.py`。

## 类别

| 类别 | 含义 |
|---|---|
| `probe` | 素材探测（尺寸 / alpha / 字体名），只读 |
| `preview_cache` | 画布预览位图（带磁盘缓存，缓存键含 BACKEND_NAME/VERSION） |
| `text_metrics` | 画布文字度量：宽度、字形归属、缺字、覆盖表 |
| `compose` | 画布合成（scope=canvas）：一页画布 place → save_pdf/png/tiff |
| `original_vector_copy` | 原图 PDF：矢量整页搬运不重画（insert_pdf） |
| `original_png_byte_copy` | 原 PNG 逐字节复制（含 pHYs 等元数据），JPEG 只换容器 |
| `original_native_grid` | 原位图保源像素网格（TIFF 换容器 / PDF 装容器），不重采样 |
| `annotation_writeback` | 写回原图携带画布标注（PDF 矢量 + 由同一份 PDF 栅格化的 PNG） |
| `compare_png` | 写回像素门的比较器（逐 RGBA 通道） |
| `identity` | 后端身份 / 单位换算 / 闭集常量（进缓存键、严格同源对） |
| `worker_direct` | **不经 facade**：worker 的 matplotlib 直接序列化（SVG / EPS，及要了 EPS 时的 PDF/PNG/TIFF） |
| `cancel_partial` | **不经 facade**：导出作业的取消 / partial / 终局字段顺序（engine/exportjob.py） |

## 测试分类

| 类 | 含义 |
|---|---|
| `user_contract` | 用户合同：说的是用户能观察到的行为（尺寸、文件类型、像素网格不变、文本可检索、写回原件零改动…）。换后端后**逐字保留**。 |
| `implementation_specific` | 实现特定断言：钉的是 PyMuPDF 的内部对象 / base-14 字体名 / get_drawings() 形状 / 私有函数。换后端时在迁移 ADR 里逐条换成新实现的独立证据（03 §6、01 §3）。 |

## `__all__` 的 19 个导出项

| 导出项 | 类别 | 状态 | 产品调用方（file:line · 函数） | 现有测试（类） | 已知缺陷 | 迁移判据 |
|---|---|---|---|---|---|---|
| `BACKEND_NAME` | `identity` | public_contract | `src/tavotto/app.py:870` · `api_render`<br>`scripts/gen_canvas_coverage.py:50` · `build` | （无） | — | U08：新后端给出不同的 BACKEND_NAME；缓存键因此天然失效（旧预览不复用）。用例：同一 PDF 换后端名后 /api/render 必须重画（test_render_cache 的键用例扩一维）。 |
| `BACKEND_VERSION` | `identity` | public_contract | `src/tavotto/app.py:870` · `api_render`<br>`scripts/gen_canvas_coverage.py:51` · `build`<br>`scripts/gen_canvas_coverage.py:97` · `main`<br>`scripts/gen_glyph_plan_vectors.py:95` · `build` | `tests/test_glyph_plan.py:183` (implementation_specific) | — | U08：新后端报自己的版本串；canvas_coverage.json 与 glyph_plan_vectors.json 必须由新后端重生成（--check 红即证明表在跟着后端走）。 |
| `CANVAS_TEXT_FAMILIES` | `identity` | public_contract | `scripts/gen_glyph_plan_vectors.py:59` · `<module>` | `tests/test_typography_families.py:28` (user_contract)<br>`tests/test_glyph_plan.py:126` (user_contract)<br>`tests/test_font_provenance.py:131` (user_contract)<br>`tests/test_scientific_text_matrix.py:155` (user_contract) | — | U06/U08：闭集**是一句能力承诺**——新后端画得出什么，闭集就是什么，不能照抄（facade 文档原话）。三个通用族 + 粗 / 斜 / 粗斜必须由批准字体集合兑现；前端摆得出的后端必须画得出（test_typography_families 保留）。 |
| `COVERAGE_MAX_CP` | `text_metrics` | public_contract | `scripts/gen_canvas_coverage.py:52` · `build` | （无） | — | U06：新字体集合的覆盖上界由新后端给；生成器与 glyphPlan.ts 读同一个数。 |
| `annotate_asset` | `annotation_writeback` | public_contract | `src/tavotto/app.py:4243` · `_write_source_files` | `tests/test_annotate_asset.py:26` (user_contract)<br>`tests/test_annotate_asset.py:115` (implementation_specific) | #252 原图写回缺 fsync（原子性已有；不归本项） | U08：同一组 objects 在新后端上写进 PDF，**独立读取器**（U01 选定，不是生产 transform 反算）读回文字与路径位置在容差内；PNG 由同一 PDF 栅格化；只有 PNG 的素材仍回 annotations_need_pdf（app 层判据不动）。 |
| `compare_png` | `compare_png` | public_contract | `src/tavotto/app.py:4092` · `_replay_pixel_diff` | `tests/test_pixel_compare.py:45` (user_contract)<br>`tests/test_pixel_compare.py:163` (user_contract)<br>`tests/test_pixel_compare.py:192` (implementation_specific)<br>`tests/test_mcp_normalize.py:287` (user_contract) | #265 持久 tight 布局图上像素门比的是两张不可复现的渲染（判据问题，不是比较器） | U07/U08：PNG 解码换成新后端（或纯标准库 zlib 解码）后，三指标在 tests/test_pixel_compare.py 的全部样例上逐值相同；对拍用例保留。 |
| `compose` | `compose` | public_contract | `src/tavotto/app.py:1155` · `_export_produce_canvas` | `tests/test_typography_families.py:96` (user_contract)<br>`tests/test_glyph_plan.py:46` (user_contract)<br>`tests/test_export_endpoint.py:297` (user_contract)<br>`tests/test_export_pipeline.py:243` (user_contract)<br>`tests/test_export_pipeline.py:1291` (user_contract) | — | U07：RenderPlan → IR → 新合成器实现同一个 Canvas 面（见 canvas_methods）；PDF 真矢量、PNG/TIFF 出自同一次栅格化；透明背景 = 不画白底 + PNG 带 alpha。 |
| `coverage_ranges` | `text_metrics` | public_contract | `scripts/gen_canvas_coverage.py:53` · `build` | `tests/test_glyph_plan.py:184` (user_contract) | — | U06：由批准字体集合现算三层区间；`gen_canvas_coverage.py --check` 在新后端上必须先红（表变了）再由 --write 更新，PR 里附 diff。 |
| `hex2rgb` | `identity` | exported_without_product_caller | （无产品调用方） | `tests/test_paths_and_baked.py:320` (user_contract) | — | U06：纯函数原样搬到与后端无关的模块（IR 层）；四条取值用例逐字保留。 |
| `missing_glyphs` | `text_metrics` | public_contract | `scripts/gen_glyph_plan_vectors.py:84` · `build` | `tests/test_glyph_plan.py:59` (user_contract)<br>`tests/test_glyph_plan.py:111` (user_contract)<br>`tests/test_scientific_text_matrix.py:159` (user_contract) | — | U06：新字体集合下 tests/test_scientific_text_matrix.py 必须仍为空缺字；golden 向量按 D07 批准一次迁移（缺字集合可以变小，不许变大而不说）。 |
| `mm2pt` | `identity` | public_contract | `src/tavotto/app.py:1336` · `_original_page_pt`<br>`src/tavotto/app.py:1336` · `_original_page_pt` | `tests/test_export_endpoint.py:116` (user_contract)<br>`tests/test_compose_text.py:81` (implementation_specific)<br>`tests/test_compose_arrow.py:41` (implementation_specific) | — | U06：纯换算搬到 IR 层；页面尺寸类断言（user_contract）逐字保留。 |
| `original_pdf` | `original_vector_copy` | public_contract | `src/tavotto/app.py:1246` · `_export_produce_original` | `tests/test_export_pipeline.py:129` (user_contract)<br>`tests/test_export_pipeline.py:100` (user_contract)<br>`tests/test_export_pipeline.py:713` (user_contract)<br>`tests/test_export_pipeline.py:903` (user_contract)<br>`tests/test_export_request.py:169` (user_contract) | — | U08（RC-002/003）：矢量源**整页搬运不重画**——新后端输出的页面尺寸、字体子集、路径数与源页一致（独立读取器比）；位图源 vector=False 且页面 = page_pt；original 段里没有 x/y/w/h（结构不变）。多页 PDF 只取第一页（与画布所见一致）。 |
| `original_png` | `original_png_byte_copy` | public_contract | `src/tavotto/app.py:1273` · `_export_produce_original` | `tests/test_export_pipeline.py:140` (user_contract)<br>`tests/test_export_pipeline.py:161` (user_contract)<br>`tests/test_export_pipeline.py:892` (user_contract)<br>`tests/test_export_pipeline.py:871` (user_contract) | — | U08（RC-003 / 03 §6「必须精确」）：PNG 源 → 输出**逐字节相同**（shutil.copyfile 语义，pHYs 保留）；JPEG 源 → 像素数不变、签名是 PNG；矢量源栅格化尺寸 = round(pt × ppi / 72)；`resampled` 恒 False。 |
| `original_tiff` | `original_native_grid` | public_contract | `src/tavotto/app.py:1265` · `_export_produce_original` | `tests/test_export_pipeline.py:1304` (user_contract)<br>`tests/test_export_pipeline.py:1316` (user_contract)<br>`tests/test_export_pipeline.py:1266` (user_contract) | — | U08：位图源像素网格不变（px_w/px_h == 源）、TIFF 解码像素 == 同源 PNG 解码像素（03 §6）、分辨率标签只在源声明过时才写；矢量源与 original_png 同一次栅格化参数。编码器 tiffwrite.py 纯标准库，本身不属于后端。 |
| `pdf_fonts` | `probe` | public_contract | `src/tavotto/engine/artifactcheck.py:82` · `check_pdf` | `tests/test_mcp_normalize.py:325` (user_contract) | — | U08：读取侧换成新后端 / 独立读取器，同一 PDF 返回同一列表（含子集前缀处理与保序）。 |
| `probe_asset` | `probe` | public_contract | `src/tavotto/app.py:551` · `scan_panels`<br>`src/tavotto/app.py:1337` · `_original_page_pt`<br>`src/tavotto/app.py:1353` · `_declared_density`<br>`src/tavotto/app.py:4351` · `_post_check_size`<br>`src/tavotto/engine/artifactcheck.py:80` · `check_pdf`<br>`src/tavotto/engine/artifactcheck.py:149` · `check_raster`<br>`src/tavotto/engine/tutorial.py:573` · `validate_tutorial_resources`<br>`codex-plugin/mcp/tavotto_mcp/bridge.py:1982` · `_original_artifact_facts`<br>`codex-plugin/mcp/tavotto_mcp/bridge.py:1858` · `_probe_asset` | `tests/test_original_spec.py:122` (user_contract)<br>`tests/test_export_pipeline.py:786` (user_contract)<br>`tests/test_mcp_normalize.py:282` (user_contract) | — | U08：八个调用点一个不漏（RC-001）；返回结构逐键相同；`alpha` 真值来自新读取器；密度仍由 originalspec 解析。 |
| `render_preview_png` | `preview_cache` | public_contract | `src/tavotto/app.py:319` · `_write_render_cache` | `tests/test_render_cache.py:196` (user_contract)<br>`tests/test_mcp_normalize.py:285` (user_contract) | — | U07/U08：新栅格运行时（PDFium 候选）按同一宽度出 PNG；缓存层不动；跨 renderer 的抗锯齿差异按 03 §6「需要校准」记录阈值，不全局放大。 |
| `text_plan` | `text_metrics` | public_contract | `scripts/gen_glyph_plan_vectors.py:82` · `build` | `tests/test_glyph_plan.py:57` (user_contract)<br>`tests/test_glyph_plan.py:87` (implementation_specific)<br>`tests/test_glyph_plan.py:93` (implementation_specific)<br>`tests/test_glyph_plan.py:110` (user_contract)<br>`tests/test_font_provenance.py:141` (user_contract) | — | U06：四层顺序不可交换（glyphplan.py 不动）；oracle 换成新字体集合；哪些字符落在哪一层按 D07 批准一次迁移并附向量 diff。 |
| `text_width` | `text_metrics` | exported_without_product_caller | （无产品调用方） | `tests/test_typography_families.py:115` (user_contract)<br>`tests/test_glyph_plan.py:175` (user_contract) | — | U06：Typography 层给出同一签名；「量宽用的族 == 落笔用的族」这条用户合同保留。 |

### 各项备注

- **`BACKEND_NAME`**：现值 `pymupdf`；U08 迁移证据 `tests/test_rendercore_facade.py::test_the_default_backend_is_pymupdf_and_the_contract_names_follow_the_selection`；U08 迁移证据 `tests/test_rendercore_app.py::test_switching_the_backend_changes_the_preview_cache_key`
- **`BACKEND_VERSION`**：现值 `pymupdf.__version__（1.28.2）`；U08 迁移证据 `tests/test_rendercore_facade.py::test_the_default_backend_is_pymupdf_and_the_contract_names_follow_the_selection`；U08 迁移证据 `tests/test_rendercore_glyph_vectors.py::test_the_candidate_generators_check_clean_against_the_live_fonts`
- **`CANVAS_TEXT_FAMILIES`**：现值 `("serif", "sans-serif", "monospace")`；严格同源对：web/src/lib/typography.ts CANVAS_TEXT_FAMILIES；U06 迁移证据 `tests/test_rendercore_typography.py::test_the_family_closed_set_and_default_match_the_old_facade`；U06 迁移证据 `tests/test_rendercore_fonts.py::test_allowlist_is_the_thirteen_ofl_faces_with_sha256_identity`；U08 迁移证据 `tests/test_rendercore_facade.py::test_text_metrics_agree_with_the_old_backend_within_the_approved_migration`；U08 迁移证据 `tests/test_rendercore_glyph_vectors.py::test_the_candidate_primary_layer_is_the_intersection_of_all_twelve_faces`
- **`COVERAGE_MAX_CP`**：现值 `0x30000`；U06 迁移证据 `tests/test_rendercore_typography.py::test_the_family_closed_set_and_default_match_the_old_facade`；U08 迁移证据 `tests/test_rendercore_facade.py::test_text_metrics_agree_with_the_old_backend_within_the_approved_migration`
- **`annotate_asset`**：签名 `annotate_asset(pdf_path, png_path | None, objects, dpi=600) -> None`；U08 迁移证据 `tests/test_rendercore_facade.py::test_annotate_asset_overlays_vector_annotations_without_redrawing_the_source`；U08 迁移证据 `tests/test_rendercore_facade.py::test_annotate_asset_refuses_panel_objects`；U08 迁移证据 `tests/test_rendercore_app.py::test_writeback_with_annotations_under_the_candidate_overlays_the_staged_pdf_then_commits`；U08 迁移证据 `tests/test_rendercore_app.py::test_writeback_with_annotations_fails_closed_when_the_staged_pdf_is_unusable`
- **`compare_png`**：签名 `compare_png(baseline, candidate) -> {ok, changed_pixel_ratio, mean_abs_diff, max_abs_diff, ...}`；U08 迁移证据 `tests/test_rendercore_facade.py::test_compare_png_gives_the_same_metrics_as_the_old_backend`
- **`compose`**：签名 `compose(page_w_mm, page_h_mm, transparent=False) -> Canvas`；U07 迁移证据 `tests/test_rendercore_compose.py::test_the_visible_box_is_the_crop_box_not_the_media_box`；U07 迁移证据 `tests/test_rendercore_compose.py::test_crop_flip_rotate_combinations_land_each_quadrant_where_the_contract_says`；U07 迁移证据 `tests/test_rendercore_compose.py::test_panel_opacity_is_a_transparency_group_not_per_object_alpha`；U07 迁移证据 `tests/test_rendercore_compose.py::test_a_mirrored_import_keeps_its_vector_and_text_layer`；U07 迁移证据 `tests/test_rendercore_compose.py::test_png_with_alpha_is_placed_with_an_smask_and_its_pixels_survive`；U07 迁移证据 `tests/test_rendercore_job.py::test_a_panel_canvas_exports_a_vector_pdf_with_the_source_page_inside`；U07 迁移证据 `tests/test_rendercore_rasterize.py::test_pdf_png_and_tiff_come_from_one_canonical_pdf_and_one_raster`；U07 迁移证据 `tests/test_rendercore_rasterize.py::test_transparent_background_yields_rgba_with_straight_alpha_in_both`；U07 迁移证据 `tests/test_rendercore_calibration.py::test_old_and_new_backends_agree_within_the_case_thresholds`；U08 迁移证据 `tests/test_rendercore_facade.py::test_the_canvas_face_saves_png_and_tiff_from_one_canonical_pdf`；U08 迁移证据 `tests/test_rendercore_app.py::test_export_canvas_under_the_candidate_writes_a_searchable_pdf_and_a_png_from_it`；U08 迁移证据 `tests/test_rendercore_app.py::test_a_panel_with_overrides_is_rendered_by_the_worker_and_carries_a_receipt`；U08 迁移证据 `tests/test_rendercore_app.py::test_a_child_failure_under_the_candidate_is_partial_and_leaves_nothing_behind`；U08 迁移证据 `tests/test_rendercore_app.py::test_export_under_the_candidate_verifies_text_layer_fonts_and_carrier_from_the_plan`；U08 迁移证据 `tests/test_rendercore_app.py::test_strict_export_under_the_candidate_accepts_clean_text_and_rejects_low_ppi_panels`
- **`coverage_ranges`**：签名 `coverage_ranges() -> {layer: [[start, end], ...]}`；U06 迁移证据 `tests/test_rendercore_typography.py::test_layers_are_primary_cjk_missing_and_never_fallback`；U06 迁移证据 `tests/test_rendercore_evidence.py::test_coverage_table_has_no_fallback_layer_and_records_the_known_limits`；U08 迁移证据 `tests/test_rendercore_glyph_vectors.py::test_the_candidate_generators_check_clean_against_the_live_fonts`；U08 迁移证据 `tests/test_rendercore_glyph_vectors.py::test_the_candidate_primary_layer_is_the_intersection_of_all_twelve_faces`；U08 迁移证据 `tests/test_rendercore_glyph_vectors.py::test_candidate_vectors_differ_from_the_default_only_in_the_approved_ways`
- **`hex2rgb`**：签名 `hex2rgb(s) -> (r, g, b) 0..1`；产品代码里只有实现模块内部用（_draw_* 系列）；导出是给实现模块之间复用的纯换算。；U06 迁移证据 `tests/test_rendercore_ir.py::test_hex2rgb_keeps_the_old_facade_contract`；U08 迁移证据 `tests/test_rendercore_facade.py::test_the_contract_names_in_all_are_the_ledger_nineteen`
- **`missing_glyphs`**：签名 `missing_glyphs(s, family="serif", bold=False, italic=False) -> list[str]`；engine/manifest.py:1194 有同名函数 missing_glyphs(text, families)——那是 worker 侧问 matplotlib 字体的判据，与本项不同源、不同进程，不是第二份实现。；U06 迁移证据 `tests/test_rendercore_typography.py::test_missing_and_cjk_characters_are_reported_on_the_block`；U06 迁移证据 `tests/test_rendercore_writer.py::test_a_missing_glyph_is_written_as_notdef_and_reported_not_substituted`；U08 迁移证据 `tests/test_rendercore_facade.py::test_text_metrics_agree_with_the_old_backend_within_the_approved_migration`；U08 迁移证据 `tests/test_rendercore_glyph_vectors.py::test_candidate_vectors_differ_from_the_default_only_in_the_approved_ways`
- **`mm2pt`**：签名 `mm2pt(mm) -> pt (×72/25.4)`；U06 迁移证据 `tests/test_rendercore_ir.py::test_mm2pt_is_the_one_conversion`；U06 迁移证据 `tests/test_rendercore_plan.py::test_a_panel_lands_at_mm_to_pt_with_y_flipped_exactly_once`；U08 迁移证据 `tests/test_rendercore_facade.py::test_the_canvas_face_saves_png_and_tiff_from_one_canonical_pdf`
- **`original_pdf`**：签名 `original_pdf(src, out, page_pt=None) -> {w_pt, h_pt, px_w, px_h, vector, pages}`；U08 迁移证据 `tests/test_rendercore_facade.py::test_original_pdf_moves_only_the_first_page_and_does_not_redraw_it`；U08 迁移证据 `tests/test_rendercore_facade.py::test_original_pdf_of_a_raster_source_fills_the_given_page_and_is_not_vector`
- **`original_png`**：签名 `original_png(src, out, ppi, transparent=False) -> {px_w, px_h, resampled, transcoded}`；U08 迁移证据 `tests/test_rendercore_facade.py::test_original_png_copies_a_png_source_byte_for_byte_and_transcodes_jpeg`；U08 迁移证据 `tests/test_rendercore_app.py::test_export_original_under_the_candidate_copies_the_png_byte_for_byte`
- **`original_tiff`**：签名 `original_tiff(src, out, ppi, transparent=False, *, dpi_meta=None) -> {px_w, px_h, resampled, transcoded}`；没有直接以 pdfbackend.original_tiff 为主语的单元用例，覆盖全经端点 / 作业管线；RC-001 的 must_fail_example 点名它：漏它不能被总通过掩盖。编码器 tests/test_tiffwrite.py 单独看护。；U08 迁移证据 `tests/test_rendercore_facade.py::test_original_tiff_writes_only_the_declared_density_and_keeps_the_pixel_grid`
- **`pdf_fonts`**：签名 `pdf_fonts(path) -> list[str]（去子集前缀、去重、保序）`；U08 迁移证据 `tests/test_rendercore_facade.py::test_pdf_fonts_matches_the_old_backend_including_fonts_inside_form_xobjects`；U08 迁移证据 `tests/test_rendercore_facade.py::test_pdf_fonts_has_a_budget_for_self_referencing_forms`
- **`probe_asset`**：签名 `probe_asset(path, kind) -> {kind, w_pt, h_pt} | {kind, px_w, px_h, alpha}`；**密度刻意不从这里取**：MuPDF 的 Pixmap.xres 对「没写 pHYs」与「写着 96」一律回 96（project-system.md）。U00 夹具 pdf_png_assets 的 original_nophys.png 实测 xres=96、original.png（pHYs 300）xres=300，再次证实。；U08 实测：PyMuPDF 1.28.2 的 page.rect 同样乘 /UserUnit（540 = 2×270），U07 交接里「probe 含 UserUnit 是有意差异」不成立，两边一致。；U07 迁移证据 `tests/test_rendercore_renderchild.py::test_real_child_probe_reports_the_visible_size_with_rotation_and_userunit`；U08 迁移证据 `tests/test_rendercore_facade.py::test_probe_asset_returns_the_same_structure_and_values_on_the_fixtures`；U08 迁移证据 `tests/test_rendercore_facade.py::test_probe_pdf_applies_userunit_exactly_once_like_the_old_backend`
- **`render_preview_png`**：签名 `render_preview_png(path, width_px, out) -> None`；U07 迁移证据 `tests/test_rendercore_preview.py::test_a_real_pdf_preview_is_rendered_by_the_child_at_the_bucket_width`；U07 迁移证据 `tests/test_rendercore_preview.py::test_touching_the_file_keeps_the_key_but_changing_its_bytes_changes_it`；U07 迁移证据 `tests/test_rendercore_preview.py::test_the_key_carries_width_background_renderer_build_and_fonts_policy`；U07 迁移证据 `tests/test_rendercore_preview.py::test_a_render_failure_raises_and_leaves_no_blank_or_stale_file`；U07 迁移证据 `tests/test_rendercore_preview.py::test_publish_yields_to_a_reader_holding_the_target_on_windows`；U08 迁移证据 `tests/test_rendercore_facade.py::test_render_preview_png_renders_the_first_page_at_the_bucket_width`；U08 迁移证据 `tests/test_rendercore_app.py::test_api_render_under_the_candidate_serves_a_png_of_the_bucket_width_from_the_cache`；U08 迁移证据 `tests/test_rendercore_app.py::test_api_render_under_the_candidate_reports_backpressure_as_503_and_other_failures_as_500`
- **`text_plan`**：签名 `text_plan(s, family="serif", bold=False, italic=False) -> list[(segment, layer)]`；严格同源对：src/tavotto/glyphplan.py ↔ web/src/lib/glyphPlan.ts（算法同源、oracle 不同源）；U06 迁移证据 `tests/test_rendercore_typography.py::test_layers_are_primary_cjk_missing_and_never_fallback`；U06 迁移证据 `tests/test_rendercore_typography.py::test_degree_sign_below_the_cjk_break_point_still_reaches_the_cjk_face_last`；U08 迁移证据 `tests/test_rendercore_facade.py::test_text_metrics_agree_with_the_old_backend_within_the_approved_migration`；U08 迁移证据 `tests/test_rendercore_glyph_vectors.py::test_candidate_vectors_differ_from_the_default_only_in_the_approved_ways`
- **`text_width`**：签名 `text_width(s, size_pt, bold=False, italic=False, family="serif") -> pt`；产品代码里没有 pdfbackend.text_width 的调用点：换行 / 量宽在实现模块内部经 _mixed_width 走（_draw_text）。前端画布文字的量宽在浏览器里自己排（TextView）。契约层保留它是为了让 MCP / 预检将来能问同一把尺。；U06 迁移证据 `tests/test_rendercore_typography.py::test_width_is_the_sum_of_shaped_advances_of_the_same_plan`；U06 迁移证据 `tests/test_rendercore_writer.py::test_pen_position_between_runs_equals_the_shaped_advance`；U08 迁移证据 `tests/test_rendercore_facade.py::test_text_metrics_agree_with_the_old_backend_within_the_approved_migration`

## Canvas 面（RC-002）

RC-002：compose() 返回的画布对象是 facade 唯一泄漏的后端对象，只经这几个方法使用。

| 方法 | 签名 | 调用方 | 作用 |
|---|---|---|---|
| `place` | `place(o, dpi, resolve_panel) -> None` | `src/tavotto/app.py:1168` · `_export_produce_canvas` | 按对象类型落一个元素：panel（PDF 真矢量 show_pdf_page；位图带 crop/rotation 经 convert_to_pdf；opacity<1 或 flip 时按 dpi 位图嵌入）/ text / arrow / shape |
| `size_pt` | `size_pt -> (w_pt, h_pt)` | `src/tavotto/app.py:1151` · `_export_produce_canvas` | 样式检查报告里的页面尺寸 |
| `save_pdf` | `save_pdf(path) -> None` | `src/tavotto/app.py:1168` · `_export_produce_canvas` | 真矢量 PDF（deflate） |
| `save_tiff` | `save_tiff(path, dpi) -> dict` | `src/tavotto/app.py:1180` · `_export_produce_canvas` | 与 save_png 同一页同一次栅格化参数（ADR 0046）；编码器 tiffwrite.py |
| `save_png` | `save_png(path, dpi) -> None` | `src/tavotto/app.py:1182` · `_export_produce_canvas` | 同一页渲染 |
| `close` | `close() -> None` | `src/tavotto/app.py:1204` · `_export_produce_canvas` | finally 里关文档 |
| `__enter__/__exit__` | `context manager` | `tests/test_typography_families.py:96` · `test_the_family_reaches_the_pdf_font_resources`<br>`tests/test_glyph_plan.py:46` · `_place` | 测试里的 with 形态；app.py 用显式 close() |

迁移判据：U07：同实例多格式保存、重复关闭 / 错误后关闭的用例（RC-002 verification）；save_png 不许重新解析另一份来源（must_fail_example）。

## 不经 facade 的路径

这些路径**不经 facade**，替换 PyMuPDF 时它们不变——但它们是 facade 边界之外「谁还碰 PDF/位图」的完整名单，U10 的退役扫描主语要把它们分清。

| 路径 | 类别 | 说明 |
|---|---|---|
| `src/tavotto/app.py:942 _serialize_figure` | `worker_direct` | worker 的 matplotlib savefig：SVG / EPS 只从这里出；要了 EPS 时 PDF/PNG/TIFF 也让 worker 现画（_resolve_panel_source(rerender=True)），四个格式出自同一次脚本运行。 |
| `src/tavotto/app.py:1213 _produce_original_eps` | `worker_direct` | scope=original 的 EPS；没有脚本报 eps_needs_script。 |
| `src/tavotto/engine/exportjob.py` | `cancel_partial` | 作业生命周期：临时目录 → 全部产出 → atomicio.publish_file 逐个原子 replace；partial 独立一档；取消清临时文件；终局字段先于 status 可见（#381 修复，本轮差异清点里）。 |
| `src/tavotto/tiffwrite.py` | `original_native_grid` | 纯标准库 TIFF 编码器（Deflate）；父进程没有 Pillow，别为它引进 Pillow。 |
| `src/tavotto/engine/epsfile.py` | `worker_direct` | EPS 文件的 BoundingBox 读取（artifactcheck.check_eps）。 |
| `src/tavotto/engine/originalspec.py` | `probe` | 位图密度（pHYs / JFIF / Exif）纯标准库解析——唯一出处，后端不认识密度。 |
| `scripts/ci/pixelcompare.py` | `compare_png` | CI 的灰度像素比较（numpy + Pillow）；与 compare_png 同构不同源，对拍用例钉交集。 |
| `scripts/ci/compat_matrix.py` | `probe` | CompatBench 驱动直接 import pymupdf 读产物（CI 工具，不在发行闭包里）。 |
| `scripts/build_brand_assets.py / build_dmg_background.py / build_installer_assets.py / recover_frac_positions.py` | `probe` | 构建 / 维护脚本直接 import pymupdf（不在发行闭包里；D15：退役扫描的主语是应用 / 发行闭包）。 |

## 测试对实现模块的直接依赖

测试直接碰实现模块（pymupdf_backend）而不是 facade 的名字——迁移 ADR（U08/U10）要逐条给出新实现的独立证据或删除。

- 私有名：`_crop_clip (tests/test_paths_and_baked.py)`, `_mixed_width (tests/test_compose_text.py)`, `_draw_text (tests/test_compose_text.py)`, `_draw_arrow (tests/test_compose_arrow.py)`
- 非 facade 的公开名：`latin_font`, `latin_family`, `cjk_font`, `get_font`, `PNG_NOISE_FLOOR`
- 涉及文件：`tests/test_compose_text.py`, `tests/test_compose_arrow.py`, `tests/test_paths_and_baked.py`, `tests/test_typography_families.py`, `tests/test_glyph_plan.py`, `tests/test_font_provenance.py`, `tests/test_pixel_compare.py`
- 直接 `import pymupdf` 的测试文件数：37；角色：绝大多数只把 pymupdf 当**读取器**（打开产物读页面尺寸 / 文本 / get_drawings）——U01 选定独立读取栈后可整体替换；少数（test_compose_*）读 base-14 字体度量反算期望，属 D07 要批准迁移的实现特定断言。

## 只在注释里承诺的

| 出处 | 承诺 | 现实 |
|---|---|---|
| src/tavotto/pdfbackend/__init__.py 模块 docstring | 「换用其它 PDF 库只需新写一个实现模块，HTTP 层一行不用动」 | app.py 只认 facade 名字这一条成立（本清单 0 处直接 import pymupdf）；但 37 个测试文件直接 import pymupdf、7 个测试文件碰实现模块内部名——「上层零改动」对测试不成立，是 U08/U10 的迁移量。 |
| pymupdf_backend._CJK_FACE 注释 | 「衬线中文 / 无衬线中文在这条路上不是一个真实的选择」 | 实测成立（四个别名同一张 Droid Sans Fallback）；新字体集合若提供两张 CJK 脸，这条注释与 _LAYER_CACHE「键里没有字体」的假设都要改（test_every_base14_face_shares_one_charset 会先红）。 |
| docs/rules/backend/figure-capture-and-execution.md | savefig 的 kwargs（bbox_inches / pad_inches / dpi / transparent）「一个都没记」，将来第一步是记进捕获描述符 | 仍未做（ADR 级决定未立）；不属于 pdfbackend，但属于 U01 SourceArtifact 合同要接的现状。 |

## 候选后端对拍（U08）

U08（ADR 0067）：旧契约用例在 TAVOTTO_RENDER_BACKEND=rendercore 下逐字重跑（scripts/dev/u08_parity.py，rc-venv：pymupdf 只当读取器）。user_contract 一条不删；deselected 里的每一条都是实现特定断言（monkeypatch 旧 facade 内部 / base-14 字体名 / 旧字体 oracle / 旧栅格器阈值），逐条给出候选侧的替代证据；runner 拒绝没有替代证据的 deselect。结果记在 evidence/u08/parity.json。 U08 C 加 tests/test_mcp_server.py / tests/test_mcp_export_inspection.py：MCP 导出的产物检查在候选下经契约层 probe_asset 走候选 child。

重跑的套件（26 个）：`tests/test_export_pipeline.py`, `tests/test_export_endpoint.py`, `tests/test_export_inspection.py`, `tests/test_export_request.py`, `tests/test_original_spec.py`, `tests/test_annotate_asset.py`, `tests/test_render_cache.py`, `tests/test_compose_annotations.py`, `tests/test_compose_switched_shapes.py`, `tests/test_typography_families.py`, `tests/test_glyph_plan.py`, `tests/test_compose_text.py`, `tests/test_compose_arrow.py`, `tests/test_paths_and_baked.py`, `tests/test_font_provenance.py`, `tests/test_pixel_compare.py`, `tests/test_mcp_normalize.py`, `tests/test_write_back.py`, `tests/test_glyph_coverage_figure.py`, `tests/test_scientific_text_matrix.py`, `tests/test_cjk_figure_text.py`, `tests/test_tutorial.py`, `tests/test_worker_roundtrip.py`, `tests/test_windows_regressions.py`, `tests/test_mcp_server.py`, `tests/test_mcp_export_inspection.py`

| 在候选下 deselect 的用例 | 类 | 理由 | 替代证据 |
|---|---|---|---|
| `tests/test_export_pipeline.py::test_nothing_is_left_behind_when_a_format_fails` | `implementation_specific` | 经 monkeypatch `pdfbackend.compose` 注错；候选路不经 compose（job.produce）。用户合同（partial + 临时目录清掉）由替代用例经真实端点注 child 失败覆盖。 | `tests/test_rendercore_app.py::test_a_child_failure_under_the_candidate_is_partial_and_leaves_nothing_behind` |
| `tests/test_export_endpoint.py::test_failed_export_captures_nothing` | `implementation_specific` | 同上（monkeypatch compose.save_pdf 抛 OSError）；候选侧改成写入器炸。 | `tests/test_rendercore_app.py::test_a_writer_failure_under_the_candidate_is_a_500_and_captures_no_telemetry` |
| `tests/test_render_cache.py::test_backend_version_is_part_of_the_key` | `implementation_specific` | monkeypatch `pdfbackend.BACKEND_VERSION`——候选的键在 rendercore.preview 里取版本，契约常量不是它的输入；用户合同（换 build 换键）由候选的键用例 + 换后端名用例覆盖。 | `tests/test_rendercore_preview.py::test_the_key_carries_width_background_renderer_build_and_fonts_policy`<br>`tests/test_rendercore_app.py::test_switching_the_backend_changes_the_preview_cache_key` |
| `tests/test_render_cache.py::test_same_key_renders_once_under_concurrency` | `implementation_specific` | monkeypatch `pdfbackend.render_preview_png` 数调用；候选路经 PreviewCache，同键去重由它自己的用例量 `renders`。 | `tests/test_rendercore_preview.py::test_concurrent_requests_for_the_same_key_render_exactly_once`<br>`tests/test_rendercore_app.py::test_api_render_under_the_candidate_serves_a_png_of_the_bucket_width_from_the_cache` |
| `tests/test_typography_families.py::test_the_family_reaches_the_pdf_font_resources` | `implementation_specific` | 断言 base-14 名字（Times-Roman / Helvetica / Courier）——D07：默认新合法字体仍必须叫 Times-Roman 是实现细节。用户合同（三个族各到产物里各自的脸）由候选侧用例覆盖。 | `tests/test_rendercore_facade.py::test_three_families_reach_three_different_embedded_faces` |
| `tests/test_glyph_plan.py::test_golden_vectors_match_python_side` | `implementation_specific` | 默认 golden 向量是旧字体 oracle 的产物（fallback 层）；候选有自己的一份向量，与默认的差异钉成闭集。 | `tests/test_rendercore_glyph_vectors.py::test_candidate_vectors_differ_from_the_default_only_in_the_approved_ways`<br>`tests/test_rendercore_glyph_vectors.py::test_the_candidate_generators_check_clean_against_the_live_fonts` |
| `tests/test_glyph_plan.py::test_generator_is_up_to_date` | `implementation_specific` | 同上：默认生成物对默认后端；候选生成物由候选的 --check 看护。 | `tests/test_rendercore_glyph_vectors.py::test_the_candidate_generators_check_clean_against_the_live_fonts` |
| `tests/test_glyph_plan.py::test_subscript_two_stays_on_the_fallback_layer` | `implementation_specific` | 钉的是旧 oracle 的 fallback 层；候选没有 fallback 层（ADR 0060 §1），₂ 是 Liberation 自带的 primary。 | `tests/test_rendercore_typography.py::test_layers_are_primary_cjk_missing_and_never_fallback`<br>`tests/test_rendercore_glyph_vectors.py::test_candidate_vectors_differ_from_the_default_only_in_the_approved_ways` |
| `tests/test_glyph_plan.py::test_plan_matches_the_faces_the_pdf_actually_uses` | `implementation_specific` | 断言 PDF 里用到的 base-14 / Noto Serif 回退脸名字；候选写入的是批准字体子集，落笔与计划同一份由写入器用例看护。 | `tests/test_rendercore_writer.py::test_fonts_are_embedded_subsets_of_both_program_kinds_with_tounicode`<br>`tests/test_rendercore_writer.py::test_pdfium_object_census_counts_real_text_objects_not_outlines` |
| `tests/test_glyph_plan.py::test_fallback_face_is_the_same_regardless_of_family_and_weight` | `implementation_specific` | 量的是 PyMuPDF 隐式回退脸；候选没有回退脸（画不出进 missing，不换脸）。 | `tests/test_rendercore_typography.py::test_without_a_cjk_face_han_is_missing_not_substituted`<br>`tests/test_rendercore_writer.py::test_a_missing_glyph_is_written_as_notdef_and_reported_not_substituted` |
| `tests/test_glyph_plan.py::test_measured_width_equals_the_advance_actually_written` | `implementation_specific` | 直接用 pymupdf.TextWriter + 旧后端私有 latin_font/cjk_font 落笔；候选侧「量宽 == 写进去的 advance」由写入器用例看护。 | `tests/test_rendercore_writer.py::test_pen_position_between_runs_equals_the_shaped_advance`<br>`tests/test_rendercore_typography.py::test_width_is_the_sum_of_shaped_advances_of_the_same_plan` |
| `tests/test_glyph_plan.py::test_coverage_table_matches_the_live_fonts` | `implementation_specific` | 读的是默认表 `glyphplan.coverage_table_path()`；候选表在 rendercore/canvas_coverage.json，由候选生成器 --check 看护。 | `tests/test_rendercore_glyph_vectors.py::test_the_candidate_generators_check_clean_against_the_live_fonts` |
| `tests/test_glyph_plan.py::test_scientific_mode_draws_everything_with_one_face` | `implementation_specific` | 断言 used == {Helvetica}（base-14 名字）；候选侧 scientific 合成由 typography 用例看护。 | `tests/test_rendercore_typography.py::test_scientific_mode_composes_a_superscript_the_primary_face_lacks`<br>`tests/test_rendercore_writer.py::test_composed_superscript_and_multi_glyph_cluster_get_actualtext_spans` |
| `tests/test_mcp_normalize.py::test_zero_edit_roundtrip_keeps_page_size_and_pixels` | `implementation_specific` | 阈值（changed_pixel_ratio < 2%）按 MuPDF 栅格器校准；PDFium 对 Type 3 vs Type 42 字形的抗锯齿差异在 Two 样例上是 3.3%（03 §6「需要校准」：跨 renderer 按 case 记阈值，不全局放大）。页面尺寸那一半两边都过；像素那一半归校准用例。 | `tests/test_rendercore_calibration.py::test_old_and_new_backends_agree_within_the_case_thresholds` |

## 本轮新增目标

- U06：批准字体集合 + Typography 层 + Render IR（替换 base-14 与 Droid Sans Fallback 的 oracle）
- U07：矢量合成 + 受控栅格运行时（PDFium 候选，集中串行）
- U08：全部 19 个导出项 + 7 个 Canvas 面（含 context manager） + 8 个 probe_asset 调用点迁移 parity；有限产物验证（ArtifactManifest）
- U10：切默认 → 移除 pymupdf 依赖 → 发行闭包退役扫描（主语：应用 / 发行 / runtime 闭包，不是用户环境；D15）
