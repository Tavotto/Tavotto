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
  * **旧契约一个字节不变**：没有 `filename` 的请求（`stem`/`dpi`，或
    `items[]`+`texts[]`）抬成同一个作业，文件名照旧带时间戳，回执照旧有
    `files[]`/`export_dir`/`warnings`，报告照旧叫 `_proof.json`。
    新路径的报告叫 `<基名>_style-check.json`（v3，`kind` 不变）。
  * 文件名规则是**严格同源对**（`web/src/lib/exportName.ts`），
    `tests/golden/filename_vectors.json` 两侧各跑一遍；**首尾空白的字符集
    写死一份**，不许退回 `str.strip()`/`String.trim()`（两者认的集合不同）。
  * **EPS 与 TIFF（ADR 0046，2026-09-06）**：`FORMATS = (pdf, png, eps, tiff)`，
    新格式追加在后。TIFF 与 PNG 出自**同一次栅格化**（`Canvas.save_tiff` /
    `pdfbackend.original_tiff`），编码器是纯标准库的 `tavotto/tiffwrite.py`
    （Deflate 无损；父进程没有 Pillow，**别为它引进 Pillow**）；位图源的分辨率
    标签只写源文件自己声明过的密度。EPS **只有 worker 的 matplotlib 写得出**
    （PyMuPDF 没有 PostScript 写入器）：`scope=canvas` 逐项报 `eps_not_for_canvas`，
    没有脚本的图报 `eps_needs_script`，其余格式照常交付；要了 EPS 时 PDF/PNG/TIFF
    也让 worker 现画（`_resolve_panel_source(rerender=True)`），四个格式出自同一次
    脚本运行。**不许**用 `Pixmap.save(…, "ps")` 之类把位图裹成 PS 冒充矢量。
    「谁来渲染」在导出路上的唯一调用点是 `_serialize_figure()`。
