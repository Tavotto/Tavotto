# U08 · 所有用户路径接上，而不是只完成 save_pdf — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U08.facade_parity，三个叠栈 PR：**A** = 候选切换开关 + facade 候选实现（19 项 + Canvas 面）+ SourceResolver /
ExportJob 接线（执行侧源 + 回执）+ 原图三格式 + compose + 预览 + probe + 候选生成物 + 对拍纪律（ADR 0067）；**B** =
ArtifactInspector / ArtifactManifest / D08 检查政策 + 写回接线 + 负例（ADR 0068）；**C** = 入口审计（HTTP / SSE / MCP / 前端回执 +
i18n）+ enrollment 提升。本文件记的是 **A 之后**的状态，B / C 落地时逐节补。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `d957e57d`（`foundation/u07-compose-raster-b` 的 head，即 U07 的 PR B；
下面依次是 U07 A #463 → U06 B #460 → U06 A #458 → U02 #455 → main）；结束 = PR A 的 head（合并后以 `git log origin/main`
里 PR 号为准）。全部在 worktree `tavotto-wt/foundation-u08` 里做，用户主工作区一个字节没碰；候选包只装在 scratchpad 的独立
`rc-venv`（`pip install -e '<worktree>[rendercore,dev]' pdfminer.six pypdf pymupdf`——pymupdf 只当对拍的另一侧与读取器），
主仓库 `.venv` 零改动；字体经 `scripts/fetch_fonts.py` 取到 `src/tavotto/resources/fonts/`（gitignored，`git ls-files` 零字体二进制）。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（`pdfbackend.BACKEND_DEFAULT = "pymupdf"`：`app.py` 的
`_export_produce_canvas` / `_write_render_cache` / `original_*` 全走 PyMuPDF）；候选路径 = `TAVOTTO_RENDER_BACKEND=rendercore`
时契约层选中 `rendercore/facade.py`，`scope=canvas` 走 `rendercore.job.produce` + `sources.ExecutionSourceResolver`，`/api/render`
走 `rendercore.preview.PreviewCache`，其余 17 项经契约层自动切换；拟启用能力**无**（`plan.json` `new_default_capabilities_enabled: []`）。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`web/AGENTS.md`、`codex-plugin/AGENTS.md`、
`packaging/AGENTS.md`；`docs/rules/backend/` 的 pdf-backend-boundary / export-pipeline / writeback-transaction / rendercore /
preparation-and-receipts / process-boundaries；`docs/rules/repo/same-origin-pairs.md`、`predicate-subject.md`；ADR 0016 / 0046 /
0049 / 0051 / 0053 / 0059 / 0060 / 0065 / 0066；实施包 00 / 01（D03 / D07 / D08 / D15）/ 03 §6 / 04 §1–§3 / 05 §5 / 06 §1 / 07 /
`phases/U08_facade_validation.md` / registry RC-001 ~ RC-006、RC-054 ~ RC-060、RC-083 ~ RC-090、RC-093；U00 ledger、U06 / U07
交接。复用（权威不动）：`exportjob.prepare / run`（作业生命周期、命名预留、覆盖、取消提交点、report 失败一字不改）、
`exportreq`（请求形状不变）、`_serialize_figure`（「谁来渲染」那扇门的唯一导出调用点，只拆出一个多回 worker 的本体）、
`receipt.from_worker` / `source_artifact_for`（U01）、`originalspec`（密度唯一出处）、`_write_source_files` 的写回事务
（`annotate_asset` 经契约层切换，prepare → verify → commit 一字不改）、`job.produce` / `plan` / `sources` / `pdfwriter` /
`renderhost` / `preview`（U06 / U07）。

**实际代码与 API / 数据结构变更（PR A）**：

| 层 | 变更 |
|---|---|
| `pdfbackend/__init__.py` | 契约层改成**选择器**：19 个函数显式委托 `_impl()`，四个常量 PEP 562；`TAVOTTO_RENDER_BACKEND` ∈ {pymupdf（默认）, rendercore}；`selected()` / `BackendSelectionError(backend_unknown)`；不静默回退 |
| `pdfbackend/pymupdf_backend.py` | `compare_png` 的指标循环**逐字搬**到 `tavotto/pixelmetrics.py`（本模块只剩解码），`PNG_NOISE_FLOOR` 保留同名 |
| `tavotto/pixelmetrics.py`（新，纯标准库） | `rgba_metrics()`：两个后端共用的一把尺子 |
| `rendercore/facade.py`（新，native 入口） | 19 项同签名实现 + `Canvas` 面 + 进程级 `provider()` / `host()` / `preview_cache()`（跟着 cache_dir 走）/ `reset_for_tests()`；`pdf_fonts` 递归 Form 有预算；`coverage_ranges` primary = 12 张脸交集 |
| `rendercore/sources.py` | `ExecutionSourceResolver(static, execute)`：`needs_execution` 的交给 `execute`，回来必须 `origin=execution` |
| `rendercore/rasterio.py` | `header_info()`（尺寸 + alpha，不解码） |
| `rendercore/preview.py` | 第一次算键时 child ping 失败也翻成 `PreviewError`（原来的 `identity=` memo 注入点随 U07「键与渲染绑同一份抄出来的字节」取消） |
| `rendercore/job.py` | 画布 EPS 报旧路同一个稳定码 `eps_not_for_canvas`（params 带结构化 `unsupported`） |
| `engine/receipt.py` | `from_native_session(session, script)`（argv 只记数量 `NATIVE_ARGV_PLACEHOLDER`、`source_revision` 空串不猜） |
| `app.py` | `_serialize_figure_with_worker`（本体，多回 worker + 脚本）；`_execution_receipt` / `_execution_source`（回执 + `origin=execution` 产物）；`_export_produce_rendercore`（候选下 `scope=canvas` → `job.produce`，项目根只在碰面板时才问）；`_api_render_rendercore`（PreviewCache；队列满 503 + Retry-After）；`reset_projects(wait=True)` 末尾 `renderhost.shutdown_shared()` |
| `scripts/gen_canvas_coverage.py` / `gen_glyph_plan_vectors.py` | `--backend {pymupdf,rendercore}`；候选落点 `rendercore/canvas_coverage.json` / `tests/golden/glyph_plan_vectors.rendercore.json` |
| `scripts/dev/u08_parity.py`（新） | 对拍运行器：读 ledger `candidate_parity`、拒绝没有替代证据的 deselect、写 `evidence/u08/parity.json` |
| `tests/support/importgraph.py` + baseline | `pdfbackend` 层拆成 `pdfbackend_impl`（与新核心零边）与契约层（两条 `extra_edges` 登记） |
| `tests/test_rendercore_facade.py`（新，19 条） | 开关 / 无回退 / 对拍（probe / pdf_fonts / compare_png / 文字度量）/ 候选合同（原图三格式 / Canvas 面 / annotate / 预览 / Form 预算 / 三族三脸） |
| `tests/test_rendercore_app.py`（新，10 条） | 真实入口：同步 / 异步导出、EPS 码、原图逐字节、child 失败 partial、写入器失败 500 + 零遥测、`/api/render` 缓存 + 503 / 500、换后端换键、关停 reap、带 override 的面板经真 worker + 回执 |
| `tests/test_rendercore_sources.py`（新，3 条） | 执行侧解析器路由 / origin 必核 / native 回执 |
| `tests/test_rendercore_glyph_vectors.py`（新，5 条） | 候选生成物存在、与默认差异闭集、生成器 `--check` 退出码、12 脸交集 |
| `tests/test_foundation_facade_ledger.py` | 加 `test_candidate_parity_deselections_have_replacement_evidence` |
| `tests/test_rendercore_model.py` / `_job.py` / `_rasterize.py` / `test_foundation_harness.py` | 钩子自测改指实现模块；EPS 码；observing 计数 2 |
| `.github/workflows/foundation-u06-rendercore.yml` | 装 `worker` extra；加四个新用例文件 + `u08_parity.py` 步骤 + `U08_EVIDENCE` 工件；paths 加契约层 / ledger / 向量 |
| 文档 | ADR 0067；`docs/rules/backend/rendercore.md` / `pdf-backend-boundary.md`；`src/tavotto/AGENTS.md` 两行 + 验证段；ledger 19 项 U08 `migration_evidence` + `candidate_parity` + probe 备注；`enrollment.json` 加 `U08-R1`（observing）；`plan.json` U08 `in_progress`；README U08 段；本文件；PACKAGE_CONTENTS 重算 |

**关联旧要求 ID / 场景 ID**：R09 / R10 / R12 / R13 → RC-001 ~ RC-006、RC-054 ~ RC-060、RC-083 ~ RC-090、RC-093。逐条处置（A 之后）：

| ID | 处置 | 证据 |
|---|---|---|
| RC-001 每个导出项有迁移实现和契约测试 | 做了（19 项 facade + 八个 probe 调用点经契约层一处切换） | ledger 19 项 `migration_evidence`（U08）；`test_the_contract_names_in_all_are_the_ledger_nineteen` |
| RC-002 Canvas 面契约 | 做了（同实例多格式出自同一 Canonical PDF；save_png 不重新解析；重复 close 幂等） | `test_the_canvas_face_saves_png_and_tiff_from_one_canonical_pdf` |
| RC-003 原图与画布尺寸 / 变换隔离 | 做了（`scope=original` 路不动，契约层切换；候选 `original_pdf` 只搬第一页不吃 canvas x/y/w/h） | `test_export_pipeline.py::test_original_export_ignores_the_layout_scale` 在候选下逐字过（parity） |
| RC-004 annotation / legacy 字段语义 | 做了（`test_compose_annotations.py` / `test_compose_switched_shapes.py` 在候选下逐字过） | parity.json |
| RC-005 没有可兑现输出路径时明确失败 | 做了（画布 EPS `eps_not_for_canvas`；child 失败 `format_failed` 带 `raster_code`；不拿栅格冒充矢量） | `test_export_canvas_eps_under_the_candidate_reports_the_old_stable_code`、`test_a_child_failure_under_the_candidate…` |
| RC-006 worker SVG / EPS 直出仍可用 | 做了（`_serialize_figure` 权威不动；`scope=original` EPS 路一字不改，parity 里 `test_original_eps_*` 过） | parity.json |
| RC-054 原 PNG 逐字节 | 做了 | `test_original_png_copies_a_png_source_byte_for_byte_and_transcodes_jpeg`、`test_export_original_under_the_candidate…` |
| RC-055 JPEG 转码保 native grid | 做了 | 同上（30 × 20 在 600 ppi 请求下不变） |
| RC-056 PDF 只搬第一页、不吃画布变换 | 做了 | `test_original_pdf_moves_only_the_first_page_and_does_not_redraw_it` |
| RC-057 未知密度 TIFF 不伪造 | 做了 | `test_original_tiff_writes_only_the_declared_density_and_keeps_the_pixel_grid` |
| RC-058 标注写回同一几何 + 原件事务 | A 做了写回入口的候选实现（覆盖层 + 同一 PDF 栅格；事务在 `_write_source_files` 一字不改）；**端到端写回用例归 B** | `test_annotate_asset_overlays_vector_annotations_without_redrawing_the_source`、`test_annotate_asset.py` parity |
| RC-059 / RC-060 写回失败不坏原件 / 签名加密不虚假承诺 | **B** | — |
| RC-063 ~ RC-074 产物检查 / Proof | **B** | — |
| RC-083 partial / 取消提交点 / 状态机 | 做了（`exportjob` 权威不动；候选下 `test_export_pipeline.py` 的取消 / partial / 终局顺序用例逐字过） | parity.json |
| RC-084 / RC-085 命名预留 / 不冒充整批原子 | 做了（同上） | parity.json |
| RC-086 HTTP 同步 / 异步 / SSE 真链路 | A 做了同步 + 异步 + `/state`；**SSE 事件流与前端回执归 C** | `test_export_start_under_the_candidate_streams_progress_and_finishes` |
| RC-087 MCP 与版本探针 | **C** | — |
| RC-088 UI / 错误码 / 双语 | **C**（A 只保证候选不新造用户可见码：`eps_not_for_canvas` / `format_failed` / `export_render_failed`） | — |
| RC-089 四类 Web 构建 / Playground | **C** | — |
| RC-090 旧响应结构兼容 | 做了（`files[] / export_dir / warnings` 投影不变，`vector` 语义不变；候选下老契约用例 `test_export_legacy_items_texts_contract` 过） | parity.json |
| RC-093 旧几何 / 文字测试迁移保留断言意图 | 做了（14 条实现特定断言逐条替代证据；用户合同一条不删；运行器 + 主 .venv 用例拒绝无证据 deselect） | ledger `candidate_parity`、`test_candidate_parity_deselections_have_replacement_evidence` |

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `<rc-venv>/bin/python -m pytest tests/test_rendercore_facade.py tests/test_rendercore_app.py tests/test_rendercore_sources.py tests/test_rendercore_glyph_vectors.py tests/test_rendercore_job.py tests/test_rendercore_rasterize.py tests/test_rendercore_preview.py` | rc-venv（候选包 + 字体 + pymupdf） | 0 | 见 PR 正文数字 / 0 skip |
| `<rc-venv>/bin/python scripts/dev/u08_parity.py` | 同上；`evidence/u08/parity.json` | **0** | **517 通过 / 0 失败 / 9 skip（workerd 产物 ×6、dist 产物 ×3——基础设施，与候选无关）/ 14 deselect**，121.6 s |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest <针对性>`（主 `.venv`，没装候选包） | 同上 | 0 | 开关 / 无回退 / sources / ledger / harness / importgraph / 文档索引真跑；候选用例 skip 并写明理由 |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest`（全量，后台 + 日志） | 同上 | 见 PR 正文 | 见 PR 正文 |
| 变异反证（`scratchpad/u08/mutate_a.py`，ADR 0067 §3） | 同上 | 每条非零 | 见 PR 正文 |
| `gen_canvas_coverage.py --backend rendercore` / `gen_glyph_plan_vectors.py --backend rendercore`（`--check`） | rc-venv | 0 / 0 | 候选表 575 区间（与 U06 evidence 表逐层相同）；向量 69 条 |
| Linux / Windows / 3.10 | `foundation-u06-rendercore.yml`（PR 上随文件变动触发） | 见 PR 正文 | run 号与四条腿结论填在 PR 正文 |
| 前端 `pnpm test && pnpm build` | — | — | **not_run**：A 没有改 `web/src`（C 才改） |

**本切片的正例、负例、旧行为回归**：正例 = 上表；负例 = 未知后端名 / 字体缺席不回退 / `execute` 交回 static 产物 / child 死
partial / 写入器炸 500 零遥测 / 队列满 503 / 面板混进标注 / 自引用 Form / 交集之外的码位；旧行为回归 = 默认路径零改动
（`app.py` 的旧分支、`pdfbackend/pymupdf_backend.py` 只搬走指标循环、`web/src` 一字未动），旧用例一个不删（`candidate_parity`
只在候选下 deselect，默认矩阵照跑全部）。

**本次是否改变 case enrollment**：**加了一条 `U08-R1`（observing）**——RenderBench 实例：facade 19 项 + Canvas 面在候选下的对拍
+ 真实入口，挂在已有的非 required `foundation-u06-rendercore.yml` 三平台上；不进 required（默认后端仍是 PyMuPDF，候选只在
显式选中时接管，所以不是 FO 场景的资格）。台账计数 planned 31 / later 1 / enforced 1 / observing 2。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：Linux / Windows / 3.10 上的 facade / app / parity（workflow 每腿给）；macOS x86_64；前端；SSE 事件流 /
  MCP / 前端回执的入口审计（C）；ArtifactInspector / D08（B）；跨项目并发真图；registry 220 条产品实例。
* 基础设施问题：主仓库 `.venv` 没装候选包，候选用例在必需矩阵里是 skip（有理由）；parity 的 9 个 skip 是 workerd 二进制
  与 dist 产物不在本机（与候选无关）。
* 真正产品失败：无新增。顺带发现的事实：① **U07 交接里「probe 含 /UserUnit 是有意差异」不成立**——PyMuPDF 1.28.2 的
  `page.rect` 同样乘了 /UserUnit（540 = 2 × 270），忽略它的只是 PDFium 的 `get_size()`，child 已补上那一次；两边一致，
  差异表划掉；② Liberation 12 张脸的 cmap 差 16 个码位（U+0237、U+2000 ~ U+200B、U+2016、U+202F、U+F004、U+FFFC），候选
  覆盖表的 primary 必须取交集（旧后端 base-14 各脸同一字符集，这一维以前不存在）；③ PDFium 对 Type 3 字形的抗锯齿与
  MuPDF 不同：`test_mcp_normalize.py::test_zero_edit_roundtrip…[two]` 的 2% 阈值在 PDFium 下 3.3%（03 §6 需要校准，按 case 记）；
  ④ 主 `.venv` 跑会起子进程的用例时 `PYTHONPATH` 必须是**绝对**路径（相对 `src` 在子进程的 cwd 下解析到别处 →
  `/api/engine/preparation` 404，是主工作区被 import 的形状，不是产品缺陷）。

**批准的字体 / 视觉差异，及未授权变更检查**：默认路径零改动。候选与旧后端的批准差异表在 ADR 0067 §2（文字层归属 D07、
基线 1.77 pt @ 12 pt、opacity / flip 保矢量、矢量源 PNG 的 pHYs 写真实 ppi、annotate 整份重写 vs incremental）。
`git diff --stat` 只含上表的文件；`LICENSE`、ruleset、`aggregate_gate.py`、required job、默认后端、`requirements.txt`、
默认 `dependencies`、`packaging/tavotto.spec`、`web/src` 一个都没动。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改 `src/` / `tests/` / `scripts/` / `.github/`）→ `@codex review`
（叠栈 PR 按 runner 纪律先不打 `full-ci`）；ruff 两条 0；rc-venv 针对性 0 skip；parity 退出码 0；主 `.venv` 针对性 0；变异逐条红；
全量 pytest 见 PR 正文；workflow 合同用例（`test_merge_queue_workflows`）全过、actionlint 0。

**仍缺哪些默认启用 / 精确安装物资格**：全部。候选栈未默认启用；前端不读候选覆盖表；产品包不带候选包 / 字体；有限产物验证
（B）与入口审计（C）未落。

**下一个无阻塞阶段 / 子切片**：U08 PR B（ArtifactInspector / Manifest / D08）→ PR C（入口审计 + i18n）→ U09（与完整准备链联调）。
给 B / C / U09 / U10 的输入：

* 候选后端的开关只有一处（`pdfbackend.selected()`），入口审计里「哪条路」的判据一律问它，别再复制一个 `if env`；
* `_execution_source` 是 `scope=canvas` 的执行侧；`scope=original` 仍走 `_resolve_panel_source`——B 的 Manifest 若要记
  原图导出的回执，接线点在 `_export_produce_original`（那里今天不产回执）；
* 候选下 `job.produce` 的 `Produced.error_params` 里已经带 `plan_identity` / `sha256`（PDF）与 `renderer / px / channels / dpi`
  （位图）——B 的 ArtifactManifest 的 `plan` 半张可以直接吃它，`observed` 半张要重新打开封口文件量；
* U10 切默认：改 `pdfbackend.BACKEND_DEFAULT`、删 `pymupdf_backend.py` 与 `app.py` 的旧分支（`_export_produce_canvas` /
  `_write_render_cache` / `_publish_render_cache` / `source_sha1` 的键段）、`gen_*` 两个脚本的默认落点换成候选表、
  `candidate_parity.deselected` 里的 14 条按替代证据删；
* U10 之前不许把 `TAVOTTO_RENDER_BACKEND=rendercore` 写进任何默认配置 / 打包脚本。

**回退方式、不能假装可回滚的外部副作用**：revert PR A 即回退（契约层回到静态 import；新模块 / 用例 / 文档 / 台账状态）；
没有设置写入、没有发布。外部副作用只有本机 scratchpad 里的 rc-venv 与 `src/tavotto/resources/fonts/`（gitignored）。

## 其它目标（`foundation-u06-rendercore.yml` 的四条腿，PR A 上随文件变动自动触发）

（结论按 run 号逐腿填；没跑出来的腿保持 **not_run**，不预填。判的是四个新用例文件 0 红 + `u08_parity.py` 退出码 0。）

| 腿 | run | rendercore 用例 | parity（passed / failed / skipped / deselected） | u07.pdf 逐字节 | freeze 7/7 |
|---|---|---|---|---|---|
| ubuntu-latest py3.10 | not_run | — | — | — | — |
| ubuntu-latest py3.13 | not_run | — | — | — | — |
| windows-latest py3.13 | not_run | — | — | — | — |
| macos-latest py3.13 | not_run | — | — | — | — |
