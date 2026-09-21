# ADR 0066：render child、RasterBuffer 与 PNG / TIFF 同源

日期：2026-09-21 · 状态：**Accepted（U07 第二切片；候选栈未默认启用，不切默认后端）**
相关：[0065 合成](0065-imported-page-composition.md)、[0059 Render IR](0059-render-ir-and-render-plan.md)、
[0055 render_spike](0055-render-spike.md)（§2.3 child 四条路径、§7 PDFium PNG 跨平台不同）、[0046 EPS / TIFF](0046-eps-and-tiff-export-formats.md)、
[0049 写回像素门](0049-write-back-pixel-verification.md)、[0031 导出](0031-unified-export-pipeline.md)；实施包
`04_ARCHITECTURE.md` §4、`03_CI_POLICY.md` §6、`05_TEST_STRATEGY.md` §5、`phases/U07_render_output.md`、
registry RC-047 ~ RC-053、RC-061 / 062、RC-094 / 096。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| PDFium 在哪跑 | **一个应用自己的 render child 子进程**（`rendercore/renderchild.py`，pypdfium2 只在 `child_main()` 里 import），父进程经 `rendercore/renderhost.RenderHost` **一把锁串行**地说话；probe / render / inspect 三种 native 调用全部经它（RC-047：并发 preview / probe / export 之间没有进程内 native 并发——native 根本不在本进程）。不重写 Rust supervisor，不把 PDF 库装进科学环境（child 是应用运行时的一部分，RC-048） | `tests/test_rendercore_renderchild.py`：8 线程 × 5 请求无串包、seq 严格递增；6 线程混合 probe / render / inspect 经同一个 child |
| 背压 | **有界队列**：`max_waiting`（默认 32）个请求在等锁时第 N+1 个立刻 `render_queue_full`，不无限堆积（RC-062） | `test_a_bounded_queue_pushes_back_instead_of_piling_up` |
| 超时 / 崩溃 / 关停 | 每个请求**一个 deadline 管到底**（等锁 + 起 child + 发 + 收）：等锁超时只报 `render_child_timeout`、不打断正在忙的 child；收响应超时 → kill → `wait()` reap（`last_exit`）→ 本次 `render_child_timeout` → **下一次请求自动重启**；child 崩溃 / 被外力杀 → `render_child_died` → 下一次重启；child **起不来**（exe 不在 / 没权限 / 冻结产物命令写错）→ `Popen` 的 OSError 翻译成 `render_child_spawn_failed`，锁与队列槽照常释放、下一次再试；像素文件长度核对与整个 `RasterBuffer` 的构造都在锁内的 `verify` 回调里做，bytes 对得上而尺寸不成一张图同样是 `render_child_protocol`；`close()` 发 close 等它自己退出，等不到就 kill，之后一定 reap（RC-050） | 假 child 与真 child 各一组：超时 / 崩溃 / 外杀 / close |
| 像素 / 内存上限 | 像素预算**父子两侧都判**（父侧按 probe 尺寸精确判、child 打开页面后再判一次）；`RLIMIT_AS` 只在 Linux 生效（macOS 内核不强制、Windows 无 resource 模块——ADR 0055 §2.3 实测，不假装） | `test_pixel_budget_is_rejected_by_the_parent_before_any_child_call`、真 child 侧预算用例 |
| native 对象释放 | child 里每个请求的 doc / page / bitmap 在 `finally` 里 `close()`；像素在关之前**复制**成 `bytes`——`RasterBuffer` 拿到的是自己的字节（RC-050 must_fail：共享已关闭的 handle） | `renderchild._render` |
| 像素怎么回父进程 | 经文件不经管道：child 把 RGB / RGBA 原样字节写进父进程给的临时文件（`.part` + `os.replace`），父进程读完就删 | `test_the_pixel_file_is_removed_after_reading` |
| RasterBuffer | 本包一块像素的**唯一形状**（ADR 0065 已定，本 ADR 用它做输出）：8 bit、RGB（白底）或 RGBA（透明底）、通道顺序 R G B (A)、`stride ≥ 行字节`（行尾填充由编码器剥）、**alpha 一律 straight**（PDFium `FPDFBitmap_BGRA` + `FPDF_REVERSE_BYTE_ORDER`，不请求 Premul）、sRGB、`dpi` 已知才写、`premultiplied` 恒 False（RC-051） | `raster.RasterBuffer`；`tests/test_rendercore_raster.py` |
| PNG / TIFF 同源 | **同一个 RasterBuffer** 编两个容器：`raster.encode_png`（纯标准库；色型 2 / 6、pHYs 只在 dpi 已知时写）与 `raster.write_tiff`（复用 ADR 0046 的纯标准库 `tiffwrite.py`：Deflate、`ExtraSamples = 2` 非预乘、dpi 未知写「没有绝对单位」）。不是「记得要一致」，是同一份字节（RC-052 / RC-053）——**同一 buffer 的两个文件规范解码后像素必须逐个相同（精确）**；跨 renderer 只比几何再比图像、不比压缩字节（03 §6） | `tests/test_rendercore_rasterize.py`：padding stride / alpha 边缘 / 同灰度异色三种 fixture 在两个容器里逐字节相同；经真实 ExportJob 的 PNG 与 TIFF 经 Pillow 解码逐像素相同 |
| PNG / TIFF 从哪来 | 只从 **Canonical PDF**：`job.produce` 一定把 PDF 写进作业临时目录（要没要都写），要了 PNG / TIFF 就把这份 PDF 交给 child 按 `ppi` 栅格**一次**；透明背景 = 页面不画底 + child 从全 0 起算；尺寸 = `round(pt·ppi/72)`（报出去的就是 buffer 的尺寸）；重新栅格交付的 PDF 与交付的 PNG **逐字节相同**（RC-052 must_fail：PNG 用另一个 layout 引擎） | `test_pdf_png_and_tiff_come_from_one_canonical_pdf_and_one_raster` |
| 能力表 | `ir.CAPABILITIES["png"] / ["tiff"]`：十个操作全 `rasterized`（理由：PDFium 栅格化 Canonical PDF）；EPS 仍 unsupported（没有 PostScript 写入器，ADR 0046） | `test_rendercore_ir.py` |
| 失败政策 | child 起不来 / 超时 / 崩溃 → PNG / TIFF 各自 `format_failed`（带 `raster_code`），PDF 照常交付（partial）；PDF 没写出来 → PNG / TIFF 说「Canonical PDF 未写出」——**不拿旧文件冒充、不画空图** | `test_a_child_failure_fails_only_the_raster_formats_and_keeps_the_pdf`、加密源用例 |
| 预览缓存 | `rendercore/preview.PreviewCache`：键 = `sha1(源 id \| 内容 sha256 \| 页号 \| 宽 \| 背景 \| rendercore 名-版本 \| PDFium 版本 \| 字体政策版本)`（RC-061：内容身份不是 mtime；换 build / 换字体集合旧预览不命中）；同键并发只渲染一次（每键一把锁、锁表封顶、淘汰只看**登记使用者数**——拿到手还没 acquire 的锁 `locked()` 是 False，按它淘汰会让第三个同键请求另建一把）；副本抄到一半失败当场删掉；临时文件（.png 后缀）+ `os.replace`；Windows 上撞读者句柄**退让**；零字节重建；hash 与渲染**绑在同一份字节上**：源先一次读成缓存目录里的不可变副本（边抄边算 sha256，`.src.part`，用完即删），child 渲染的是副本——算键与渲染之间源被换掉、哪怕又换回去，都影响不到这张预览（「渲染后再核一次 hash」挡不住 A→B→A）；**异常抛出**，不返回空白图 / 旧图。**U07 不接 `app.py`**：`/api/render` 仍走 PyMuPDF，U08 换线 | `tests/test_rendercore_preview.py`（假 host 任何机器跑；真 child 一条） |
| probe 与 /UserUnit | PDFium 的 `get_size()` 忽略 `/UserUnit`（本机实测）；child 的 `probe` 用 pikepdf 读它并乘一次（RC-039）——与旧 `probe_asset`（PyMuPDF `page.rect`，同样忽略）是**有意差异**，进 U08 对拍表 | `test_real_child_probe_reports_the_visible_size_with_rotation_and_userunit` |
| 冻结 | `scripts/dev/u07_freeze_child.py`（U02 freeze 配方收编）：PyInstaller onedir 里 libpdfium + pikepdf native + 13 张字体 + allowlist 都在，冻结 exe 以 `--render-child` 再起自己（`renderchild.child_argv()` frozen 分支，RC-049），干净环境下真 probe / 真渲染。**不是** `packaging/tavotto.spec`；签名 / 公证归 U11 | `evidence/u07/freeze/report-<platform>.json` |
| spike 退役 | `scripts/dev/u02_spikes/` 的 render 半边（`fonts.py` shim / `pdfwrite.py` / `render_spike.py` / `render_child.py` / `freeze_spike.py`）删除；`tests/test_foundation_u02_render_child.py` 由 `test_rendercore_renderchild.py` 取代；`test_source_hygiene.py` 的单文件例外删除；`foundation-u02-spikes.yml` 只剩 runtime 半边（U05 收编时删）。`evidence/u02/render/` 与 `test_foundation_u02_render.py`（纯标准库读 evidence）留作记录 | `git diff --stat` |
| 跨平台基线 | PDFium 的 PNG **跨平台像素不同、同平台可复现**（ADR 0055 §7）；`evidence/u07/u07_pdfium.png` 是 macOS arm64 基线，`foundation-u06-rendercore.yml` 三平台各自记 sha256 进工件、**不判相等**；判的是 `u07.pdf` 逐字节相同（写入侧无平台维度）与 truth 里的像素采样点（每平台都过） | `scripts/dev/u07_evidence.py`、workflow |
| 旧新后端校准 | 同一画布经旧 `pdfbackend.compose` 与 RenderCore 各出一份 PDF，**同一读取栈**（PDFium）先比几何（对象包围盒），再比固定读取器的图像，**按 case 记阈值、不自动位移对齐**（03 §6「需要校准」）；旧后端在 opacity < 1 / flip 时退位图是旧缺陷不是真值（RC-096） | `tests/test_rendercore_calibration.py`（rc-venv：pymupdf 与候选包同在） |
| enrollment | RenderBench 实例 `U07-R1` 登记为 **observing**（lane `pr`，挂在已有的非 required `foundation-u06-rendercore.yml` 三平台上；测试 = `tests/test_rendercore_evidence.py::test_u07_evidence_is_current`），不新开 workflow、不进 required | `enrollment.json` |

## 1. 为什么是这个形状（与实测数字）

* **一个 child、一把锁**而不是线程池 / 多进程：PDFium 不是线程安全的（pypdfium2 文档），04 §4 明说首轮不建通用调度层；
  一个 150 ppi 的 160×120 mm 合成页（4 个导入页 + 位图 + 文字）在本机 child 里渲染 < 20 ms，child 冷启动（import
  pypdfium2）约 0.3 s——串行的代价远低于第二个进程的复杂度。实测需要吞吐时再扩，扩的是「child 数」，协议与预算不变。
* **像素经文件不经管道**：一张 600 ppi A4 的 RGB 是 4960 × 7016 × 3 ≈ 104 MB，走行分隔 JSON 管道要 base64 + 一次
  Python 级拷贝；文件 + `os.replace` 是零解析、且 child 死在半路时父进程看得见 `.part`。
* **RGB / RGBA 两种缓冲**：白底不带一条全 255 的 alpha（PNG 色型 2 / TIFF 3 样本，与旧后端 `alpha=False` 的用户合同
  同形），透明底才是 RGBA；本机实测 BGR 与 BGRA 两条路渲出的 RGB 逐字节相同。
* **evidence（macOS arm64，PDFium 153.0.7999.0，pikepdf 10.13.0.post1，Pillow 12.3.0）**：`u07.pdf` 8 179 B，
  sha256 `68866353ce66…`（两次运行逐字节相同）；栅格 150 ppi → 945 × 709 RGB，stride 2835；PDFium 对象普查
  text 6 / path 6 / image 3（自己放的 1 张 + 两个源里的 /X1）/ form 5（4 个导入 + 1 个透明组）；30 条核对全过。
  freeze：PyInstaller 6.19 onedir 9.6 s，202 文件 87 MB，`libpdfium.dylib` + `libqpdf` + 13 张字体 + allowlist 在
  `_internal/`，冻结 exe 以 `--render-child` 自起（`child_argv()` frozen 分支）、probe 报 CropBox 270 × 160、
  渲染 400 × 237 RGB，2.67 s；干净环境（无 PYTHONPATH / site-packages）。
* **校准对拍**（`tests/test_rendercore_calibration.py`，本机实测后再定阈值）：整页导入 0.34% 像素 / mean 0.73；
  旋转 + crop 2.5% / mean 2.07（矩阵差 0.007 pt 让每条边的抗锯齿挪一档，差全在边上）；形状 0.003%；opacity 0.5
  面板 3.1%（旧位图 vs 新透明组，只比「新内容框落在旧图框内」）；文字：advance 兼容（墨的左右边 ≤ 0.5 pt），
  基线**按批准的量**差 1.77 pt @ 12 pt——同一条公式，PyMuPDF 给 Times 的 ascender 是 bbox 的 1.053、Liberation
  Serif 的 OS/2 typo ascender 0.693（ADR 0060 §4 的 D07 迁移）。判据是「差恰好等于那个量」，不是「差得不多」。
  反证：把面板挪 2 mm 必红（不位移对齐）。

## 2. 反证（每条变异一次就红，`scratchpad/u07/mutate_b.py`，19 条）

| 变异 | 红在 |
|---|---|
| host 去锁 | 8 线程串行用例 + 混合 probe / render / inspect 用例（seq 不再严格递增） |
| 队列不封顶 / 超时不 kill / kill 后不 wait / 父侧不判预算 / 等子进程退出才读 | 各自的假 child 用例 |
| child 透明底也铺白 / probe 忽略 /UserUnit / child 侧不判预算 | 真 child 用例（rc-venv） |
| PNG 编码不剥行尾填充 / dpi 未知也写 pHYs 72 | rasterize 纯模型用例 |
| PNG 不从 Canonical PDF 出（另画一张空页栅格） | `test_pdf_png_and_tiff_come_from_one_canonical_pdf_and_one_raster`（第一版变异写了一张**相同**的 PDF——语义 no-op、绿；换成不同的页才红） |
| child 失败时拿 PDF 字节冒充 PNG | `test_a_child_failure_fails_only_the_raster_formats_and_keeps_the_pdf` |
| 预览键用 mtime / 不含 PDFium 版本 / 不含页号（Codex #471 P2）/ 失败时把旧文件当成功 / 撞锁不退让 / 零字节当成品 / 同键不去重 | preview 用例（假 host，主 .venv 就能跑） |
| child 写到一半被 kill 留下的 `.part` 不清 / 像素文件长度与响应不符时不 reap（Codex #471 第二轮 P2） | `test_a_stale_part_file_is_removed_when_the_render_fails`、`test_a_pixel_file_that_disagrees_with_the_response_reaps_the_child` |
| 身份整个读进内存（Codex #471 第二轮 P2） | `test_source_identity_hashes_in_chunks_without_read_bytes` |
| `Popen` 的 OSError 裸抛（不是 `RenderChildError`，job 接不住、整个作业炸）/ 像素文件核对在释放锁之后才做（排在后面的请求会挤进去和说谎的 child 说话）/ 预览渲染源文件而不是 hash 过的副本（A→B→A 换回时「渲染后再核」恒绿）/ dpi 尺寸忽略 `/UserUnit`（Codex #471 第三轮 P2） | `test_a_child_that_cannot_be_spawned_is_a_structured_failure_not_an_oserror` + rasterize 的 `cannot-spawn` 参数、`test_pixel_validation_runs_inside_the_request_lock`（SpyLock：render 释放锁那一刻 child 已被 reap）、`test_the_child_renders_the_hashed_bytes_even_if_the_source_is_swapped_and_restored`、`test_real_child_dpi_render_is_sized_by_the_physical_page_including_userunit`（/UserUnit 2 的 160 × 270 @ 72 dpi → 320 × 540）；四条变异各红在自己的用例上，第二轮的四条用例对它们全绿——那正是为什么要新用例 |
| 锁表淘汰看 `lock.locked()`（拿到手还没 acquire 的被淘汰，同键两把锁）/ 抄源失败留下半截 `.src.part`（Codex #471 第四轮 P2） | `test_a_handed_out_but_not_yet_acquired_lock_survives_table_eviction`（`_reserve` 之后灌满表、同键第三个请求拿到同一把；用完才可淘汰）、`test_a_half_written_staging_copy_is_removed_when_copying_fails`（第二块 ENOSPC，目录里没有 `*.src.part`）；各自变异红 |
| 响应的 bytes 对得上但 width / stride / channels 与字节不成一张图：`RasterBuffer` 在锁外才炸（RasterError 不是 RenderChildError，job 整个 export_failed、说谎的 child 留给下一个请求）/ `prune()` 把别人在飞的 `.part.png` 当缓存删、预算够小连刚发布要交出去的也删（Codex #471 第五轮 P2） | `test_a_response_whose_shape_does_not_fit_the_bytes_is_a_protocol_failure_reaped_in_the_lock`（RasterBuffer 在锁内的 verify 里建，建不出来 = `render_child_protocol`，释放锁那一刻已 reap）、`test_prune_leaves_in_flight_part_files_and_the_just_published_one_alone`（只认 `<sha1>.png`，`keep=` 那张预算再小也留）；各自变异红 |
| `keep=` 只护自己这一次 prune：两个不同键的 `get()` 在紧预算下并发发布，互删对方正要交出去的那张（Codex #471 第六轮 P2） | `test_a_result_being_handed_out_by_another_get_survives_a_concurrent_pruner`——`get()` 从算出键到 return 钉住自己那张（`_pin` 计数），任何线程的 prune 都不删钉住的，pruner 之间串行；用 `_publish` 钩子把线程 1 卡在发布之后、return 之前，不赌时序；变异（只看 keep）红 |
| 等锁不计时（拿到锁才开始计时） | `test_the_timeout_covers_waiting_for_the_lock_and_does_not_kill_a_busy_child`——判据的主语是「B 回来时 A 还没完」+ 余量 0.5 s 的上界（第一版 0.3 s 慢请求 + `< 0.25 s` 贴边，Windows runner 上量到 0.28 s 假红） |
| 等锁不计时（deadline 从拿到锁才起算）/ 等锁超时也 kill 正在忙的 child（Codex #471 P2） | `test_the_timeout_covers_waiting_for_the_lock_and_does_not_kill_a_busy_child`（A 拿锁 0.3 s，B 带 0.05 s 超时必须 ~0.05 s 内拿到 timeout 且 A 照常、child 不重启） |

## 3. 没做 / 边界

* `RLIMIT_AS` 在 macOS / Windows 上没有有效机制；像素预算是那两处唯一护栏。
* 多进程 child（吞吐）：首轮一个；实测需要再扩。
* `app.py` 的 `/api/render` / `_export_produce_canvas` 仍走 PyMuPDF（U08 换线时 `source_sha1` 的 (mtime, size) memo
  与本模块的 `source_identity()` 合一；本轮预览缓存每次读字节算 sha256，没有 memo）。
* Canvas 面的 `annotate_asset`（写回携带标注）与 `scope=original`：U08。
* 三平台的 PNG sha256 与 freeze 报告由 `foundation-u06-rendercore.yml` 每腿写进工件，本 ADR 不预填。
