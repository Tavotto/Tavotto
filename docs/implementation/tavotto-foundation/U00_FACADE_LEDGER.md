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
| `BACKEND_NAME` | `identity` | public_contract | `src/tavotto/app.py:853` · `api_render`<br>`scripts/gen_canvas_coverage.py:50` · `table` | （无） | — | U08：新后端给出不同的 BACKEND_NAME；缓存键因此天然失效（旧预览不复用）。用例：同一 PDF 换后端名后 /api/render 必须重画（test_render_cache 的键用例扩一维）。 |
| `BACKEND_VERSION` | `identity` | public_contract | `src/tavotto/app.py:853` · `api_render`<br>`scripts/gen_canvas_coverage.py:51` · `table`<br>`scripts/gen_canvas_coverage.py:97` · `main`<br>`scripts/gen_glyph_plan_vectors.py:95` · `main` | `tests/test_glyph_plan.py:183` (implementation_specific) | — | U08：新后端报自己的版本串；canvas_coverage.json 与 glyph_plan_vectors.json 必须由新后端重生成（--check 红即证明表在跟着后端走）。 |
| `CANVAS_TEXT_FAMILIES` | `identity` | public_contract | `scripts/gen_glyph_plan_vectors.py:59` · `module` | `tests/test_typography_families.py:40` (user_contract)<br>`tests/test_glyph_plan.py:117` (user_contract)<br>`tests/test_font_provenance.py:98` (user_contract)<br>`tests/test_scientific_text_matrix.py:154` (user_contract) | — | U06/U08：闭集**是一句能力承诺**——新后端画得出什么，闭集就是什么，不能照抄（facade 文档原话）。三个通用族 + 粗 / 斜 / 粗斜必须由批准字体集合兑现；前端摆得出的后端必须画得出（test_typography_families 保留）。 |
| `COVERAGE_MAX_CP` | `text_metrics` | public_contract | `scripts/gen_canvas_coverage.py:52` · `table` | （无） | — | U06：新字体集合的覆盖上界由新后端给；生成器与 glyphPlan.ts 读同一个数。 |
| `annotate_asset` | `annotation_writeback` | public_contract | `src/tavotto/app.py:4071` · `_write_source_files` | `tests/test_annotate_asset.py:26` (user_contract)<br>`tests/test_annotate_asset.py:115` (implementation_specific) | #252 原图写回缺 fsync（原子性已有；不归本项） | U08：同一组 objects 在新后端上写进 PDF，**独立读取器**（U01 选定，不是生产 transform 反算）读回文字与路径位置在容差内；PNG 由同一 PDF 栅格化；只有 PNG 的素材仍回 annotations_need_pdf（app 层判据不动）。 |
| `compare_png` | `compare_png` | public_contract | `src/tavotto/app.py:3920` · `_replay_pixel_diff` | `tests/test_pixel_compare.py:45` (user_contract)<br>`tests/test_pixel_compare.py:179` (user_contract)<br>`tests/test_pixel_compare.py:192` (implementation_specific)<br>`tests/test_mcp_normalize.py:287` (user_contract) | #265 持久 tight 布局图上像素门比的是两张不可复现的渲染（判据问题，不是比较器） | U07/U08：PNG 解码换成新后端（或纯标准库 zlib 解码）后，三指标在 tests/test_pixel_compare.py 的全部样例上逐值相同；对拍用例保留。 |
| `compose` | `compose` | public_contract | `src/tavotto/app.py:1006` · `_export_produce_canvas` | `tests/test_typography_families.py:96` (user_contract)<br>`tests/test_glyph_plan.py:46` (user_contract)<br>`tests/test_export_endpoint.py:297` (user_contract)<br>`tests/test_export_pipeline.py:243` (user_contract)<br>`tests/test_export_pipeline.py:1291` (user_contract) | — | U07：RenderPlan → IR → 新合成器实现同一个 Canvas 面（见 canvas_methods）；PDF 真矢量、PNG/TIFF 出自同一次栅格化；透明背景 = 不画白底 + PNG 带 alpha。 |
| `coverage_ranges` | `text_metrics` | public_contract | `scripts/gen_canvas_coverage.py:53` · `table` | `tests/test_glyph_plan.py:184` (user_contract) | — | U06：由批准字体集合现算三层区间；`gen_canvas_coverage.py --check` 在新后端上必须先红（表变了）再由 --write 更新，PR 里附 diff。 |
| `hex2rgb` | `identity` | exported_without_product_caller | （无产品调用方） | `tests/test_paths_and_baked.py:321` (user_contract) | — | U06：纯函数原样搬到与后端无关的模块（IR 层）；四条取值用例逐字保留。 |
| `missing_glyphs` | `text_metrics` | public_contract | `scripts/gen_glyph_plan_vectors.py:84` · `main` | `tests/test_glyph_plan.py:59` (user_contract)<br>`tests/test_glyph_plan.py:111` (user_contract)<br>`tests/test_scientific_text_matrix.py:159` (user_contract) | — | U06：新字体集合下 tests/test_scientific_text_matrix.py 必须仍为空缺字；golden 向量按 D07 批准一次迁移（缺字集合可以变小，不许变大而不说）。 |
| `mm2pt` | `identity` | public_contract | `src/tavotto/app.py:1187` · `_original_page_pt`<br>`src/tavotto/app.py:1191` · `_original_page_pt` | `tests/test_export_endpoint.py:116` (user_contract)<br>`tests/test_compose_text.py:81` (implementation_specific)<br>`tests/test_compose_arrow.py:41` (implementation_specific) | — | U06：纯换算搬到 IR 层；页面尺寸类断言（user_contract）逐字保留。 |
| `original_pdf` | `original_vector_copy` | public_contract | `src/tavotto/app.py:1122` · `_export_produce_original` | `tests/test_export_pipeline.py:129` (user_contract)<br>`tests/test_export_pipeline.py:100` (user_contract)<br>`tests/test_export_pipeline.py:713` (user_contract)<br>`tests/test_export_pipeline.py:903` (user_contract)<br>`tests/test_export_request.py:169` (user_contract) | — | U08（RC-002/003）：矢量源**整页搬运不重画**——新后端输出的页面尺寸、字体子集、路径数与源页一致（独立读取器比）；位图源 vector=False 且页面 = page_pt；original 段里没有 x/y/w/h（结构不变）。多页 PDF 只取第一页（与画布所见一致）。 |
| `original_png` | `original_png_byte_copy` | public_contract | `src/tavotto/app.py:1149` · `_export_produce_original` | `tests/test_export_pipeline.py:140` (user_contract)<br>`tests/test_export_pipeline.py:161` (user_contract)<br>`tests/test_export_pipeline.py:892` (user_contract)<br>`tests/test_export_pipeline.py:871` (user_contract) | — | U08（RC-003 / 03 §6「必须精确」）：PNG 源 → 输出**逐字节相同**（shutil.copyfile 语义，pHYs 保留）；JPEG 源 → 像素数不变、签名是 PNG；矢量源栅格化尺寸 = round(pt × ppi / 72)；`resampled` 恒 False。 |
| `original_tiff` | `original_native_grid` | public_contract | `src/tavotto/app.py:1141` · `_export_produce_original` | `tests/test_export_pipeline.py:1304` (user_contract)<br>`tests/test_export_pipeline.py:1316` (user_contract)<br>`tests/test_export_pipeline.py:1266` (user_contract) | — | U08：位图源像素网格不变（px_w/px_h == 源）、TIFF 解码像素 == 同源 PNG 解码像素（03 §6）、分辨率标签只在源声明过时才写；矢量源与 original_png 同一次栅格化参数。编码器 tiffwrite.py 纯标准库，本身不属于后端。 |
| `pdf_fonts` | `probe` | public_contract | `src/tavotto/engine/artifactcheck.py:82` · `check_pdf` | `tests/test_mcp_normalize.py:325` (user_contract) | — | U08：读取侧换成新后端 / 独立读取器，同一 PDF 返回同一列表（含子集前缀处理与保序）。 |
| `probe_asset` | `probe` | public_contract | `src/tavotto/app.py:550` · `scan_panels`<br>`src/tavotto/app.py:1188` · `_original_page_pt`<br>`src/tavotto/app.py:1204` · `_declared_density`<br>`src/tavotto/app.py:4154` · `_post_check_size`<br>`src/tavotto/engine/artifactcheck.py:80` · `check_pdf`<br>`src/tavotto/engine/artifactcheck.py:149` · `check_raster`<br>`src/tavotto/engine/tutorial.py:573` · `validate_tutorial_resources`<br>`codex-plugin/mcp/tavotto_mcp/bridge.py:1683` · `_original_artifact_facts` | `tests/test_original_spec.py:122` (user_contract)<br>`tests/test_export_pipeline.py:786` (user_contract)<br>`tests/test_mcp_normalize.py:282` (user_contract) | — | U08：八个调用点一个不漏（RC-001）；返回结构逐键相同；`alpha` 真值来自新读取器；密度仍由 originalspec 解析。 |
| `render_preview_png` | `preview_cache` | public_contract | `src/tavotto/app.py:318` · `_write_render_cache` | `tests/test_render_cache.py:196` (user_contract)<br>`tests/test_mcp_normalize.py:285` (user_contract) | — | U07/U08：新栅格运行时（PDFium 候选）按同一宽度出 PNG；缓存层不动；跨 renderer 的抗锯齿差异按 03 §6「需要校准」记录阈值，不全局放大。 |
| `text_plan` | `text_metrics` | public_contract | `scripts/gen_glyph_plan_vectors.py:82` · `main` | `tests/test_glyph_plan.py:57` (user_contract)<br>`tests/test_glyph_plan.py:87` (implementation_specific)<br>`tests/test_glyph_plan.py:93` (implementation_specific)<br>`tests/test_glyph_plan.py:110` (user_contract)<br>`tests/test_font_provenance.py:109` (user_contract) | — | U06：四层顺序不可交换（glyphplan.py 不动）；oracle 换成新字体集合；哪些字符落在哪一层按 D07 批准一次迁移并附向量 diff。 |
| `text_width` | `text_metrics` | exported_without_product_caller | （无产品调用方） | `tests/test_typography_families.py:115` (user_contract)<br>`tests/test_glyph_plan.py:175` (user_contract) | — | U06：Typography 层给出同一签名；「量宽用的族 == 落笔用的族」这条用户合同保留。 |

### 各项备注

- **`BACKEND_NAME`**：现值 `pymupdf`
- **`BACKEND_VERSION`**：现值 `pymupdf.__version__（1.28.2）`
- **`CANVAS_TEXT_FAMILIES`**：现值 `("serif", "sans-serif", "monospace")`；严格同源对：web/src/lib/typography.ts CANVAS_TEXT_FAMILIES
- **`COVERAGE_MAX_CP`**：现值 `0x30000`
- **`annotate_asset`**：签名 `annotate_asset(pdf_path, png_path | None, objects, dpi=600) -> None`
- **`compare_png`**：签名 `compare_png(baseline, candidate) -> {ok, changed_pixel_ratio, mean_abs_diff, max_abs_diff, ...}`
- **`compose`**：签名 `compose(page_w_mm, page_h_mm, transparent=False) -> Canvas`
- **`coverage_ranges`**：签名 `coverage_ranges() -> {layer: [[start, end], ...]}`
- **`hex2rgb`**：签名 `hex2rgb(s) -> (r, g, b) 0..1`；产品代码里只有实现模块内部用（_draw_* 系列）；导出是给实现模块之间复用的纯换算。
- **`missing_glyphs`**：签名 `missing_glyphs(s, family="serif", bold=False, italic=False) -> list[str]`；engine/manifest.py:1194 有同名函数 missing_glyphs(text, families)——那是 worker 侧问 matplotlib 字体的判据，与本项不同源、不同进程，不是第二份实现。
- **`mm2pt`**：签名 `mm2pt(mm) -> pt (×72/25.4)`
- **`original_pdf`**：签名 `original_pdf(src, out, page_pt=None) -> {w_pt, h_pt, px_w, px_h, vector, pages}`
- **`original_png`**：签名 `original_png(src, out, ppi, transparent=False) -> {px_w, px_h, resampled, transcoded}`
- **`original_tiff`**：签名 `original_tiff(src, out, ppi, transparent=False, *, dpi_meta=None) -> {px_w, px_h, resampled, transcoded}`；没有直接以 pdfbackend.original_tiff 为主语的单元用例，覆盖全经端点 / 作业管线；RC-001 的 must_fail_example 点名它：漏它不能被总通过掩盖。编码器 tests/test_tiffwrite.py 单独看护。
- **`pdf_fonts`**：签名 `pdf_fonts(path) -> list[str]（去子集前缀、去重、保序）`
- **`probe_asset`**：签名 `probe_asset(path, kind) -> {kind, w_pt, h_pt} | {kind, px_w, px_h, alpha}`；**密度刻意不从这里取**：MuPDF 的 Pixmap.xres 对「没写 pHYs」与「写着 96」一律回 96（project-system.md）。U00 夹具 pdf_png_assets 的 original_nophys.png 实测 xres=96、original.png（pHYs 300）xres=300，再次证实。
- **`render_preview_png`**：签名 `render_preview_png(path, width_px, out) -> None`
- **`text_plan`**：签名 `text_plan(s, family="serif", bold=False, italic=False) -> list[(segment, layer)]`；严格同源对：src/tavotto/glyphplan.py ↔ web/src/lib/glyphPlan.ts（算法同源、oracle 不同源）
- **`text_width`**：签名 `text_width(s, size_pt, bold=False, italic=False, family="serif") -> pt`；产品代码里没有 pdfbackend.text_width 的调用点：换行 / 量宽在实现模块内部经 _mixed_width 走（_draw_text）。前端画布文字的量宽在浏览器里自己排（TextView）。契约层保留它是为了让 MCP / 预检将来能问同一把尺。

## Canvas 面（RC-002）

RC-002：compose() 返回的画布对象是 facade 唯一泄漏的后端对象，只经这几个方法使用。

| 方法 | 签名 | 调用方 | 作用 |
|---|---|---|---|
| `place` | `place(o, dpi, resolve_panel) -> None` | `src/tavotto/app.py:1019` · `_export_produce_canvas` | 按对象类型落一个元素：panel（PDF 真矢量 show_pdf_page；位图带 crop/rotation 经 convert_to_pdf；opacity<1 或 flip 时按 dpi 位图嵌入）/ text / arrow / shape |
| `size_pt` | `size_pt -> (w_pt, h_pt)` | `src/tavotto/app.py:1027` · `_export_produce_canvas` | 样式检查报告里的页面尺寸 |
| `save_pdf` | `save_pdf(path) -> None` | `src/tavotto/app.py:1044` · `_export_produce_canvas` | 真矢量 PDF（deflate） |
| `save_tiff` | `save_tiff(path, dpi) -> dict` | `src/tavotto/app.py:1056` · `_export_produce_canvas` | 与 save_png 同一页同一次栅格化参数（ADR 0046）；编码器 tiffwrite.py |
| `save_png` | `save_png(path, dpi) -> None` | `src/tavotto/app.py:1058` · `_export_produce_canvas` | 同一页渲染 |
| `close` | `close() -> None` | `src/tavotto/app.py:1080` · `_export_produce_canvas` | finally 里关文档 |
| `__enter__/__exit__` | `context manager` | `tests/test_typography_families.py:96` · `test`<br>`tests/test_glyph_plan.py:46` · `test` | 测试里的 with 形态；app.py 用显式 close() |

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

## 本轮新增目标

- U06：批准字体集合 + Typography 层 + Render IR（替换 base-14 与 Droid Sans Fallback 的 oracle）
- U07：矢量合成 + 受控栅格运行时（PDFium 候选，集中串行）
- U08：全部 19 个导出项 + 8 个 Canvas 面 + 8 个 probe_asset 调用点迁移 parity；有限产物验证（ArtifactManifest）
- U10：切默认 → 移除 pymupdf 依赖 → 发行闭包退役扫描（主语：应用 / 发行 / runtime 闭包，不是用户环境；D15）
