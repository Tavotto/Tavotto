# 导出（ADR 0031 / 0046）

> 原文出自 `src/tavotto/AGENTS.md`「布局层（R18）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_export_pipeline.py`、`test_export_route.py`、`test_worker_export_pixel_budget.py`、`test_export_request.py`、`test_export_endpoint.py`、`test_tiffwrite.py`、`test_epsfile.py`、`tests/golden/filename_vectors.json`

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 210
改到: `/api/export*`、`engine/exportreq.py`、`exportjob.py`、`exportroute.py`、`svgstore.py`、`svgexecution.py`、`tiffwrite.py`、`_serialize_figure`
主题: 导出（ADR 0031 / 0046） → `export-pipeline.md`
必守要点: 五端点一服务；文件名严格同源
-->

- **导出（ADR 0031，2026-08-31 定版）**：`POST /api/export`（同步）/
  `/api/export/start`（后台作业，进度经 SSE `export.progress`）/ `/state` /
  `/cancel` / `/validate` —— **五个端点、一个服务**。「要什么」只有
  `engine/exportreq.py` 的 `ExportRequest`（缺省值一处），「怎么落盘」只有
  `engine/exportjob.py`（临时目录 → 全部产出完成 → `atomicio.publish_file()`
  逐个原子 replace）。
  * `scope=canvas` 合成页；`scope=original` 走 `pdfbackend.original_pdf/png`
    （矢量整页搬运不重画，位图**保源像素网格**）。`original` 段里**没有**
    x/y/w/h 与页面尺寸——想让画布缩放漏进原图导出得先改结构。
  * **PPI 只在有位图格式时是数字**，否则 `None`；`partial` 是独立一档
    （成功的照常交付，失败的带自己的 `error.code`）；覆盖策略闭集
    `ask`/`replace`/`rename`，`ask` 撞名时**不渲染不写盘**。
  * 后台作业必须 `app.bound_project(ctx)`：`_request_ctx()` 的兜底是默认项目，
    多项目并存时不绑定 = 成功地导出了另一个图库的同名图。
  * **作业的终局字段先于终局 `status` 可见**（issue #381，2026-09-16）：
    `/api/export/state` 在另一线程读 `to_payload()`，看到的是一份快照；写者的
    顺序固定为 `finished_at` / `phase` / `error_*` → `status` → `_emit()`，三条
    终局路径（`run()` 的 finally、`_fail()`、conflict 分支）一个不例外。反过来写
    的话读者会拿到 `done` + `elapsed_ms: None`——Windows 上删临时目录慢到 20ms
    的轮询都踩得中。判据的主语是**读者看到的快照**，不是写者的顺序；看护用例
    `test_a_terminal_status_is_never_visible_before_its_timing` 把窗口撑开量它。
  * **作业记得是哪个项目发起的**（T04）：`ExportJob.project_id`（私有，不进 `to_payload()`）。`/api/export/state` 与
    `/cancel` 对别的项目的 job_id 读作 `unknown` / 不可取消；终局时 `_emit` 冻结一份白名单诊断快照（见 `diagnostics.md`
    「任务绑定诊断」）。**不读 `to_payload()`**：完整请求（含文件名、导出目录）留在既有私有执行层。
  * **旧契约一个字节不变**：没有 `filename` 的请求（`stem`/`dpi`，或
    `items[]`+`texts[]`）抬成同一个作业，文件名照旧带时间戳，回执照旧有
    `files[]`/`export_dir`/`warnings`，报告照旧叫 `_proof.json`。
    新路径的报告叫 `<基名>_style-check.json`（v3，`kind` 不变）。
  * 文件名规则是**严格同源对**（`web/src/lib/exportName.ts`），
    `tests/golden/filename_vectors.json` 两侧各跑一遍；**首尾空白的字符集
    写死一份**，不许退回 `str.strip()`/`String.trim()`（两者认的集合不同）。
  * **EPS 与 TIFF（ADR 0046，2026-09-06）**：格式顺序最初为 `(pdf, png, eps, tiff)`，
    新格式追加在后。TIFF 与 PNG 出自**同一次栅格化**（`Canvas.save_tiff` /
    `pdfbackend.original_tiff`），编码器是纯标准库的 `tavotto/tiffwrite.py`
    （Deflate 无损；父进程没有 Pillow，**别为它引进 Pillow**；条带经 `tavotto/deflate.py` 并行压、产物逐字节同串行，ADR 0077）；位图源的分辨率
    标签只写源文件自己声明过的密度。EPS **只有 worker 的 matplotlib 写得出**
    （父进程没有 PostScript 写入器）：`scope=canvas` 逐项报 `eps_not_for_canvas`，
    没有脚本的图报 `eps_needs_script`（有脚本但没有活 Figure 另报 `eps_needs_live_figure`），其余格式照常交付；要了 EPS 时 PDF/PNG/TIFF
    也让 worker 现画（`_resolve_panel_source(rerender=True)`），四个格式出自同一次
    脚本运行。**不许**用 `Pixmap.save(…, "ps")` 之类把位图裹成 PS 冒充矢量。
    「谁来渲染」在导出路上的唯一调用点是 `_serialize_figure()`。
  * **SVG（X03，2026-10-15）**：追加为 `FORMATS = (pdf, png, eps, tiff, svg)`，前后端请求闭集同步；
    `original` 与 `canvas` 都走既有作业，不另开发布通道。SVG 是矢量格式，PPI 规则不变，文字**默认保留为 `<text>`**（ADR 0144，取代 X03 起的「一律转轮廓」；
    `scope=original` 的 matplotlib 图走 worker 独立的 `svg.fonttype=none` 一路：export 的 `svg_fonttype` / `svg_text_path` 两个可选字段，
    path 基准字节与等价证人不动）；
    画布写入与有限产物验证见 `rendercore.md`，不承诺已通过真实 Office 验证。
    Figure 的 PDF/SVG 必须在一个 owner-thread 命令里、一次 overrides 应用下生成，沿用同一页面框、
    执行回执与 generation；**PDF 与 SVG（path 基准 + 保留文字）出自同一次 `Figure.draw`**（ADR 0150，`engine/teedraw`：
    `print_pdf` 里的 `MixedModeRenderer` 换成 `TeeRenderer`，版面度量取自 PDF），**单次绘制资格是白名单**（`_svg_pair_artist_blocker`）：图里每个 artist 的类与实例两层、所有可调用属性都必须是 matplotlib / mpl_toolkits 自己定义的
    （按代码出处认，不看 `__module__`；只读、不调用用户代码），否则阻断——能看到 renderer / 会被每个目标各跑一次 / 两遍之间会不同的代码（`draw_event`、自定义子类、
    `get_window_extent` 等实例覆盖、`agg_filter`、axes locator、注解坐标回调、path effect……）靠这一条整体覆盖，不再逐个列黑名单。transform / scale（用户 Transform 子类、FuncScale / FuncTransform 的用户函数：renderer 拿对象自己求值）两种口径都核；
    唯一例外是封闭的值槽 = Axis 的 formatter 与 locator（它们在 Axis.draw 阶段只算一次，计数器 FuncFormatter 照常放行）；另保留 `_svg_pair_env_blocker`（自定义 `Figure.savefig` / canvas / 打印方法 / 注册后端）。`teedraw.status()` 自检先于任何导出，
    不过就退回「二次绘制 + 完整回调阻断」并把自检结论带进原因。响应里 `svg_pairing` = `single_draw` | `two_pass`。
    `teedraw` 的替身只对「当前线程会话 + 会话那张 figure」生效，别的线程 / 别的 figure 的 `savefig` 原样。
    **#1185 SVG 素材字节绑定的等价证人不走 tee**（独立 savefig，见 ADR 0150「为什么不走 tee」），且**有任何用户回调就不绑**（响应 `svg_strict_blocker`）。
    **自动捕获和导出要求 `require_built`**：只用已经运行并接纳的 Figure 或已验证的冻结对，冷 worker /
    过期会话 / 缺失表示不能触发脚本重跑；自动捕获遇到被保留的阻断在 draw 之前拒绝。**未知当前回执不是旧配对的许可**：磁盘 current marker
    与预览 metadata 不能补造当前执行证据；没有文档引用时，执行源必须有当前进程实际观察到的回执。
    文档面板的 `figureSnapshot` 经请求字段 `figure_snapshot` 传入，闭集结构与存储合同见
    `layout-versions-and-documents.md`。冷态且当前回执未知时，明确引用可导出保存时的版本；
    **仅导 PDF 也必须按该引用取同一版本**，不能换成当前文件或另一次执行。已知新回执与引用不符时拒绝旧引用，
    即使新一代捕获 / 持久化失败也不回退。旧无 owner 引用只可由注册表或缓存描述符正向重算
    精确 runtime ID 的归属证据否决，不能借缓存授当前资格或猜静态来源。实际执行边界按 script + run_config 撤旧输出全集，不依赖新描述符非空；
    执行中、零图、异常、部分捕获与开始后取消都不能借旧配对。safe 回执实例含真实 execution ID，workerd 透明重开也分代；
    隔离写回 verify/one_shot 不替代当前执行。native 只用可信 target-start / 成功 release 的事实，不把 hello 当执行。
    引用无效或对应资产缺失同样报错，不静默换来源；只读快照查询同步核在途执行事实。
    无 override 的静态 PDF 可由独立接纳且绑定到**当前精确字节 hash** 的配对授权，不依赖未知的运行回执；
    同名邻居文件、预览、任意旧导出不算，已知新回执仍须匹配。
    opaque PDF 的 SVG 逐格式失败并指出来源，其他成功格式照常交付，不整页转位图或少画面板。
    面板缺同 Figure 的 SVG 表示时，该项失败带**面向用户的码**（`rendercore/job._svg_panel_gap`）：脚本现画的图 `svg_figure_unavailable`
    （`figure` 图名；`reason` = worker 给的配对阻断原因，如自定义刻度函数；`detail` = 内部原文，只进诊断），静态 PDF / 位图 `svg_source_unsupported`；
    不得再让 `imported_page: A static PDF requires …` 这类内部串落到界面。
    首次明确运行后自动尽力捕获全质量对，捕获失败不把成功运行或正常 PDF 导出变成失败；
    保存布局先冻结精确编辑变体再一次原子写正文，失败清除待保存正文的旧引用并返回来源 warning，布局仍可保存。
    持久化与旧项目恢复边界见 `layout-versions-and-documents.md`。
    看护：`tests/test_export_svg.py`、`tests/test_svg_execution_lifecycle.py`、`tests/test_worker_svg_pair.py`、`tests/test_svgstore.py`。
  * **一次作业里，同一张图的所有格式出自同一个基准（#1185 / #1209，A3）**：做不到的格式逐项失败并给出
    真实原因，**不得悄悄换来源、不得为某个格式重新执行脚本换来源**。SVG 素材（`scope=original`、无编辑）的
    基准是**文件字节**（sha256 取磁盘原始字节）：svg = 这份字节；eps 只有「活 Figure 被证明出自这份字节」（`_svg_asset_binding`，
    与 PDF 路径同一个 `svgstore.load(…static_sha256=…)` 先例）才给，且只走 `existing_only`（不启动 worker）。
    断言写在**作业汇总处**而不是各格式路径里：`Produced.basis`（`copy`/`convert`/`redraw` + 基准 sha256）由生产路径
    如实声明，`exportjob.enforce_one_basis` 对结果核一遍，基准身份与参照不同（含无证明的 `None`）的那一项改成
    `export_basis_mismatch`，其余照常交付；没声明 `basis` 的路径尚不受约束（#1209 A2 把其余路径接进来）。
    失败原因三档、互不混用：`eps_needs_script`（注册表里确实没有脚本）/ `eps_source_unverified`（有脚本，
    但没有字节绑定的证明）/ `eps_needs_live_figure`
    （有脚本、没有活 Figure，即冷 worker：先在画布上打开并运行这张图）。前端 `epsAvailability` 拿不到绑定事实，不预先禁用，
    由后端逐项失败并显示真实原因。看护：`tests/test_export_svg.py`
    （`never_come_from_two_bases` / `byte_bound` / `asserts_one_basis` / `cold_worker_eps` / `saved_by_the_live` / `differs_from_the_live`）。
    **绑定的写入**（`_bind_svg_asset`，在 `_capture_svg_snapshot_best_effort` 里，与 PDF 的 `static_sha256` 同一机制、同一个 `svgstore`）：
    脚本运行后，项目根下 `<stem>.svg` 与活 Figure 刚导出的候选 SVG 抹平 matplotlib 每次都变的日期 / 随机 id 后
    摘要相同（`svgcheck.canonical_digest`）才写；文件名只提名候选、不构成证明；不同就不写。
  * **来源成为值：`engine/exportroute.py`（#1209 / A2a）**：「这次导出这张图用哪份来源」只在这一个纯标准库模块里回答
    （不执行、不读文件、只收调用方给的事实）。`wants_execution()` 是 `_resolve_panel_source`、原图范围的
    `execution_source`、`rendercore.sources.needs_execution` 共用的唯一谓词，`original_rerender()` 是
    `app._original_rerender` 的本体——**不许在这三处之外再写一遍「有 override / runtime / 要 EPS 就现画」**。
    `/api/export/validate` 在响应里带 `route`（`panels[id].source / embeds_disk_bitmap / formats[fmt] = {how: copy|convert|redraw|unavailable, reason?, conditions?}`），
    只回事实、不拦导出；`conditions`（`live_figure` / `svg_asset_binding` / `figure_snapshot`）是 route 给不出、导出时才核的前提。
    route 算不出时响应不带 `route`（附加事实，validate 不因此失败）。原图范围里嵌入磁盘原件的 PDF / PNG / TIFF，
    产物 manifest 的 `provenance.sources` 记那份原件的 SourceArtifact（现画的临时 PDF 没有可独立核对的回执，不编）。
    与前端 `exportRoute.ts` 的 `embedsDiskBitmap` 同源对：`tests/golden/export_route_vectors.json`；看护 `tests/test_export_route.py`、
    `tests/test_export_route_vectors.py`。每个面板另有 `offers.eps`（不论勾没勾，EPS 给不给得出）。前端读 route 见 `docs/rules/frontend/export-pipeline.md`。
  * **有限产物验证（U08，ADR 0068）**：`exportjob.run(inspect=…)` 在 `produce` 之后、提交点之前重新打开
    每个封口的临时文件（接线层 `engine/artifactinspect.py`，两个入口共用；检查器 `rendercore/inspector.py`），拒绝的那一项 `artifact_rejected` 不发布（partial 语义不变），合格的
    带 `Output.manifest`（`inspector.summary()` 投影，旧字段一个不动）。请求可带 `inspection: {mode: standard|strict,
    profile_id}`（缺省 standard，老客户端等价）；strict 的阈值只从 `profilestore.resolve_spec` 来。细则在 `rendercore.md`。

## 多页 PDF：把同一份 PdfPages 的几张图合成一个文件（#1370 PR3）

- **请求**：`merge_pdf: {pages: [<original 段>, …]}`（`exportreq.MergePdf`）。`pages` 按页序给每一页的来源，形状与 `original` 完全相同
  （各带自己的 overrides / 快照引用 / 来源上下文）；页序由请求定、后端不改——前端按 `pdf_pages.page_stems` 的顺序传，同一张图写了多页就重复。
  `filename` 就是多页 PDF 的基名（默认取 PdfPages 的文件名），走**既有**命名规则，没有新规则，`exportName.ts` ↔ `filename_vectors.json` 不动。
  加字段不升协议版本。
- **只在 `scope=original` 且 `formats` 恰为 `["pdf"]` 时合法**；同时带 `original`、页数为 0 或超过 `MERGE_PAGES_MAX`、非 pdf 格式、canvas 范围一律 `bad_merge`
  （碰磁盘之前拒绝，文案两种语言）。SVG / PNG 没有「多页」，所以对话框的勾选只在 PDF 格式下出现，勾上后每张图的单页 PDF 不再单独出，其余格式照旧逐张导。
  页内的错误沿用 `original` 的码（`missing_figure` / `bad_overrides` …）。此时请求的 `original` 就是 `pages[0]`，所有只认单张图的消费者（route / 遥测）按首页读。
- **合并只有一个调用点**：`app._export_produce` 在 `merge_pdf` 非空时转到 `_export_produce_merged_pdf`。每一页先走**现有的单页 PDF 导出**
  （`_export_produce_original`，借 `_PageJob` 替身换成单页请求，`do_export` 与 #1355 的配对代码一字不动），再由 `pdfbackend.concat_pdf`（实现在 `rendercore/facade.py`，应用不直接碰原生 PDF 库）
  用 pikepdf `pages.extend` 按页序拼成一个文件，合成**一个** `Produced`；同一来源 + 同样编辑的页只导一次、按页序重复放（不做快照，只提示）。
- **任何一页失败 = 整个多页 PDF 失败**，带那一页自己的错误码，不交付缺页的文件。页数与页序写进回执 `request.merge_pdf = {page_count, figure_ids}`。
- **已知代价**：字体每页各嵌一份（每个单页文件自带子集字体，搬运不跨页去重），文件比脚本里 `PdfPages` 直接写出的偏大，内容不变。
  产物检查器按首页核页面尺寸；`vector` 取各页的与。
- MCP 的 `tavotto_export` 走 `bridge.export`（按会话里的图直出、没有 `original` 段），不接这个字段。
- 看护：`tests/test_export_multipage_pdf.py`（页数 / 页序 / 重复页 / 非法组合 / 失败不交付 / 单页不变）；前端 `lib/pdfPagesMerge.test.ts`、`ExportDialog.test.tsx`。

## 位图像素预算：两条路径各一份，互不替代

- **worker 直出位图**（`figsession.do_export` → `Figure.savefig`，写回事务的 600 dpi PNG 目标走这条）：
  Agg **整幅 RGBA 一次分配**，PNG / TIFF / JPEG 编码再各拷一份。预算 `figcapture.MAX_EXPORT_RASTER_PIXELS`
  = 1 亿像素（≈ 400 MB 栅格，峰值约 3 倍），格式闭集 `RASTER_EXPORT_FORMATS`（pdf / svg / eps 等矢量格式不检查）。
  **判据的主语是 savefig 即将分配的每一块栅格**：最终栅格取 `bbox_inches` 生效后的真实尺寸
  （frame、脚本设的 `savefig.bbox="tight"` 都按 `print_figure` 同一做法量，tight 先在导出 dpi 下走一遍不出图的
  draw 再取 `get_tightbbox` + `pad_inches`，看护用例与 `savefig` 真写出的像素逐一相等）；名义画布（figsize×dpi）
  在有布局引擎或 tight 时也会被分配，同样核。检查在 `overrides.apply` 之后、`mkdir` / 任何写入之前
  （`figsession._check_export_raster_budget`），超了抛 `ExportRasterTooLarge` → 协议错误
  `export_raster_too_large`（extra：`width` / `height` / `max_pixels`，非 retryable）→ `pool.WorkerError`
  → `pool.recovery_params` → `app._worker_error_payload` 的 `params`；写回事务沿用既有 409 + `stage: "verify"`、原件零改动。
  **不降 dpi、不缩尺寸、不放行「差一点」**；预算常量没有对外调参入口（测试改模块常量）。
- **RenderCore 最终导出**（`rendercore/renderchild.py` 的 `DEFAULT_MAX_PIXELS` / `DEFAULT_MAX_TOTAL_PIXELS`，
  条带栅格）：PDFium 按带渲染、整页不落内存，上限更高、模型不同。**它不能证明 worker 直出安全，反之亦然**；
  改任一份预算不连带另一份。
- 看护：`tests/test_worker_export_pixel_budget.py`、`tests/test_write_back.py`（超预算 409 零改动）、
  `tests/test_error_codes.py`（文案两种语言）。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 五个端点一个服务
- `original` 段没有 x/y/w/h
- `partial` 独立一档
- 终局字段先于 `status` 可见
- 文件名规则严格同源对
- EPS 只有 worker 写得出、不伪称矢量

- X03 原图仅 SVG：请求未带明确快照引用时，runtime / 有修改或显式已接纳的 Figure 可单次原生序列化，
  `require_built=True`，不需要 PDF 配对 guard。无修改静态文件仍须同源 hash 绑定；
  不把单份原生 SVG 写入配对存储，不启动或重建 worker。
- Figure 引用的约束同样覆盖 PDF-only 导出（见 `layout-versions-and-documents.md` 的引用一节）
