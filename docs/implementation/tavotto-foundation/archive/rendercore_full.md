# Tavotto RenderCore 完整实施提示词 · 合并版

参考源码：`Tavotto/Tavotto@6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。审计日期：2026-09-15。

这是一份实施任务说明，不是已实现的代码或已通过的测试结果。把全局约束与当前阶段一起交给编码 Agent；按阶段保留证据与交接，不必一次会话执行全部阶段。源码已更新时先 R00 差异审计。仓库审计与来源索引见同包 01_REPOSITORY_AUDIT.md、SOURCES.md。

## 章节导航

- 00_MASTER_PROMPT.md
- phases/R00_inventory_baseline.md
- phases/R01_feasibility_spikes.md
- phases/R02_ir_geometry_capabilities.md
- phases/R03_render_plan_snapshot.md
- phases/R04_font_registry_coverage.md
- phases/R05_typography_layout.md
- phases/R06_pdf_text_emitter.md
- phases/R07_pdf_compositor.md
- phases/R08_pdfium_raster_runtime.md
- phases/R09_original_annotation.md
- phases/R10_artifact_validation.md
- phases/R11_manifest_fingerprint_trace.md
- phases/R12_integration_http_mcp_ui.md
- phases/R13_ci_oracles_renderbench.md
- phases/R14_distribution_native.md
- phases/R15_cutover_retire.md
- phases/R16_final_qualification.md
- extensions/E01_manuscript_compiler.md
- extensions/E02_scoped_svg_backend.md
- 03_CI_BLUEPRINT.md
- 02_ACCEPTANCE_MATRIX.md
- 04_HANDOFF_TEMPLATE.md
- 05_GO_NO_GO.md

---

<!-- 原文件：00_MASTER_PROMPT.md -->

# 总提示词：实际实现 Tavotto RenderCore，并用证据完成 PyMuPDF 退役

你在 Tavotto 仓库内工作。任务不是讨论可行性，不是创建空目录，不是只加 Protocol，也不是把 `pymupdf` 改名为另一个依赖。你要交付可运行、可打包、可回归验证的新渲染基础设施，保持现有科研编辑行为，并证明新增价值。

请始终结合当前阶段 `phases/Rxx_*.md` 执行。先做 R00，再按依赖推进；不要把十几个阶段塞进一次不可审查的提交。完成某阶段后，运行它的测试，记录证据和交接，再继续可执行的后续阶段。遇到真实阻塞时保持可运行状态，准确说明失败，不得用空实现、吞异常或回退旧依赖来假装完成。

## 一、先确认的事实与范围

参考审计基线是 `Tavotto/Tavotto@6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。运行 `git rev-parse HEAD`、`git status --short`，阅读根目录和目标目录的 AGENTS/CLAUDE 规则；若 HEAD 不同，先作差异审计。不得覆盖用户未提交改动，不得自动 checkout/reset，不得 push、合并、发版或修改分支保护。

已有权威层必须复用：

1. `engine/exportreq.py` 定义 ExportRequest、scope、格式、命名、PPI、覆盖策略；不得新造等价请求定义。
2. `engine/exportjob.py` 负责作业生命周期、路径预留、取消提交点、逐产物发布、partial 和报告失败；不要另建 PublicationJob 服务复制它。
3. `engine/originalspec.py` 是原图物理尺寸/密度来源；不得在渲染器加第三套默认 DPI。
4. `engine/normalize.py`、`interference.py`、`preflight.py`、`profiles.py` 保留科研语义、保留式规范化和规则权威。
5. `pdfbackend/__init__.py` 是兼容门面；现有 Canvas 方法和普通 Python 返回值要有契约测试。
6. Matplotlib worker / native bridge 与应用运行时是不同环境；不要向用户科研环境偷装 PDFium、pikepdf 或应用私有字体包来弥补设计漏洞。
7. CI 继续以 `CI fast gate`、`CI integration gate`、`CodeQL gate` 为稳定聚合接口，适配既有闭集判定，不另开绕过路径。
8. `plugin-stable` 是既有机器维护发行通道；不要把生成的 `codex-plugin/mcp/widget/canvas.html` 重新提交进源码分支。

核心交付必须包括本方案的八项基础设施，而不只是后端替换：Render IR、Typography、Capabilities、Artifact Manifest、RenderPlan、Fingerprint、Semantic IDs、Trace。Publication Proof 是基于已观察事实的规则结果，不是宣传性“通过”字样。

## 二、目标分层

```text
现有 Document / ExportRequest / PublicationSpec
           │
           ├─ 现有 normalize + worker：修改图内科研语义、生成新源图
           │
           ▼
应用侧 SourceResolver：作业私有、冻结的源文件/字体/规范/override 快照
           ▼
RenderPlan：记录目标、授权、来源、输出和验证要求，不复制 ExportJob
           ▼
Render IR：物理页面、Path、Image、ImportedPage、Group、ShapedText
           ▼
Tavotto PDF emitter + pikepdf/QPDF：受限、测试充分的 PDF 合成
           ▼
最终 canonical PDF bytes
           ├─ PDF 成品
           └─ PDFium → canonical RasterBuffer → PNG / TIFF
           ▼
独立 ArtifactInspector → observed facts → profile evaluator
           ▼
Manifest / Proof / Trace / Fingerprint
           ▼
原有 ExportJob 发布和回执
```

原图源 PNG 的逐字节复制、原生像素网格等路径不可为了“统一 IR”被强制 PDF round-trip。现有 Matplotlib 原图 SVG/EPS 仍走科学 worker 的直接序列化。IR 中的 ImportedPage 是不透明源页，不代表里面的 axes/title/legend 已被转换成可编辑 IR。

PDFium、pikepdf、fontTools、HarfBuzz/uharfbuzz、Pillow 是候选依赖，不是未经验证的版本处方。R01 必须验证具体 wheel、许可证、字体写入器和冻结包。pikepdf 是 MPL-2.0，不能写成全栈 MIT/Apache；本仓库自身仍然是 AGPL-3.0-only，技术替换不自动改变既有授权。

## 三、强制保持的行为

- `original` 与 `canvas` 两种尺寸语义隔离。原图导出不应用画布摆放、缩放、裁剪和旋转。
- 页面仍默认只使用源 PDF 第 1 页；不要顺手变成多页导出。
- 画布 PDF/PNG/TIFF 出自同一份已冻结语义状态；PNG/TIFF 同参数下解码像素一致。PDF-only 请求不强制重跑无 override 的脚本。
- 同图的不同实例和不同作业不能共用可被后续导出覆盖的中间文件；资产身份与实例身份分开。
- 缺失源文件、worker 失败和未经授权的变更必须可见；不能拿过期 materialized cache 补成“成功”。
- 保持 hidden、顺序、箭头旧字段、shape-line 旧端点、富文本上下标/自动解释、换行、内边距、行距、基线、边框等兼容语义。
- EPS 不可用逐项失败，其余格式正常交付；没有可兑现的矢量路径就不得新增格式按钮。
- 普通导出是逐文件原子发布并允许 partial，不得宣传整批文件具有数据库式原子事务。
- 写回原件继续走原有验证、冲突检测和临时文件事务；不要直接覆盖原件尝试 pikepdf 保存。
- BackendCapabilities 与 Proof 必须基于实际路径，不允许以格式名、请求 DPI、IR 中字体名或前端传来的通过数作为最终证据。

## 四、IR / Plan / Typography 契约

IR/Plan/Manifest 的模型、规范化、哈希、能力判定、trace 模型应尽可能是纯标准库、无 I/O、可序列化的数据。不得含 PDFium/pikepdf/Pillow 原生对象、进程句柄或 Flask 上下文。注册器可执行 I/O，但必须与纯模型分层。

明确单位、坐标、transform 顺序、clip space、alpha 语义、stroke、fill rule、paint order。建议内部统一 pt、页面左上角为原点且 y 向下，在 PDF 出口只转换一次；不得把字符串 `150mm` 当成类型安全。输出数值拒绝 NaN/Inf，保留足够精度，不在每一层重复取整。

每个节点有稳定 `node_id`，并能关联 `source_id`、实例、源 revision/manifest hash 和可用的 gid。未知来源不伪造 axes ID。内部语义 ID 不得成为执行脚本、任意读文件或自动“反向编辑 PDF”的授权。

Typeface 身份至少包含字体文件哈希、face index、variation coordinates、实际 style、来源和嵌入/子集政策。不要按 family 名或 codepoint 单独缓存字形归属。

Typography 先产生稳定排版结果，再由后端消费：run 的字体、glyph IDs、cluster、Unicode 逻辑文本、advance/offset、基线、line boxes、ink bounds、script/direction/language/features 都有明确定义。HarfBuzz 不负责完整的 bidi、换行或 fallback，不能只调用 shape 就宣称全 Unicode 支持。

Matplotlib 图内字体仍由现有科学引擎解析和渲染；本轮不替换 Matplotlib 的完整文字排版系统。必须在 manifest 中区分 canvas typography 与 imported/worker typography。

## 五、真正的 PDF 文字写入器

必须交付经过测试的 ShapedText→PDF 映射，不能用 `draw_text(str)` 重新排版，也不能把全部文字转路径或位图来宣称“字体嵌入完成”。在 R01 实测成熟写入器与受限自有 emitter，选择后在 R06 完成。

覆盖字体资源、编码、Type0/CIDFont 的合适组织、TTF/CFF 的能力边界、字宽、子集 glyph 重映射、CID 与实际 glyph 对应、ToUnicode、必要的 cluster/ActualText、旋转和偏移。至少让默认已获授权字体集合上的中英混排、希腊字母、数学符号、富文本和可支持的组合字符达到稳定、可检索、可嵌入。

复杂字体类型/脚本未经实现和测试必须明示 unsupported/degraded；但不得因此静默降低现有必需样例的能力。不得把任意 glyph ID 直接当成 Unicode。多个 font 资源名称相同也不能按名称错误去重。

## 六、字体政策与预览

现有 `tests/test_font_provenance.py` 禁止仓库字体和 @font-face。独立 Typography 的字体来源必须以 ADR 正面调整，而不是删掉测试绕过。默认策略采用经许可核验、固定哈希、离线可用的字体资源集合，或明确声明环境依赖的系统字体模式；两者不能混称“跨平台确定”。

不从 MuPDF 的资源里抽取字体来规避其许可，不下载/重分发 Times New Roman 等专有字体。字体 EULA、再分发权、嵌入/子集许可和字体技术标志分别记录；技术标志不代替法律许可。

浏览器用另一个系统字体测宽就不能宣称 WYSIWYG。为正式编辑器提供同源的测量/排版与可见预览结果，可采用受许可约束的本地资源或服务器生成的矢量文字预览；若选择后一种，编辑交互、可访问逻辑文本与异步结果修订号必须保留。MCP、桌面、浏览器构建和独立 Playground 的能力边界分别测试，不把 C++/PDFium 直接塞给 Pyodide。

## 七、原生库运行隔离

PDFium 不允许多线程同时调用，即使文件不同。全部调用点包括 probe、preview、compare 相关读取、artifact inspect 都必须受同一生命周期策略管理。

生产建议使用应用管理的渲染子进程：每个进程串行使用 PDFium，多个进程按内存预算并行。它不是用户的科学 worker；冻结程序不得以 `sys.executable -m ...` 意外重启 GUI。R01 必须证明 Windows spawn 和冻结 sidecar 的启动方式。短期全局互斥只可作为明确的过渡模式，不得伪称并发隔离完成。

建立超时、内存/像素上限、排队背压、取消、worker 重启、临时目录收尾、显式 close、无 native object 跨进程的契约。进程隔离不等于完整安全沙箱，不得夸大安全承诺。

## 八、Manifest / Proof / Fingerprint 的真实性

每条事实明确 `expected`、`observed`、`status`、`method`、`scope`、`evidence` 与 `limitations`。状态至少区分 pass/fail/unknown/not_applicable，mandatory unknown 不自动变为 ready。

分别记录：页面物理尺寸；可观察的文字/路径/栅格混合情况；实际使用而不是仅声明的字体及嵌入/子集状态；输出 DPI 与每个位图的有效 DPI；支持范围内的裁切/文字几何；颜色/透明度降级。未遍历的 PDF 结构就是未知，不是“未发现问题所以通过”。

验收必须重新打开已完成的 staging 文件，用最终交付的同一字节内容验证；验证后到发布不得再改 PDF 内容。已有报告回调在发布后执行，不能依赖它来阻止不合格产物发布。强制产物验证应在提交点之前完成，再由原 ExportJob 发布；可选报告失败维持 partial，不拖垮成功图文件。

区分四个东西：语义/计划 hash、包含依赖/字体指纹的 render fingerprint、最终 artifact SHA-256、一次作业的 run ID。输入 hash 不证明输出已确定；用重放实验分别验证 semantic、visual、byte 级可复现性。跨平台字节级一致不是默认承诺。

避免哈希自引用：PDF 内嵌 render/source 身份后完成文件；再计算 artifact hash；外部 manifest 绑定该 hash。不要把文件自己的最终 hash 再写进同一 PDF 形成循环。日志时间和个人绝对路径不进入可公开证据或内容身份。

## 九、测试与发行纪律

读 `03_CI_BLUEPRINT.md`。迁移现有 PyMuPDF 测试时保留原意，不允许删几何断言只保留“文件存在”。实现独立检查器与解析/渲染双重验证；生产 emitter 的几何 helper 不能同时充当检验它的唯一 oracle。

旧后端只在迁移期间的隔离基线环境/历史样例中使用；正式依赖、默认测试套件与发行运行时最终不依赖 PyMuPDF/fitz/MuPDF。不改写 git 历史。遗留基线的字体/示例许可另行核验，不能因为“测试使用”就随意分发。

不得自动更新 golden 来掩盖变化；必须提交差异解释、规则/字体变化原因与反例。旧实现发现明确缺陷时修正契约和独立证据，而不是把旧 bug 当作真值。

安装包必须离开源码目录、清空 PYTHONPATH、在没有开发环境和 PyMuPDF 的机器上运行真实导出。检查 wheel/sdist、Windows sidecar/NSIS、签名公证后的 macOS app、CLI、MCP 和插件候选。Pillow 的新用途会影响 `packaging/tavotto.spec` 当前的 `PIL` 排除项，必须同步处理，不得只在开发机 pip install。

## 十、完成与交接

每阶段产出实际代码、测试、必要文档、机器可读 evidence 和 handoff。任何 `not_run` 都不能标记为通过。结果包含当前 SHA、依赖与字体身份、命令、退出码、日志/产物路径、已知限制、下一阶段。

如某平台在当前环境不能运行，写 `not_run` 并让相应 CI 执行，不能写“预计通过”。到 R16 时仍有必需项未验证就不宣布发行资格通过，但其他已完成工作照实交付。

不要在当前任务中构建收费系统、任意 PDF 逆向编辑器、第三方插件系统、完整 PDF parser 或自行发明 shaping。E01/E02 可在核心完成后继续，但不能用扩展功能掩盖核心缺口。


---

<!-- 原文件：phases/R00_inventory_baseline.md -->

# R00 · 建立完整迁移清单与可复现基线

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** 无；这是所有阶段的起点。

**必须定位阅读：** 根 AGENTS/CLAUDE、`src/tavotto/AGENTS.md`、`web/AGENTS.md`、`codex-plugin/AGENTS.md`；`pdfbackend` 全模块及其所有调用方；`app.py` 的导出/预览/写回；`engine/exportreq.py`、`exportjob.py`、`originalspec.py`、`normalize.py`、`artifactcheck.py`；现有 CI、打包与测试。

## 目标

在改默认后端之前，知道需要替换的每个能力、每条入口和每个现有测试的真实含义。输出能驱动后续验收的清单，而不是只有一份自然语言计划。

## 实际任务

1. 记录当前 SHA、工作区状态、Python/Node/包管理器与现有锁文件。使用 `git ls-files` 与 `rg` 搜索：`pymupdf`、`fitz`、`MuPDF`、`pdfbackend`、`Pixmap`、`get_drawings`、`get_text`、`canvas_coverage`、`BACKEND_VERSION`、`PIL`。进一步检查动态导入、运行时安装列表、打包 hooks、NOTICE、测试工具和发行脚本；不要把文档提及与运行依赖混在一起。
2. 为门面每个导出项建立 ledger：调用方、输入输出与异常、涉及 scope/格式、旧后端所做工作、替代职责、已有测试、尚缺的反例、最终移除步骤。Canvas 补充 `size_pt`、context manager、close 和多次保存的行为。
3. 将用户方案八项基础设施映射到真实代码接缝。明确 `normalize.plan_patches()` 与 RenderPlan 的区别；将 HTTP、同步/异步 export、MCP 直出、native runtime asset、标注写回、原图 PNG byte copy、EPS/SVG 分别列项。
4. 新建 `docs/implementation/rendercore/MIGRATION_LEDGER.md` 与机器可读清单（可以从本包 acceptance_matrix.json 导入，但必须用当前路径/测试更新）。为 `not_run`、已运行失败、遗留缺陷、范围边界设置不同状态。
5. 建立小型合法测试语料意图清单和基线运行器。优先复用 CompatBench 的 manifest/matrix/baseline 分离、分类和报告；如需新 runner，放入 `scripts/ci/`，先实现参数解析/schema/空用例拒绝/结果身份验证的测试。不得产生永远 exit 0 的占位脚本。
6. 在旧实现上运行相关现有测试；至少定位 `test_compose_*`、`test_export_*`、`test_annotate_asset.py`、`test_glyph*`、`test_typography*`、`test_font_provenance.py`、`test_normalize.py`、`test_mcp_roundtrip.py`、`test_runtime_build.py`、`test_support_matrix.py`。名称以当前文件为准，不得声称不存在的测试已跑。
7. 记录小语料的几何/文字/资源/像素/性能原始数据。基线文件要有 SHA、字体/后端版本、输入哈希、fixture 来源与许可。没有运行环境就保留 not_run，不能手填一份通过基线。

## 小语料必须覆盖

- 空白画布与每种 annotation、富文本、显式 opacity=0、旋转文字。
- 同源两个实例不同 override、crop、90/180/270 度旋转和双向 flip。
- 半透明重叠形状与面板整体透明。
- 非零 MediaBox/CropBox、源页 /Rotate、/UserUnit、第一页选择。
- 中英、Greek、数学符号、组合字符、缺字、fallback、bold/italic。
- 原图 PNG 原字节、JPEG 转码不重采样、未知密度 TIFF、canvas PNG/TIFF alpha。
- EPS partial、runtime asset 缺 worker、报告失败、取消、同名并发和写回冲突。

## 完成条件

清单不能仅统计 import 次数：每个 facade API 都有迁移目标和测试；八项基础设施有实际落点；首次 baseline 的运行与未运行严格区分；没有改变用户出图默认行为。阶段末给出 R01 的最小技术 spike 输入与成功判据。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R01_feasibility_spikes.md -->

# R01 · 先验证最危险的技术选择、字体政策和冻结发行

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R00 清单与基线。

**必须定位阅读：** `pyproject.toml`、`packaging/tavotto.spec`、`packaging/runtime-lock.json`、`docs/support-matrix.json`、字体来源测试、pikepdf/PDFium/HarfBuzz/fontTools 对应候选版本官方文档。

## 目标

用真实产物证明组合技术路线能覆盖核心行为，再确定生产接口。不要一上来抽象 20 个类后才发现中文字体或桌面包无法工作。

## Spike A：真实 PDF 合成

用 pikepdf/QPDF 导入一份自生成 PDF 为 Form XObject，加 Tavotto annotation；验证 CropBox/MediaBox 偏移、/Rotate、/UserUnit、clip、缩放、镜像、z-order 和 opacity。整体透明必须在存在内部重叠的源页上测试，确认不是给每条绘图命令分别乘 alpha。输出仍含源矢量/文字，不能整体转 Image XObject。

## Spike B：真实字体写入

以可合法使用的默认字体候选做中英混排、希腊符号、上下标、组合字符、连字和不同字体同名资源。先拿到 shaped glyph positions，再写 PDF，独立渲染和提取文字，检查 font program/子集/字宽/ToUnicode。

比较两种路径：有明确 glyph-position/embedding 支持的成熟写入器适配，或由 Tavotto 提供受限字体 emitter、pikepdf 负责对象/流管理。不能选一个只会 drawString 的接口然后重新排版；不能以轮廓路径或图片替代可检索文字。选定一种生产方案，并记录另一种被否决的实测原因。默认字体及现有 must 字符集合必须可画；CFF、variable、color font 等范围另行列出，不做无条件承诺。

## Spike C：渲染和进程

用 pypdfium2 打开 Spike A/B 最终 PDF，生成 RGB/RGBA；验证 stride、通道顺序、alpha 预乘语义和显式 close。构建应用管理的最小 render child，验证线程中并发提交不会变成进程内并发 PDFium 调用。验证 timeout、坏 PDF、child 崩溃后可恢复。此 child 不使用用户 project .venv。

## Spike D：最小冻结包

在受支持桌面 OS 上，以候选锁版本构建最小 PyInstaller/sidecar；从中文+空格路径、无开发环境、无 PyMuPDF 的条件运行 A/B/C。核实 native libraries、DLL/dylib 搜索路径、PIL 排除项和 spawn 启动。当前环境缺某平台时生成明确的 CI job，结果在该 job 真正执行前仍是 not_run，不视为 Gate A 已过。

## 依赖与字体政策

记录每个 Python wheel、底层 native 库、字体资源的版本/哈希/平台标签/许可证/NOTICE/源码获取路径。核对 Python 3.10–3.14 和产品 OS 最低版本，不能把最新文档的 wheel 表直接当自己的锁文件验证结果。pip 安装不得静默退成客户机器源码编译。

pikepdf 是 MPL-2.0，qpdf 的许可证不能代表整个组合。仓库自身 AGPL 授权不在此阶段擅自更改。字体采用离线、固定身份、来源可审核的资源策略；不得抽取 MuPDF 私有资源绕开来源，不下载专有字体。修改“绝不分发字体”的旧政策必须有明确 ADR、资源 allowlist 和对应强化测试，而不是删除 provenance 测试。

## 交付

在当前可用的 ADR 编号下写：技术栈选择、字体政策、文字发射方案、进程边界、各格式支持矩阵和风险。保留小型可重复 spike 作为后续回归测试。给出 Gate A 结果：每个指标 pass/fail/not_run、命令、产物。Gate A 未成立时不进入默认迁移；先修复或调整候选，不能靠缩减现有 must 范围骗过门禁。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R02_ir_geometry_capabilities.md -->

# R02 · 实现纯 Render IR、坐标规则和能力模型

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R01 核心技术方案已确定；可并行做纯模型，但默认切换仍被 Gate A 阻挡。

**必须定位阅读：** 后端 `_draw_*`、`_place_panel`，前端 `shapeGeometry.ts`、document 类型、TextView/ShapeView/ArrowView，以及源方案节点模型。

## 实际实现

新建或按当前目录组织 `rendercore/model.py`、`geometry.py`、`capabilities.py`、`errors.py` 与 serialization/schema 模块。保持纯标准库，不导入 Flask/Matplotlib/PDFium/pikepdf/Pillow。

实现 Page、Group、Path、Image、ImportedPage、ShapedText/GlyphRun 与资源引用。Arrow/Brace/Shape 是语义角色或编译成 Path，不必为了目录美观给每种图形创建 backend 插件。节点不可变；Page 长宽单位 pt，Tavotto 内部 y 向下，PDF 出口统一翻转；文字 glyph 空间与页面空间转换单独明确。

必须定义：仿射矩阵相乘顺序、局部/页面 clip、中心旋转、镜像、stroke 和 dash、round cap/join、fill rule、group opacity 与 fill/stroke opacity 区别、paint order 与 hidden、路径关闭。禁止矩阵链多处重复应用，禁止用 z-order 排序破坏相同层级的稳定顺序。

ImportedPage 保存源 asset 身份、页码、源可见页盒与页面规范化信息；不是解析后的可编辑图表。Image 保存像素身份和色彩/alpha 信息；不允许靠路径扩展名推断是矢量。

为所有节点保留稳定 node_id 和可选语义来源。重复使用同资产的两个实例必须是两个 node_id。IR 内无绝对路径、原生对象或可执行 callback；资源解析在外部受控层。

## 能力模型

不要只有 `supports_pdf=True`。按 operation/format/scope 评估，至少返回：`native`、`rasterized`、`unsupported`、原因码与限制。加入能力版本/适用条件；只在已实现并测试后声明 native。Policy 明确是否允许局部栅格降级；用户没同意就不得默默整页 flatten。

未知节点、非法矩阵、负尺寸、NaN/Inf、资源引用缺失、unsupported 字体与格式用稳定结构化错误。错误 code 同步现有双语 error-code 看护机制，不在每个 backend 自己创造不同消息协议。

## 验收

- mm↔pt 边界仅转一次；手算矩阵 fixture 覆盖嵌套 transform、负坐标、反射和裁剪空间。
- 序列化 round-trip、canonical ordering、schema version、未知字段政策和重复 ID 测试。
- 子节点顺序改变影响身份；不影响渲染的 run ID/绝对路径不影响内容身份。
- 禁止 native imports 的 AST/运行时测试；无第三方库也能读模型并做能力判定。
- 能力 negative test：未实现的 canvas EPS / opaque PDF→SVG / unsupported blend 不得宣称 native。
- 本阶段不要求在用户 UI 增加分组工具、格式按钮或新文档格式。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R03_render_plan_snapshot.md -->

# R03 · 冻结源资产并将现有导出编译为 RenderPlan/IR

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R02；R00 已确认所有源解析和导出入口。

**必须定位阅读：** `app._resolve_panel_source`、`_serialize_figure`、`_panel_render_target`、`_export_produce_*`；ExportRequest/ExportJob；normalize 与 originalspec；项目上下文绑定和 runtime assets。

## 目标

让一个作业引用的源内容、字体、override、规则与渲染选项被冻结，而不是每次保存格式时重新读取“当前状态”。不要复制现有导出服务和原图尺寸规则。

## 实现

实现应用侧 source resolver 与纯 `RenderPlan` / compile 模块。RenderPlan 引用规范化的 ExportRequest/PublicationSpec，记录目标、授权、normalized operations、resolved sources、输出能力、验证要求和环境身份。不要重新定义 filename/overwrite/PPI 默认值。

在 staging 内获取作业私有源文件并计算实际内容哈希。磁盘路径相同不是内容相同；源文件在读/复制期间被修改时需检测并按明确政策重新冻结或报 source_changed，不能交付混合版本。源 asset hash 与两个不同实例的 override/instance ID 分开。读取 project 上下文必须固定到作业发起时的项目，不受 UI 切换影响。

科学 worker 的输出用每作业、每实例私有目标文件；同源两个不同 override 不相互覆盖。请求 EPS 时延续现有跨格式同一科学状态要求，核验 worker export 是否会发生不同状态/脚本重跑；需要时使用既有 session/capture 机制固定状态，而不是开一套新的科学运行器。

`Canvas.place()` 可以变为录制接口，先编译后绘制；在正式 cutover 前保留受控的旧适配路径做对照，但不靠进程级可变全局 backend 影响正在运行的作业。未知种类不静默忽略。

原图路径不是强制 composition：原图 PNG byte copy、native raster grid、源 PDF 第1页 copy、worker direct SVG/EPS 作为明确 plan operation 保留。普通无 override 的 PDF-only 请求不能因“新计划”总是重跑脚本。

## 验收

- 同一作业多格式引用同一冻结源；两个标签页不同 override 无串图。
- 同源不同实例和相同文件名不同内容正确区分；同内容可安全复用不可变资源。
- 冻结过程中修改/删除源文件、切换项目、取消、worker 崩溃都有可预期结果。
- RenderPlan 不改变源脚本、不自动修复布局；已有 normalize 的授权和保护属性不可扩大。
- SourceResolver 错误不触发旧缓存“兜底成功”。
- 原图/canvas/EPS/no-script/runtime 各自路径有端到端 smoke；至少保持现有相关测试的契约。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R04_font_registry_coverage.md -->

# R04 · 实现字体注册、来源政策、coverage 与 fallback 身份

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R01 字体来源已裁决；R02 模型可用。

**必须定位阅读：** `glyphplan.py`、后端 text_plan/missing_glyphs/coverage_ranges/_FONT_CACHE/_LAYER_CACHE、gen_canvas_coverage、gen_glyph_plan_vectors、四份 Vite 的 @glyphcoverage、font provenance 测试。

## 实现

建立 `typography` 的 registry、coverage、fallback、resource policy 与字体身份模型。注册器区分批准的离线字体资源和用户系统字体。Face 身份基于实际字体文件哈希、collection face index、style/variation，而不是仅 family 名。记录实际解析结果、来源、license/embedding/subset policy。

主字体→CJK→fallback→missing 的既有语义保留为兼容策略；策略可以版本化，但生产测宽、排版、缺字报告和前端 coverage 必须消费同一份结果。不要继续使用只有 codepoint 的 `_LAYER_CACHE`；至少包含字体集合身份、policy version 与必要 shaping 参数。

处理 cmap 非 BMP、组合序列、可用 glyph 与真正可塑形的区别。按 grapheme/cluster 考虑 fallback，不把所有组合字符拆成互不相关的独立字形。支持范围之外应报告 missing/unsupported，不借助 PDF 库隐式换脸。不能因 `COVERAGE_MAX_CP=0x30000` 就声称该范围之外一概不存在字形。

所有生成的 coverage 与 golden 数据包含 schema/policy/font-set identity。静态默认字体表与动态系统字体必须区分：动态内容不能冒充默认静态表。更新 `canvas_coverage.json` 的路径或数据结构时，四份 Vite、前后端 glyphPlan、构建脚本和 PyInstaller datas 一起更新。

## 字体资源合规

按 R01 的 ADR 强化 `test_font_provenance.py`：批准资源 allowlist、内容哈希、LICENSE/NOTICE、构建获取来源、禁止未审查字体、禁止运行时静默联网。旧“字符串不许出现 fontfile”的测试应替换为更强的真实来源检查，不是直接删除。不得捆绑专有系统字体，不得把 MuPDF 内置字体未经独立许可复制到新包。

本阶段若提供本地字体端点，必须会话授权、限定批准的 font ID、拒绝任意路径、无 directory listing，不泄露用户其他字体。能嵌入 PDF 不等于能通过 web 重新分发，不能混淆许可。

## 验收

- 默认字体资源离线安装可用；损坏或缺失资源产生可诊断错误，不静默换系统字体。
- 同名不同字体版本不共用缓存；增删系统字体/切换字体资源包正确失效。
- 对旧 must 字符集合做新旧 coverage 差异审查，不能静默减少中文或符号能力。
- 字体真来源、许可证与依赖 metadata 进入打包扫描。
- 纯 glyph 策略可无 native/PDF 库运行；生产字体 I/O 与纯策略分离。
- 前后端 golden 在同一字体集合身份下对拍；动态系统模式如有环境差异如实标注。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R05_typography_layout.md -->

# R05 · 实现可复现的 shaping、rich text、换行与同源预览

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R04 字体身份与 fallback；R02 ShapedText 模型。

**必须定位阅读：** `richtext.py`、前端 richText/typography/TextView、后端 `_draw_text` 和 text_width、现有 compose_text 与 typography 测试。

## 实现

构建 TypographyEngine，将逻辑文本及样式转换成最终 ShapedText：分段、实际字体、script/language/direction、glyph ID、cluster→Unicode 对应、advance/offset、line breaks、baseline、line box、ink bounds。缓存键必须包括字体文件、size、features、variation、语言/方向、富文本解释开关和可用宽度。

采用 HarfBuzz/uharfbuzz 等成熟 shaping；明确 bidi itemization、line breaking、fallback 是上层职责。对本轮支持的脚本做好完整链路；对未支持类型明示能力限制，不因为 shape() 返回数字就宣称排版正确。

保持 Tavotto 富文本 `^{}` / `_{}`、自动上/下标开关、显式换行、padding、行距、对齐、背景/描边/下划线、角度和现有几何语义。上/下标要有真实 run size/rise，不能用字符串替换偷偷改变源文本。换行按照实际 shaped advances 与安全 cluster 边界，禁止切断 surrogate、组合字符或 ligature cluster。

改变字体导致历史换行/基线不同的情况必须版本化并审查：默认给出 layout change 诊断，不在迁移时自动改变用户文本框尺寸或全画布排版。可以保留兼容 layout policy，但不能保留旧库隐式 fallback 作为正式依赖。

## 前端的事实来源

为 canvas annotation 提供带 revision/font-set identity 的 authoritative layout/preview 数据。正式可见字形必须与该布局对应，不能用 CSS 系统字体再次测宽并声称准确。可选方案是许可允许的本地 font resources 配合明确坐标，或同源生成的 SVG glyph outline 预览；PDF 输出仍用真实可检索字体，不受 SVG 预览轮廓策略影响。

保持文本编辑、键盘输入、选择、可访问文本和快速拖动。异步布局返回要匹配文本对象 revision，旧响应不覆盖新输入。offline/Playground 缺服务时说明能力，不显示虚假的已验证状态。不要用每次鼠标移动都启动 native process 的方式实现预览。

## 验收

中英混排、Greek/±/−/μ、emoji/缺字边界、连字、组合重音、粗斜体、上下标、空白、长词、CJK 换行、紧边界、不同字号/行距/角度、不同浏览器缩放都测试。区分 advance bbox 与 ink bbox：斜体或重音不能因只看 advance 被漏检。

每次布局输出能重放、可解释；同身份输入结果稳定。至少检查关键 canvas 文本的前端可见位置与 PDF 字形位置，不能只对比 Python 两个函数彼此一致。Matplotlib 图内排版不在此阶段重写，保持源 worker 信息的独立性。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R06_pdf_text_emitter.md -->

# R06 · 完成可检索文字、字体嵌入和子集的 PDF 发射器

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R01 已选写入方案；R04/R05 可用。

**必须定位阅读：** 选定写入器源码/文档、fontTools subset 对应版本、PDF 字体资源处理、现有字体与 richtext 几何测试、R01 真实样例。

## 实际实现

完成 ShapedText→PDF 的生产适配，不再次选择字体、不重新测宽、不重新换行。以字体资源哈希缓存嵌入对象，避免同名不同脸冲突；同一个 PDF 内不同页面/Form 的资源命名确定且不碰撞。

实现所选字体类型需要的 FontDescriptor、font program、Type0/CIDFont/encoding、widths、CID→GID 与 subset remapping；以实际 glyph 集合闭包做子集，不只按 Unicode 字符集合删表。组件字形、shaping 产生的 glyph、variation 实例化后的对应关系必须正确。CFF 与 TrueType 不可套同一错误映射；未实现的格式按照能力裁决，而不默默换脸。

写出正确的逻辑 Unicode 映射。glyph ID 不等于 Unicode，ligature 可能对应多个码点，cluster 可能有多个 glyph；不能把相同 cluster 的逻辑文本对每个 glyph 重复写一遍。必要时用合适的 marked content/ActualText 补充语义，确保所支持的独立提取器获得正确文本。处理 Unicode 非 BMP 与字节编码，不依赖 `Text.show(str)` 的隐含 UTF-16BE 假设。

精确应用 glyph offsets、advance、rise、rotation、text transform 与颜色。文本的背景/边框/下划线用明确的 Path 绘制，顺序与旧语义一致。合法的 0 opacity 不被 `or default` 吞掉。

字体嵌入/子集被授权策略禁止时，给出结构化错误或由显式用户策略选择可见降级；绝不将全部 outline 之后仍报告 embedded/searchable。默认 approved 字体 must 场景不允许降级。

## 独立验证

1. 用至少一个不同于生产 writer 的渲染引擎打开最终 PDF，验证字形与位置。
2. 用独立文本提取检查逻辑字符串和关键字符，不只检查字体资源名字。
3. 遍历实际字体程序和映射，验证嵌入、子集、glyph closure、字体资源冲突与非 .notdef。
4. 删除 ToUnicode、替换 GID、删 FontFile、打乱宽度、移除 mark glyph 的反例应触发相关检查；一项坏了不能被另一项正常覆盖。
5. 比较小字符集子集与同一合法字体完整嵌入的实际大小，不预先承诺具体 KB 或压缩倍数。
6. 图内 imported PDF 字体不由此发射器重新写，不能为了统一把源 PDF 的字体全部换成默认字体。

## 完成条件

默认字体范围的关键语言与富文本输出既正确可见又能正确检索，嵌入与子集证据真实；正式 emitter 不依赖 PyMuPDF，不把字体文件或布局决定泄露给无关业务模块。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R07_pdf_compositor.md -->

# R07 · 完成矢量合成、ImportedPage、图形、裁剪和透明组

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R02/R03，文字集成依赖 R06。

**必须定位阅读：** 旧 `_place_panel`、`_draw_arrow`、`_draw_shape`、Canvas，全套 compose 几何测试和前端 shapeGeometry。

## 实际实现

以 pikepdf/QPDF 管理 PDF 对象、资源、导入页与保存；Tavotto emitter 只实现明确 IR 的绘制语义，不自写完整 parser/xref/repair engine。确保每个节点 graphics state push/pop 成对，异常也释放 native 资源。

ImportedPage 以 Form XObject 导入，正确处理资源继承、非零 MediaBox/CropBox、/Rotate、/UserUnit 和可见页几何。应用 Tavotto 的 crop→placement/rotation/flip 的顺序必须由 R02 契约给定，用可辨方向的非对称 fixture 验证。源页的原始图形资源尽可能保持，不能顺手把所有内容重新编码成图像。

对 flip 和 opacity 实现真正的矢量路径：仿射反射 + 合适的透明组/ExtGState。整体 opacity 与每个子元素 opacity 不等价；内部重叠、group isolation、已有透明组/soft mask 必须纳入用例。复杂 blend/mask 未在支持范围时给出明确错误或经策略允许的局部降级，记录节点和原因。

完整移植 rectangle/rounded rectangle/ellipse/triangle/diamond/polygon/brace/line、arrow 两端头型、dash、stroke inset、cap/join、shape fill_opacity、legacy lineEndpoints 和旧 head 字段。使用统一 Path/geometry 编译，前端同源规则继续由 golden 或独立几何 fixture 看护。对疑似 `fill_opacity=0` 旧缺陷先补反例、确认语义、再修复。

Image 支持明确 RGB/gray/CMYK 输入边界与 alpha/smask，保留已批准的色彩语义。不要把“能打开 CMYK PDF”写成“已完成 CMYK 印前管理”。源 PDF 的 active content/附件/annotations 与纯页面内容分别定义导入政策，不要一边 preview 显示注释、一边 export 静默漏掉；未支持外观要报告边界。

页面白底与透明背景遵守旧契约。输出 canonical PDF 一次完成，PDF-only 不需要先栅格化。多次 save 或复用应有明确生命周期，不让后续保存改变先前已验证字节。

## 验收

独立读取最终 PDF 的路径、文字、图片和 CTM，验证完整几何，而不是仅断言对象数量。相同源页两实例资源去重不能混淆 instance transform；同名资源不同内容不冲突。

旧正常无变换场景保持合格；新翻转/透明场景存在可核验的真实路径/文字，且没有被整页替换成 Image。显示正确但文字变乱码、图形变位图或 clip 丢失都必须失败。PNG 像素对照只是补充，不是唯一依据。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R08_pdfium_raster_runtime.md -->

# R08 · 完成 PDFium 读取/栅格化、像素路径和受控进程

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R01 进程与冻结 spike；R07 产物；可先独立实施 reader。

**必须定位阅读：** 现有 probe_asset/render_preview_png/compare_png/original_*，`tiffwrite.py`，`app._write_render_cache`/缓存锁，进程和打包入口。

## 实际实现

提供普通数据结果的 PDF reader/rasterizer：第一页尺寸/页数、预览、字体/文字等 inspector 所需能力。统一源页 boxes/rotation/user unit 的几何与 R07，不能 probe 一套尺寸而 compositor 另一套。为损坏、加密、密码缺失、超限、unsupported PDF 返回稳定错误，不因 parser 修复就冒称原文件完全有效。

建立 canonical RasterBuffer（宽高、stride、channel order、bit depth、colorspace、alpha mode、dpi/来源）。明确 PDFium 返回缓冲区的所有权，复制或保活到编码结束；不能关闭 bitmap 后继续读其 bytes。显式 close PDF/document/page/bitmap，处理异常和取消。

PNG/TIFF 在同一参数下消费同一 RasterBuffer，正确处理 straight/premultiplied alpha、RGB/BGR/BGRA 和行填充。保留/适配现有 tiffwrite 的 Deflate/分辨率语义；确需 Pillow 作为 codec，就在 R14 完成正式依赖与打包，不引入 NumPy/Matplotlib 到 app 作为捷径。

preview、probe、栅格输出、inspect 中所有 PDFium 调用通过同一受控 runtime。生产 child 每进程串行，parent 负责调度、timeout/cancel、背压、像素/内存预算和重启；不要使用线程池同时执行 PDFium，也不借用用户科研 venv。支持源码启动与冻结 CLI/sidecar 的专门 child mode；防止 Windows spawn 再启动 GUI，JSON/IPC 输入限制 schema 与大小。

预览缓存 identity 不再只有单库版本：包含实际源内容、像素参数、颜色/alpha政策、后端 build identity，必要时含 font policy。保持同键锁、临时文件发布和 Windows 已打开句柄的处理。多个项目、旧服务缓存、后端升级不能复用错误图。不要把 native handle 或不可序列化对象放进共享缓存。

生产 `compare_png` 也替换 MuPDF 解码依赖；保留现有返回 shape 与噪声语义或做明确版本扩展，与 CI 算法的共有部分有 golden 对拍。生产无需 NumPy。

## 验收

- RGB/RGBA/gray/CMYK、透明边缘、padding/stride 的颜色与 alpha 对拍；PNG/TIFF 解码一致。
- 并发 export/preview/probe/inspect 不出现进程内并发 PDFium 调用；取消/异常资源归零或在文档化缓存预算内。
- 大画布合法高 DPI 也被像素/内存预算约束，不因只检查 PPI 上限而崩溃。
- 坏 PDF、child exit、timeout 后下一请求可成功；错误不伪装空白图。
- 源码与冻结最小 app 都从中文/空格路径运行；项目科学环境不含这些新库仍可正常通过 app 使用。
- 此阶段的进程隔离不可描述为完整 OS 安全沙箱。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R09_original_annotation.md -->

# R09 · 完成原图导出、预览与标注写回的等价迁移

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R06–R08 的文字、合成和栅格链路可用。

**必须定位阅读：** `pdfbackend.original_*`、`probe_asset`、`render_preview_png`、`annotate_asset`；`app._write_source_files` 及实际调用链、`_export_produce_original`、`originalspec.py`、原图与写回测试。

## 实际任务

逐个迁移 facade 的剩余入口，不允许只完成 `Canvas.save_pdf()` 就宣布后端替换完成。

1. `probe_asset` 返回原契约。PDF 使用实际可见页尺寸并正确处理页盒、Rotate/UserUnit；多页只取第一页的产品规则保持。位图保留真实像素尺寸和 alpha 事实；密度仍由 originalspec 决定。
2. 原图 PDF：PDF 源复制第一页面及其必需资源，不经过画布布局或文字重排；明确不承诺重新序列化后字节完全相同。位图源装进给定 page_pt 的页面，像素来源仍是栅格，不得报告纯矢量。对合法 JPEG 可保留原压缩流时验证颜色/方向，不能只验证扩展名。
3. 原图 PNG：源为 PNG 时逐字节复制，包括已有元数据。JPEG/其他支持位图转码时保持 native pixel grid，不按请求 PPI 放大/缩小。不要偷偷套 EXIF 旋转或颜色转换而不定义迁移政策；以真实旧行为和图片契约决定。
4. 原图 TIFF：无损、native grid、alpha 和 stride 正确；只写源明确声明的密度，不把 assumed 变成 metadata。保留未知密度语义。复用已验证 tiffwrite 或以 ADR 替换，不因为引入 Pillow 就无理由改写全部编码器。
5. PDF 源输出 PNG/TIFF 使用同一最终源页与同一渲染参数；正确写出实际 DPI 元数据。这里仍不受画布的 crop/rotation/flip 等影响。
6. 预览缓存采用完整渲染身份，保留同键去重、临时文件发布和 Windows 读句柄占用的处理。处理损坏缓存、零字节、旧后端缓存的失效；不要用 mtime 替代内容身份。

## 标注写回

找到 `annotate_asset` 的外层事务，不要让底层新实现绕过现有权限、expected_mtime、重放/像素验证、文件锁和恢复机制。坐标为原图自身 mm；text/arrow/shape 使用与画布完全相同的 IR 编译与 emitter。输出 PDF 之后用同一份字节生成伴随 PNG。

pikepdf 的完整重写不等于旧 incremental save。始终在作业私有临时目标创建新 PDF，关闭所有句柄后交原有发布层处理。不得通过允许覆盖输入文件的危险选项直接写原件。原件在任何验证失败、取消或进程崩溃时必须可恢复。

明确加密、密码、权限及数字签名策略：缺少授权凭据就结构化拒绝；不得在日志记录密码，不得宣称修改后原数字签名仍然有效。对于不支持保留的安全/交互结构，明示边界，不能静默剥离后称完全保真。

## 验收

真实文件验证：源 PNG byte hash 不变；JPEG 转码尺寸不变；未知密度 TIFF 不被写成 600 dpi；原图源多页只输出第一页；画布变换不影响 original；PDF 文本仍可提取、路径仍存在；写回携带的 annotation 与画布同几何；PNG 来自注好后的同一 PDF。

失败注入：锁文件、磁盘写失败、源在处理中变化、像素验证失败、取消、源被删除、同源两次并发写回、PDF 解密失败。不得出现用户原件半写、另一作业结果串入或隐式使用旧后端。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R10_artifact_validation.md -->

# R10 · 实现真正读取最终产物的验证器

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R06–R09 的主要产物入口可用。

**必须定位阅读：** `engine/artifactcheck.py`、`preflight.py`、`profiles.py`、`exportjob.py` 的提交点、现有 TIFF 独立读取测试、MCP normalize 的验收调用。

## 目标与模型

实现独立 ArtifactInspector，而不是将 emitter 自己声称写过的内容复制成“检查结果”。每条检查返回 expected、observed、status、method、scope、evidence、limitations；status 为 pass/fail/unknown/not_applicable。明确 mandatory unknown 阻止“已核验可提交”，可选 unknown 可以交付但必须可见。

允许生产检查器使用 pikepdf/PDFium 的读取能力，但不能调用生产 emitter 的坐标/字体编码 helper 作为唯一真值。测试须增加独立解析/提取器与解析式样例，避免读写共用同一个错误。

## PDF 检查

重新打开已经完整保存并关闭的 staging 文件。读取实际页数、物理页盒和旋转后的尺寸，处理 UserUnit。区分载体是 PDF 与其内容是 vector/mixed/raster/unknown。

实现受限、资源安全的内容遍历：图形状态栈、CTM、字体选取/文字操作、Image XObject、递归 Form XObject 及资源继承/覆盖。处理循环引用、深度/对象数/解码大小预算。不要正则扫描 PDF 字节；不支持的 Type3、Pattern、复杂遮罩/字形结构等明确 unknown，而不是遇不到就当没问题。

字体事实区分：资源已声明、实际被使用、FontDescriptor/font program 存在、实际可解析、subset 的证据、可检索文本映射。`ABCDEF+` 名称只是线索，不独立证明子集正确；ToUnicode 存在不独立证明文本正确。递归 Form 中的字体不能遗漏。来源字体 family 名称、实际 face、fallback 层和角色分别记录，不要求数学/CJK 合理回退与主字体名称机械相同。

对支持的字体/编码类型检查必需 glyph 映射和可观察文字；复杂未知必须报告范围。区分“文字转轮廓”“字体缺失”“文件本就无文字”，不得都写字体通过。

位图有效 PPI 从像素网格及累计 CTM 的实际放置尺寸计算，不能拿输出请求 600 dpi 代替。说明非均匀缩放、shear 的度量方法及保守策略；裁切不凭空增加密度。资源自身 px 与有效 PPI、导出栅格 DPI 是三个不同字段。

## 其他产物与裁切

PNG/TIFF 读取实际宽高、alpha、压缩和真实密度标签；TIFF 不再默认“未解析”。仍允许源本就无绝对密度，并据 profile 判定它是 unknown、允许还是失败。对 SVG/EPS 保留格式特有容差，勿套 PDF 精度。

裁切报告区分 intentional clip 与意外 overflow；支持范围内依据实际 glyph/path bounds、clip stack 和语义来源建立证据。IR 分析得到的是 planned，最终产物能观察到的才是 observed。任意导入 PDF 的“无裁切”不能仅由对象包围盒或像素图证明：无法判断时 unknown。不要把现有 preflight/normalize 的判据复制成第二套权威。

## 接入位置

提供 staging validation 入口：产物保存 → 完成所有必要 metadata → 关闭 → inspect → 按 policy 决定该项是否可发布。验证之后不得再改产物字节。强制规则失败返回该格式的结构化错误，其他符合条件格式可按原 partial 规则继续。

旧 `vector` 布尔在 R12 审计所有消费者后兼容迁移；新增实际 content_kind/degradations 等丰富事实。不能因为扩展名是 PDF 就设 vector=True，也不能把 True/None 的旧兼容函数改成含义不明的 truthy dict。

## 必须抓到的反例

删 FontFile；只声明不用的字体；Form 内用另一字体；ToUnicode 故意错映射；将文本全转路径；用低 PPI 图像装进矢量 PDF；把所有内容压成整页 bitmap；改 UserUnit/页盒；伪造 TIFF DPI；PNG 大小与请求不一致；未知结构/资源循环；故意裁掉标签；JSON 声称通过而文件不通过。每类至少一个真实负例，不能只 mock 返回失败。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R11_manifest_fingerprint_trace.md -->

# R11 · 交付 Manifest、可复现性、语义 ID 和诊断 Trace

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R10 的事实模型成立；可与 R12 的接口接入协作。

**必须定位阅读：** 已有 style-check/proof 报告、engine manifest/schema 与 gid、normalize.manifest_hash、render 缓存键、诊断包和隐私规则。

## Artifact Manifest

实现带 schema version 的模型、JSON Schema 或等效严格验证、迁移策略和稳定序列化。区分 request/plan、observed artifact、rule evaluation、provenance/environment。每个 output 有独立文件身份和验证范围；一份失败结果不能消失。

内容包括 scope、实际尺寸、字体事实、raster/vector facts、降级记录、检查状态、渲染环境、源哈希、规范身份、节点映射与限定条件。Manifest 不将客户端传来的 errors=0 当最终证据。前端旧样式报告与新 observed facts 要有明确分区和来源。

内部完整 manifest 不必每次给用户导出 sidecar；用户请求留档时才由 ExportJob 命名/预留/发布，不在 renderer 随手写用户目录。其失败维持现有 partial 语义。必需验证本身在提交点之前完成，不依赖 sidecar 写成功。

## 四种身份

1. plan/semantic hash：规范化后的有序语义与资源内容身份。
2. render fingerprint：plan/IR、字体文件与 face/variation、应用及实际底层库版本/构建身份、影响输出的 flags、DPI、背景、色彩策略与规范内容哈希。
3. artifact hash：完成后的文件原始字节 SHA-256。
4. run/job ID：这次执行的关联 ID，不冒充内容身份。

定义 canonical JSON、浮点表示、缺省值、Unicode 文本不擅自归一化的策略；拒绝 NaN/Inf。绝对路径、mtime、随机运行 ID、日志时间不进入内容语义 hash。内容相同而换路径应保持语义身份；改字体文件、实例 override、影响渲染的版本应失效。

PDF metadata 只嵌入允许公开的 render/source/semantic 身份，随后封口并计算 artifact hash，由外部 manifest 绑定。不要把最终文件自身的 hash 再写进该文件。若修改 XMP 改变字节，旧 hash 必须无效。

## 可复现性

分别实现 semantic、visual、byte 三档实验，报告确切适用条件。对字节级模式规范化时间戳、文档 ID、对象/字体子集命名与输出顺序等影响因素；只在固定依赖/资源环境实证成立时声明。跨 OS 字体不同应产生可解释的环境差异，不要为了得到相同 hash 隐去影响因素。相同输入 hash 但不同输出是失败证据，不能把 fingerprint 当“证明”。

## Semantic IDs / Trace

节点 ID 使用文档/画布/实例/语义来源的稳定关系，不用 PDF object number 或列表下标作为长期身份。对 imported page 只声明真实已知映射；未知内部文字不要伪造 axes_0.title。

Trace 记录 source resolve、坐标变换、clip、字体选择/缺字、栅格降级、验证和发布阶段；标记 planned/observed。默认有界、可关闭，不在性能关键循环无界记录。公开诊断脱敏路径、账户、源码、全文科研标签、密码；需要含内容的本地诊断必须沿现有明确授权流程。

metadata 只是关联信息，不是可信权限或完整反向编辑能力；重新打开 PDF 时不执行其中脚本、不读取 metadata 指向的任意磁盘路径、不把嵌入的旧 proof 当当前核验结果。

## 验收

同语义不同路径、顺序稳定、相同资源复用、不同实例、字体同名不同文件、同文件不同 face、版本变化、metadata 后改、缓存旧证据、trace 超限、敏感路径扫描、缺映射与不可信 PDF metadata 均有测试。实际重放失败就报告差异，不允许只测 hash 函数返回稳定字符串。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R12_integration_http_mcp_ui.md -->

# R12 · 接通 HTTP、MCP、UI 与既有导出事务

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R09–R11 可用；先完成所有入口，再评估默认切换。

**必须定位阅读：** `app._export_produce*`、`_style_check_report`、`exportjob.Produced/Output/run`、前端 api/exportRequest/exportStore/validationStore/ExportDialog、Codex bridge/server、版本探测和插件清单生成器。

## 单一路径接入

保持 ExportRequest 两个 scope、默认值与命名权威，不新建平行 PublicationJob。RenderPlan 在既有 job 内生成；Canvas façade 在过渡期间可记录/编译 IR，允许旧调用方式，但 production 只认经过选定的一个后端。

HTTP 同步导出、异步/SSE、MCP 科学引擎直出、native runtime asset 和写回都必须收到真实产物检查结果。科学引擎直出 SVG/EPS 不经 PDF 强制转换，但与其他输出共享正确的快照身份和验证协议。请求 EPS 时确认实际 worker 生命周期没有重新执行带随机数据的脚本得到另一张图；测试状态身份而不只检查调用了 rerender=True。

## 提交点与报告

在 produce 阶段完成每项产物封口、检查和生成证据，再进入原 `_commit_lock`。失败项是 Produced 的显式失败，不留半文件；成功项照常发布。可选 manifest/style-check 报告仍采用既有 names、reservation、rename/ask/replace 和 partial；图发布之后生成的报告不能反过来声称阻止了该图。

保留取消可接受区间、提交后取消拒绝、报告失败不丢成果、两作业重名防串、项目绑定和原件事务。不要把多文件依次 os.replace 改写成“整批原子”。如需内部证据持久化，写入应用可写数据目录并定义 GC/保留期，不能写 site-packages。

## 回执与界面

扩展 Output/Produced 与 API 类型时保持旧客户端可解析；审计所有 `vector` 使用点和测试。新增 carrier、content_kind、degradations、observed_dimensions、verification 等字段；旧 vector 应按明确兼容 ADR 保守投影，不能继续以 .pdf 为 True。界面必须区分“矢量载体”“含位图”“未核验”。

展示简洁的检查结论：已验证、失败、未核验、不适用；未核验不画绿色勾。每个 warning 有可定位对象/原因与下一步。只显示事实，例如“2 个栅格对象低于要求”，而非“600 dpi 文件所以通过”。规则阈值来自现有 profiles，不新抄一份。

实际字体身份、替代、missing、文字轮廓化与预览修订号应与 Typography 的真相一致。对异步预览应用 revision guard，快速输入后旧请求不覆盖新字形。遵守 Tavotto 既有 UI 原语、双语、可访问性与动效规则，不趁迁移重做整个界面。

客户端 style_check_report 可以作为编辑前检查快照，但不作为服务器最终文件证明。对未知字段/过大 trace/非法 ID 与未认证访问做防护，绝对路径不下发。

## MCP / 多发行入口

复用现有 bridge，不复制检查器和 normalize 算法。同步导入探针、BRIDGE_IMPORTS_AT_MIN、插件最小版本资格与发行说明；版本号按真实将包含本功能的版本决定，不编造已发布号。保持工具 schema、旧结果投影；新增详情是版本化字段。

更新普通 Web、MCP widget、Playground、桌面构建的 aliases/资源。Playground 没有应用后端时明确能力限制，不假装存在 PDFium；不从网页静默联网下载字体/服务。只构建候选插件并验证，不自动发布 plugin-stable，也不提交生成 canvas.html 到源码分支。

不新增遥测 payload 或改变隐私同意版本；需要统计改动另走既有流程，产物 hash/科研文字/路径不进入遥测。

## 验收

跑真实 HTTP+SSE 与 MCP 导出、partial、取消、覆盖、窗口并发、runtime asset 和用户科学环境分离用例；前端用实际新 payload 而非只 mock 文案。验证仅 PNG 请求也得到正确证据；SVG/EPS 边界不回归；artifact hash 与最终发布文件一致；失败报告不出现“Ready for submission”。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R13_ci_oracles_renderbench.md -->

# R13 · 升级 CI 检验依据并建立有价值的 RenderBench

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R00 的意图清单贯穿此前阶段；此阶段收口完整资格，非等到此时才写测试。

**必须定位阅读：** `03_CI_BLUEPRINT.md`、ci/nightly/lab/release 工作流、aggregate_gate.py、test_merge_queue_workflows.py、CompatBench corpus/matrix/baseline、pixelcompare.py、现有 compose/export/字体/打包测试。

## CI 集成

保留三个稳定 required contexts。将 RenderCore 纯契约、小规模真实输出与无 PyMuPDF import 检查接入 PR fast；将跨平台/native 包/关键 corpus 接入 merge_group 和 full-ci；深层 fuzz/soak/performance/多版本组合放 nightly/lab；发行检验指向实际待发布产物。

新增 job 必须同步 gate needs、required 闭集、事件条件和对应单测。普通 PR 重型可整体 deferred，但 merge_group/full-ci 不可。required skipped/cancelled/missing/unknown 仍失败；不可通过修改 aggregate_gate 放宽语义。不要更改分支保护来让红灯通过，不把真实 source 冲突当生成物问题。

一个 workflow 若使用路径优化，应由有测试的保守选择器确定 case 集；未知路径触发广覆盖。任务可以报告不适用，但必需核心集合不允许空选择、全部 skip 或只执行 mocks。

## 三层测试与双轴差分

第一层纯模型/几何/能力/哈希/状态机；第二层真实文件解析、独立文字提取、结构和元数据；第三层应用入口与干净安装包。禁止所有断言都读取生产 emitter 的自报事实。

建立两种差分：同一 PDF 分别用旧/新 rasterizer 渲染，隔离光栅差异；旧/新 compositor 产物都用同一固定第三方读取栈观测，隔离合成语义差异。旧环境只能在迁移专用隔离 lane/已核准历史基线中出现，不进入正式依赖。

所有几何旧测试都进迁移 ledger；迁移测试接口不应机械仿制完整 PyMuPDF API。保留原断言意图，用限定的独立 inspector + 解析式 fixture 代替。合法透明度/抗锯齿变化与内容错误分开说明，不追求跨渲染器全图 bit exact。

## 像素判据升级

当前 CI 灰度算法会丢颜色/alpha。新增显式、版本化 RGBA 比较模式，不静默改变全部旧基线。标准化 straight alpha/通道/stride，比较原 RGBA 及黑白底合成；alpha=0 处 RGB 的比较政策单独定义。对大面积、平均误差、小局部灾难分别有判据，并结合节点/区域语义，防大白底稀释缺一行字。

同灰度不同色、纯 alpha 改动、文字缺一个 glyph、边缘裁掉一行、细线消失必须失败；允许的抗锯齿噪声由基线实验校准而非遇红就放宽。PNG/TIFF 同一次 canonical buffer 应逐像素一致，这是与旧新 rasterizer 容差比较不同的强契约。

## 变异与基准

针对删 q/Q、变换顺序、错 crop origin、UserUnit、丢 ToUnicode/FontFile、GID 重映射、group alpha、dtype/stride、DPI、错 snapshot、关闭验证、unknown→pass、跳过 gate job 设计 must-fail 反证。记录具体变异击中哪条测试；不能只报整体 mutation 百分比。

复用 CompatBench 的 intent/matrix/observation 分类，新增病例不能用 product_bug 基线豁免高频回归。每个 case 标明合法资产来源、预期边界、旧行为、新要求和独立证据。未运行不是不支持；不支持不是通过。

记录 render latency、cold/warm、峰值 RSS、长跑泄漏、文件大小、字体子集开销、缓存命中/失效、CI wall time 与累计执行成本。阈值来自 R00 实测/产品预算并说明硬件；不要预设“必须快 30%”证明价值，也不以速度改善抵消正确性失败。

## 报告与完成条件

每次输出机器 JSON 和可读摘要，含 source SHA、run/event、依赖与字体身份、实际 case IDs、检查总数、状态、失败类型、产物/日志 hashes。缺证据文件、错 SHA、空 case、旧结果复用应让资格失败。保存失败样例及差异图时遵守隐私/许可证。

完成时展示三类证据：旧必须行为保住；指定新能力真实提升；测量得到的性能/成本变化。不得仅以测试数量、覆盖率或建了 rendercore/ 目录宣布价值。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R14_distribution_native.md -->

# R14 · 验证 wheel、桌面 sidecar 与插件真实发行闭包

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R01 已做早期冻结 spike；R12/R13 接口与门禁已集成。

**必须定位阅读：** pyproject、packaging/tavotto.spec、runtime-lock、build_desktop/build_worker_runtime、运行时源码闭包测试、support-matrix、release/nightly 安装链、插件候选发布与 receipt。

## 依赖闭包

按 R01 选定并验证的版本维护锁/约束及来源，区分应用运行时、用户科学 worker、独立浏览器运行时和测试工具。不要为了方便把所有新 native 包装进用户 .venv 或科学 runtime；也不要只装到 dev extra 让最终产品缺库。

检查 pikepdf 的 QPDF 与其他 wheel 打包的依赖、PDFium、HarfBuzz、字体资源及 codec。每个发行平台实际验证 wheel/ABI/最低 OS；Python 3.10–3.14 的承诺必须继续与当前 matrix 同源。不能静默缩小已支持范围；不满足时先调整候选版本和依赖策略，产品范围变化需显式记录决定。

## PyInstaller / sidecar

按实际包内容收集 DLL/dylib/so、data、字体清单、NOTICE/许可。若 Pillow 进入应用运行时，修复当前 `excludes` 中的 PIL，并验证 native codecs 真在包里；保留 Matplotlib/NumPy 等科学栈在父应用中的隔离，不广泛取消所有排除。

验证启动 PDF render child 的明确模式、冻结资源定位、可执行位、Windows spawn、sys.executable/CLI/GUI 区别、macOS rpath 与重签名。CPU 原生库不能当普通纯 Python 资源假装发现即可运行。路径含中文+空格、只读安装目录和独立 TAVOTTO_DATA_DIR 必测。

保持 worker.py/bridge_runner 及其原有外部解释器 import 闭包的源码交付；新增 import 如跨越边界必须有对应测试和 rationale。保留已有 Rust supervisor，不因这次 Python PDF 替换重建整套进程管理。

## 真产物验证

从构建出的 wheel/sdist 安装，而非 `pip install -e .`。测试离开 checkout、清空 PYTHONPATH、没有 PyMuPDF/fitz、没有开发工具、没有事先安装的额外字体；执行 import、启动服务、probe、文字合成、导出 PDF/PNG/TIFF、MCP 及 native worker 交互。

Windows 检验 sidecar/CLI 与现有 NSIS 安装、升级、卸载链；macOS 检验实际签名和公证后 .app，而不是签名前 build 目录。不得声称 Linux 有未构建的桌面发行，或顺便宣传 Windows ARM/macOS Intel 原生支持。

验证 native loader 和可选依赖：不是只有 importlib.find_spec 能找到模块就通过，必须真的调用渲染、字体、图像编码并解读成品。升级旧版本之后 cache/font layout 身份失效正确；旧工程仍能打开并明确显示字体变更。

## 合规与供应链

产出 SBOM、第三方 NOTICE、来源/版本/许可证清单以及发行包内部扫描。扫描本轮需要退役的 PyMuPDF/fitz/MuPDF 模块和 native 文件，不能仅 grep Python import。检查下载字体/库 hash 与锁一致；不在包里混入个人字体、旧 backend 资源或未授权 fixture。

pikepdf 的 MPL-2.0 文件级义务、底层库和字体各自义务分别处理；“主仓库仍 AGPL”与“移除了 PyMuPDF 依赖”是两件事，不擅自将项目 LICENSE 换成商业或 MIT。

构建、核验插件候选与 receipt，满足当前引擎最小版本探针；source SHA 与实际内容一致，不自动更新 plugin-stable/Release。运行渠道与源码通过分别记录，只有对应真实渠道通过才能报告该渠道发行资格。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R15_cutover_retire.md -->

# R15 · 切换默认后端并彻底退役运行时 PyMuPDF

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R00–R14 证据齐备；Gate B 必须满足再切换默认。

**必须定位阅读：** MIGRATION_LEDGER、acceptance_matrix、所有 backend imports、pyproject/locks/native packaging、缓存版本、font policy/schema、旧新后端基线与平台运行证据。

## 切换前审查

逐项核对每个 façade API、HTTP/MCP/写回/预览入口已有新实现和真实测试；核心 must 样例与实际受支持安装渠道有证据；旧 bug 修正与不可避免字体外观变化分别有已审查说明。必需缺口未闭合时先修复，不以环境变量双默认或静默 fallback 混过去。

## 退役实施

让 `pdfbackend` 指向新 RenderCore 兼容门面；移除生产旧实现及默认 PyMuPDF 依赖、fitz 别名、动态 import/探测、运行时安装和打包 hooks。所有普通测试切到新实现和独立检验；保留几何/内容语义断言，不批量删 compose/export 测试。

迁移期间需要的旧后端差分工具不再成为默认 test/dev/package 依赖。可留经过许可审核的历史报告、最小 fixture 或显式隔离工具；必须不进入 wheel/桌面包/runtime。不要改写 Git 历史假装从未使用过旧库。

实际 SBOM/native 扫描确认旧 MuPDF 依赖不被其他库重新引入。不得把 `pymupdf` 换成其他包名但底层仍加载 MuPDF。也不要把“没有任何 AGPL 代码”作为目标：仓库当前自身许可证仍为 AGPL-3.0-only。

清理 `BACKEND_NAME/VERSION`、字体 coverage 路径、生成物/测试规则、文档和诊断字段，保持必要旧 API 的兼容投影。为新渲染身份、字体政策、coverage/schema 和 preview cache 设置明确版本，确保升级后不命中旧渲染的缓存。用户 layout/source 不得为切换被破坏或无授权重排。

## 回滚策略

默认切换后故障要结构化暴露，不能偷偷调用旧 backend。开发/发布回滚应回到上一份已经验证的版本或调整新后端补丁；不能把 AGPL 第三方又塞进新的目标发行包做“应急依赖”。不自动执行回滚发布，本任务只写流程和验证程序。

## 验收

新建干净进程与干净安装环境，主动阻断 `pymupdf`、`fitz` 导入并证明实际全部主要路径仍可用。静态 import 扫描、安装依赖图、磁盘/native 库扫描及实际运行四者同时成立。

将 ledger 每项标为已迁移且有证据，或显式范围边界；必须保留功能中的未知/不可用不能消失。R16 前输出 final candidate SHA、各 artifact hash 和不含旧依赖的证据，不把“已切默认”写成“已发行”。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：phases/R16_final_qualification.md -->

# R16 · 完成独立验收、价值对照和发行资格报告

## 执行上下文

你在 Tavotto 当前 checkout 内实际实施本阶段。先阅读本目录上一级的 `00_MASTER_PROMPT.md`、`01_REPOSITORY_AUDIT.md` 与已有阶段 handoff。参考审计 SHA 为 `6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`；当前代码优先。不要只输出建议或空接口，不要自动推送/合并/发版。

**前置：** R15 默认切换完成；全部核心功能必须已有实现。

**必须定位阅读：** 完整 acceptance_matrix、05_GO_NO_GO.md、CI 报告、发行候选及 SBOM、源 proposal 的八项基础设施、迁移 ledger、保留式 normalize/授权边界。

## 逐项最终审计

以最终候选 SHA 和真实发行包为对象，不使用上一个提交的成功报告。对 acceptance_matrix 全部 core/must 条目逐个检查代码、运行记录、产物身份和独立判据。每条给 pass/fail/not_run 及证据；设计文档和 mock 测试不能独立满足实际出图要求。扩展 E01/E02 不计作核心未完成，但不能误称已交付。

独立复核者优先审查：坐标/alpha、字形重映射/Unicode、imported PDF 资源、原图 native grid、取消/并发/事务、未知事实和合规。没有第二位审查者时，明确记录是自审，不伪造外部 review。

## 至少五个价值演示

1. 同一科学矢量源在 flip 与 panel group opacity 后仍保留真实矢量/文字，并通过重叠内容的视觉正确性测试；与旧需位图的路径对照。
2. 默认授权字体上的中英/Greek/上下标可检索、可解析地嵌入与按政策子集化；缺字/字体不可用不静默替代。
3. PDF/PNG/TIFF 同一快照，PNG/TIFF 像素同源；original native grid/PNG byte copy 不回归。
4. 伪造客户端通过数、丢字体、低有效 PPI、错误 TIFF DPI、未知裁切等能得到正确的 fail/unknown，不能误显示全部通过。
5. 同环境重放、换路径、换字体版本和后端版本时，fingerprint/cache/proof 分别正确；可定位的 Trace 不泄露科研内容。

每个演示提供可重跑命令、合法 fixture、旧新结果、结构/文本/像素证据及限制。不能只放宣传截图，也不能把数据未测写成百分比收益。

## 发行与性能

运行目前 support matrix 真实要求的核心 suite 和渠道冒烟，验收 Windows/macOS 的实际 candidate artifact 与干净 wheel/sdist。CI 必需腿若未运行、超时、取消或平台不可用，发行资格为未通过/未完成验证；已完成开发部分仍照实交付。

对性能和 CI 成本给实测表，记录硬件、版本、重复次数与 warm/cold。性能略回退不是自动等于失败，应对照预先审查的预算和换来的功能价值；超预算必须修复或显式决策，不临时更改数字掩盖。

运行精选故障注入/变异证明门禁能红。检查是否存在 `importorskip`、空选择、过宽图像容差或 snapshot update 让必需测试失效；故意破坏关键能力应被对应资格门阻止。

## 最终输出

写 `FINAL_QUALIFICATION.md` 与 JSON：实现范围、准确 SHA、artifact IDs/hashes、完整需求状态、实际测试结果、没有执行的事项、平台资格、已知边界、许可/NOTICE、性能变化与回滚步骤。

结论明确区分“功能已实现”“该源码测试通过”“该候选安装包取得发行资格”“已发布给用户”。本任务不自动发布。只有 Gate C 真正满足才说发行资格通过；没有任何 CI 能证明任意 PDF、任意字体或所有未来环境绝对正确。

更新开发文档/API 边界/错误码/升级说明，删除过渡性 TODO 和假 capability。保留可扩展模型但不交付空目录伪装资产。本阶段完成后，核心八项应都有真实使用者、真实成功及失败路径，而不仅是类型定义。

## 阶段交付纪律

提交可审查的实现、对应测试和必要文档；在当前环境执行能够执行的命令，保存真实退出码、日志和产物身份。按 `04_HANDOFF_TEMPLATE.md` 记录需求、证据、未运行和下一阶段。不要把依赖缺失、平台不可用、fixture 未选择、未知结果写成 pass。改变 golden 必须解释差异，不可用降低门禁或删除语义断言获得绿色。


---

<!-- 原文件：extensions/E01_manuscript_compiler.md -->

# E01_manuscript_compiler · 批量论文图编译与一致性检查

**这是核心 R00–R16 之后的独立扩展，不是现有替换验收的偷换或前置条件。**

继续遵守 `00_MASTER_PROMPT.md`，阅读当前 AGENTS、已有阶段证据及相关源码。此任务要求实际代码、测试和可运行入口；不自动推送/合并/发布，不加入收费系统。

## 范围与入口

实现可被应用和 MCP 使用的 ManuscriptBatch 服务，辅以最小真实可用的调用入口（先本地 CLI/API 再按现有交互规范提供 UI）。它接受多个已有 figure/canvas 标识、共享 PublicationSpec、每项 override/授权、目标输出和命名策略。不是读取任意 PDF 自动重绘，也不自动扫描磁盘和执行未授权脚本。

先读 normalize 的 B0/contract/authorize/compare/rollback 机制、原 ExportJob 的命名与取消、来源解析和新 RenderPlan。不要另造一个平行渲染器或把原图所有文字压成同字号。

## 实施

1. 定义不可变 BatchSpec 和 BatchPlan：每项来源/输入 hash、预期变化、权限、资源预算与 output，共享规范的内容身份固定。沿用每项现有 ExportRequest/ExportJob，不把 batch 塞成第三个 scope。
2. 预检只读取，列出不可用源、字体缺失、无脚本 opaque PDF 无法改字体、命名冲突、估计资源。单项失败不使其他项消失。
3. 对用户授权的科学语义修改复用 normalize；只要求最小字号就只修低于阈值的文字。不能以批量之名扩大修改白名单、反复执行脚本获得新数据或静默挪动全部 axes。
4. 每项 freeze source → normalize/validate → RenderPlan → 当前输出链 → artifact check → publish。规范化失败恢复该项 B0；已成功的其他项照实保留。不承诺整批原子事务。
5. 控制并发和峰值内存：批量调度共享 render process budget，不能给每项各自启动无限 PDFium 池。取消只影响尚可取消的项，已提交的项保留明确状态。
6. 复用全局名字预留/覆盖政策，预先展示名称；不得用列表顺序临时覆盖用户文件。各项状态 pending/running/done/partial/failed/cancelled 有明确聚合规则。
7. 恢复/幂等基于内容和规范/字体/后端身份。已有结果必须校验真实 artifact hash 和验收版本后才可复用，不能凭文件存在跳过。源改变只重新处理受影响项；不得误用另一个 project 的同名缓存。
8. 聚合 consistency report：字体角色/实际 face、图幅、字号下限、线宽、色彩政策等以已支持事实为准。不同 figure 合理的数学/CJK 字体不机械算不一致；unknown 单列。Trace 可从失败项定位到节点。

## UI / MCP

最小页面只需清晰的批次摘要、每项状态/问题、取消与已交付结果入口；沿用现有组件和错误码。MCP 只触发授权范围内任务，返回有限结果摘要与本地诊断引用，不把整段科研数据嵌入响应。不要做账号、云同步或支付墙。

## 必需验收

12 项混合语料：科学可编辑图、重复来源不同 override、opaque PDF、native PNG、缺字体、低 PPI、名称冲突、规范化超预算、处理中取消、部分报告失败。验证成功项真实输出且各自同快照，失败项不改原件，未授权属性和批次外资产完全不变；重试、进程恢复、修改一项后的增量编译都有真实测试。批量报告不可把 10 成功+2 未核验写成全部通过。

按 `04_HANDOFF_TEMPLATE.md` 交接；未运行保持 not_run，不以核心通过推断扩展通过。


---

<!-- 原文件：extensions/E02_scoped_svg_backend.md -->

# E02_scoped_svg_backend · 范围明确的 SVG 后端与格式能力扩展

**这是核心 R00–R16 之后的独立扩展，不是现有替换验收的偷换或前置条件。**

继续遵守 `00_MASTER_PROMPT.md`，阅读当前 AGENTS、已有阶段证据及相关源码。此任务要求实际代码、测试和可运行入口；不自动推送/合并/发布，不加入收费系统。

## 先确定可兑现范围

核心既有 Matplotlib 原图 SVG 直出保持不变。新的画布 SVG 只为明确受支持的 IR 和输入类型开放。ImportedPage 不包含内部可编辑路径；pikepdf/PDFium 组合不自动提供任意 PDF 到语义完整 SVG 的转换。

建立逐节点/逐输入 capability：原生 Path/Group/Clip/Image/ShapedText 的支持；原始 worker SVG 是否与同次科学快照配对；opaque PDF 无可验证矢量转换时的 unsupported。不得用 `<image href="data:image/png...">` 包一整页再声明纯矢量。

## 实施

- 为受支持 IR 编写 SVG writer：明确 width/height/viewBox 物理单位、坐标、路径、fill rule、stroke/dash/cap、transform 顺序、clipPath、分组 opacity、资源命名和节点 ID。
- ShapedText 必须消费已决定的 glyph positions。若可依法提供 exact font 且浏览器结果可验证，可输出明确定位文本；如选择 glyph outline 模式，标明文字已转轮廓、不可把它报告为保留可检索文字/嵌入字体。逻辑标签/无障碍 metadata 不是实际可编辑文字的替代证明。
- 支持同次 worker SVG 作为来源时，正确处理嵌套 viewBox、ID 冲突/资源引用、clip、字体和外部资源；不得因相同文件名混入上一个 override 的 SVG。
- opaque PDF 源要么明确不可用，要么用户显式允许局部栅格降级且清楚标 mixed/raster、effective PPI。不得静默联网转换或增加未锁版本外部 CLI。
- XML 必须正确转义，限制 DTD/entities/外部 href、脚本、事件属性、CSS url、远程字体；输入不可信 SVG 需受控解析/清理并明确可能不支持的结构。输出不可泄露本地路径。
- 只在 capability 验证支持时扩展既有 allowed_formats/前端可用性，不在 backend、HTTP、MCP 分别写三份格式规则。Unsupported 逐项失败，PDF/PNG/TIFF 可照常交付。

## 验收

浏览器独立渲染成品 SVG，对照同 IR canonical PDF 的真实几何/文字位置与容差像素；测试 nested transforms/clip/资源同名/group opacity、缺字、字体不可嵌、原生位图与 opaque PDF。不同 carrier 的抗锯齿不要求逐字节一致，但 geometry、文本政策和降级事实必须一致。

检查实际成品中矢量/位图/轮廓文本，不能只测 XML 可解析。加入恶意外链/DTD/脚本资源、错误 viewBox 和 bbox 的负例。

## 不包含

本阶段不新增任意画布 EPS：EPS 的透明度/字体/渲染能力需要独立方案和验收，不是 SVG writer 的顺手功能。PDF round-trip、完整 PDF→SVG parser、PDF/X/CMYK 同样不在此范围。不能把未实现边界写进宣传功能表。

按 `04_HANDOFF_TEMPLATE.md` 交接；未运行保持 not_run，不以核心通过推断扩展通过。


---

<!-- 原文件：03_CI_BLUEPRINT.md -->

# CI 设计：在现有门禁里验证保真、真实性与新增价值

本文件是实施规范，不是已执行结果。具体 job ID 和锁文件从当前 checkout 读取。不要修改 required contexts 的三个稳定名字；若新增 job，同步 `needs`、`--required`、矩阵预期与工作流测试，避免闭集失配。

## 1. 分层执行

| 事件/层 | 必须验证 | 不应该做 |
|---|---|---|
| 普通 PR 快线 | IR/Plan/schema、字体政策与依赖边界、纯函数、核心几何、短真实导出、负例 smoke、前端契约 | 每个 PR 都重建所有桌面包；把未跑重型检查伪报为已通过 |
| full-ci PR / merge_group | 快线 + 跨平台真实后端/字体/并发 + 干净 wheel/sdist + frozen sidecar 与桌面真产物 + 关键 RenderBench | allow-deferred；按单 PR SHA 代替队列组合 SHA；全 skipped 当成功 |
| push main | 保持既有落地审计与生成物一致性 | 无依据重复完整构建；把 main 审计当成此前没跑的资格 |
| nightly / lab | 全量 corpus、两渲染器交叉、长时间泄漏/取消、复杂字体/透明度/损坏 PDF、性能分布、mutation | 把 exploratory 成功冒充核心范围完成；用基线接纳 product_bug |
| release | 发布确切字节的包、许可证/SBOM、干净机器离线出图、签名后 macOS、安装升级卸载、CLI/MCP/插件同内容资格 | 用 editable 安装的成功证明用户安装包；复用别的 SHA 的资格结果 |

普通 PR 的重型整组 deferred 可按现有聚合器保留；merge_group/full-ci/release 的必需 RenderCore 检查不能 deferred、skipped 或因缺依赖降成绿灯。现有 CodeQL、CLA、安全与更新资格不能被本次替换移除。

## 2. 三层不同的 oracle

### A. 解析得到的几何/语义事实

用独立测试读取器遍历最终 PDF 的页面与 Form XObject，跟踪资源继承、CTM、clip、font/text/image 操作。期望几何来自手算/独立 fixture，不从生产 IR 变换函数反算。PDFium 或 pdfminer.six 等独立引擎可用于实际文字/几何提取，pikepdf/QPDF 可作结构检查；同一库读写本身不能单独证明语义正确。

QPDF 的语法检查、PDFium 能打开、字体资源存在，都只是有限证据。对“使用了该字体”的判断需要跟随实际绘制操作；未用资源不应造成假失败或假通过。

### B. 两轴视觉对照

先将旧后端与新后端各自产生的 PDF 用**同一个固定渲染器**栅格化，隔离“PDF 发射差异”。再把同一个 PDF 用旧/新栅格引擎栅格化，隔离“rasterizer 差异”。避免直接比较两条端到端链路后把全部差异都归因于一个模块。

新正式算法在 `scripts/ci/pixelcompare.py` 中增加版本化的 RGBA 模式/判据；保留旧灰度模式以免无声改变全仓已有基线。增加黑底、白底合成差异，检查 alpha 和等亮度异色。PNG/TIFF 同源测试应比较独立解码后的像素，明确 straight/premultiplied alpha，不比较压缩字节是否相同。

不对不同字体文件承诺像素一致。字体迁移的批准差异必须有明确的字体哈希、换行/基线报告和人工审查记录，不能自动 update-baseline。

### C. 故障注入与变异

必须存在以下“应该红”的产物：

- 故意改 MediaBox 或 /Rotate /UserUnit，使尺寸或布局错误。
- 删除 FontFile 或篡改 CID→GID 映射；删除 ToUnicode；视觉可能正常但提取错误。
- 添一个未使用字体资源：不应被当成实际使用字体；相反嵌套 Form 真正使用的字体不能漏掉。
- 删除 clip；把整体组透明误写成逐对象透明；交换 paint order。
- 把图整体画成 Image XObject；仍命名 `.pdf`，不能通过纯矢量要求。
- PNG 的 pHYs 或 TIFF 的分辨率标签故意与像素/规格冲突。
- 所有 RGB 的灰度近似相同但色相替换，或只改变 alpha。
- 产物写完被篡改；报告与 artifact hash 不匹配。
- required fixture 为空、无测试被选择、报告缺失/过期、缺独立检查库、校验脚本异常。
- 取消恰好与提交点竞态；源图两个实例不同 overrides；同名并发导出与文件锁。

验证器本身必须经过这些反例，而不是只有正常图全绿。

## 3. 测试语料与基线

优先沿用 `scripts/ci/compat_corpus.py` 的设计：人工意图 manifest、版本矩阵与观测 baseline 分离；`product_bug` 不得成为发布豁免。RenderCore 独有场景可设独立 corpus schema，但共享验证、报告和分类机制，不再造不兼容的 PASS/FAIL 平台。

每个 fixture 记录 ID、生成来源/许可证、必需能力、期望事实、可能有意差异、对应需求、适用系统与输出格式。推荐三档：must、expected、exploratory。默认字体上的 must 不允许因机器缺字体而 skip；正式包应保证其离线可用。

旧 PyMuPDF 只存在迁移基线生成的隔离环境，固定旧 SHA 与版本。预生成 PDF/PNG 也要有合法分发来源。最终常规测试和发行包不依赖旧库；保留 git 历史不等于重新打包旧依赖。

## 4. 性能与资源

记录冷启动/热启动、单图/多面板、同源重复实例、CJK 子集、透明页、600/1200 DPI、并发任务的墙钟时间、峰值 RSS、子进程与打开文件、包体、缓存命中率。中位数和尾延迟分别报告，保留样本数、runner、OS、CPU、内存和依赖身份。

阈值先测当前基线与机器噪声，再以评审后的预算入库；不是事先拍一个“快 50%”。硬性错误如内存超限未拦截、取消后泄漏进程、固定小页面就崩溃不需要等性能阈值才拦。

RasterBuffer 的上限按实际像素、通道、stride、中间副本和并发数估计；不要只限制 PPI。大页即使合法 1200 DPI 也可能耗尽内存。队列背压、超时与回收应进入压力测试。

## 5. 构建和缓存

- 后端/字体变化不要求源分支重新提交 canvas.html；沿用插件候选构建和 plugin-stable 通道。
- 缓存键含 OS/arch/Python ABI、依赖锁哈希、字体资源锁哈希、IR/schema/build identity、构建输入哈希。只缓存可再生依赖或完整性可验证产物，不缓存无来源的测试通过结果。
- 安装包资格使用精确待发布字节；签名可能改变字节身份，应区分未签名候选身份与最终发布身份，最终冒烟针对签名后的包。
- 外部 PR 不接触签名密钥，不在有凭据的常驻 self-hosted 环境直接执行不受信任输入；受信任重型环境保持最小权限。
- fontTools/uharfbuzz/pikepdf/PDFium/Pillow 的 runtime 文件、动态库、数据、NOTICE 必须按发行产物扫描，不能只扫 pyproject 的字符串。
- Pillow 若成为应用路径的解码/编码依赖，修正 PyInstaller 的 PIL 排除及相关测试；不得随手取消 NumPy/Matplotlib/科学栈的边界。

## 6. 结果格式

新建或扩展一个机器可读的验收汇总，建议字段：

```json
{
  "schema_version": 1,
  "source_sha": "actual-checkout-sha",
  "event": "merge_group",
  "build_identity": {},
  "font_identity": {},
  "expected_cases": [],
  "executed_cases": [],
  "requirements": [
    {"id": "RC-001", "status": "not_run", "evidence": [], "reason": null}
  ],
  "artifact_hashes": {},
  "regressions": [],
  "improvements": [],
  "unverified": [],
  "verdict": "not_run"
}
```

具体 case ID 可来自 corpus，不能只记录统计总数。汇总器验证 expected/executed 闭集、schema、SHA、身份、必要产物存在和哈希；丢报告、空报告、重复 ID、过期报告、未知状态都应失败。CLI 若声明完成却 exit 0 但没有证据，也应失败。

## 7. 不声称“绝对正确”

CI 的产出是“在明确输入范围和测试预算下，比旧实现保留了哪些能力、增加了哪些证据，并且能抓住哪些故障”。它不能证明所有 PDF、所有字体、所有未来平台都永远正确。发行页面和 Proof 用语必须保留这个边界。


---

<!-- 原文件：02_ACCEPTANCE_MATRIX.md -->

# RenderCore 逐项验收矩阵

共 114 条要求：核心 110 条，独立扩展 4 条。**本包初始状态全部为 not_run。** 这些是待实现的验收要求，不是本次审计运行过的测试结果。

编码 Agent 应将这些要求映射到当前仓库中的实际测试与产物；不必为每项机械创建一个测试文件，一条高质量测试可满足多个相关要求，但证据必须可定位。新增实现边界/安全事实时应扩展此表。

状态更新必须附 source SHA、命令与退出码、case IDs、日志和实际产物。原提案中的“无比有价值”用具体能力改善和可观察反例验收，不声称任意输入绝对正确。

## 兼容门面与范围

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-001<br>R00,R09,R12 | 每个 pdfbackend 导出项均有实际迁移实现和契约测试 | 将 __all__、调用方和 ledger 逐项对照；执行真实输入/异常测试 | 漏 original_tiff 或 missing_glyphs 不能被总通过掩盖 | not_run |
| RC-002<br>R00,R09,R12 | Canvas 的 place/save_pdf/save_png/save_tiff/size_pt/close/context manager 保持契约 | 同实例多格式保存和重复关闭/错误关闭的测试 | save_png 重新解析另一份来源 | not_run |
| RC-003<br>R00,R09,R12 | 原图和画布的尺寸及变换语义隔离 | 同一源改变画布缩放/裁切后导 original，观察原件尺寸与画面 | original 载入 canvas x/y/w/h | not_run |
| RC-004<br>R00,R09,R12 | 所有当前必须 annotation 与 legacy 字段保持语义 | 箭头端型、shape-line 缺省、brace、圆角、文字样例真实出图 | 把旧 head=both 解释为单箭头 | not_run |
| RC-005<br>R00,R09,R12 | 没有可兑现的输出路径时返回明确格式失败 | canvas EPS、无脚本原图 EPS、opaque PDF SVG 逐项验收 | 栅格装进 EPS/SVG 后标纯矢量 | not_run |
| RC-006<br>R00,R09,R12 | 已有科学 worker SVG/EPS 直出仍可用 | MCP 与合法原图调用实际导出并检查最终文件 | 为统一 IR 删除科学直出格式 | not_run |

## IR 与渲染计划

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-007<br>R02,R03 | IR/Plan 不泄漏 native 对象、Flask 上下文或科学栈 | 无上述依赖环境中 import/序列化/往返测试 | 模型 import matplotlib | not_run |
| RC-008<br>R02,R03 | 所有长度、矩阵、坐标原点和顺序有明确一致契约 | 解析式 transform fixture 与前端对拍 | PDF 出口重复 y 翻转 | not_run |
| RC-009<br>R02,R03 | paint order、hidden 和同层稳定顺序保持 | 部分重叠对象和隐藏对象的最终像素/结构 | 按 ID 排序覆盖真实 z-order | not_run |
| RC-010<br>R02,R03 | clip 空间、fill rule、stroke 与路径闭合可验证 | 嵌套 clip/洞/未闭合路径 fixture | even-odd 与 nonzero 混用 | not_run |
| RC-011<br>R02,R03 | Capabilities 按操作/格式/scope 有 native/rasterized/unsupported 和理由 | 能力表与真实输出交叉测试 | 未实现 backend 对外声明 native | not_run |
| RC-012<br>R02,R03 | 非法数字/尺寸/矩阵/资源被安全拒绝 | NaN/Inf/负尺寸/坏引用输入测试 | NaN 进入 PDF 内容流 | not_run |
| RC-013<br>R02,R03 | ImportedPage 是源页而非虚构完整科学图 IR | opaque PDF 返回有限语义和 unknown 内部映射 | 为没有 gid 的源文字编造 axes 身份 | not_run |
| RC-014<br>R02,R03 | 一个作业冻结来源字节及规范/override 身份 | 渲染中源文件更改和规范更新竞态测试 | 输出后半段读取新源字节 | not_run |
| RC-015<br>R02,R03 | 同图两实例不同 override 不串用临时文件 | 并发/同画布两实例真图比较 | 以 stem 单独命名作业内中间文件 | not_run |
| RC-016<br>R02,R03 | 不同项目和作业没有共享覆盖源文件 | 项目切换、双窗口、同名源同时导出 | 第二项覆盖第一项未读完的 PDF | not_run |
| RC-017<br>R02,R03 | runtime asset 使用当次权威 worker，不用过期缓存替代 | 杀 worker 后仍有旧 materialized cache 的负例 | 缺 worker 时偷偷输出旧缓存 | not_run |
| RC-018<br>R02,R03 | RenderPlan 复用科学 normalize 的修改权限和预算 | B0 保护属性、最大修复轮数和 rollback 测试 | 导出时无授权改 axes/data | not_run |
| RC-019<br>R02,R03 | PDF-only 原图无 override 不强制重跑科学脚本 | 运行次数与源 artifact 身份同时断言 | 每次导出重新执行随机数据脚本 | not_run |
| RC-020<br>R02,R03 | EPS 与同作业其他格式确实共用科学快照 | 带随机/状态依赖脚本检验多格式数据身份 | 仅检查 rerender 参数而实际数据不同 | not_run |

## 字体政策与排版

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-021<br>R01,R04,R05,R06 | 默认字体资源离线可用、许可/来源/哈希可核验 | 字体 policy ADR、资源 allowlist、干净安装包测试 | 从 MuPDF 提取字体后伪装自有资源 | not_run |
| RC-022<br>R01,R04,R05,R06 | 专有系统字体不被随意下载或重分发 | 包内字体扫描与合法资源闭集比对 | 把 Times New Roman 放进安装包 | not_run |
| RC-023<br>R01,R04,R05,R06 | 字体身份包含 bytes/face/style/variation 而非 family 名 | 同名不同字体和 TTC 两个 face 的测试 | codepoint-only 或 family-only 缓存串字形 | not_run |
| RC-024<br>R01,R04,R05,R06 | fallback 按字体真实覆盖和 cluster 正确处理 | 中英/Greek/组合符号/缺字 fixture | 一个 cluster 被拆到不兼容的两张字体 | not_run |
| RC-025<br>R01,R04,R05,R06 | 旧 primary/cjk/fallback/missing 分层有明确兼容迁移 | Python/TS 同源向量与真实字体 oracle 比较 | 前端 coverage 与打包字体不匹配 | not_run |
| RC-026<br>R01,R04,R05,R06 | coverage 的 Unicode 范围限制明确而非假定全覆盖 | BMP/SMP 及范围外码位测试 | 范围外字符被默认判 drawable | not_run |
| RC-027<br>R01,R04,R05,R06 | HarfBuzz 排版结果保留 glyph/cluster/Unicode/offset | 连字、组合标记、方向 run 和位移测试 | glyph ID 直接当 Unicode 编码 | not_run |
| RC-028<br>R01,R04,R05,R06 | 换行、bidi/itemization、fallback 的责任不遗漏 | 超长词、中英文、RTL 支持边界负例 | 调用 shape 后宣称完整 Unicode 排版 | not_run |
| RC-029<br>R01,R04,R05,R06 | 富文本上下标、行距/基线/padding/underline 有对拍 | 旧 richtext fixture 与新真实 PDF 文字几何 | 上标按正文尺寸量宽 | not_run |
| RC-030<br>R01,R04,R05,R06 | 字体选择与度量/绘制使用同一 shaped plan | measure 与 PDF 实际 glyph positions 比较 | 写入器再次 drawString 自行排版 | not_run |
| RC-031<br>R01,R04,R05,R06 | 浏览器权威预览与导出字体身份一致或明示差异 | 实际桌面/Web 字体与渲染结果对照 | CSS system serif 冒充后端固定 face | not_run |
| RC-032<br>R01,R04,R05,R06 | 异步文字预览不会让旧 revision 覆盖新内容 | 快速输入/切项目交错返回端到端测试 | 晚到的旧 layout 覆盖新 text | not_run |
| RC-033<br>R01,R04,R05,R06 | Matplotlib 图内文字与画布排版责任保持区别 | 图内字体 override 经原 worker 并检查真实 face | 尝试通过 ImportedPage 元数据改内部字体 | not_run |
| RC-034<br>R01,R04,R05,R06 | 子集 glyph 重映射、字宽和编码正确 | 提取实际 font program 与实际输出文字并独立解码 | subset 后继续引用旧 GID | not_run |
| RC-035<br>R01,R04,R05,R06 | PDF 文字为真实可检索文字，不以轮廓冒充 | 文字提取 + font resources + 最终像素三重测试 | 整段文字转换 path 然后报告 embed pass | not_run |
| RC-036<br>R01,R04,R05,R06 | ToUnicode/cluster/必要 ActualText 与逻辑文字匹配 | 连字/非 BMP/组合字符提取比较 | 只存在 ToUnicode 但映射错误 | not_run |
| RC-037<br>R01,R04,R05,R06 | 不支持的字体格式/脚本显式报告边界 | CFF/variable/color font 等能力表反例 | 静默换系统字体并报告匹配 | not_run |

## PDF 合成

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-038<br>R07 | 导入页 MediaBox/CropBox 非零原点正确 | 非零页盒 fixture 的解析式坐标+真实渲染 | crop 原点当作 0 | not_run |
| RC-039<br>R07 | 源页 Rotate 与 UserUnit 仅正确应用一次 | 四档 Rotate 与 UserUnit fixture | 重复应用旋转或忽略 UserUnit | not_run |
| RC-040<br>R07 | 镜像后仍保留导入页真实矢量/文字 | 结构读取 + flip 前后对应坐标像素 | flip 触发整页 Image XObject | not_run |
| RC-041<br>R07 | 面板 opacity 是正确整体透明，不是逐笔 alpha | 内部重叠源图的 group opacity 解析式像素 | 重叠区域被重复乘 alpha | not_run |
| RC-042<br>R07 | 透明度与零值处理正确 | opacity=0、fill_opacity=0、1 和中间值测试 | 使用 or 1.0 把零变一 | not_run |
| RC-043<br>R07 | 多个 PDF 资源同名和重复引用不冲突 | 两图 F1/X1 同名不同内容 fixture | 按资源名称错误去重 | not_run |
| RC-044<br>R07 | 同一源的透明/镜像/裁切组合语义正确 | 组合矩阵而非只测单开关 | 镜像发生在错误坐标空间 | not_run |
| RC-045<br>R07 | 白底与透明画布保持明确规则 | 透明/非透明输出在黑白底独立合成 | 透明背景被无意烘焙成白色 | not_run |
| RC-046<br>R07 | 不可信或异常 PDF 的交互/资源有明确处理策略 | 嵌入动作/附件/密码/损坏资源等测试 | 打开源页自动执行动作 | not_run |

## 光栅与原图写回

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-047<br>R08,R09 | PDFium 全部调用符合进程内串行策略 | 并发 probe/preview/export/inspect 压力测试 | 不同文档在两线程并行调用 native PDFium | not_run |
| RC-048<br>R08,R09 | render child 属于应用运行时而非用户科学环境 | 不含新 PDF 包的用户 .venv 端到端测试 | 往用户 .venv 偷装 pikepdf | not_run |
| RC-049<br>R08,R09 | 冻结程序正确启动子进程而不重复弹 GUI | Windows spawn/macOS sidecar 真包测试 | sys.executable -m 在 frozen app 错启动 GUI | not_run |
| RC-050<br>R08,R09 | native 对象显式释放并有 timeout/crash 恢复 | 长跑、取消、坏 PDF、child 终止压力测试 | 共享已关闭 page/bitmap native handle | not_run |
| RC-051<br>R08,R09 | RasterBuffer 通道、stride、alpha 语义正确 | padding stride/灰度/RGBA/透明边缘 fixture | 将 premultiplied 当 straight alpha | not_run |
| RC-052<br>R08,R09 | PDF/PNG/TIFF 由同一 canonical PDF 状态生成 | 同时导出后重新光栅 PDF 比较 | PNG 使用另一个 layout 引擎 | not_run |
| RC-053<br>R08,R09 | 同参数 PNG/TIFF 解码像素逐个相同 | 独立解码两文件比较完整 RGBA | TIFF 忽略 alpha 或 stride | not_run |
| RC-054<br>R08,R09 | 原图 PNG 源逐字节复制 | 含特殊 metadata 的 PNG 复制 hash 对照 | 重编码造成 metadata 丢失 | not_run |
| RC-055<br>R08,R09 | JPEG 等原图转码保留 native pixel grid | 高/低请求 DPI 下 px 不变 | 按 600 dpi 重采样原图 | not_run |
| RC-056<br>R08,R09 | 原图 PDF 仅复制第一页面且不应用画布变换 | 多页源与画布改变后独立检查 | 多页全带出或嵌入页面被裁 | not_run |
| RC-057<br>R08,R09 | 未知密度 TIFF 不伪造绝对分辨率 | 读 ResolutionUnit/分辨率 tags | 把 assumed dpi 写为源声明 | not_run |
| RC-058<br>R08,R09 | 标注写回使用同一几何并保持原件事务 | 原件保存、注释 PDF 与伴随 PNG 内容对拍 | 底层直接覆盖原始 PDF | not_run |
| RC-059<br>R08,R09 | 写回失败/冲突/取消不会破坏用户原件 | expected_mtime/文件锁/磁盘失败真实故障注入 | 失败后只剩半文件 | not_run |
| RC-060<br>R08,R09 | 数字签名和加密处理不被虚假承诺 | 签名或加密 fixture 的拒绝/授权路径测试 | 修改后报告原数字签名仍有效 | not_run |
| RC-061<br>R08,R09 | cache 内容身份、并发发布和旧版本失效正确 | 改 backend/font 但内容路径不变、Windows 占用测试 | mtime 当唯一 cache key | not_run |
| RC-062<br>R08,R09 | 像素/内存/队列预算与背压可执行 | 超大页、高 DPI、多作业负载测试 | 耗尽内存导致父应用退出 | not_run |

## 产物检查与 Proof

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-063<br>R10,R11,R12 | 检查重新读取已封口 staging 文件 | 改文件但不改 IR 的负例 | 仅回传计划里的宽高 | not_run |
| RC-064<br>R10,R11,R12 | 页面真实尺寸/DPI 标签而非请求数值被验证 | 误改 PDF 页盒、PNG pHYs、TIFF tags | 请求 600 就报告实测 600 | not_run |
| RC-065<br>R10,R11,R12 | 字体资源声明与实际使用分开 | 未使用 F1+Form 内实际 F2 的 fixture | 只扫描顶层 Resources | not_run |
| RC-066<br>R10,R11,R12 | 字体存在/嵌入/子集/可搜索为不同事实 | 有名无 FontFile、有 ToUnicode 错映射等负例 | 看到 ABCDEF+ 前缀就判 subset 正确 | not_run |
| RC-067<br>R10,R11,R12 | vector/mixed/raster/unknown 与载体分别报告 | 全 bitmap PDF、混合 PDF、纯线图 | 所有 PDF 输出 vector=True | not_run |
| RC-068<br>R10,R11,R12 | 有效图像 PPI 从像素与累计 CTM 实测 | 缩放/shear/clip 低密度图像 fixture | 输出 TIFF 600dpi 替代 PDF 内图像有效ppi | not_run |
| RC-069<br>R10,R11,R12 | 裁切检查注明实际观察范围和 intentional clip | 已知 annotation overflow 与 opaque 源未知对比 | 碰不到裁切就声称所有内容 no clipping | not_run |
| RC-070<br>R10,R11,R12 | 未知必需规则不能成为绿色 ready | 不支持字体/资源/剪裁判断的严格 profile | None/unknown 按 truthy 默认通过 | not_run |
| RC-071<br>R10,R11,R12 | 规则权威与已有 profile/preflight/normalize 不分叉 | 同规范ID阈值两入口对拍 | 新验证器复制一份过时 Nature 阈值 | not_run |
| RC-072<br>R10,R11,R12 | 深度/资源循环/解码超限被安全处理 | 恶意递归 Form 或超大资源 fixture | 无限递归或预算耗尽后判 pass | not_run |
| RC-073<br>R10,R11,R12 | 强制产物检查在提交点之前完成 | 不合格 staging 不出现在用户导出目录 | 使用发布后的 report callback 阻挡产物 | not_run |
| RC-074<br>R10,R11,R12 | 客户端自报 proof 不能伪造服务端验收 | 客户端 errors=0 搭配坏文件 | 把前端 JSON 直接当 Publication Proof | not_run |

## 身份与诊断

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-075<br>R11 | semantic/render/artifact/run 四种身份分离 | 同源不同run和字体版本变化测试 | run UUID 进入语义 hash | not_run |
| RC-076<br>R11 | render fingerprint 包含所有已知影响输出的资源身份 | 改 face index/flags/DPI/backend build | 同名新字体仍命中旧缓存 | not_run |
| RC-077<br>R11 | 最终 artifact hash 与发布的真实字节一致 | 封口后metadata被改及 sidecar 配对测试 | 把最终hash再写入PDF导致自引用 | not_run |
| RC-078<br>R11 | 可复现性分 semantic/visual/byte 且实际重放 | 同环境重复、跨OS固定字体与不同字体实验 | hash相同就断言bytes必然相同 | not_run |
| RC-079<br>R11 | node ID 区分同资产实例并保持稳定关系 | 同图多次引用及插入其他对象测试 | 用PDF object number作为长期语义ID | not_run |
| RC-080<br>R11 | Trace planned/observed 分开且有规模上限 | 节点超限、资源缺失与裁切定位测试 | 无限记录全文文字/路径 | not_run |
| RC-081<br>R11 | 公开 Manifest/Trace/诊断不泄露路径或科研内容 | 真实含中文用户名/文本的脱敏测试 | 绝对字体路径进入可公开报告 | not_run |
| RC-082<br>R11 | PDF metadata 不成为执行/文件读取授权 | 恶意source path/scripts metadata | 打开PDF自动运行来源脚本 | not_run |

## 事务与跨入口

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-083<br>R12 | 保留部分成功、取消提交点与现有状态机 | PDF成功PNG失败、提交前后取消竞态 | partial 被写成 done | not_run |
| RC-084<br>R12 | 报告/sidecar 命名遵守预留与覆盖策略 | 同名并发 ask/rename/replace 和报告失败 | 图改名而报告仍覆盖旧同名文件 | not_run |
| RC-085<br>R12 | 不冒充多文件整批原子提交 | 发布中第2文件失败时观察状态/已成功文件 | 承诺全部回滚但已发布文件仍存在 | not_run |
| RC-086<br>R12 | HTTP 同步/异步/SSE 的实际新链路跑通 | 真实服务API请求与最终字节核验 | 单元mock全部返回ok而endpoint不可用 | not_run |
| RC-087<br>R12 | MCP 与版本探针/bridge 导入闭包同步 | 干净安装版本探测+实际导出 | 旧最低版本不含新import却允许启动 | not_run |
| RC-088<br>R12 | UI 状态、错误码、双语与可访问性同步 | 新真回执端到端展示与定位 | unknown绘制绿色通过勾 | not_run |
| RC-089<br>R12 | 多Web构建与Playground能力区别明确 | 四套构建/别名资源验证 | Pyodide界面调用不存在的native后端 | not_run |
| RC-090<br>R12 | 旧响应结构兼容且 vector 迁移不误导 | 旧客户端/脚本响应消费测试 | 更改vector类型或丢失files清单 | not_run |

## CI 与发行

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-091<br>R13,R14,R15,R16 | 保持三个稳定 gate 和 required 闭集语义 | gate truth-table 与实际 workflow needs 对拍 | required job 缺失仍返回success | not_run |
| RC-092<br>R13,R14,R15,R16 | merge_group/full-ci 必须真实重型成功 | skipped/cancelled/deferred事件反例 | 允许队列重型整体deferred | not_run |
| RC-093<br>R13,R14,R15,R16 | 旧几何/文字测试迁移保留原断言意图 | test inventory与独立新oracle映射审查 | 删get_drawings只保file.exists | not_run |
| RC-094<br>R13,R14,R15,R16 | RGBA颜色/alpha比较覆盖灰度漏检 | 同灰度异色与纯alpha负例 | CI只转L后比较 | not_run |
| RC-095<br>R13,R14,R15,R16 | 写入/读取不是同源自证且存在解析式fixture | 独立reader和有预知几何的文件断言 | 检查器重用错误transform函数 | not_run |
| RC-096<br>R13,R14,R15,R16 | 双轴旧新差分不把旧缺陷当真值 | 同PDF不同raster/不同PDF同raster实验 | 发现旧bug仍按像素必须复制 | not_run |
| RC-097<br>R13,R14,R15,R16 | 关键故障注入/变异确实使资格失败 | 删字体/禁验证/错矩阵/unknown→pass等 | 仅报告覆盖率却不能抓故障 | not_run |
| RC-098<br>R13,R14,R15,R16 | 空case、全部skip、错SHA或旧报告不能绿 | 破坏结果身份与case计数的runner测试 | 默认空数组导致exit0 | not_run |
| RC-099<br>R13,R14,R15,R16 | 性能/内存/CI成本源于记录的真实测量 | hardware/version/cold-warm重复实验 | 手填速度提高30% | not_run |
| RC-100<br>R13,R14,R15,R16 | 依赖实际覆盖当前Python/OS承诺 | 具体wheel解析与支持matrix运行 | 最新wheel不支持旧OS却照常宣传 | not_run |
| RC-101<br>R13,R14,R15,R16 | PIL/native libraries/data 与PyInstaller正确打包 | 实际调用codec/fonts/PDFium而非仅import | Pillow已加依赖但PIL仍被exclude | not_run |
| RC-102<br>R13,R14,R15,R16 | 科学worker与应用runtime继续隔离 | 不含科学栈的app与原用户环境联动测试 | 广泛解除excludes让matplotlib进父应用 | not_run |
| RC-103<br>R13,R14,R15,R16 | wheel/sdist在源码目录之外真实可用 | 清PYTHONPATH且无editable/noPyMuPDF安装运行 | 只能在checkout里找到缺失资源 | not_run |
| RC-104<br>R13,R14,R15,R16 | Windows真实sidecar/CLI/NSIS链取得对应资格 | 安装/升级/卸载/中文路径测试 | 只测开发Python不测exe | not_run |
| RC-105<br>R13,R14,R15,R16 | macOS签名公证后实际.app取得对应资格 | 待发行.app调用新native库并导出 | 签名前正常就推断签后正常 | not_run |
| RC-106<br>R13,R14,R15,R16 | 插件候选/receipt与源码SHA内容对应 | 既有plugin-stable构建验证而非自动推送 | 重新提交canvas.html到源码分支 | not_run |
| RC-107<br>R13,R14,R15,R16 | 运行/依赖/native/SBOM均无退役PyMuPDF/fitz/MuPDF | 干净环境阻断import并扫描产物闭包 | 另一个包名下仍带MuPDF | not_run |
| RC-108<br>R13,R14,R15,R16 | 许可证/NOTICE和字体来源分别满足对应义务 | 实际分发清单与来源核验 | 把pikepdf误写Apache或擅改项目AGPL | not_run |
| RC-109<br>R13,R14,R15,R16 | 切换不静默回退旧库、不破坏旧工程 | 旧项目升级及新backend出错路径测试 | 新backend异常时隐藏调用旧库 | not_run |
| RC-110<br>R13,R14,R15,R16 | 最终资格报告区分实现/源码测试/发行包/已发布 | 逐项证据绑定最终SHA并审阅GateC | 尚有must not_run却说发布资格通过 | not_run |

## 批量扩展

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-111<br>E01 | 多图共享规范但保持逐项来源/授权/事务边界 | 12项混合批次、部分失败、取消、增量重试的实际产物 | 批量统一把受保护axes/data改了 | not_run |
| RC-112<br>E01 | 聚合一致性报告不掩盖unknown与合理字体差异 | CJK/math fallback、缺字、opaque PDF并列验证 | 10通过2未知报12全部ready | not_run |

## SVG扩展

| ID / 阶段 | 必须满足 | 验证方法 | 必须抓到的反例 | 初始状态 |
|---|---|---|---|---|
| RC-113<br>E02 | SVG只对受支持节点/来源开放并真实报告降级 | 最终SVG独立浏览器渲染、资源与文字策略验收 | 整页PNG包SVG却报告vector | not_run |
| RC-114<br>E02 | SVG外部资源/脚本安全及坐标能力得到独立验证 | DTD、script、remotehref、重复ID、nestedviewBox测试 | 导出SVG拉取未经授权外网资源 | not_run |



---

<!-- 原文件：04_HANDOFF_TEMPLATE.md -->

# RenderCore 阶段交接

## 身份
- 阶段：
- 当前 HEAD：
- 工作区是否包含用户原有未提交改动：
- 当前默认后端 / 测试后端：
- 依赖锁 / 字体锁 / IR schema 身份：

## 本阶段已实际实现
说明文件、接口和行为，不写“已搭好基础”代替具体实现。

## 运行证据
| 命令 | 环境/平台 | exit code | 日志/产物 | 结果 |
|---|---|---|---|---|
| | | | | pass/fail/not_run |

## 需求映射
| RC ID | 状态 | 对应测试 | 证据路径 | 限制 |
|---|---|---|---|---|

## 新旧差异
区分保留行为、批准变更、修复旧缺陷、新回归。记录相应 fixture、字体与渲染器身份。

## 未运行与阻塞
明确为什么当前环境无法验证、由哪项 CI 完成。不使用“应该通过”替代。

## 安全与许可证
新增 native 库/字体来源、NOTICE/SBOM 是否齐全；无变更也应说明。

## 下一阶段
入口文件、依赖、仍需读取的代码、可并行范围与不可并行共享文件。不要让下一会话重新做整个架构决策。


---

<!-- 原文件：05_GO_NO_GO.md -->

# 三道技术门与最终交付判定

## Gate A：选型成立（R01）

必须有真实样例同时证明：PDF 导入的 clip/旋转/镜像/透明组；中文和拉丁文字嵌入/子集/检索；PDFium 渲染与生命周期；拟选依赖在实际发行平台的冻结最小包可运行；字体来源与许可证可分发。任何一项失败，先调整适配器/字体/版本，再做大规模迁移，不把问题藏到最后。

## Gate B：允许默认切换（R15）

门面所有运行入口有映射，截至 R14 的切换前核心验收无未解决回归；正常、负例、并发、写回、缓存、HTTP/MCP、正式前端和离线资源通过；候选新后端的干净安装运行无需 PyMuPDF。适用于切换前的 must 需求均有证据；R15 退役后的扫描与 R16 最终候选资格在切换后验证，不作为循环前置条件。缺跨平台资格则不宣布跨平台默认已准备完毕。

## Gate C：允许发行（R16）

待发布的精确包通过所宣称平台资格，最终签名包也验证；常规测试和正式依赖树不再包含 PyMuPDF；SBOM、NOTICE、字体许可、支持矩阵与回滚方案齐全；新旧价值对照可复核。源码模式成功或某个开发者电脑成功不满足发行资格。

## 回滚

迁移期间可在非发行开发模式切换已记录的旧 backend，但不能在新后端失败后静默 fallback。默认切换前保留旧稳定版本的下载/升级路径。切换后回滚优先回到已验证的旧发行版本或上一份兼容配置，不把 PyMuPDF 再作为未披露依赖装回新包。

用户项目格式不应为本次内部 IR 改造做破坏性迁移；确需添加布局版本时采用可读兼容扩展、备份和显式版本规则。完整 PDF round-trip 与收费业务不进入这三道门。

## 最终报告的三个结论必须分开

- 实现完成：代码具备该行为。
- 验证完成：特定 SHA/平台/数据上的测试已通过。
- 发行完成：确切发行包的资格与发布操作已实际完成。

未经用户明确要求不要执行发布；完成 R16 可交付 release-ready 的证据，不可谎称已经向用户发版。

