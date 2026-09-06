# ADR 0044：导出格式加 EPS 与 TIFF——同一个请求结构，两条不同的出图路

日期：2026-09-06 · 状态：**Accepted**
相关：[0031 统一导出管线](0031-unified-export-pipeline.md)（`ExportRequest` / 作业生命周期 /
「一个作业 = 一份快照」）、[0028 原图输出规格](0028-original-output-spec.md)（位图源的密度从哪来）、
用户反馈 2026-09-06 第 4 条「导出功能要增加 eps 和 Tiff 格式」。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 格式枚举 | `FORMATS = (pdf, png, eps, tiff)`，新格式**追加在后**；`tiff` 进 `RASTER_FORMATS`，`eps` 进 `VECTOR_FORMATS`。PPI「只在有位图格式时是数字」这句话对 tiff 成立、对 eps 不成立 |
| TIFF 由谁写 | 与 PNG **同一次栅格化**（`Canvas.save_tiff` / `pdfbackend.original_tiff`），编码器是纯标准库的 `tavotto/tiffwrite.py`（Baseline TIFF + Deflate，无损）。worker 直连那条路（MCP `tavotto_export`）由 matplotlib 经 Pillow 写，压缩同样钉成 Deflate |
| EPS 由谁写 | **只有 worker 侧的 matplotlib**（`savefig(format="eps")`，`ps.fonttype 42`）。父进程的 PyMuPDF 没有 PostScript 写入器 |
| EPS 给不出的两档 | `scope=canvas` → `eps_not_for_canvas`；`scope=original` 但注册表里没有这张图的脚本 → `eps_needs_script`。**逐项失败、其余格式照常交付（`partial`）**，磁盘上不出现一个栅格化后裹一层 PS 的冒牌矢量 |
| 一个作业一份快照 | 同一次作业里要了 EPS，PDF/PNG/TIFF 也让 worker **现画**（`rerender`），四个格式出自同一次脚本运行；不要 EPS 时行为一个字节不变 |
| 界面 | 四个格式按钮；EPS 不可用时**禁用并说原因**（`epsUnavailable.canvas_scope` / `no_script`），请求里自动不带它；只勾 EPS 又切到画布时主按钮灰。透明背景开关的标签改成「仅 PNG / TIFF」 |
| TIFF 元数据 | 分辨率标签：画布 / 矢量源栅格化 = ppi；位图源**只写源文件自己声明过的**密度（`dpi_source == "metadata"`），假定值不进用户文件，没有就写 `ResolutionUnit = 1`（没有绝对单位） |
| 遥测 | `export_completed` 的形状不变（仍只有 `pdf` / `png` 两个布尔）——加字段要动代理白名单与同意版本，这次不做 |

## 1. 管线里位图与矢量各从哪来

ADR 0031 之后，「怎么把一次导出变成磁盘上的文件」只有两条产出路：

```text
scope=canvas    pdfbackend.compose() 在 PyMuPDF 里合成一页 → save_pdf / save_png(ppi)
scope=original  矢量源：insert_pdf 整页搬运 / 按 ppi 栅格化
                位图源：装进 PDF 页 / 保源像素网格
                带 override 的图：worker 先 savefig 成中间 PDF，再走上面两条
```

父进程只有 flask + pymupdf（没有 matplotlib、没有 Pillow）。这决定了两种新格式的命：

* **TIFF 是位图**：上面每一条路的末端都有一个 `Pixmap`，把它写成 TIFF 只差一个编码器。
  PyMuPDF 的 `Pixmap.save()` 不认 TIFF，`pil_save()` 要 Pillow——所以编码器自己写
  （`tavotto/tiffwrite.py`，`struct` + `zlib`，一百多行），产物由 Pillow / libtiff /
  PyMuPDF 独立解码对拍。**TIFF 与 PNG 出自同一个 `get_pixmap` 调用序列**，像素逐个相同，
  不是"记得要一致"。
* **EPS 是 PostScript**：PyMuPDF 写不出它（MuPDF 的 `ps` 输出是分带位图，不是矢量）。
  唯一写得出真矢量 EPS 的是 worker 侧的 matplotlib——而 worker 只认识**它自己跑出来的
  Figure**。所以 EPS 只在「按原图导出一张引擎能重新运行的图」时存在。

## 2. EPS：如实说给不出，而不是伪称矢量

`exportreq.FORMATS` 上方那句注释从第一天就在：「不许在这里加一个当前管线给不出真矢量的
格式」。EPS 与它的关系是：**有一条路给得出真矢量，另外两条给不出**。被否掉的三个做法：

* **栅格化后裹一层 PS**（`Pixmap.save(..., "ps")`）：扩展名说矢量、里面是位图，期刊的
  投稿系统会收下然后印出一张糊图。这正是 §8 禁止的事。
* **PDF → EPS 外部转换**（Ghostscript / `pdftops`）：引入一个我们不打包、不锁版本、
  用户机器上未必有的依赖，两台电脑导出同一张图会得到两个不一样的文件。
* **不加 EPS**：产品所有者明确要它，且它在 matplotlib 用户的世界里是真实需求（不少
  期刊系统只收 EPS/TIFF）。

所以：`scope=canvas` 里的 EPS 逐项报 `eps_not_for_canvas`，`scope=original` 里没有脚本的
图报 `eps_needs_script`，其余格式照常交付。两个 code 进 `exportjob.ERROR_CODES` 与两份
`errors.json`（`test_error_codes.py` 看护双语与占位符）。界面在勾选那一刻就禁用了它并说原因
（`lib/exportRequest.epsAvailability()`，判据与后端 `_serialize_figure()` 的前提逐条对应：
runtime 素材放行，磁盘面板看素材清单里的 `script`），`buildExportRequest()` 在不可用时
不把它放进请求——后端那两个 code 是老客户端 / 脚本直接 POST 时的兜底。

### 2b. 「一个作业 = 一份快照」延伸到 EPS

没有 override 的图，PDF/PNG 原来取**磁盘上的产物**；EPS 却必须由 worker **现在**画。
两者之间隔着一次脚本运行：脚本改过而产物没重跑时，同一次导出会交出两个时刻的图。
处置：请求里有 EPS 时 `_resolve_panel_source(..., rerender=True)`，有脚本就重画（与带
override 的路完全相同），四个格式出自同一次运行；没有 EPS 时 `rerender` 为假，行为不变
（`test_pdf_alone_still_uses_the_disk_file_when_nothing_forces_a_rerender` 钉住反面）。

### 2c. EPS 的已知限制（写进界面，不静默）

* **不支持透明度**：matplotlib 的 PS 后端把半透明元素画成不透明（它自己会打一条日志）。
  格式按钮下的提示写着「不支持透明度」，`transparent` 开关本来就只对位图起作用。
* **文字按 Type 42 嵌入**（`figsession.export_font_context`，与 PDF 同一条 T-122 纪律）。
* **画布标注不会出现在 EPS 里**——EPS 只在 `scope=original` 存在，而那个 scope 里本来就
  没有画布对象；这不是丢内容，是这条路的定义。

## 3. TIFF：无损、带 dpi、不编造密度

* 压缩选 **Deflate（tag 8）**而不是 LZW：纯 Python 的 LZW 逐字节编一张 600 ppi 的整页要
  几十秒，Deflate 由 zlib 的 C 实现完成；所有主流读取端都认 8。worker 直连那条路由
  matplotlib 经 Pillow 写，Pillow 的缺省是**不压缩**，`figsession.TIFF_PIL_KWARGS` 钉成
  `tiff_adobe_deflate`——两条路出来的文件同一种压缩。
* 分辨率标签：画布与矢量源栅格化写 ppi；位图源**只写源文件自己声明过的**（`_declared_density`
  只认 `dpi_source == "metadata"`）。`assumed` 是我们按扩展名猜的（PNG 600 / 其余 300），
  猜的数写进用户的文件之后就成了"文件说的"，下一个读它的人分不出来。没有就写
  `ResolutionUnit = 1`（TIFF 自己表达「没有绝对单位」的写法；整组不写的话 Pillow 会按默认值
  报 1 dpi）。
* RGBA 写 `ExtraSamples = 2`（非预乘），与 PyMuPDF / PNG 的像素语义一致。
* 位图源没有「逐字节复制」这一档：源是 PNG/JPEG 时容器必换，像素网格照搬、不重采样。

内置 runtime（`packaging/runtime-lock.json`）里的 Pillow 12.3 wheel 自带 libtiff，
`tiff_adobe_deflate` 在两个平台都可用；本机 worker 解释器实测 `PIL.features.check("libtiff")`
为真。

## 4. 看护

* `tests/test_export_request.py`：枚举、顺序、PPI 规则、`.tif` 别名；golden
  `tests/golden/filename_vectors.json` 多了 eps / tif / tiff 的 strip / output_name / dedupe 向量，
  vitest 侧同一份。
* `tests/test_tiffwrite.py` + `tests/support/tiffcheck.py`：独立读取端（与写入端无共享代码）
  + Pillow 第三把尺；多 strip、行填充、未知密度、标签升序。
* `tests/test_export_pipeline.py`：画布 TIFF 与 PNG 逐像素相同、透明 alpha、矢量源按 ppi、
  位图源保网格 + 只写声明过的密度、画布 EPS 逐项失败、无脚本 EPS 逐项失败、有脚本时 EPS
  来自 worker 且 PDF 同一次重画、无 EPS 时不碰 worker。
* `tests/test_worker_roundtrip.py`：真 matplotlib 出 EPS（DSC 头、BoundingBox、Type 42）与
  TIFF（Deflate、dpi 标签、zlib 解得开）。
* `web/src/lib/exportRequest.test.ts` / `ExportDialog.test.tsx`：可用性闭集、请求不带不可用的
  EPS、只勾 EPS 切画布时按钮灰、TIFF 让分辨率行出现。
