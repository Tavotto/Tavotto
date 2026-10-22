# 导出（ADR 0031 / 0046）

> 原文出自 `src/tavotto/AGENTS.md`「布局层（R18）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
    执行回执与 generation；SVG 复用 PDF draw 已确定的布局，临时 `svg.fonttype=path` 和 Figure 状态在结束时还原。
    **自动捕获和导出要求 `require_built`**：只用已经运行并接纳的 Figure 或已验证的冻结对，冷 worker /
    过期会话 / 缺失表示不能触发脚本重跑。动态 draw 回调、自定义 draw 等无法保证配对稳定的 Figure
    不授予 SVG 资格，自动捕获在 draw 之前拒绝。**未知当前回执不是旧配对的许可**：磁盘 current marker
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
