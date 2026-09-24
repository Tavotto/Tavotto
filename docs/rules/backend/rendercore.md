# RenderCore：Render IR、RenderPlan、字体政策、可检索文字、合成、栅格、facade 接线与有限产物验证（统一实施包 U06 / U07 / U08，ADR 0059 / 0060 / 0065 / 0066 / 0067 / 0068 / 0077）

> 2026-09-20 随 U06 新增，2026-09-21 随 U07 加合成与栅格两节、随 U08 加 facade 接线与产物验证两节；速查行在 `src/tavotto/AGENTS.md`「按改动路径找细则」
> 表里（`rendercore/` 那一行）。这里是这一主题规则的**唯一全文**；速查表只留一行。
> 改规则改这里，并同步那一行。

- **分层是硬边界**（`tests/support/importgraph.py` 的 `rendercore_model` / `rendercore_native`
  两层 + 层规则，`tests/test_rendercore_model.py` 钉外部名字）：纯模型（`ir` / `geometry` /
  `typography` / `fonts` / `sources` / `plan` / `placement` / `raster`）**只许标准库**与仓库里
  同样纯标准库的模块；候选包（pikepdf / fontTools / uharfbuzz / pypdfium2 / Pillow）只在 native 适配层
  （`hbshaper` / `pdfwriter` / `rasterio`）的函数 / 类里 import，PDFium **只在 `renderchild.child_main()` 里**（父进程
  import `renderchild` / `renderhost` / `preview` 不拉起任何候选包，`tests/test_rendercore_model.py` 钉着）；**整包零 `import pymupdf`**，也没有边进
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
  位图冒充矢量；实现了新操作先改表再改写入器，`tests/test_rendercore_writer.py` 逐操作交叉核对两边。
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
- **字体只认 allowlist 里的字节**（ADR 0060）：`rendercore/fonts_allowlist.json` 是唯一真值（13 张 OFL 脸：
  Liberation 2.1.5 × 12 + Noto Sans SC 子集），身份 = sha256 + face 序号，不是族名；注册表扫目录逐个算 hash，
  不在表里的进 `rejected`、绝不当字体用；找不到脸抛 `FontsUnavailable(code)`，**不摸系统字体、不用别的脸冒充**。
  字体文件不进 git：`scripts/fetch_fonts.py` 按 sha256 取到 `src/tavotto/resources/fonts/`（`.gitignore` 挡、wheel
  `artifacts` 收回、PyInstaller `resources/` datas 带走、许可证全文同目录）；`TAVOTTO_FONTS_DIR` 是排他覆盖。
  加一张脸 = 改 allowlist + ADR 0060 §1 的限制表，`tests/test_rendercore_fonts.py` / `test_font_provenance.py`
  第四档看住。集合外的限制（没有 fallback 层、Hangul 不在、`⁻` 靠合成）写在 ADR 0060 §1，是能力边界不是缺陷。
- **候选包只在函数里 import**：pikepdf / fontTools / uharfbuzz / pypdfium2 走 pyproject 的 `rendercore` extra
  （候选，未默认启用；`requirements-rendercore.txt` 是钉死镜像），没装时 `import tavotto.rendercore.*` 仍成功，
  `hbshaper.require()` 报 `CandidatePackagesMissing`；用例缺包 / 缺字体一律 skip 并写理由（skip 不是绿）。
- **可检索文字写入的三条纪律**（ADR 0060 §3，读取器实测）：一个 ActualText 段只放一个 `TJ`；`<</ActualText <…>>>`
  的分隔符按 spec 写全；缺字写 .notdef + ToUnicode 回原字 + `notdef_codes` 记数，不换脸。ToUnicode 按 cluster
  写（一个 code 记它第一次覆盖的原文），多字形 cluster / 同 code 不同原文 / 合成上下标三种情形包 ActualText。
  写入器的产物要经**不同源**的读取器验（`tests/support/pdfread.py` 纯标准库 + pypdfium2 + pdfminer + poppler），
  负例（丢 FontFile / 丢 ToUnicode / 错 GID）要在读取侧判据下真的红。
- **面板落位只有一份顺序合同**（ADR 0065，`rendercore/placement.py::place()`）：crop（顶原点归一化、相对源可见框）→
  缩放到内容框（90° 奇数倍宽高对调、填满目标框）→ 绕中心翻转 → 绕中心顺时针旋转 → 平移到目标框中心，逐句来自旧
  `_place_panel` 与前端 `PanelView`（`rotate(r) scale(±1, ±1)`）。PDF 源与位图源走同一个函数，只是可见框不同；
  `plan._panel_node` 把面板旋转四舍五入到 90 的倍数（PDF 与位图同一条）。测试里的落点期望**手算**（`tests/test_rendercore_placement.py`
  的矩阵、`test_rendercore_compose.py` 自己的归一化公式），不调 `placement` 反推。
- **外来页是 Form XObject，不重画、不退位图**（ADR 0065）：qpdf `as_form_xobject(handle_transformations=True)` +
  `copy_foreign`，源页的页盒 / `/Rotate` / `/UserUnit` 全折进 form 的 /Matrix，写入器只看 `placement.visible_box()`
  ——**不再自己转、自己乘**（RC-039：恰好一次）；资源随 form 各自一份，同名 /F1 / /X1 互不相干（RC-043），同一 (资源 key, 页)
  只搬一次（按字节身份去重，绝不按名字）。面板 opacity < 1 是**透明组**（组内 alpha 从 1 起算，RC-041），镜像是 `cm` 里的
  负缩放（RC-040）——两者都仍是矢量、文字层在；`opacity: 0` 是取值不是缺席（RC-042）。注释 / 动作 / JavaScript 不进产物，
  要密码 / 坏文件 / 缺页以 `source_unreadable` 拒绝（RC-046；只有 owner 密码的按普通 PDF 导入，#516），不画空框。**源的编码原样照搬**（ADR 0077）：单段带过滤器的内容流
  直接用源的已编码字节（`_keep_source_encoding`，解码后与 Form 明文逐字节相同才换）；保存**不交给** qpdf 的 `compress_streams`
  （它会把带 predictor 的 Flate 图片流解码重压），只压确实没有过滤器的流（`_compress_unfiltered` → `deflate.zlib_compress`，
  XMP /Metadata 保持明文）。
- **位图源经 `rasterio.decode()`（Pillow，U07 起是 `rendercore` extra 的直接依赖）成 `RasterBuffer`**：8 bit RGB / RGBA、
  紧凑 stride、**alpha 一律 straight**（Pillow 报 `RGBa` / `La` 的预乘图先反预乘，不许静默丢 alpha）；写成 DeviceRGB Image XObject + /SMask；8 bit RGB / 灰度 JPEG 原字节直通 `/DCTDecode`。
  像素网格不变，缩放只在 `cm` 里。`raster.RasterBuffer` 是本包里一块像素的唯一形状（栅格输出也用它，ADR 0066）。
- **写入器再核一次字节身份**：`files`（`job` 里是 `sources.read_frozen()` 核过 hash 的那一份）交进来的每份字节按 sha256
  与 `FileResource.sha256` 比，不符 `source_identity`、没交 `source_bytes_missing`——与 `read_frozen()` 是有意的两道（RC-014）。
- **native（PDFium）调用只在 render child 里，父进程一把锁串行**（ADR 0066，`renderchild.py` / `renderhost.py`）：probe / size /
  render / inspect 四种 op 都经 `RenderHost`（只要可见尺寸的调用走 `size`：`FPDF_GetPageSizeByIndexF` 不加载页、不解析内容流，
  与 `probe` 的尺寸逐项相同）（一个进程一个 child，`renderhost.shared()`；有界等待队列 `max_waiting`，
  满了立刻 `render_queue_full`——背压不堆积）；像素预算**父子两侧都判**（整页超过单张预算时按行带渲染：`banded.py`，带高只由宽度定、上下各多渲 `BAND_OVERLAP` 行只交中间、每带受单张预算、整页另有 `max_total_pixels`，预算以内一律整页一次、逐字节同从前，ADR 0077 P2）；每请求一个 deadline 管到底（等锁超时不打断正在忙的 child），收响应到点 kill → `wait()` reap →
  本次 `render_child_timeout` → 下一次自动重启；child 崩溃 / 外杀 → `render_child_died` → 下一次重启；起不来（exe 不在 / 冻结产物命令写错）→ `Popen` 的
  OSError 翻译成 `render_child_spawn_failed`（结构化，job 落到该格式的 `format_failed`）；像素文件的长度核对与
  整个 `RasterBuffer` 的构造都在 `request()` 的**锁内**做（`verify` 回调），bytes 对得上而尺寸不成一张图同样是
  `render_child_protocol`，说谎的 child 在释放锁之前就被 kill + reap；`close()` 之后一定 reap。
  dpi 是**物理**密度：位图尺寸 = PDFium 尺寸 × `/UserUnit` × dpi / 72（与 probe 同一次乘）。child 里 doc / page / bitmap 在 `finally` 关，像素在关之前**直接从 native 缓冲写进文件**（ADR 0077 P1，不再先复制成
  `bytes`）——父进程读回的是它自己的字节，`RasterBuffer` 不共享 native 句柄。`RLIMIT_AS` 只在 Linux 生效（macOS 内核不强制、Windows 无 resource），像素预算是那两处唯一护栏——不假装。
  child 是应用运行时的一部分，绝不装进用户的科学环境；冻结产物里同一个 exe 以 `--render-child` 再起自己
  （`renderchild.child_argv()`；配方 `scripts/dev/u07_freeze_child.py`，产品打包 / 签名归 U10 / U11）。
- **PNG 与 TIFF 从同一个 `RasterBuffer` 编码，RasterBuffer 只从 Canonical PDF 来**（RC-052 / RC-053）：`job.produce`
  一定把 PDF 写进作业临时目录（要没要都写），要了 PNG / TIFF 就把它交给 child 按 `ppi` 栅格**一次**，`raster.encode_png`
  与 `raster.write_tiff`（复用 ADR 0046 的纯标准库 `tiffwrite.py`）吃同一份 `samples`；两个编码器都经 `tavotto/deflate.py`
  并行、有界地压（ADR 0077 P1）：TIFF 逐字节不变，PNG 像素不变、压缩流字节确定且与线程数无关（一块以内同旧）；白底 RGB、透明底 RGBA
  （straight alpha，PNG 色型 6 / TIFF `ExtraSamples = 2` 同义）；尺寸 = `round(pt·ppi/72)`，报出去的就是 buffer 的尺寸；
  dpi 未知不写（不编一个数）。同一 buffer 的两个文件规范解码后像素**必须逐个相同**（精确，03 §6）；跨 renderer 只比
  几何再比固定读取器的图像，按 case 记阈值、不自动位移对齐（`tests/test_rendercore_calibration.py`）。child 起不来 /
  超时 → PNG / TIFF 各自 `format_failed` 带 `raster_code`，PDF 照常；PDF 没写出来 → 位图无从栅格，不拿旧文件冒充。
- **预览缓存的键是内容身份，不是 mtime**（`rendercore/preview.py`，RC-061）：`sha1(源 id | 内容 sha256 | 页号 | 宽 | 背景 |
  rendercore 名-版本 | PDFium 版本 | 字体政策版本)`——换 build / 换字体集合旧预览不命中；同键并发只渲染一次（每键一把锁、
  锁表封顶，淘汰只看登记使用者数——拿到手还没 acquire 的也算在用）；临时文件（.png 后缀）+ `os.replace`、Windows 撞读者句柄退让、零字节重建；`prune()` 只删成品 `<sha1>.png`（在飞的
  `.part.png` / `stage.*.src.part` 不碰；任何线程的 `get()` 正要交出去的那张从算出键到 return 都钉着、谁的 prune 都不删，pruner 串行）；hash 与渲染绑在同一份字节上——源先一次读成缓存目录里
  的不可变副本（边抄边算 sha256，`.src.part`，用完即删），child 渲染的是副本，算键与渲染之间源被换掉哪怕又换回去都影响
  不到这张预览；**位图素材（PNG / JPEG / TIFF）不交给 PDFium**（它不认，`Data format error`）：副本没有扩展名，类型从源路径取（`rasterio.kind_of`），在父进程经 `rasterio.preview()` 解码（解码前按 `SOURCE_MAX_PIXELS` 记账）缩放、白底合成，只有 PDF 进 child；身份分块算不整个读进内存；**命中快路**（ADR 0077）：文件指纹（`sources.file_fingerprint`）与上次抄副本时相同**且**成品在，
  就用那次副本上算出的 sha256 算键、直接交出成品——不抄、不读源；快路**从不渲染**，其余一切走副本路；**同一个源的副本路串行**（每源一把 staging 锁、与每键锁同表，锁序 staging → 每键；#489 第 4 条）：拿到锁先复查快路，排在前面的刚渲完的直接交出，同一时刻一个源最多一份副本；**异常抛出**，不返回空白图 / 旧图。U07 不接 `app.py`；U08 把候选下的 `/api/render` 接到这里（ADR 0067），`source_sha1` 的 (mtime, size) memo 留在 PyMuPDF 路——这里的身份从副本上算；指纹快路复用的是副本上算出的那个 sha256，不是 memo 出来的身份。
- **派生值按文件指纹复用，身份仍是字节 hash**（ADR 0077，`sources.FingerprintMemo`）：`file_fingerprint` = (设备, inode, 字节数,
  mtime_ns, ctime_ns)，**Windows 上不复用**（`st_ctime` 是创建时间，看不见改写；指纹恒为 None），只决定「能不能复用已经算出来的派生值」（`probe_asset` 的尺寸、预览键里的内容 sha256），从不进身份——冻结 / 写入 /
  渲染一律按 sha256 核。**最后一次改动离现在不到 `RACY_WINDOW_NS`（2 s）的不记**：时间戳按 tick 走（Linux ~ms、Windows ~16 ms、FAT 2 s），
  同一 tick 里的等长改写指纹相同（git 的 racy clean）；失败不记；`probe_asset` 每次回新 dict。用例里要量复用的，把 mtime **往回**拨
  （往前拨会落进「刚改过 / 来自未来」，快路根本走不到——这条假绿抓到过一次）。
- **冷启动预热**（ADR 0077）：`pdfbackend.warm()` 装载实现之后，实现若有 `prewarm()` 就交给它；候选在后台线程里起 child、建字体注册表、
  载 CJK 脸。失败只记日志，同一个错误在第一次真用到时原样抛出（不静默回退）。
- **PDFium 的 PNG 跨平台像素不同、同平台可复现**（ADR 0055 §7）：`evidence/u07/u07_pdfium.png` 是 macOS arm64 基线，
  `foundation-u06-rendercore.yml` 三平台只记各自的 sha256、**不判相等**；判的是 `u07.pdf` 逐字节相同与 truth 里的像素
  采样点；pdftotext 一律显式 `-enc UTF-8`（U06 的教训）。
- **接 ExportJob 只给 `produce`**（`rendercore/job.py`）：作业生命周期一字不改；给不出的格式逐项 `format_failed`
  且 `error.params.unsupported` 带操作与理由，写入器的 `WriterError` 与 child 的 `RenderChildError` 也落到这一档；
  编译期事实（缺字 / cjk 脸 / hidden）进 `job.warnings`；冻结源在写入前 `read_frozen()`。U06 / U07 里 `app.py` 不 import 它。
- **候选后端接 facade（U08，ADR 0067）**：`rendercore/facade.py` 是 pdfbackend 契约的候选实现（19 项同签名 +
  Canvas 面适配器；对拍表在模块头），由契约层 `pdfbackend/__init__.py` 按 `TAVOTTO_RENDER_BACKEND` 选中；
  进程级共享三样：字体注册表 `facade.provider()`、render child `facade.host()`（= `renderhost.shared()`）、
  预览缓存 `facade.preview_cache()`。产品导出路（`app._export_produce`）在候选下 `scope=canvas` 走
  `job.produce` + `sources.ExecutionSourceResolver`（带 override / runtime 素材由当次 worker 现画并附回执：
  `receipt.from_worker` / `from_native_session` → `receipt.source_artifact_for`，`origin=execution` 必核；
  磁盘原件不跑脚本），`scope=original` 经契约层的 `original_*`；EPS 报旧路同一个稳定码 `eps_not_for_canvas`；
  作业生命周期 / 写回事务 / 命名预留 / 覆盖 / 取消提交点全是既有权威，一份不复制。`/api/render` 走 `PreviewCache`
  （队列满 503）；`reset_projects(wait=True)` 收 child。**候选自己的生成物**：`rendercore/canvas_coverage.json`
  （`gen_canvas_coverage.py --backend rendercore`，`primary` = 12 张 Liberation 脸的交集）与
  `tests/golden/glyph_plan_vectors.rendercore.json`（`gen_glyph_plan_vectors.py --backend rendercore`），与默认表的
  差异是闭集（`tests/test_rendercore_glyph_vectors.py`）。**对拍纪律**：旧契约用例在候选下逐字重跑
  （`scripts/dev/u08_parity.py`，清单在 ledger `candidate_parity`），只许 deselect 实现特定断言且每条带存在的
  替代证据，运行器与 `tests/test_foundation_facade_ledger.py` 都拒绝没有替代证据的 deselect；用户合同一条不删。
- **有限产物验证（U08，ADR 0068）**：`rendercore/inspector.py` 重新打开**封口的** staging 文件量事实（PDF 页盒 / 旋转 /
  UserUnit → 可见尺寸、内容流普查、字体声明 vs 实际用到、抽回来的文字层、位图有效 ppi = 像素 ÷ 累计 CTM；PNG 逐块 CRC /
  IHDR / pHYs；TIFF IFD / 条带 / 分辨率标签），manifest 的 `plan`（生产者填，候选路 `job.plan_facts()`）/ `observed`（只来自字节）/
  `policy` 分开；四值判据 `verified / failed / unknown / not_applicable`，**unknown 不是 verified**。两档政策（D08）：`standard`
  只要求完整性 + 核心尺寸、可选项 unknown / failed 只写说明；`strict`（请求 `inspection.mode`）必需项失败或 unknown 都阻断，
  阈值只从 `profilestore.resolve_spec` 的规范来。钩子在 `exportjob.run(inspect=)`：`produce` 之后、**提交点之前**，拒绝的换成
  `artifact_rejected` 不发布，合格的带 `Output.manifest` 发布（发布只是 `os.replace`，字节就是核过的）。内容流遍历有预算
  （深度 / Form 数 / 指令数），耗尽 → 全 unknown；客户端的样式检查报告从不进检查器；检查器自己炸 → `uninspected()` 全 unknown。
  没有 pikepdf 的机器 PDF 走 `probe_asset` 基本观测（完整性 + 尺寸），其余 unknown 并写明。两个后端都接；默认后端下
  `dpi_tag`（PyMuPDF PNG 的 pHYs 是 96）与 `fonts_embedded`（base-14）是可选项 failed——如实记，不改默认路径。
- **四身份、来源段与公开投影（U09，ADR 0070）**：`rendercore/identity.py`——`semantic` = `plan_identity`、`render` =
  semantic + 后端 build + 栅格器与版本 + 字体政策版本（`identity.fonts_policy_version()`，与预览缓存键同一份）+ 像素参数、
  `artifact` = 封口字节 sha256（**绝不**再写进文件）、`run` = 作业 id（不进前三个）；不知道的一维写 None 不省略键。
  manifest 多 `identity` 与 `provenance`（源产物公开身份、回执公开事实 `receipt.public_facts()`、节点表：画布对象 id +
  实例序号，同一份源放两次是两个实例，外来页 `internal=unknown` 不编造，有界 `job.NODES_LIMIT`）；执行侧源随附
  `FrozenSource.receipt`；MCP 直出路经 `engine/artifactinspect.execution_provenance()` 用同一份算法补齐（源是这次执行的
  Figure，`kind=figure`，semantic 对格式不变）。**可以离开本机的只有 `inspector.public_projection()`**：身份与结论，
  不带 notes / `plan.text` / 对象框 / `source_id` / 节点 id / 路径 / argv；本轮不写 XMP，将来 XMP / 报告 / 遥测要带产物身份
  只许带它。故障阶段：`job.produce` 在 `compile` / `compose` / `raster` 各自 `job.trace.mark / fail`（ADR 0071）。
- **不切默认**：PyMuPDF 仍是默认后端（`pdfbackend.BACKEND_DEFAULT`）；候选只在显式选中时接管，前端不读候选
  覆盖表，产品包不带候选包 / 字体（U10 / U11）；facade 19 项的迁移证据逐项记在
  `docs/implementation/tavotto-foundation/U00_FACADE_LEDGER.md`。
