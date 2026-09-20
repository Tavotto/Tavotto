# 可实现性评估与源码对应

## 总判断

方向可行，且适合利用现有 `pdfbackend` 边界进行渐进替换；价值来自更强的保真、可解释与可验收能力，而非新增 RenderCore 命名空间。来源方案关于 Render IR、字体策略和产物证据的方向值得保留，但“总计 9–12 天、多个模块各加半天”的估计没有工作分解或运行证据支撑，不应成为承诺。

本次是远程静态评审，不是运行确认。基线固定为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`，具体阅读面见 SOURCES.md。下面区分代码事实与设计判断。

## 1. 适合渐进替换的现有边界

**代码事实。** `pdfbackend/__init__.py` 已集中导出探测、预览、合成、原图导出、文字度量、coverage、字形计划、像素比较、PDF 字体检查、标注写回和后端身份。返回值基本为普通数据，Canvas 对象是重要例外。`app._export_produce_canvas()` 只建一次 Canvas；源图解析和重渲染通过回调留在应用层。[S01, S02, S03]

**设计判断。** 最稳的入口是保留门面，把 Canvas 变为稳定的录制/编译接口，再逐项替换适配器。不要先把 app.py 的每处使用改成 pikepdf/PDFium。

## 2. RenderPlan 不应该再造导出服务

**代码事实。** ExportRequest 已统一请求、scope、文件名、格式和覆盖策略；ExportJob 已统一 staging、路径预留、取消提交点、逐文件原子发布、partial 和可选报告。`run()` 在 `produce()` 返回后设置提交点，发布成功图文件后再执行报告回调。[S04, S05]

**设计判断。** RenderPlan 是 ExportRequest 与产物生成之间的冻结计划，不是第二套任务服务。强制最终产物验证必须在 staging 完成后、第一次发布前发生；不能在已有 report 回调里做检查却声称它能拦截坏产物。Manifest 如成为用户侧 sidecar，也必须进入命名/预留/覆盖流程，不能单独 `write_text`。

现有原子性是单文件发布，不是多格式全有或全无；新增证据不能篡改这一合同。

## 3. 目前有两种不同的字体世界

**代码事实。** 画布文字是 serif/sans-serif/monospace 三族与 PyMuPDF base-14、CJK、隐式 fallback；Matplotlib 图内文字由科学 worker 的 fontManager、manifest 和 overrides 处理。`glyphplan` 的 primary/cjk/fallback/missing 已经是单独策略模块，但具体字体存在性和度量仍通过后端求得。仓库的字体来源测试明确禁止分发字体与前端 @font-face。[S02, S06, S07]

**设计判断。** 抽 Typography 能解放画布文字，不能自动接管导入 PDF 中的科研文字。ImportedPage 不透明；批量统一坐标轴字体仍应由 normalize/worker 变更原图，再导入它的新产物。要实现跨平台稳定文字，必须同时确定字体文件身份、许可、子集策略与前端可见排版，不是只替换 text_length。

## 4. 原方案缺少受验证的 PDF 字体写入器

fontTools 的字体处理和 HarfBuzz 的 shaping 不会自动完成 PDF 字体资源、编码和文本语义。pikepdf 提供低层对象与内容流能力，官方把文本接口描述为基础接口，并要求调用方正确编码。[W01–W04]

**设计判断。** 这是最高风险之一，必须最早做技术验证。选择成熟写入器适配，或实现支持范围明确的 Tavotto emitter；无论哪条，必须同时通过视觉、字形、提取逻辑文本、嵌入、子集和多字体冲突测试。不能把文字转路径称为“可检索字体嵌入”。

## 5. 一个值得验证的实际收益：翻转/透明度不再强制位图化

**代码事实。** 现有后端的 `_place_panel()` 在 `opacity < 1`、`flip_h` 或 `flip_v` 时选择 bitmap 路径，普通无翻转 PDF 则使用 show_pdf_page。[S02]

**设计判断。** Form XObject、坐标变换和正确的透明组可以作为新实现目标，保留这些操作下的源矢量内容。这不是已完成的优化；必须以多层重叠、clip、旋转、镜像和组透明度用例证实，不能只看截图。

**待复现的现有问题。** `_draw_shape()` 中 `float(o.get("fill_opacity") or 1.0)` 会把显式 0 当成 1。应先补最小反例并确认调用端语义，再作为有记录的修复，不能把旧输出自动当正确基线。

## 6. Publication Proof 目前还缺哪些证据

**代码事实。** `engine/artifactcheck.py` 对 PDF 主要读取物理尺寸和第一页字体名称；TIFF 的密度标签目前明确标为未核验。`pdf_fonts()` 读字体资源名字，不等于已核验字形实际使用、字体嵌入或子集。画布导出的 Produced 对 PDF 直接填 `vector=True`。现有 style-check 报告包含前端上传的检查部分。[S02, S03, S08]

**设计判断。** 新 Manifest 必须区分计划声明与最终文件事实。新的 inspector 可增加真实使用的字体资源、嵌入状态、文字/路径/位图混合情况、TIFF 元数据和支持范围内的有效 DPI。任意导入 PDF 的全部裁切、全部字号、色彩标准不能无条件判“通过”。图像输出为 600 DPI 不等于它内部原本 150 DPI 的照片具有 600 DPI 信息。

## 7. 并发和桌面发行是实质工作，不是收尾

**代码事实。** PDFium 官方说明即使不同文档也不能被多个线程同时调用。Tavotto 的导出有后台线程，预览也可能并发；PyInstaller 当前将 PIL 排除，科学栈在独立解释器内。支持矩阵承诺 Python 3.10–3.14，桌面主发 Windows x64 与 macOS arm64。[W05, S05, S09, S10]

**设计判断。** 所有 PDFium 调用必须纳入同一进程/互斥策略。推荐应用管理的 render subprocess，不能混用用户科学 worker。打包必须验证 native library、显式资源、离线字体、冻结进程启动、签名公证后 dylib、Windows 文件锁和取消收尾。当前 pikepdf 官方 wheel 表有 macOS 最低版本门槛，选版本时必须与产品支持范围一起核验，不能默认最新版本全平台无代价。[W06]

## 8. CI 应扩展现有体系，不该推倒重来

**代码事实。** 当前工作流已有普通 PR 快线、full-ci PR/merge_group 重型资格、main 落地审计、nightly/lab 深测和 release；聚合器把必需 job 缺失、额外、skip、cancel、未知状态显式判定。CompatBench 已区分人工意图、版本矩阵与观测基线；发布门禁不接受 product_bug。像素比较器当前有意转换为灰度。多项几何测试直接依赖 PyMuPDF 的 get_drawings/get_text。[S11–S15]

**设计判断。** 本次应该增加 RenderCore 的独立语义检查、RGBA/alpha 反例、无 PyMuPDF 干净安装资格以及真实的性能对照。不能删掉旧测试后用“能打开 PDF”顶替；也不能只看灰度忽略等亮度异色和透明通道。

## 9. 格式范围不能偷换

**代码事实。** FORMATS 为 PDF/PNG/EPS/TIFF；ENGINE_FORMATS 额外含 SVG。EPS 只有原图且可由 worker 序列化时支持；TIFF 与 PNG 同栅格源；原图 PNG 源可逐字节复制；原图位图不重采样；源 PDF 只取第一页。[S02, S04, S16]

**设计判断。** 这些路径全部属于“完整替换”的验收范围。但通用画布 SVG/EPS 不是当前能力，不能因新架构图画了多个箭头就默认已经实现。E02 提供有限 SVG 扩展，透明度和不可解析 ImportedPage 必须能力驱动地拒绝或明确降级。

## 10. 法务结论要比原方案更准确

pikepdf 许可是 MPL-2.0，不能拿底层 qpdf 的 Apache-2.0 代替它。Mozilla 官方 FAQ 说明 MPL 是文件级 copyleft，允许与专有代码组合，但存在源码可获取和通知等义务。新 stack 的 wheel、native 库和字体需要各自的 license/notice/SBOM；Tavotto 自身当前仍是 AGPL-3.0-only。[S17, W07, S18]

因此，这是“消除 PyMuPDF 依赖并建立可核验合规分发”的路线，不是“改完依赖立即自动获得闭源权”的法律结论。

## 可衡量的价值完成线

1. 所有受支持运行和发行路径不需要 PyMuPDF，干净环境真实导出通过。
2. 选定 PDF fixture 的镜像/透明组仍有真实矢量与可检索文字，不被静默整页栅格化。
3. 画布文字的字体文件、fallback、度量、子集与导出证据不再由 PDF 库隐式决定。
4. 验证器能够把嵌入字体被删、错误页面尺寸、错误密度、受支持范围内的错误 clip 等故障判出来。
5. PDF/PNG/TIFF 同源、取消/partial/写回合同无回归。
6. 可复现报告明确区分相同输入、相同排版、相同像素和相同字节。
7. Pro 的批量规范化可以复用冻结计划与证据，不需要再造第二个任务/规范系统。
8. 性能、包体和内存用实测报告比较；没有测量时不宣称更快、更轻或更省。


---

# 来源与阅读范围

日期：2026-09-15。除特别说明，源码固定到 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。远程阅读，不是本地测试结果。

## 用户提供的方案

`粘贴的 markdown (1)。md`，900 行。重点：IR 43–143；Typography 147–255；Capabilities 259–341；Manifest/Proof 348–419；RenderPlan/批量 423–517；确定性 523–585；IDs 589–647；Trace 651–702；明确排除项 706–753；工期估算 824–850。原方案的工期和无条件 proof 不是本实施包认可的事实。

## 源码

- **S01** `src/tavotto/pdfbackend/__init__.py`：完整门面。
- **S02** `src/tavotto/pdfbackend/pymupdf_backend.py`：主要实现分段；字体、面板、shape、Canvas、original、annotate；部分长响应截断，非逐行完整覆盖。
- **S03** `src/tavotto/app.py`：950–1450；另阅读 2350–2550、4500–4670；非完整 app。
- **S04** `src/tavotto/engine/exportreq.py`：1–170，另有搜索定位。
- **S05** `src/tavotto/engine/exportjob.py`：1–220、330–700。
- **S06** `src/tavotto/AGENTS.md`：关键规则；长响应部分截断。
- **S07** `tests/test_font_provenance.py`：完整。
- **S08** `src/tavotto/engine/artifactcheck.py`：1–220。
- **S09** `packaging/tavotto.spec`：1–160。
- **S10** `docs/support-matrix.json`：完整。
- **S11** `.github/workflows/ci.yml`：1–220；长响应部分截断。
- **S12** `scripts/ci/aggregate_gate.py`：1–210。
- **S13** `scripts/ci/compat_corpus.py`：1–120。
- **S14** `scripts/ci/pixelcompare.py`：1–130。
- **S15** `tests/test_compose_annotations.py`：代码搜索片段；关联 arrow、switched_shapes、annotate、MCP、runtime_asset 测试也为搜索定位。
- **S16** `docs/adr/0046-eps-and-tiff-export-formats.md`：完整。
- **S17** `pikepdf/pikepdf:LICENSE.txt`：第三方主仓库完整许可；未固定其 main commit。
- **S18** `pyproject.toml`：1–185。
- **S19** `src/tavotto/engine/normalize.py`：1–180。
- **S20** `docs/ci/plugin-stable-channel.md`：搜索片段；并检索 ADR0043、brand.py、AGENTS 发行规则。
- **S21** `codex-plugin/mcp/tavotto_mcp/bridge.py`：搜索定位；normalize/导出相关调用，不是全文。
- **S22** `scripts/make_plugin_manifest.py`：搜索定位 minimum-version/bridge imports。

源码定位格式：`https://github.com/Tavotto/Tavotto/blob/6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e/<path>`。以上工具阅读没有验证仓库线上 ruleset 的实时配置，也没有启动 CI。

## 外部官方资料

- **W01** pikepdf Canvas / text API：`https://pikepdf.readthedocs.io/en/latest/api/canvas.html`。
- **W02** pikepdf content streams / extraction boundaries：`https://pikepdf.readthedocs.io/en/latest/topics/content_streams.html`。
- **W03** HarfBuzz responsibilities and exclusions：`https://harfbuzz.github.io/what-harfbuzz-doesnt-do.html`。
- **W04** fontTools subsetting：`https://fonttools.readthedocs.io/en/latest/subset/index.html`。
- **W05** pypdfium2 threading / memory / API versions：`https://pypdfium2.readthedocs.io/en/stable/python_api.html`。
- **W06** pikepdf supported wheels：`https://pikepdf.readthedocs.io/en/latest/installation.html`。
- **W07** Mozilla MPL 2.0 FAQ：`https://www.mozilla.org/en-US/MPL/2.0/FAQ/`。
- **W08** pypdfium2 introduction / distribution：`https://pypdfium2.readthedocs.io/en/stable/readme.html`。

这些网页用于核实组件职责、线程限制、许可和发行约束，不能证明某个候选组合已经在 Tavotto 里跑通。使用时应再次核实所锁定版本的对应文档与实际 wheel。
