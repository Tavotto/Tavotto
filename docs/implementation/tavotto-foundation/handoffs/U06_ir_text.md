# U06 · IR、源图计划和可检索文字先形成真实闭环 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U06.ir_text，两个叠栈 PR：**A** = 纯模型（Render IR / RenderPlan 编译 / 排版 / 几何 /
静态源冻结 + importgraph 守卫，不需要候选包），**B** = 字体政策 + 依赖 extra + 可检索文字写入 + 接 ExportJob +
四把独立读取器 + evidence。架构决策：[`docs/adr/0059-render-ir-and-render-plan.md`](../../../adr/0059-render-ir-and-render-plan.md)、
[`docs/adr/0060-font-policy-and-allowlist.md`](../../../adr/0060-font-policy-and-allowlist.md)。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `e04b9747`（`foundation/u02-spikes` 当时的 head，即 U02 的
PR #455；`origin/main` 当时 `8406b361`）；U02 三次追加 / rebase 后 `rebase --onto 1b95003a e04b9747`，无冲突；
结束 = 两个 PR 的 head（合并后以 `git log origin/main` 里 PR 号为准）。全部在 worktree `tavotto-wt/foundation-u06`
里做，用户主工作区一个字节没碰；候选包只装在 scratchpad 的独立 `rc-venv`（`pip install -e '.[rendercore,dev]'`），
主仓库 `.venv` 零改动。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（PyMuPDF 后端、`app.py` 的导出 produce、
`canvas_coverage.json`、前端字体栈）；候选路径 = `src/tavotto/rendercore/`（IR → RenderPlan → pdfwriter → PDF，
只由测试与 `scripts/dev/u06_evidence.py` 驱动，`app.py` 不 import 它）；拟启用能力**无**
（`plan.json` `new_default_capabilities_enabled: []`）。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`packaging/AGENTS.md`、`web/AGENTS.md`；
`docs/rules/backend/` 的 pdf-backend-boundary / export-pipeline / richtext / runtime-figure-assets /
process-boundaries / preparation-and-receipts / layout-versions-and-documents；`docs/rules/repo/same-origin-pairs.md`、
`predicate-subject.md`；ADR 0031 / 0033 / 0045 / 0053 / 0055 / 0056。复用（权威不动）：`exportreq.ExportRequest` /
`normalize` / `render_plan_ref`（身份不另造）、`exportjob.prepare / run / cancel`（作业生命周期一字不改）、
`figcapture.SourceArtifact / source_artifact_from_file`、`richtext`（三常量 + parse + interpret，同源对不动）、
`glyphplan.layer_of / plan / ranges_of / text_diagnostics`（四步顺序不动）、旧 facade 的 `_polygon_points` /
`_dash_pattern`（当 oracle 对拍）、`tests/support/importgraph.py`（加两层九条规则）、U02 的 `pdfwrite.py`
（收编为 `pdfwriter.py`）与 `fonts.py`（清单收编为 allowlist，spike 模块改成薄 shim）。

**实际代码与 API / 数据结构变更**：

| 层 | 变更 |
|---|---|
| `src/tavotto/rendercore/`（新包） | 纯模型：`ir.py`（Page / Group / Path / Image / ImportedPage / ShapedText、FontResource / FileResource、矩阵、`validate()` 12 个错误码、`CAPABILITIES` 三档能力表）、`geometry.py`（shape / arrow → Path，同源对换宿主）、`typography.py`（Face 协议、FaceSet、分层 / 量宽 / 换行 / 排版、`interpreted_pieces` 把用户原文带到 `actual_text`）、`fonts.py`（allowlist、`FontRegistry`、纯标准库 sfnt 读取）、`sources.py`（`StaticSourceResolver` / `FrozenSource` / `read_frozen`）、`plan.py`（`compile_page` / `compile_plan` / `RenderPlan`）；native 适配：`hbshaper.py`（HarfBuzz + fontTools，`require()` / `HbFace` / `HbFaceProvider`）、`pdfwriter.py`（pikepdf + fontTools 子集，受限 emitter）；入口 `job.py`（`exportjob` 的 `produce` 形状）；`fonts_allowlist.json`（13 张 OFL 脸的 sha256 / 来源 / 许可） |
| `scripts/fetch_fonts.py`（新） | 按 allowlist 的 sha256 取字体到 `src/tavotto/resources/fonts/`（`--check` 核对；缓存在 `build/fonts-cache/`） |
| `scripts/dev/u06_evidence.py`（新） | 真字体 → PDF → 四把读取器 → `evidence/u06/`（42 条核对）+ 覆盖表 diff |
| `scripts/dev/u02_spikes/fonts.py` | 改成薄 shim：三张表派生自产品 allowlist、`fetch / verify / face_path` 转发到 `scripts/fetch_fonts.py`；`render_spike` 经它重跑 52/52，`spike.pdf` 字节不变 |
| `pyproject.toml` | 可选 extra `rendercore`（候选，未默认启用）；`[tool.hatch.build] artifacts` 收回 `src/tavotto/resources/fonts/**` |
| `requirements-rendercore.txt`（新）、`.gitignore`、`.gitattributes` | extra 的钉死镜像；字体目录不进 git；evidence 的 LF / binary |
| `tests/support/importgraph.py` | 层 `rendercore_model` / `rendercore_native` + 九条规则（不进 pdfbackend / worker，反向也不许；模型不进 native） |
| `tests/import_architecture_baseline.json` | `hbshaper.require()` 的 `importlib.import_module(name)` 登记（目标全是第三方候选包） |
| `tests/support/fakeface.py`、`tests/support/pdfread.py`（新） | 合成脸（纯模型用例不需要候选包）；纯标准库 PDF 读取器（第四把尺子） |
| `tests/test_rendercore_*.py`（9 个新文件） | ir / geometry / typography / plan / model / fonts / writer / job / evidence |
| `tests/test_font_provenance.py` | 第四档：allowlist sha256 钉住的 OFL 字体（闭集判据 + 反证）；旧三档用例一个不删 |
| `tests/test_foundation_facade_ledger.py`、`U00_FACADE_LEDGER.json` / `.md`、`tools/generate_facade_ledger.py` | 8 个导出项加 `migration_evidence`（D07），门禁核用例真的存在；`test_font_provenance.py` 两处引用行号更新 |
| `tests/test_windows_regressions.py` | U06 evidence 的四份 JSON 进 LF 看护表 |
| `.github/workflows/foundation-u06-rendercore.yml`（新，非 required） | ubuntu 3.10 / ubuntu 3.13 / windows / macos：装 extra + 取字体 + 跑 rendercore 用例（不 skip）+ 重生成 evidence 与 git 里的比（PDF / 覆盖表逐字节，PNG 只作信息） |
| 文档 | ADR 0059 / 0060；`docs/rules/backend/rendercore.md`（新主题）；`src/tavotto/AGENTS.md` 一行 + 验证段一条；`packaging/AGENTS.md` 一段；`docs/rules/repo/same-origin-pairs.md` 两行；`README.md` U06 段；`plan.json` U06 done；本文件；`evidence/u06/`（README + 6 个文件）；PACKAGE_CONTENTS 重算 |

**关联旧要求 ID / 场景 ID**：R02 / R03（RC-007 ~ RC-020）、R04 / R05 / R06（RC-021 ~ RC-037）、R13（RC-095）。
逐条处置：

| ID | 处置 | 证据 |
|---|---|---|
| RC-007 IR 不泄漏 native / Flask / 科学栈 | 做了 | `test_rendercore_model.py`（外部名 ⊆ 标准库、零 pymupdf、屏蔽全部重包的子进程 import + 编译） |
| RC-008 长度 / 矩阵 / 原点 / 顺序 | 做了 | `test_rendercore_ir.py` 矩阵约定；`test_rendercore_plan.py` y 翻一次（数据刻意不对称） |
| RC-009 paint order / hidden / 稳定顺序 | 做了 | `test_rendercore_plan.py::test_paint_order…`、writer 的 z 序像素、evidence `overlap_is_blue` |
| RC-010 clip / fill rule / 闭合 | 部分：IR 有 `fill_rule` / `clip`、写入器写 `W n` / `W* n` / `f*`，能力交叉核对里 `clip` 真写出；**嵌套 clip / 洞的像素 fixture 归 U07** | `test_each_declared_capability…[clip]` |
| RC-011 Capabilities 三档 + 理由 | 做了 | `ir.CAPABILITIES`；`test_each_declared_capability_matches_what_the_writer_really_does[10 操作]` |
| RC-012 非法数字 / 尺寸 / 矩阵 / 资源 | 做了 | `test_validate_rejects_each_illegal_input_with_a_stable_code`（33 条）+ 每个 code 至少一条 |
| RC-013 ImportedPage 是源页 | 做了（模型层；写入归 U07） | `test_imported_page_never_claims_to_know_its_inside` |
| RC-014 冻结字节 / 竞态 | 做了 | `read_frozen` 核 hash；`test_a_frozen_source_that_changes_before_writing_fails_the_job` |
| RC-015 / 016 两实例 / 两项目不串 | 模型层做了（资源 key 含字节 hash + 语义身份；中间文件在作业私有临时目录） | `test_two_instances_with_different_execution_identity_get_two_resources`；跨项目并发真图归 U08 |
| RC-017 runtime asset 用当次 worker | 边界做了：静态解析器对 runtime 素材抛 `source_needs_execution`，不拿缓存冒充；执行侧解析器归 U08 | `test_a_panel_with_overrides_is_not_executed_by_the_static_resolver` |
| RC-018 复用 normalize 权限 / 预算 | **未做**（RenderPlan 不碰 axes / data；normalize 的接入在 U08 的执行侧解析器） | — |
| RC-019 静态源不重跑 | 做了 | `StaticSourceResolver` 不起子进程；上同 |
| RC-020 EPS 与多格式同快照 | 未涉及（EPS 仍走 worker；本切片 `eps` 能力表 unsupported 且理由指向 ADR 0046） | — |
| RC-021 默认字体离线可用、许可 / 来源 / hash | 做了（本机 + 三平台 workflow） | allowlist + `fetch_fonts.py --check` + `test_rendercore_fonts.py` |
| RC-022 专有系统字体不重分发 | 做了 | `test_packaged_fonts_are_exactly_the_allowlist` + 反证 |
| RC-023 身份含 bytes / face / style | 做了（sha256 + face 序号 + 样式；variation 显式不支持） | `test_a_same_named_copy…`、`test_a_same_named_face_with_different_bytes_is_refused` |
| RC-024 fallback 按真实覆盖、cluster 不拆到两张脸 | 做了（分层按 cmap；一个 cluster 只在一张脸里 shaping） | `test_rendercore_typography.py`、writer 的 `x̃` / `café` 用例 |
| RC-025 四层兼容迁移 | 做了：primary / cjk / missing 语义保留，fallback 明示为空；前端 coverage 与打包字体一致的判据 = evidence 表由真字体现算 | `evidence/u06/canvas_coverage.rendercore.json` + `coverage_diff.json`；切表在 U08 |
| RC-026 覆盖范围明确 | 做了（BMP + SMP 上界 0x30000，超出即 missing） | `test_coverage_table_has_no_fallback_layer…` |
| RC-027 HarfBuzz 结果保留 glyph / cluster / Unicode / offset | 做了 | `HbFace.shape` → `ir.Glyph`；writer 的 advance / offset / Ts 用例 |
| RC-028 换行 / bidi / itemization 责任 | 首轮范围内做了（换行同旧算法；itemization 按分层）；**bidi / 竖排显式不支持** | ADR 0060 §6 |
| RC-029 富文本上下标对拍 | 做了（size / rise 与 richtext 常量；上标按上标字号量宽） | `test_markup_scripts_get_the_richtext_size_and_rise`、`test_pen_position_between_runs…` |
| RC-030 度量与绘制同一 shaped plan | 做了 | `test_width_is_the_sum_of_shaped_advances…`、`test_pen_position_between_runs_equals_the_shaped_advance`、`test_widths_in_w_array…` |
| RC-031 浏览器预览与导出字体一致或明示 | **未做**（前端仍是 CSS 系统字体栈；差异明示在本文件「下一阶段」） | — |
| RC-032 异步预览 revision 保护 | **未做**（随 RC-031） | — |
| RC-033 图内文字与画布排版分开 | 做了（RenderCore 只排画布文字；面板是 ImportedPage 引用） | 结构性 |
| RC-034 子集 GID / 字宽 / 编码 | 做了 | `test_every_code_in_the_content_stream_has_a_tounicode_entry_and_the_subset_cmap_agrees` + 错 GID 负例 |
| RC-035 真文字不以轮廓冒充 | 做了 | PDFium 对象普查（文字对象 ≥ 9）+ 三把文字尺子 |
| RC-036 ToUnicode / cluster / ActualText | 做了（三种情形 + 一段一个 TJ） | `test_composed_superscript_and_multi_glyph_cluster_get_actualtext_spans`；ADR 0060 §3 读取器实测 |
| RC-037 不支持的格式显式报告 | 做了（变量 / 彩色 / CFF2 / 非 CID CFF / TTC 拒绝；缺脸 `FontsUnavailable`） | `test_font_kind_rejects_the_formats_the_writer_cannot_embed`、`test_a_missing_fonts_dir_is_a_structured_error_not_a_system_font` |
| RC-095 写入 / 读取不同源 | 做了 | `tests/support/pdfread.py` + pypdfium2 + pdfminer + poppler；几何期望手算 |

registry 220 条 `execution_status` 一条没动；enrollment 台账没有 U06 的 case（本阶段没有经产品入口的场景用例；
RenderBench 实例的登记留给 U08 接 facade 之后）。

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `PYTHONPATH=$WT/src pytest tests/test_rendercore_ir.py tests/test_rendercore_typography.py tests/test_rendercore_plan.py tests/test_rendercore_geometry.py tests/test_rendercore_model.py tests/test_import_architecture.py tests/test_docs_references.py`（主仓库 `.venv`，**PR A**） | 同上 | 0 | 140 通过（合成脸，不需要候选包） |
| PR A 变异反证 10 条（NaN 检查 / 按 id 排序 / y 翻两次 / fallback=cjk / `import pymupdf` / 不核 hash / 不判奇异矩阵 / hidden 不丢 / 帽半宽 / 原文对齐） | 同上 | 每条非零 | 第一版「y 翻两次」绿——测试数据 y + h == H − y − h，改成不对称后红（ADR 0059 §3） |
| `PYTHONPATH=$WT/src pytest`（全量，**PR A** 树 `a254638f`，主仓库 `.venv`，后台 + 日志） | 同上 | 1 | 5225 通过 / 53 skip / **4 红**：`#240 ctrl-c`、`test_run_messages_only_stderr`（#452 本机配置）、`test_mcp_normalize` 两条像素（#452），全是已知本机噪音；与本 PR 无关 |
| `<rc-venv>/bin/python -m pytest tests/test_rendercore_*.py tests/test_font_provenance.py tests/test_import_architecture.py -rs`（**PR B**，候选包 + 字体） | 同上 | 0 | **220 通过 / 0 skip** |
| 同一批用例在主仓库 `.venv`（没装候选包） | 同上 | 0 | 169 通过 / **38 skip**（理由：候选包未装 ×37、fontTools 未装 ×1——skip 不是绿，真跑在 rc-venv 与 workflow） |
| `scripts/fetch_fonts.py` → `--check` | 同上；13 个字体文件 + 2 份 OFL 全文落到 `src/tavotto/resources/fonts/` | 0 / 0 | 每个文件 sha256 与 allowlist 一致 |
| `scripts/dev/u06_evidence.py --out evidence/u06` | 同上；`u06.pdf` 14 037 B、`u06_pdfium.png` 851×454、`report.json`、两份覆盖表 | 0 | **42/42**：确定性字节、页盒、四张脸两种字体程序、五行文字三层读数（stdlib / PDFium / pdfminer / poppler）、hidden 不在、每个 code 有 ToUnicode、9 个文字对象、5 个像素采样、覆盖交集 / 科学矩阵零方框 |
| `PYTHONPATH=scripts:src <rc-venv> -m dev.u02_spikes.render_spike`（经薄 shim） | 同上 | 0 | 52/52，`spike.pdf` sha256 `12966287d846…` 与 git 里逐字节相同（shim 没改变任何字节） |
| PR B 变异反证 9 条（注册表按文件名收 / 写入器按 face_id 找 + 不核身份 / Span 前不 flush / Span 里两个 TJ / 缺字换 CJK 脸 / 不核冻结 hash / imported_page 假报 native / ToUnicode 少三条 / 透明组不建组） | 同上 | 每条非零 | 「按 face_id 找」单独变异绿：`face.resource != resource` 的第二道核对还在；两道一起去掉才红——两道是有意冗余，ADR 0060 §5 记了 |
| `PYTHONPATH=$WT/src pytest`（全量，**PR B** 树 `9635536c`，主仓库 `.venv`，后台 + 日志，28 分钟） | 同上 | 1 | **5286 通过 / 91 skip / 5 红**：4 条是已知本机噪音（同上），**1 条真回归**——`test_tutorial.py::test_pyproject_keeps_resources_inside_the_wheel_and_sdist` 用子串 `"resources" not in ignore` 守「教程资源不被 gitignore」，本 PR 新加的 `/src/tavotto/resources/fonts/` 撞上；判据改成按**规则**判（resources 下被 ignore 的每条必须有 artifacts 收回、教程项目不许被挡），去掉 artifacts 那条变异红。91 skip 里 38 条是 rendercore 用例（主 `.venv` 没装候选包），真跑在 rc-venv |
| Linux / Windows / 3.10 | `foundation-u06-rendercore.yml`（PR 上随 rendercore 文件变动自动触发） | 见 PR 正文 | run 号与四条腿结论填在 PR 正文与下面「其它目标」 |
| 前端 `pnpm test && pnpm build` | — | — | **not_run**：本阶段没有改 `web/src`（PR C 未做） |

**本切片的正例、负例、旧行为回归**：正例 = 简单页 + 形状 / 箭头 + 中英 Greek 上下标 / 组合序列的真实 PDF（evidence
42/42、writer 用例 31 条）、经真实 `exportjob.prepare / run` 的作业（PDF 原子落盘、`vector: True`、PNG `format_failed`
带结构化理由、warnings 带缺字 / cjk / hidden）；负例 = IR 33 条拒绝、丢 FontFile / 丢 ToUnicode / 错 GID 三种坏文件在
读取侧判据下红、缺字写 .notdef 并报告、同名不同字体拒绝、allowlist 外文件拒绝、字体目录缺失结构化报错、冻结后改文件
`source_changed`、带 override 的面板 `source_needs_execution`、取消不留文件、`imported_page` 在本切片 `format_failed`
（不是空框）；旧行为回归 = 产品默认路径零改动（`app.py` / `pdfbackend/` / `canvas_coverage.json` / `web/src` 一字未动），
`test_font_provenance.py` 旧三档用例原样 + 第四档，U02 用例与 spike 经 shim 字节不变，全量 pytest 只有已知噪音。

**本次是否改变 case enrollment（planned / observing / enforced / later）及理由**：**没有**。U06 的用例是切片自己的
单元 / 短链路（不经产品 HTTP / MCP 入口），不是 FO / RC 场景的资格；台账 32 条 FO 原样，`U01-S1` 仍是唯一 enforced。
RenderBench 实例不登记：facade 尚未接（U08）。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：前端（PR C 未做，`web/src` 零改动）；`Image` / `ImportedPage` 写入、透明组里的文字、栅格 / PNG /
  TIFF（U07）；`scope=original`（U08）；跨项目并发真图（U08）；Linux / Windows / 3.10 的 rendercore 用例在 PR 上由
  非 required workflow 给（结论填 PR 正文）；macOS x86_64；registry 220 条产品实例。
* 基础设施问题：主仓库 `.venv` 没装候选包，rendercore 的 38 条用例在必需矩阵里是 skip（有理由）——**skip 不是绿**，
  它们真跑的地方是 rc-venv 与 `foundation-u06-rendercore.yml`；候选栈切默认（U10）时进必需矩阵。全量 pytest 的
  4 条本机红是 #240 / #452（U01 交接已记）。
* 真正产品失败：无新增。顺带发现的**事实**（不是本阶段修的缺陷，已进 ADR 0060）：
  1. 12 张 Liberation 脸的 cmap 不完全相同（交集 2305 / 并集 2336）——旧后端「所有脸共用一张覆盖表」的假设在新
     集合上不成立，evidence 表的 primary 取交集，U08 换表时不能照抄一张脸；
  2. PDFium 对跨多个文字对象的 ActualText 段会把 ActualText 与对象自己的 ToUnicode 各吐一遍（`x̃y x̃`），一段一个
     `TJ` 才对；代价是多字形 cluster 里标记字形的 `y_offset` 不写；
  3. `<</ActualText <…>>> BDC` 少一个 `>`（U02 emitter 没踩到，因为它没写 ActualText）：PDFium 宽容、poppler 报
     Syntax Error——poppler 是唯一抓到它的尺子；
  4. PDFium 把 CID 0（.notdef）当空白：缺字在 PDFium 文字层里是空格，poppler / pdfminer / 本仓库读取器抽回原字；
  5. 新集合可画码位 61 238 → 32 608：Hangul 11 172 个不在，旧 fallback 脸（Noto Serif）画的 27 610 个其它脚本 /
     符号不在；科学文本矩阵靠 `auto` 合成零方框（原文里只有 `⁻` 谁都没有）。

**批准的字体 / 视觉差异，及未授权变更检查**：本阶段没有改任何产品输出（默认路径零改动）。会变的旧断言按 D07
逐条列在 ADR 0060 §4，**一条都没删**，替代证据写进 `U00_FACADE_LEDGER.json` 的 `migration_evidence`
（8 个导出项，门禁核用例存在）。`git diff --stat` 只含上表的文件；`LICENSE`、ruleset、`aggregate_gate.py`、
required job、默认后端、`requirements.txt`、默认 `dependencies`、`security._PUBLIC_PATHS` 一个都没动。

**评审轮次与处置**：PR A（#458）Codex 第一轮 4 条 P2，全修（`00f97dbb`）：写入器落地前 PDF 的每个操作如实
`unsupported`（带写入器的 PR B 才翻 native，`test_before_the_writer_lands_every_pdf_operation_is_declared_unsupported`
/ B 里换成 `test_u06_pdf_declares_text_and_paths_native_and_placements_unsupported`）；文字 / 面板零或负宽高、形状 /
箭头负宽高在 compile 就拒；`fill_opacity: 0` 是取值不是缺席（旧 facade 的 `or 1.0` 不照抄，U08 对拍记为有意差异）；
`validate()` 先判每段形状再读第一段 opcode。四条各一变异红。**一次失误已纠正**：修 A 的那个提交在 A 分支上
`git add -A` 时把工作区里的 13 个字体文件（当时 `.gitignore` 的字体规则还只在 B）收进了提交并推到了 PR 分支；
发现后 `git rm --cached` + 把 `.gitignore` 规则前移到 A + `--force-with-lease` 只推自己的分支覆盖，
`git ls-files` 零字体二进制，`test_repository_ships_no_font_binaries` 绿；main 从未含它们。教训进交接：叠栈里
「上层的 .gitignore 规则」在下层不生效，取字体这类会落进 `src/` 的产物要先把 ignore 规则放在最底层的 PR。

**当前可合并依据（不等于可以默认启用 / 发行）**：两个 PR 都是中高风险档（改 `src/` / `tests/` / `scripts/` /
`pyproject.toml` / `.github/` / `packaging/AGENTS.md`）→ `full-ci` + `@codex review`；ruff 两条 0；针对性 pytest 0；
变异反证逐条红（含一条「有意冗余」的说明）；全量 pytest 只有已知噪音；新 workflow 只有 `workflow_dispatch` +
paths 过滤的 `pull_request`、托管 runner、有 `timeout-minutes`、顶层 `TAVOTTO_NO_TELEMETRY=1`，
`test_merge_queue_workflows` 全过；actionlint 0。

**仍缺哪些默认启用 / 精确安装物资格**：全部。本阶段不产生任何产品资格；候选栈未默认启用；安装物里带字体这件事
只验证了 wheel `artifacts` 与 PyInstaller `datas` 的**配置**（`test_wheel_artifacts_and_gitignore_cover_the_fonts_dir`），
真 wheel / 冻结产物含字体的证据归 U10 / U11（`packaging/AGENTS.md` 写明构建机先跑 `fetch_fonts.py`）。

**下一个无阻塞阶段 / 子切片**：U07（依赖 U06.ir_text）。给 U07 / U08 的输入：

* **U07**：`ir.Image` / `ir.ImportedPage` 已在模型里（rect / crop / rotate_cw_deg / flip / opacity / page_index，
  `internal` 恒 unknown，校验含 crop 落在 [0,1]²、资源种类匹配）；`pdfwriter` 对它们抛 `UnsupportedCapability`，
  U02 的 `import_page`（`copy_foreign` + 页盒 + 旋转 + crop + 透明组包装）搬进 `_emit` 即可，改 `ir.CAPABILITIES["pdf"]`
  时 `test_each_declared_capability_matches_what_the_writer_really_does` 会要求写入器真写出；透明组的 form BBox 用
  `_page_bbox_in(ctm)`；`sources.read_frozen()` 给字节；栅格 / PNG / TIFF 用 U02 的 render child（收编时删
  `test_source_hygiene.py` 里 render_child.py 的单文件例外，并把 `scripts/dev/u02_spikes/` 与
  `foundation-u02-spikes.yml` 退役——`fonts.py` 已是 shim，`pdfwrite.py` 只剩 render_spike 在用）；
  **pikepdf vs pypdf 的裁决在 U07**（ADR 0060 §2：只影响 `pdfwriter.py` 的对象模型调用）；PDFium PNG 跨平台像素
  不同（U02 三腿实测），像素门按平台分基线。
* **U08**：facade 19 项的迁移对照表在 ADR 0059 §2；`canvas_coverage.json` 换表时 primary 取 12 张 Liberation 脸的
  交集（或按脸出表），`gen_canvas_coverage.py` 要认新后端；`ExecutionSourceResolver`（worker + 回执 → `origin=execution`）
  接 `_serialize_figure`；`job.py` 的 `produce` 换掉 `app._export_produce_canvas` 时旧契约（`partial` /
  `eps_not_for_canvas` / warnings）已同形；错误码若要用户可见，`rendercore` 的 code 要进 i18n（本轮走
  `export_render_failed` / `format_failed` 两个既有码）。
* **前端（RC-031 / 032，未做）**：要改三处——`web/src/lib/typography.ts` 的 `canvasFontStack` 换成批准字体（经后端
  静态路由把 `resources/fonts/` 里的 OFL 文件喂给 `@font-face`）、`tests/test_font_provenance.py::test_no_web_font_is_fetched_or_embedded`
  的判据改成「只许本地批准字体」、`canvas_coverage.json` 切到新表（同 U08）；revision 保护随之。用户拍板前不动。
* **用户拍板项**：Hangul / 阿拉伯 / 天城等整段脚本要不要进 allowlist（每加一张脸改 ADR 0060 §1 一行）；
  pikepdf 的 Pillow + lxml 传递依赖接受与否（U07 裁决）。

**回退方式、不能假装可回滚的外部副作用**：revert 两个 PR 即回退（全是新包 / 新文件 / 可选 extra / 台账状态；
`scripts/dev/u02_spikes/fonts.py` 的 shim 回到 U02 版本）；没有设置写入、没有发布。外部副作用只有本机 scratchpad
里的 rc-venv、`build/fonts-cache/` 与 `src/tavotto/resources/fonts/`（都在 .gitignore 里，删掉即可）。

## 其它目标（`foundation-u06-rendercore.yml` 的四条腿）

（PR B 上随 rendercore 文件变动自动触发；结论按 run 号逐腿填在这里，没跑出来的腿保持 **not_run**，不预填。）

| 腿 | run | fetch_fonts --check | rendercore 用例 | evidence PDF 与 git 逐字节 | 备注 |
|---|---|---|---|---|---|
| ubuntu-latest py3.10 | not_run | — | — | — | — |
| ubuntu-latest py3.13 | not_run | — | — | — | — |
| windows-latest py3.13 | not_run | — | — | — | — |
| macos-latest py3.13 | not_run | — | — | — | — |
