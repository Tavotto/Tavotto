# ADR 0077：RenderCore 性能——派生值按文件指纹复用、源编码原样照搬、启动预热

日期：2026-09-23 · 状态：**Accepted（P0 尺寸 / 写入 / 预览命中 / 预热；P1 编码与内存；P2 超预算的条带栅格）**
相关：[0065 合成](0065-imported-page-composition.md)、[0066 render child 与 RasterBuffer](0066-render-child-and-raster-buffer.md)、
[0067 facade 切换开关](0067-render-backend-switch-and-execution-sources.md)；细则 `docs/rules/backend/rendercore.md`。

## 背景：用本机真实 PDF 实测，候选后端比默认后端慢在哪

2026-09-23 用 18 份真实 PDF（matplotlib 子图、CAD 施工图、扫描件、海报、中英文论文）在两个后端下 ABBA 交错各跑 6 轮。
候选后端在导出与并发预览上已经更快，但有几处明显更慢；逐段剖析后根因如下（都已实测，不是推测）：

| 慢点 | 实测根因 |
|---|---|
| 尺寸探测 ×13 慢 30×（4 ms → 126 ms） | **不是 IPC**（ping 往返 0.02 ms）：child 的 `probe` 用 `doc[i]` → `FPDF_LoadPage` 会**解析整页内容流**（CAD 8 ms、海报 23 ms），而尺寸只取决于页盒与 /Rotate；`app.scan_panels` 每次 `/api/panels` 对每个素材都探一遍、没有缓存 |
| 海报画布导出慢 1.35× | 渲染持平（PDFium 253 vs MuPDF 232 ms，瓶颈是解码大图）；慢在写 PDF：qpdf `as_form_xobject` 把源内容流**解码成明文**进 Form（399 KB → 2.7 MB），`save(compress_streams=True)` 再整段重压，并且把带 PNG predictor 的 Flate 图片流也解码重压——**与 `stream_decode_level` 无关**（qpdf 12.3.2，纯搬一页也 76 ms，关掉 7 ms） |
| 预览缓存命中随文件变慢 | `PreviewCache.get()` 为了 A→B→A 的安全，**每次**（含命中）整份抄副本再算 sha256：40 MB 的源每次命中 25 ms + 40 MB 写盘 |
| 冷启动慢 56 ms | 起 child ~70 ms、字体注册表 ~90 ms、第一次排版载 CJK 脸 ~60 ms，全压在用户的第一次操作上 |

## 裁决摘要（P0）

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 只要尺寸的调用 | child 新 op **`size`**：`FPDF_GetPageSizeByIndexF`（不加载页、不解析内容流），乘 /UserUnit 与 `probe` 同一次；`probe_asset` / `original_pdf` / `annotate_asset` 改用它。`probe` 原样保留给要页盒 / 旋转细节的调用方 | `renderchild._size`；`test_real_child_size_is_the_probe_size_without_loading_the_page`（CropBox / Rotate 90 + UserUnit 2 / U06 三种与 probe 逐项相同） |
| 反复探测同一个文件 | `sources.FingerprintMemo`：按 `sources.file_fingerprint`（设备, inode, 字节数, mtime_ns, ctime_ns）复用**派生值**；**最后一次改动离现在不到 2 s 的不记**（`RACY_WINDOW_NS`，git 的 racy clean——Linux / Windows 的时间戳按 tick 走，同一 tick 里的等长改写指纹相同）；失败不记；每次回新 dict | `test_the_memo_*`、`test_a_file_changed_within_the_timestamp_granularity_window_is_not_remembered`、`test_probe_asset_reuses_its_answer_until_the_file_changes` |
| Windows | **不复用**（`sources.FINGERPRINT_TRUSTED = os.name != "nt"`，指纹恒为 None）：Windows 的 `st_ctime` 是**创建**时间、NTFS 的 ChangeTime `os.stat` 不给，同长原地改写再把 mtime 设回原值时五元组一个字段都不变（Codex #506 P2）。那里行为与没有这层复用时相同 | `test_where_stat_cannot_see_rewrites_nothing_is_reused` |
| 指纹是不是身份 | **不是**。它只决定「能不能复用一个已经算出来的派生值」；冻结、写入、渲染仍一律按字节 sha256 核（`read_frozen` / `PreviewCache._stage` / 写入器 `source_identity`） | — |
| 预览缓存命中 | 修订 RC-061 的做法（不改键）：**指纹快路**只在「指纹与上次抄副本时相同**且**成品在」时直接交出成品——不抄、不读源；其余一切（第一次、指纹变了、成品被 prune）照旧走副本路：边抄边算 hash、键与渲染绑在副本上。快路**从不渲染**，A→B→A 的保护原样在渲染那一侧 | `PreviewCache.get`；`test_a_hit_on_an_unchanged_file_skips_the_staged_copy`、`test_a_changed_or_pruned_file_always_goes_back_through_the_staged_copy`、`test_a_just_written_file_is_not_trusted_by_its_fingerprint`；原有 A→B→A 用例不变 |
| 外来页内容流 | 源页**只有一段**带过滤器的内容流、且解出来与 Form 明文逐字节相同 → Form 直接用源的**已编码字节** + 同一组 /Filter /DecodeParms（在源文档里、`copy_foreign` 之前改，不牵扯跨文档对象）；多段（段界只保证落在词法边界，压缩流不能首尾相接）/ 无过滤器 / qpdf 解不开的，留明文给下一条 | `pdfwriter._keep_source_encoding`；`test_a_single_segment_source_keeps_its_encoded_content_bytes` |
| 保存时的压缩 | `save(compress_streams=False)`；保存前 `_compress_unfiltered` 只压**没有过滤器**的流（本模块写的内容流 / 字体程序 / 多段外来页的明文），压了不变小的不动，XMP /Metadata 保持明文；源的已编码流（图片、单段内容流）一个字节不碰 | `test_a_multi_segment_source_is_joined_then_compressed_by_the_writer`、`test_source_image_streams_are_copied_without_being_re_encoded` |
| 大流的压缩 | `deflate.zlib_compress`（P1 起在 `tavotto/deflate.py`，P0 时在 `raster`）：pigz 的做法——按 `deflate.BLOCK`（1 MiB）切块、每块以前一块末尾 32 KiB 为预置字典、raw deflate 并行压（每次调用临时起线程池、用完 join，不留常驻线程）、非末块 `Z_SYNC_FLUSH`、补 zlib 头与整段 adler32。块边界只由输入长度定：**线程数不同输出相同**；一块以内逐字节等于 `zlib.compress` | `tests/test_deflate.py`：`test_within_one_block_the_output_is_byte_identical_to_zlib`、`test_many_blocks_make_one_valid_stream_independent_of_the_thread_count` |
| 冷启动 | `pdfbackend.warm()` 装载实现之后，实现若有 `prewarm()` 就交给它；候选在**后台线程**里起 child、建字体注册表、载 CJK 脸。失败只记日志——同一个错误在第一次真用到时原样抛出（无静默回退，不替谁选后端） | `facade.prewarm`；`test_warm_hands_the_selected_implementation_its_background_prewarm`、`test_the_candidate_prewarm_starts_the_child_and_loads_the_faces` |

## 效果（P0 vs main a9aa23a0，同机 ABBA 交错 4 轮，中位数）

| 场景 | main | P0 | |
|---|---|---|---|
| 素材库扫描 13 个 PDF（首次） | 155 ms | 14.7 ms | 10.5× |
| 素材库扫描 13 个 PDF（之后每次，`/api/panels` 的常态） | 133 ms | 0.25 ms | 530× |
| 海报画布 → PDF | 132 ms | 35 ms | 3.8× |
| figure7 画布 → PDF | 89 ms | 14.6 ms | 6.1× |
| 8.8 MB 内嵌图的面板 → PDF | 81 ms | 44 ms | 1.8× |
| 预览缓存命中（40 MB 源） | 27 ms | 0.03 ms | — |
| 启动 0.5 s 后的第一次导出 | 240 ms | 134 ms | 1.8× |

10 份真实源（含 opacity 0.8 的透明组与镜像两种变体）的 Canonical PDF，main 与 P0 经 PDFium 300 dpi 渲染**逐字节相同**、抽回的
文字相同。体积：9 份变化 ≤ +0.7%；`fig_48forms`（PDF Expert 写的 48 个小 Form，源自己的 Flate 压得很松：172 B 明文只压到
127 B）从 94 KB 到 111 KB——qpdf 以前顺手把它们重压成 level 6，现在按源的样子照搬。这是有意的取舍：同一个「不重压源的已编码
流」在带 predictor 的图片上每次省 76 ms；体积差只在源自己压得差时出现，渲染与文字不受影响。

## 反证（每条变异一次就红；脚本在会话 scratchpad，结果 19/19）

size 忘乘 UserUnit、probe_asset 绕过复用表、交出表里那份 dict、复用表不分 key、查表不比指纹、去掉刚改过窗口（两处）、
写入器不照搬源编码、save 回到 `compress_streams=True`、不压未过滤的流、连 XMP 也压、并行块不在字节边界收尾、adler32 只算第一块、
单块不用 zlib 头、预览没有快路、快路不看成品在不在、warm 不交给 prewarm、prewarm 什么都不做、Windows 也复用（去掉平台开关）——各自红在上表的用例上。

两条中途被抓出来的**假绿**（修掉之后才算上面的计数）：「算值前后指纹相同」这条判据**不可观测**——算的过程中被改写的，指纹
已经前进（ctime 只增），挂在旧指纹上的值再也查不到，于是删掉；两条用例把 mtime **往前**拨 1 秒，落进「刚改过 / 来自未来」的
窗口，快路根本没被走到而断言照样绿——改成往回拨，并断言指纹确实命中。

## P1：PNG / TIFF 编码与内存

P0 之后，PNG / TIFF 出图的剩余时间几乎全在**单线程 zlib**上（扫描件 300 ppi：child 渲染 74 ms、`write_tiff` 101 ms；A4 600 ppi 的
`encode_png` 418 ms），内存则被同一幅像素的反复复制吃掉（A4 600 ppi 的 104 MB RGB：child 里 `bytes(bitmap.buffer)` 一份、父进程
`read_bytes` 一份、`encode_png` 再拼一份整幅扫描线 + 压缩结果 + `bytes(out)`——合计 616 MB，旧后端 257 MB）。

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 并行压缩放哪 | 新的纯标准库模块 **`tavotto/deflate.py`**（Flask 侧的 `tiffwrite` 与 RenderCore 的 `raster` / `pdfwriter` 共用，不 import 任何产品模块）：`ordered_map`（线程池、按输入顺序产出、**在飞件数封顶**，输入是惰性生成的大块时内存只和窗口一样大）、`zlib_stream`（一串块 → 一条 zlib 流，P0 的 pigz 做法）、`compress_each`（每件独立 `zlib.compress`）。线程池每次调用临时起、用完 join | `tests/test_deflate.py` |
| PNG | `encode_png`：扫描线按行数凑成约 1 MiB 的块、**惰性生成**，IDAT 由 `zlib_stream` 并行压；不再拼整幅扫描线。**像素不变**（解码后逐个等于缓冲，行尾填充剥掉）；压缩流字节与单线程 `zlib.compress` 不同（块界 `Z_SYNC_FLUSH`），同一输入永远同一份、与线程数无关；**一块以内（小图）逐字节同旧** | `test_a_multi_block_png_decodes_to_exactly_the_buffer_pixels`（含带填充的 stride）、`test_a_png_within_one_block_is_byte_identical_to_the_old_encoder` |
| 跨块字典值不值 | 值：真实页面的扫描线上，去掉「前块末尾 32 KiB 当字典」CAD / figure7 体积 +1.2%；带上比单线程还小 0.1–0.3%。判据判**机制**（非首块字典恰是前块末尾 32 KiB），不判合成数据上的压缩率——合成数据两种做法只差 0.2%，阈值会漂 | `test_each_block_is_primed_with_the_previous_blocks_last_32k` |
| TIFF | `tiffwrite`：条带逐条生成（带填充的 stride 也不再整幅紧凑复制一份——PDFium 的 BGR 行按 4 字节对齐），`compress_each` 并行压；**产物逐字节不变**（每条 = 这几行紧凑像素的 `zlib.compress(…, 6)`，本文件自己按 IFD 读条带核） | `test_tiff_strips_are_exactly_serial_zlib_of_the_compact_rows` |
| child 的那份复制 | 关位图**之前**直接把 native 缓冲写进 `.part`，不再先 `bytes(bitmap.buffer)`；父进程读回的是文件里它自己的字节——RC-050（RasterBuffer 不共享 native 句柄）不变 | `renderchild._render`；真 child 的尺寸 / 像素用例 |
| 不做的 | 父进程 mmap 读像素：`RasterBuffer.samples` 的合同是 bytes，且 Windows 上删不掉仍被映射的文件；读回那一份留着，省在编码器里 | — |

### P1 效果（同机，main / P0 / P1 各 18 次独立进程交错，中位数）

| 场景 | main | P0 | P1 | 峰值 MB（父 + child） main → P1 |
|---|---|---|---|---|
| A4 整页 → PNG600 + TIFF600 | 775 ms | 777 | 199 | 382 + 242 → 191 + 143 |
| CAD 画布 → PNG600 | 243 | 220 | 96 | 203 + 142 → 120 + 93 |
| figure7 → PNG600 | 217 | 139 | 90 | 121 + 103 → 92 + 80 |
| 扫描件原图 → TIFF300 | 176 | 172 | 85 | 56 + 168 → 71 + 163 |
| CAD 原图 → PNG300 | 307 | 300 | 110 | 272 + 193 → 125 + 118 |
| 海报 → PDF + PNG300 | 438 | 349 | 315 | 79 + 130 → 70 + 123 |

对照旧后端（PyMuPDF，同一批源）：海报 323 ms、扫描件 TIFF 154 ms——两处原先更慢的现在都更快。A4 600 ppi 的合计峰值 334 MB
仍高于旧后端的 257 MB：像素在 child（PDFium 位图）与父进程（读回的那份）各有一份，是进程边界（ADR 0066）的代价；超出像素
预算的大图由 P2 的条带栅格把两边都压到条带大小。扫描件 TIFF 的父进程多了 ~15 MB：并行时在飞的条带（上限 2 × 线程数，各 ~1 MiB）。

反证 10/10：ordered_map 不限在飞、compress_each 换级别、非末块不 SYNC_FLUSH、不带前块字典、adler32 漏最后一块、PNG 漏滤波字节、
PNG 忽略 stride、PNG 小图换级别、TIFF 条带忽略 stride、child 只写半幅像素——各自红在上表的用例上。途中「不带前块字典」
第一次漏网（判据是压缩率阈值、数据是合成的），改判机制后才红。

## P2：超出单张像素预算的条带栅格

render child 的单张预算是 64 M 像素（`renderchild.DEFAULT_MAX_PIXELS`，~8000×8000 RGBA），而界面允许 1200 ppi：A4 @ 1200、A3 @ 600
这些旧后端导得出的尺寸，候选后端以 `pixel_budget_exceeded` 拒绝。把预算抬高只是让 child 一次申请更大的位图；这里改成**按行带渲染、
边收边编码**，内存只和一带一样大。

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 什么时候切带 | 整页像素**超过** `RenderHost.max_pixels` 才切（`banded.needs_bands`）；预算以内一律整页渲染一次、与从前逐字节相同——PDFium 按带渲染与整页**不逐字节相同**（抗锯齿随位图原点 / 尺寸差 1–5 级），这条边界因此是合同的一部分 | `test_within_the_budget_nothing_changes`、`test_an_export_within_the_budget_still_renders_the_whole_page_once`（页比一带大、仍只渲染一次） |
| 带怎么切 | 带高 = min(`band_rows_for(宽)` = `BAND_PIXELS`（16 M）÷ 宽, host 单张预算 ÷ 宽 − 上下重叠)——默认预算（64 M）下**只由宽度定**，同一输入永远同一份像素；预算配得比带目标小时按预算切，连一行都放不下就在任何一带之前拒（Codex #513 P2） | `test_the_band_layout_is_fixed_so_the_same_input_gives_the_same_files`、`test_band_height_adapts_to_a_host_budget_smaller_than_the_band_target` |
| 整页尺寸核对 | 每一带都核 child 回报的整页宽高 == 父进程按页面尺寸 × dpi 独立算出的；不等就是 `render_child_protocol`——否则 child 算页盒 / UserUnit 出偏差时，按计划高度拼出的是一张被悄悄裁掉的图（Codex #513 P2） | `test_a_band_from_a_page_whose_full_size_disagrees_with_the_plan_is_refused` |
| 接缝 | 每带上下各多渲 `BAND_OVERLAP`（4）行、只交出中间：笔画跨过带界时 PDFium 在位图边缘算覆盖率与整页不同，**u06 @ 150 ppi 接缝那一行差到 88 级**；多渲 1 行就回到抗锯齿噪声（≤ 5 级），4 行给更宽的效果留余量 | `test_banded_pixels_match_a_whole_page_render_within_antialiasing_noise`（与整页比：≤ 8 级、≤ 1% 字节） |
| child 协议 | `render` 多三个可选字段 `band_y0` / `band_rows` / `band_overlap`：整页尺寸照旧算，位图只接住这一带（整数像素平移 `start_y = -(y0 - 上重叠)`）；预算按**这一带（含重叠）**判、整页另有 `max_total_pixels`（512 M：A3 @ 1200 的 278 M 在内，RGBA 最坏 2 GB 原始像素仍在经典 TIFF 的 4 GiB 偏移之内）。两侧都判；越界的带 `bad_request`。父进程核回来的就是要的那一带，不是就 `render_child_protocol`（锁内 reap） | `test_the_child_checks_band_bounds_and_both_budgets_itself`、`test_a_child_that_answers_a_band_request_with_something_else_is_reaped` |
| 一次栅格、两个容器 | 要了 PNG 与 TIFF 时每一带只渲染一次、同时喂给两个写入器（RC-052 / 053 在条带下照样成立）；作业里 child 被叫的次数 = 带数 + 1（算键的 ping） | `test_an_export_job_over_the_budget_writes_both_formats_from_one_banded_pass` |
| 写入器 | `raster.PngStreamWriter`：扫描线按与 `encode_png` **同一条**凑块规则，压好的片段边收边写成多块 IDAT（PNG 允许）——拼起来就是整幅那条 zlib 流、逐字节相同。`tiffwrite.TiffStreamWriter`：每条 strip 与 `write_tiff` 的同一条逐字节相同，IFD 挪到文件末尾、头里的偏移回填（TIFF 允许）；写过 4 GiB 结构化拒绝。两者行数不对都拒，出错 `abort()` 收线程；产品检查器（`inspector.observe_png / observe_tiff`）核得过 | `test_the_png_stream_writer_fed_in_bands_writes_the_same_zlib_stream_as_encode_png`、`test_the_tiff_stream_writer_fed_in_bands_writes_the_same_strips_with_the_ifd_last` |
| 增量压缩 | `deflate.OrderedPool` / `ZlibStreamer`：`ordered_map` / `zlib_stream` 的增量版，后两者就是它们的批量写法（字节不变）；关之前就交出已压好的片段 | `test_the_streamer_gives_the_same_bytes_as_the_batch_stream_and_hands_them_out_early`、`test_an_aborted_streamer_leaves_no_thread_behind` |
| 整页上限 | 超 512 M 在**打开输出文件之前**就拒（原图导出写的是用户看得见的路径，不留空文件） | `test_the_page_total_cap_refuses_before_any_band_is_rendered` |
| 接进哪里 | `job.produce`（产品导出路）、`facade.Canvas.save_png / save_tiff`、`facade.original_png / original_tiff`；`write_tiff` 的 IFD 构造抽成与流式写入器共用的函数（1080 组输入逐字节同 P1） | `test_original_png_of_an_oversized_source_is_banded` |

### P2 效果（同机，每项 2 轮交错，中位数；峰值 = 父进程 + child）

| 导出 | 旧后端（PyMuPDF） | main（RenderCore） | P2 |
|---|---|---|---|
| A4 论文页 @ 1200 ppi → PNG（139 M 像素） | 1844 ms，857 MB | 拒绝 | 653 ms，173 + 88 MB |
| A3 CAD @ 600 ppi → PNG + TIFF（70 M） | 1466 ms，459 MB | 拒绝 | 691 ms，159 + 89 MB |
| A3 海报 @ 1200 ppi → PNG（278 M） | 5199 ms，1622 MB | 拒绝 | 2325 ms，166 + 149 MB |

PNG 体积比旧后端大（CAD 1.4 → 2.3 MB、海报 34 → 39 MB）：RenderCore 的 PNG 编码器从 U07 起就是「每行滤波 0」，旧后端（MuPDF）
做行滤波。这不是本 ADR 引入的差异，另立题目。

反证 19/19：child 不留上下重叠 / 平移不算上重叠 / 不裁重叠行 / 不按带判单张预算 / 不判整页上限 / 不判带越界、host 不核回来的带、
banded 不先判整页上限、预算内也切带、PNG / TIFF 各渲一遍、带高随预算变、PNG 按带切块、TIFF 按带切 strip、流不提前交出、abort 不收
线程、PNG 写入器不核行数、带高不看 host 预算、放不下一行也不先拒、不核整页尺寸——各自红在上表的用例上。途中两条漏网：「banded 不先判整页上限」被 host 的父侧判据兜住、请求数不变，
而它真正的作用是**不在用户路径上留空文件**——判据补上「文件不存在」；「预算内也切带」在一带装得下整页的小页上是等价变异，
判据改用比一带大、仍在预算内的页（A4 @ 600 ppi 正是这种）。

## 跨平台字节

写入器自己的流从「qpdf 压」改成「Python 的 `zlib` 压」之后，Canonical PDF 的字节依赖进程链接的 zlib 实现。三平台的 CI
（`foundation-u06-rendercore.yml`）仍把 `u06.pdf` / `u07.pdf` 与仓库里的 macOS arm64 基线**逐字节**比——经典 zlib 1.2.x / 1.3.x 同一
级别给同一份字节（改动前 qpdf 在 macOS 链接系统 libz、在 Linux / Windows 用 wheel 自带的 zlib，也一直逐字节相同）。换成 zlib-ng
的解释器（例如 CPython 3.14 的 Windows 版）会让这条比较红——那时再决定是钉住压缩实现还是把 PDF 的比较放宽到结构。

## 不做的（评估过）

* 降 qpdf 的 Flate 级别：体积 +9%，效果不如「别重压」。
* 把 PDFium 渲染标志（NO_SMOOTHIMAGE 等）调快：实测无收益，且改像素。
* 为预览单开一个 child：大图导出进行中的预览最多等 113 ms（旧后端被 GIL 卡 1050 ms），当前不需要。
