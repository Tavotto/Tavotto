# ADR 0065：外来页与位图的矢量合成——页盒、变换顺序、透明组与降级政策

日期：2026-09-21 · 状态：**Accepted（U07 第一切片；候选栈未默认启用，不切默认后端）**
相关：[0059 Render IR 与 RenderPlan](0059-render-ir-and-render-plan.md)、[0060 字体政策](0060-font-policy-and-allowlist.md)、
[0055 render_spike](0055-render-spike.md)、ADR 0066（render child 与 RasterBuffer / PNG-TIFF 同源，U07 第二切片；落地后补成链接）、
[0031 导出](0031-unified-export-pipeline.md)、[0046 EPS / TIFF](0046-eps-and-tiff-export-formats.md)；实施包
`docs/implementation/tavotto-foundation/`（`phases/U07_render_output.md`、`01_SCOPE_AND_DECISIONS.md` D07 / D08、
registry RC-038 ~ RC-046）。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 外来 PDF 页怎么进画布 | **Form XObject**：qpdf `Page.as_form_xobject(handle_transformations=True)` + `Pdf.copy_foreign`，整页矢量搬运，文字层随之保留；每次导入的 form 带**自己的** /Resources（含从 /Pages 继承的），两个源里同名的 /F1 / /X1 / /GS0 互不相干（RC-043）；同一 (资源 key, 页) 在一份文档里只搬一次（按字节身份去重，不按名字） | `rendercore/pdfwriter.py::_foreign_form`；`tests/test_rendercore_compose.py` 同名资源 / 继承资源 / 两实例用例 |
| 页盒 | 可见框由 qpdf 定（TrimBox → CropBox → MediaBox → form 的 BBox），**非零原点**的页盒天然正确（RC-038）；写入器只看 BBox 经 form /Matrix 映射后的包围盒（`placement.visible_box`） | `test_the_visible_box_is_the_crop_box_not_the_media_box`（U00 夹具 CropBox [15 10 285 170]） |
| /Rotate 与 /UserUnit | qpdf 把两者折进 form 的 **/Matrix**（实测 CropBox [15 10 285 170] + Rotate 90 + UserUnit 2 → `[0 −2 2 0 0 540]`，可见框 320 × 540）；写入器**不再**自己转、自己乘，于是恰好应用一次（RC-039） | `test_source_rotate_is_applied_exactly_once[0/90/180/270]`、`test_userunit_scales_the_visible_box…`；反证：`handle_transformations=False`（忽略）与「再转 90°」（重复）各红 |
| Tavotto 的 crop / 旋转 / 翻转顺序 | **只有一份说法**：`rendercore/placement.py::place()`——crop（顶原点归一化、相对可见框）→ 缩放到内容框（90° 奇数倍时宽高对调，填满目标框）→ 绕中心翻转 → 绕中心顺时针旋转 → 平移到目标框中心。逐句来自旧 `_place_panel` 与前端 `PanelView`（`rotate(r) scale(±1, ±1)`，从右往左）。PDF 源与位图源走同一个函数，只是可见框不同（form 的映射框 / 单位正方形） | `tests/test_rendercore_placement.py`（手算落点，纯模型）；`test_crop_flip_rotate_combinations…` 4 转 × 4 翻 × 2 crop = 32 组像素采样（RC-044），期望由测试自己的归一化公式独立算 |
| 面板整体 opacity | **透明组**（`/Group /S /Transparency`）包住 `cm + clip + Do`，组内 alpha 从 1 起算：源页内部重叠不被二次压暗（RC-041）；**仍是矢量、文字层在**（RC-040：旧后端这一档退位图，新写入器不退）。位图的 opacity 是 ExtGState 常量 alpha（一次填充，没有组内重叠） | `test_panel_opacity_is_a_transparency_group_not_per_object_alpha`：重叠区 (255,128,128)，逐对象会给 (191,64,128)；`test_the_u00_fixture_with_internal_alpha…`：(191,128,191) vs (160,96,191) |
| opacity = 0 | 是取值不是缺席（RC-042 must_fail `or 1.0`）：像素全白、对象仍在（form + 文字层） | `test_opacity_zero_paints_nothing_but_the_vector_object_is_still_there`；`plan` 层 `test_panel_opacity_zero_is_a_value_not_an_absence` |
| 镜像 | `cm` 里的负缩放，不退位图（RC-040 must_fail：flip 触发整页 Image XObject） | `test_a_mirrored_import_keeps_its_vector_and_text_layer`：墨从左半移到右半 + 对象普查无 image + 文字抽得到 |
| 位图源 | `rasterio.decode()`（Pillow）→ `RasterBuffer` → 8 bit DeviceRGB Image XObject（Flate），alpha 单独成 /SMask（straight）；8 bit RGB / 灰度 JPEG **原字节直通** `/DCTDecode`（与旧 `insert_image` 同一取舍，不重编码；直通前整张真解一遍，读取器解不开的不直通）；CMYK / 12 bit / 无损 JPEG 走解码路。像素预算两级都在解码**之前**按头里的尺寸判：单张 `raster.SOURCE_MAX_PIXELS`（64M，`why=raster_too_large`）、整份文档累计 `raster.DOCUMENT_MAX_PIXELS`（160M，去重后按资源记账，`why=raster_budget_exceeded`）。像素网格不变，缩放只在 `cm` 里 | `test_png_with_alpha_is_placed_with_an_smask…`、`test_an_rgb_jpeg_passes_through_as_dct_and_a_cmyk_one_is_decoded`、`test_image_crop_flip_and_rotation_use_the_same_contract_as_pages` |
| 不可信源（RC-046） | 只有内容流与资源会被 `as_form_xobject` 收进 form：注释 / 页面动作 / /OpenAction / Names JavaScript **不进**产物；加密源 `source_unreadable(why=encrypted)` 拒绝（`PasswordError` 与打开后 `is_encrypted` 两道：只有 owner 密码的文件不抛就打开了）、坏文件 `why=broken`、页号越界 `why=page_index`——都不画一张空框。作业级还有冻结源字节总预算 `job.SOURCE_BYTES_BUDGET`（512 MiB，读之前按 `size_bytes` 判） | `test_actions_annotations_and_javascript_of_the_source_are_not_imported`、`test_an_encrypted_source_is_refused_structurally`、`test_a_broken_source_and_a_missing_page_are_refused_structurally` |
| 字节身份 | 作业里 `sources.read_frozen()` 核过 hash 的那一份字节经 `files` 交给写入器，写入器**再核一次** sha256（`source_identity`）——两道是有意冗余（RC-014）；没交字节 `source_bytes_missing` | `test_source_bytes_must_match_the_resource_identity`、`test_rendercore_job.py::test_a_panel_canvas_exports_a_vector_pdf…` |
| 实例隔离 | 每个 `PdfWriter` 自己一份 `pikepdf.Pdf`、自己的外来文档表、自己的 XObject 缓存；8 线程并发写各自文档互不串 | `test_concurrent_writers_do_not_share_any_state` |
| 对象模型库 | **正式裁决 pikepdf**（见 §2） | `pyproject.toml` `rendercore` extra |
| 降级政策 | 见 §3：本轮**没有**「rasterized」档的操作；给不出的以结构化错误拒绝 | `ir.CAPABILITIES["pdf"]` 十个操作全 `native` |

## 1. 为什么是 Form XObject 而不是重画 / 位图

旧 facade（PyMuPDF `show_pdf_page`）在 opacity < 1 或翻转时退到「按导出 DPI 栅格化再 `insert_image`」，
理由是「PDF 矢量 xobject 无整体 alpha、show_pdf_page 无镜像」。这两条在 PDF 自己的模型里都不成立：整体
alpha 就是透明组（PDF 32000 §11.6.6），镜像就是 `cm` 里的负缩放。U02 已经用真源页证明了这两点（ADR 0055
§2.1），本 ADR 把它们收编成产品行为：**同一张图在画布上无论半透明还是镜像，导出的 PDF 都是矢量、文字都可检索**。

`copy_foreign` 是 qpdf 唯一不可替代的地方（ADR 0060 §2 留给本阶段的裁决）：它把源页的资源树整棵、按对象
身份搬进目标文档，源页里的 /F1 与目标文档里已有的 /F1 是两个对象，各自挂在各自的 /Resources 下。自己拼
（pypdf 路线）要处理的正是这一堆——资源继承、间接对象图的深拷贝、同名冲突、流数据的按需读取。

## 2. pikepdf 正式裁决（含 Pillow / lxml）

| 组件 | 版本（钉死） | 许可证 | 体积（wheel，macOS arm64） | 用途 |
|---|---|---|---|---|
| pikepdf | 10.13.0.post1（libqpdf 12.3.2） | MPL-2.0（qpdf Apache-2.0） | 1.8 MB | 对象模型 / 序列化 / **外来页 → Form XObject** |
| Pillow | 12.3.0（U07 起**直接依赖**） | HPND / MIT-CMU | 4.8 MB | `rasterio.decode()`：位图素材 → 像素（只解码；输出侧 TIFF 仍是纯标准库 `tiffwrite.py`） |
| lxml | 6.1.3（pikepdf 传递依赖，不调用） | BSD-3-Clause | 8.6 MB | pikepdf 的 XMP；本产品不写 XMP |
| packaging | pikepdf 传递依赖 | Apache-2.0 / BSD | 0.1 MB | — |

裁决：**留 pikepdf**。理由：（1）`copy_foreign` 覆盖了资源继承 / 对象图搬运 / /Rotate / /UserUnit / 页盒选择
五件事，pypdf 路线每一件都要自己写并各配一组用例；（2）Pillow 无论如何都会进闭包——本阶段位图素材要解码
（PNG 的 alpha 必须拆成 /SMask），自己写 PNG / TIFF / JPEG 三个解码器不值；既然要用，就按正式依赖列进
`rendercore` extra（`pyproject.toml` + `requirements-rendercore.txt`）；（3）lxml 是纯粹的传递重量（8.6 MB），
接受。MPL-2.0 §3.2 的告知义务与 5 个 MPL crate 同一类，落在 #182 的 NOTICE 生成里。

pypdf 路线的实测代价（不采用，但写明）：pypdf 6.7.5 没有「页 → Form XObject」的 API；要自己读页盒、写
/BBox、按 /Rotate 写 /Matrix、把 /Resources（含继承）与内容流搬过来、对间接对象做 `clone`——约与本 PR 的
`_foreign_form` + `placement` 等量的代码，外加一组资源继承 / 同名 / 对象流的用例；省下的只有 lxml 8.6 MB。

## 3. 降级政策（CAPABILITIES 三档）

* 本轮 PDF 的十个操作全部 `native`：路径 / 裁剪 / 透明组 / 常量 alpha / 文字 / 页面底色（U06）+ 外来页 /
  位图 / 镜像（本 ADR）。**没有任何操作在 PDF 里是 `rasterized`**——不存在「整页位图后仍报 vector」的路径：
  `job.produce` 对 PDF 报 `vector: True` 的前提就是能力表里没有 rasterized / unsupported 的操作。
* 复杂 mask / blend 模式（源页自带的 /SMask、/BM）：它们在源页的内容流与资源里，随 form 原样搬运，由
  阅读器按 PDF 语义合成——写入器**不解释、不改写**。这不是降级，是「源页内部是什么，IR 不知道也不假装
  知道」（RC-013 `internal = "unknown"`）。本轮**不**支持的：给面板加一个 Tavotto 自己的 blend 模式 /
  软遮罩（画布上没有这个能力，能力表里也没有这个操作）。
* 位图源解不开（`raster_unreadable` / `raster_kind_mismatch`）、源 PDF 打不开 / 加密 / 缺页：`source_unreadable`
  结构化拒绝，`job` 落到该格式的 `format_failed`，不画空框、不用旧文件冒充。
* PNG / TIFF 在 render child 收编之前（ADR 0066）仍全 `unsupported`。

## 4. 与旧 facade 的对应（U08 对拍时逐项）

| 旧 facade `_place_panel` | 本 ADR |
|---|---|
| `page.insert_image(rect, filename)` 快路径 | `Image` → Image XObject（JPEG 直通 / Flate + SMask） |
| `show_pdf_page(rect, src, 0, clip=…, rotate=-rotation)` | `ImportedPage` → `cm + re W n + /Fm Do`，`placement.place()` |
| opacity < 1 / flip → 按 dpi 栅格化 `insert_image` | 透明组 / 负缩放，**仍矢量**（有意差异：像素不会逐个相同，几何相同） |
| `rotation` 四舍五入到 90 倍数 | `plan._panel_node` 同一条，PDF 与位图同一条 |
| `_crop_clip` 顶原点归一化 | `placement.place(crop=…)` 同一约定 |
| `_obj_morph` 顺时针取负 | `rotate_ccw(-deg)` 同一约定 |
| 探测 `probe_asset` 忽略 /UserUnit | render child 的 probe（ADR 0066）按 UserUnit 乘——**有意差异**，记进 U08 对拍表 |

## 5. 反证（每条变异一次就红，`scratchpad/u07/mutate_a.py` 16 条 + 评审处置 4 条）

| 变异 | 红在 |
|---|---|
| `placement` 不翻转 / 旋转方向反 / 先转后翻 / crop 用底原点 | `test_rendercore_placement.py` + 32 组组合用例 |
| `as_form_xobject(handle_transformations=False)`（忽略 /Rotate 与 /UserUnit） | Rotate 四档 + UserUnit 用例 |
| 有 /Matrix 时再转 90°（重复应用） | Rotate 用例 |
| 透明组换成逐对象 alpha | 两条透明组用例（数字分得开） |
| 不写 `re W n` | 组合用例的「框外一圈必须是白」 |
| form 按 kind 去重（同名撞车） | 同名资源 / 两实例用例 |
| 不核字节 sha256 | `source_identity` 用例 |
| 位图丢 /SMask | PNG alpha 用例 |
| 吞掉 `PasswordError` | 加密源用例 |
| `plan` 的 `opacity` 用 `or 1.0` | `test_panel_opacity_zero_is_a_value_not_an_absence`（第一版没有这条用例，变异绿了才补） |
| 能力表把 imported_page 改回 unsupported | 写入器交叉核对 + ir 用例 |
| `split_alpha` 把颜色当 alpha | raster 用例 + PNG alpha 用例 |
| `job` 不把冻结字节交给写入器 | job 用例 |
| 位图解码不传像素预算 / JPEG 直通不核 SOF 尺寸（Codex #463 P2） | `test_a_huge_raster_is_refused_before_it_is_decoded`（9000² 落在 Pillow 自己的炸弹闸之下，响的是我们的预算；`load` 探针证明拒绝在解码之前） |
| JPEG 直通不整张真解（Codex #463 P2） | `test_a_jpeg_that_readers_cannot_decode_is_not_passed_through`（12 字节假头、截半的真 JPEG） |
| 记账挪到 JPEG 真解之后（Codex #463 第三轮 P2） | 同一条用例：预算只剩 2000 时把 Pillow 的 `load` 换成必爆探针，拒绝必须发生在解码之前 |
| 文档级位图像素预算不累计（Codex #463 第二轮 P2） | `test_the_document_wide_raster_budget_stops_many_small_images_from_adding_up`（预算缩到用例尺度：同一张不重复计费、第三张不同的位图超线即 `raster_budget_exceeded`，JPEG 直通路同样记账） |
| 打开后不判 `is_encrypted`（只有 owner 密码的 PDF，Codex #463 第四轮 P2） | `test_an_owner_password_only_pdf_is_still_refused_as_encrypted` |
| `job` 不判冻结源字节总预算（Codex #463 第四轮 P2） | `test_rendercore_job.py::test_the_aggregate_frozen_source_bytes_budget_fails_before_any_byte_is_read`（`read_frozen` 一次没被调） |
| 退化页盒不拦（Codex #463 P2） | `test_a_source_page_with_a_degenerate_box_is_a_source_error_not_a_crash`（MediaBox 零宽 → `source_unreadable(why=degenerate_box)`，不是 ZeroDivisionError） |

## 6. 没做 / 边界

* 面板旋转非 90 倍数（画布语义本来就没有）；多页源只取 `page_index`（默认 0，与旧后端 `src[0]` 同）。
* 源页自带的注释、表单、动作、附件、JavaScript 一律不进产物（RC-046 的政策是「不带」而不是「清洗」）。
* 源 PDF 的字体没有被子集 / 合并——form 原样搬运，字体对象照旧；两个源各带一份 Helvetica 是正确的，不做跨源合并。
* PNG 的 gAMA / iCCP / sRGB 块不解释（与旧后端相同）；16 bit 取高 8 位；交错 PNG 交给 Pillow。
* 栅格（PNG / TIFF）、预览缓存、render child：ADR 0066。
