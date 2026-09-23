# ADR 0077：RenderCore 性能——派生值按文件指纹复用、源编码原样照搬、启动预热

日期：2026-09-23 · 状态：**Accepted（P0；P1 编码 / 内存、P2 条带栅格在后续 PR 里续写本 ADR）**
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
| 大流的压缩 | `raster.zlib_compress`：pigz 的做法——按 `DEFLATE_BLOCK`（1 MiB）切块、每块以前一块末尾 32 KiB 为预置字典、raw deflate 并行压（每次调用临时起线程池、用完 join，不留常驻线程）、非末块 `Z_SYNC_FLUSH`、补 zlib 头与整段 adler32。块边界只由输入长度定：**线程数不同输出相同**；一块以内逐字节等于 `zlib.compress` | `test_zlib_compress_*` |
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

## 跨平台字节

写入器自己的流从「qpdf 压」改成「Python 的 `zlib` 压」之后，Canonical PDF 的字节依赖进程链接的 zlib 实现。三平台的 CI
（`foundation-u06-rendercore.yml`）仍把 `u06.pdf` / `u07.pdf` 与仓库里的 macOS arm64 基线**逐字节**比——经典 zlib 1.2.x / 1.3.x 同一
级别给同一份字节（改动前 qpdf 在 macOS 链接系统 libz、在 Linux / Windows 用 wheel 自带的 zlib，也一直逐字节相同）。换成 zlib-ng
的解释器（例如 CPython 3.14 的 Windows 版）会让这条比较红——那时再决定是钉住压缩实现还是把 PDF 的比较放宽到结构。

## 不做的（评估过）

* 降 qpdf 的 Flate 级别：体积 +9%，效果不如「别重压」。
* 把 PDFium 渲染标志（NO_SMOOTHIMAGE 等）调快：实测无收益，且改像素。
* 为预览单开一个 child：大图导出进行中的预览最多等 113 ms（旧后端被 GIL 卡 1050 ms），当前不需要。
