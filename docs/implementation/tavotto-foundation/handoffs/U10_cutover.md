# U10 · 让切换本身可验证，不陷入先删才能测的循环 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U10.cutover，三个本地提交组、一个 PR：**A** = 切换前核验（退役扫描器 + 闭包 wheel 矩阵 +
候选在阻断器下的全路径 / 冒烟 / 冻结证据），**B** = 切换本身（默认 / 依赖 / 删旧实现 / 生成物迁移 / 旧测试迁移 /
打包 / CI / 仓库规则修订），**C** = enrollment 提升 + 文档 + 台账 + 本文件。架构决策：
[ADR 0072](../../../adr/0072-default-render-backend-cutover-and-pymupdf-retirement.md)（切换与退役、两条不变量的正式修订、
退役扫描的主语与例外、回退政策）、[ADR 0073](../../../adr/0073-default-font-layout-policy.md)（字体默认变更的布局政策与旧项目处理）。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 = U09 的本地 tip `8961883d`（两条链的汇合 merge `bf87e511` + U09 A / B；
两条链与 U09 都还没合入 main）；结束 = 本分支 `foundation/u10-cutover` 的 head（U09 合入 main 后 `rebase --onto origin/main`
再开 PR，届时以 `git log origin/main` 里 PR 号为准）。全部在 worktree `tavotto-wt/foundation-u10` 里做，用户主工作区一个
字节没碰；主仓库 `.venv` 零改动（只用它做「旧闭包下新默认怎么报错」的对照）；rc-venv 在 session scratchpad
（`pip install -e '<worktree>[dev,worker]' pdfminer.six pypdf h5py pyinstaller pipdeptree`，uharfbuzz 钉到 0.56.1 与 requirements.txt 一致）；
字体经 `scripts/fetch_fonts.py` 取到 `src/tavotto/resources/fonts/`（gitignored）。

**默认路径 / 候选路径 / 本阶段拟启用能力**：**默认路径 = RenderCore**（`pdfbackend.BACKEND_DEFAULT = rendercore`，
`BACKENDS` 闭集只剩它）；候选路径不再存在——`pdfbackend/pymupdf_backend.py` 删除、`TAVOTTO_RENDER_BACKEND=pymupdf`
是 `backend_retired` 错误；启用的能力 = **rendercore-output**（macOS arm64 本机 + CI 必需矩阵五条腿；签名安装物资格在 U11）。
`plan.json` 的 `new_default_capabilities_enabled` 按校验器约定保持空（那份清单是任务书，不是产品状态）；能力状态记在
`enrollment.json`（`capability_version: u10`）。

**已读规则与复用的权威模块**：根 `AGENTS.md`（两条不变量本阶段正式修订）、`CLAUDE.md`、`src/tavotto/AGENTS.md`、`web/AGENTS.md`、
`packaging/AGENTS.md`、`codex-plugin/AGENTS.md`、`.github/AGENTS.md`；`docs/rules/backend/` 的 pdf-backend-boundary（重写）/ rendercore /
export-pipeline / process-boundaries / brand-and-naming；`docs/rules/repo/same-origin-pairs.md`、`predicate-subject.md`；`docs/legal/`；
`docs/support-matrix.json`；ADR 0045 / 0053 / 0055 §4 / 0059 / 0060 §1 §2 §4 / 0065 / 0066 / 0067 / 0068 / 0070 / 0071；实施包 00 §4、
01 D03 / D07 / D15、03 §3–§4 / §6、06 §3、07、`phases/U10_cutover.md`、registry RC-100 ~ RC-109（映射 R13 / R14 / R15 / CP08 / CP08-E）；
U06 / U07 / U08 / U09 交接、`U00_FACADE_LEDGER.json`。复用（权威不动）：`pdfbackend/__init__.py` 的选择器形状（ADR 0067）、
`rendercore/*` 全部、`exportjob.run` 生命周期、`inspector` / `artifactinspect`、`fetch_fonts.py`（spec 的字体判据直接调它的 `check()`）、
`scripts/dev/u07_freeze_child.py` 的 PyInstaller 配方（搬进 `tavotto.spec`）、`smoke_app.py`。

**实际代码与 API / 数据结构变更**：

| 层 | 变更 |
|---|---|
| `pdfbackend/__init__.py` | `BACKEND_DEFAULT = rendercore`、`BACKENDS = (rendercore,)`、`BACKEND_RETIRED = (pymupdf,)` → `BackendSelectionError(code=backend_retired)`；`ERROR_CODES = (backend_retired, backend_unknown, backend_unavailable)` |
| `pdfbackend/pymupdf_backend.py` | **删除**（退役前的返回值冻结成批准资产 `tests/fixtures/legacy_pymupdf/`：`oracle.json` / `preview_300.png` / `calibration/<case>.pdf`，README 写明来源） |
| `app.py` | 删 `_export_produce_canvas` / 旧 `/api/render` 键 / `_write_render_cache` / `_publish_render_cache` / `source_sha1` memo 与锁表；`/api/render` 只剩 `PreviewCache` 一条路且先问 `pdfbackend.selected()`；`_export_produce` 同样先问；两个漏斗：`BackendSelectionError` → 500 + `backend_retired` / `backend_unknown`（params.value），`CandidatePackagesMissing` / `FontsUnavailable` → 500 + `backend_unavailable`（params.reason）——**不静默换库** |
| `rendercore/renderhost.py` | 起 child 前按 `find_spec("pypdfium2")`（不 import）判闭包完整：缺就 `CandidatePackagesMissing`，不再把安装问题伪装成 `render_child_died` |
| `rendercore/hbshaper.py` | fontTools.subset 的 INFO 日志压到 WARNING（一次导出十几行进 app.log）；缺包文案改指 `requirements.txt` |
| `pyproject.toml` / `requirements.txt` / `requirements-dev.txt` | 五个 native 包进 `dependencies`（`requirements.txt` 钉死镜像同名同序：flask / packaging / pikepdf / fonttools / uharfbuzz / pypdfium2 / pillow）；`rendercore` extra 与 `requirements-rendercore.txt` 删除；`legacy-pymupdf = [pymupdf>=1.24,<2]`，`dev` 经自引用 `tavotto[legacy-pymupdf]` 带进测试读取器 |
| 生成物 | `pdfbackend/canvas_coverage.json` / `tests/golden/glyph_plan_vectors.json` 由批准字体集合出（与 U08 候选表逐字节相同）；`rendercore/canvas_coverage.json` / `glyph_plan_vectors.rendercore.json` 删除；旧表存档 `evidence/u10/*.pymupdf.json`；两个生成脚本去掉 `--backend`；`tests/golden/preflight_vectors.json` 重生成（`canvas-text-glyph-substituted` 期望改成空、`bitmap-embed` 向量删除）；新增 `tests/golden/shape_geometry_vectors.json`（旧 facade 记下的多边形 / 虚线向量，pytest + vitest 各跑一遍） |
| 预检 / 前端 | `bitmap-embed` 规则、`bitmap_embed` 字段、检查器三段「将嵌入为位图」文案、`problems.title.bitmap-embed` **删除**（RenderCore 保矢量，那句话不再为真）；`errors.json` 两种语言加 `backend_retired` / `backend_unknown` / `backend_unavailable`；`resources.d.ts` 重生成；TS 注释里的 PyMuPDF 字样按实际改；`web/src/lib/shapeGeometry.golden.test.ts`（新）、`glyphPlan.test.ts` / `TextView.test.tsx` / `TextSection.test.tsx` / `ExportDialog.test.tsx` 按 D07 改口 |
| 打包 | `packaging/tavotto.spec`：缺批准字体拒绝打包（`fetch_fonts.check()`）、`collect_dynamic_libs("pypdfium2_raw")` + `collect_all("pikepdf")`、hidden imports `renderchild` / `renderhost` / `pypdfium2`、`PIL` 出 excludes、`pymupdf` / `fitz` 进 excludes、`rendercore/fonts_allowlist.json` 进 datas（冻结产物第一次真导出在这里 ENOENT 才露头）；`packaging/entry.py` 最先分派 `--render-child`；`packaging/entitlements.plist` 注释；`scripts/build_desktop.py` PyInstaller 前 `fetch_fonts.py` |
| CI | `ci.yml`：invariants / backend-fast / backend-platforms / compat-smoke / plugin-candidate / workerd / package / windows-exe-smoke / macos-app-smoke / posix-e2e 每条腿「批准字体」步（不上 actions/cache：CI02 把它钉成 CPython 归档闭集）；打包依赖装 `requirements.txt`；package 扫 wheel、两条 smoke 腿扫冻结产物（`retirement_scan.py --wheel / --dist`）；harness 步加 `tests/test_foundation_cutover.py`；`nightly.yml` / `desktop-tauri.yml` / `private-python-targets.yml` 同步；`foundation-u06-rendercore.yml` 去掉对拍步与 rendercore extra，只剩三平台 evidence 复现 + 冻结 child + FO32 观察腿；`scripts/ci/ci_baseline.py` 步骤分类加「缓存字体 / 批准字体 / 退役扫描」 |
| 扫描 / 工具 | `scripts/ci/retirement_scan.py`（新，五把尺子 + `--selftest`）、`scripts/dev/u10_wheel_matrix.py`（新）、`scripts/ci/lab_acceptance.py` 结构检查加字体 13 张 / 覆盖表 / 闭包零 pymupdf、`scripts/ci/compat_matrix.py` 的「解得开」改用 pypdfium2（不再有「没装就跳过」）、`scripts/smoke_app.py` 加画布预览步（render child 在冻结产物里自起只有真产物能验）+ HTTPError 打印 JSON 正文、`scripts/dev/u08_parity.py` 删除 |
| 测试 | `tests/support/pdftext.py`（新，PDFium 字符级文字层读取器）；`test_compose_text.py`（21 条，改走契约层 + PDFium，**期望值一字未改**）、`test_compose_arrow.py`（内容流坐标）、`test_paths_and_baked.py`（`placement.place().clip`）、`test_typography_families.py` / `test_glyph_plan.py`（D07 改口）、`test_render_cache.py`（真实端点上的用户合同，memo 用例随旧路删除）、`test_windows_regressions.py`（两条退让用例移到 PreviewCache）、`test_rendercore_facade.py` / `_calibration.py` / `_geometry.py`（对拍改对批准资产）、`test_rendercore_glyph_vectors.py`（对存档旧表钉闭集 + 覆盖表数字）、`test_rendercore_app.py`（+`backend_retired` / `backend_unavailable` 负例、换 build 换键）、`test_export_pipeline.py` / `test_export_endpoint.py` / `test_export_inspection.py`（注错点改到写入器 / 编码器 / 计划半张）、`test_mcp_normalize.py`（Two 按 case 记阈值 4% / 1.6）、`test_scientific_text_matrix.py`（量渲染表示）、`test_font_provenance.py`（AST 判 rendercore 不摸别的字体文件）、`test_rendercore_fonts.py`（镜像改为 dependencies ↔ requirements.txt）、`test_rendercore_model.py`（阻断器靶子换成 app）、`test_import_architecture` baseline（`pdfbackend_impl` 层删除）、`test_error_codes.py`（三个新 code）、`test_runtime_build.py`（+spec 闭包 / 字体 / entry 分派 + **每个跟踪的包内数据文件都在 datas**）、`test_merge_queue_workflows.py::TestApprovedFontsAndRetirementScan`、`test_retirement_scan.py`（新）、`test_foundation_cutover.py`（新，U08-R1 harness 实例）、`test_foundation_facade_ledger.py`（14 条 deselect 的 u10_disposition） |
| 台账 / 文档 | `U00_FACADE_LEDGER.json`（`implementation_module` → facade、`u10_terminal_state`、19 项各加 U10 证据、`candidate_parity.status=retired_u10` + 逐条 `u10_disposition`）+ MD 重生成；`enrollment.json`（`capability_version: u10`、U08-R1 → enforced（pr）、`backend` 一律 rendercore + notes）+ MD 重生成；`plan.json` U10 done；registry RC-100 ~ RC-109 enrollment / u10_note / promotion；根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`packaging/AGENTS.md`、`src-tauri/AGENTS.md`、`CONTRIBUTING.md`；`docs/rules/backend/pdf-backend-boundary.md`（重写）/ rendercore / process-boundaries / brand-and-naming / export-pipeline / external-handoff / project-system / override-application-and-replay、`docs/rules/frontend/canvas-objects-and-workspace.md` / typography-capability-layer、`docs/rules/repo/same-origin-pairs.md`、`docs/rules/ci/verification-chain.md`、`docs/ci/*`；`docs/legal/LICENSING.md` / README / 权利政策改口、审计文档加 2026-09-22 附录（不重基线）；`docs/support-matrix.json` + README（Intel Mac 的 pikepdf 事实）；ADR 0072 / 0073；本文件；README U10 段；PACKAGE_CONTENTS 重算 |

**关联旧要求 ID / 场景 ID**（registry 映射到 U10 的 RC-100 ~ RC-109；R13 / R14 / R15 / CP08 / CP08-E）：

| ID | 处置 | 证据 |
|---|---|---|
| RC-100 依赖实际覆盖当前 Python / OS 承诺 | **observing**：`u10_wheel_matrix.py` 五个原生包 × 3.10–3.14 × 正式三格 75/75；Linux aarch64 全有；**macOS x86_64 pikepdf 10.x 无 wheel**（已拍板：矩阵如实改口、保持 10.x） | `evidence/u10/wheel_matrix.json` |
| RC-101 PIL / native / data 与 PyInstaller 正确打包 | **enforced**（integration）：spec 显式收 PDFium / qpdf、PIL 出 excludes、缺字体拒绝打包、每个跟踪的数据文件在 datas；产物腿扫 native 名单 | `test_runtime_build.py` 两条 + `retirement_scan_frozen_dist.json`（111 个原生文件，零 mupdf，libpdfium + libqpdf 在，字体 13） |
| RC-102 科学 worker 与应用 runtime 继续隔离 | observing：`child_env()` 摘 PYTHONPATH 不变；FO32 出口断言科学环境里没有 pikepdf / pypdfium2 / uharfbuzz | `tests/test_foundation_join.py`（observing 腿） |
| RC-103 wheel / sdist 在源码目录之外真实可用 | **enforced**（integration）：package job 干净 venv 装 wheel → 起服务冒烟；wheel 扫描 Requires-Dist 七个包 / 字体 13 / 覆盖表 / 零原生文件 | `retirement_scan_wheel.json` |
| RC-104 / RC-105 Windows / macOS 签名产物资格 | planned（U11） | — |
| RC-106 插件候选 / receipt 与源码 SHA | planned（U11） | — |
| RC-107 运行 / 依赖 / native / SBOM 均无 PyMuPDF | **enforced**：五把尺子；旧闭包三尺全红（负例）；切换后 source / deps / block / run / wheel / dist 六尺全绿；SBOM 尺子 not_run（发布链才有输入） | `evidence/u10/pre_cutover/retirement_scan_old_closure.json`、`post_cutover/*` |
| RC-108 许可证 / NOTICE / 字体来源义务 | observing：许可表 + 义务写进 ADR 0072 §1 与 docs/legal；OFL 全文随字体进包；MPL §3.2 的 NOTICE 链仍是 #182 | ADR 0072 §1、`docs/legal/COMMERCIALIZATION_DEPENDENCY_AUDIT.md` 附录 |
| RC-109 切换不静默回退旧库、不破坏旧工程 | **enforced**（pr）：`backend_retired` / `backend_unavailable` 两条真实入口负例；旧项目：文档只存通用族、换行期望一字未改、缺省族走衬线 | `test_rendercore_app.py`、`test_compose_text.py`、`test_typography_families.py`、ADR 0073 |

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿（520 文件） |
| **切换前**：`scripts/ci/retirement_scan.py --python <rc-venv>`（U09 tip） | rc-venv | 1 | source / deps / block 三尺红（负例：旧闭包上判红） |
| **切换前**：`TAVOTTO_RENDER_BACKEND=rendercore … --skip source,deps` / `--smoke` | rc-venv | 0 / 0 | block 17 步全过；smoke_app 全路径退出 0、阻断器在父进程装上 |
| **切换前**：`scripts/dev/u08_parity.py`（U09 tip） | rc-venv | 0 | 654 通过 / 0 失败 / 13 skip（workerd 二进制 ×6、画布产物 ×7——基础设施）/ 14 deselect，138.9 s |
| **切换前**：`scripts/dev/u10_wheel_matrix.py` | pip download，本机联网 | 0 | 正式 75/75；informational 缺 5（全是 pikepdf × macOS x86_64），226 s |
| **切换前**：`scripts/dev/u07_freeze_child.py` | 本机 macOS arm64 冻结 | 0 | 7/7（PDFium 库、13 张脸、child 自起、真渲染） |
| **切换后**：`retirement_scan.py --python <rc-venv> --smoke` | rc-venv（源码树） | 0 | source（190+ 文件零 import）/ deps（闭包无 pymupdf，同 site-packages 里的读取器不算）/ block（17 步）/ run（smoke_app 全路径 + marker）全过 |
| **切换后**：`python -m build --wheel` → `retirement_scan.py --wheel` | rc-venv | 0 | Requires-Dist 运行时 7 个包、字体 13、覆盖表在、零原生文件 |
| **切换后**：`build_desktop.py --skip-tauri --skip-runtime` → `retirement_scan.py --dist dist/Tavotto` → `smoke_app.py --exe dist/Tavotto/Tavotto` | 本机 macOS arm64 真冻结（产品 spec；无内置 runtime，worker 经 `TAVOTTO_WORKER_PYTHON`） | 0 / 0 / 0 | 111 个原生文件零 mupdf、libpdfium + libqpdf 在、字体 13；冒烟：预览 PNG 400 px（frozen child 自起）、渲染 × 2、导出 × 2、干净退出。**第一次跑红了**：`rendercore/fonts_allowlist.json` 不在 datas（ENOENT）→ spec 补一条 + 加「每个跟踪的包内数据文件都在 datas」的用例 |
| 主 `.venv` 对照（没装候选包） | 主 `.venv` + `PYTHONPATH=<worktree>/src` | — | `text_width` → `CandidatePackagesMissing`（点名三个包）；`/api/render` → 500 `backend_unavailable`（点名 pypdfium2）；`/api/export` → 500 `export_failed` 带同一句话；**没有任何路径悄悄换库** |
| `pytest`（全量，rc-venv） | macOS arm64 | 见 §全量 | 见 §全量 |
| `cd web && pnpm test && pnpm build && pnpm i18n:check` | worktree 里真 `pnpm install` | 0 / 0 / 0 | 284 文件 4213 条全过；tsc + vite 绿；i18n 无问题 |
| `actionlint` 五份 workflow | — | 0 | — |
| `tools/validate_plan.py` / `generate_enrollment.py --check` / `generate_facade_ledger.py` | — | 0 | ok；product_qualification not_run |
| Linux / Windows / 3.10 / 3.14 | ci.yml 五条腿（rendercore 用例自 U10 起在必需矩阵里真跑）+ 产物腿的退役扫描 | — | **not_run**（PR 上首跑；结论按 run 号填进 PR 正文） |

**本切片的正例、负例、旧行为回归**：正例 = 上表 + U08-R1 harness 记录（进程内 HTTP：预览 PNG 400 px、画布 PDF 三张嵌入子集脸 +
文字层 `Hello 图 ×10⁵`、PNG 709 × 354、manifest `accepted` / `text_layer verified` / `backend rendercore` / 四身份在）；负例 =
`backend_retired`（真实入口）、`backend_unavailable`（藏起 pypdfium2）、扫描器七条自检（注入 `import fitz` / `import_module` 必红、
用户脚本与显示名字符串不红、伪造 METADATA / libmupdf / SBOM 必红、阻断器在标准库上咬）、旧闭包三尺红、ADR 0072 §4 的变异表
（每条按用例名指得出）；旧行为回归 = 用户合同一条不删：排版 21 条期望值一字未改、箭头几何四条、写回 / 原图 / 取消 / partial /
命名预留 / 覆盖 / 旧响应结构（`test_export_pipeline` / `test_export_endpoint` / `test_write_back` / `test_annotate_asset` /
`test_original_spec` / `test_mcp_server` 在新默认下全过）。

**本次是否改变 case enrollment**：**是**——`U08-R1` observing → **enforced（pr）**（`tests/test_foundation_cutover.py`，进程内 HTTP 入口，
写结果记录；提升条件逐项在 notes）；`U07-R1` 仍 observing（主语是别的平台的字节）；`FO32` 仍 observing（required 矩阵仍没有第二
解释器 + h5py）；`backend` 字段一律 rendercore（notes 记旧终点是历史）。计数 planned 8 / observing 9 / later 1 / enforced 17。
registry：RC-101 / 103 / 107 / 109 → enforced（各带 promotion 合同），RC-100 / 102 / 108 → observing，RC-104 / 105 / 106 planned（U11）；
220 条 `execution_status` 一条没动、`product_validation_status` 仍 not_run（产品资格按 lane 实例记，不按需求条目翻）。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：Linux / Windows / 3.10 / 3.14 上的全部 rendercore 用例与产物扫描（PR 上首跑）；Windows NSIS / macOS 签名公证后
  产物；no-system-Python 目标；SBOM 尺子（只有发布链产 SBOM，第一次真跑是下一次 `release.yml` 演练）；真浏览器 e2e（posix-e2e 在 CI）；
  Intel Mac 的 pip 路径（没有机器）；跨 OS byte 复现。
* 基础设施问题：本机 `.venv` 不含候选包（预期：对照用）；`agents_budget.py` 报根 / src / web 速查表超预算是**既有**状态（本阶段
  根 AGENTS.md +1.2 KB）；#240 / #452 那三条本机噪音未变；一次全量 pytest 在树被改动期间跑的那遍作废，树静止后重跑（见 §全量）。
* 真正产品失败：**一条，已修**——冻结产物缺 `rendercore/fonts_allowlist.json`（PyInstaller 只收 .py，U06 时它是候选、没人在冻结产物
  里真导出过）；顺带把 fontTools.subset 的 INFO 刷屏压掉、`render_child_died` 伪装安装问题改成 `backend_unavailable`。
  顺带发现的**事实**（不是缺陷，进 ADR 0072 §2 用户可见变化表）：`scientific` 档在 Liberation 下不再折 `⁵` / `₂`（主脸自带）；
  `bitmap-embed` 预检与三段检查器文案说的事在 RenderCore 下不发生（保矢量），已删；`Two` 样例零修改往返像素差 3.28% / mean 1.33
  （PDFium 抗锯齿，按 case 记阈值）；覆盖表交集与 U06 evidence 表差 16 / 3 个码位（交集 vs serif-regular）。

**批准的字体 / 视觉差异，及未授权变更检查**：全部在 ADR 0072 §2 的一张表（脸 / 覆盖 / 向量 7 条样例 / 预检两条规则 / scientific 档 /
基线 1.77 pt / Two 阈值 / pHYs / 写回 / 缓存 / 错误 / 核验）；布局政策在 ADR 0073（位置 / 内容 / 框尺寸 / 换行不变，基线按批准量变，
文档不迁移）。`LICENSE`、ruleset、`aggregate_gate.py`、required job 集合、`security._PUBLIC_PATHS`、worker 守卫、写回事务、遥测白名单、
用户脚本、画布尺寸一个都没动；没有新增 required job（都是接在现有 job 上的步骤）；没有 push、没有发布。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改产品默认行为 / 依赖 / 打包 / CI 拓扑 / 规则）→ `full-ci` +
`@codex review`；ruff 两条 0；rc-venv 全量 pytest（§全量）；pnpm test / build / i18n 0；actionlint 0；plan 校验 0；本机冻结产物
真冒烟 0；退役扫描六尺全绿 + 旧闭包判红。

**仍缺哪些默认启用 / 精确安装物资格**：签名 NSIS / 公证 .app / 正式 wheel 的最终字节资格（U11）；no-system-Python 目标资格
（`enabled` 全 false 未翻）；SBOM 尺子首跑；Intel Mac pip 渠道；FO32 其它平台；`MIN_TAVOTTO_VERSION` 本 PR **不抬**（桥的 import 集本阶段没变；U08 备注的那次抬版在 v0.17.0 由 U11 做）。

**用户拍板（2026-09-22，主对话确认，已写进 ADR）**：① 覆盖收窄（61 238 → 32 608）**不加脸**、如实表达（ADR 0073 §2；加脸 = allowlist
加一张 OFL 脸，是后续路径）；② 前端同源预览**不做**，只切覆盖表与测量来源（ADR 0073 §3）；③ Intel Mac 的 pip 渠道——**支持矩阵如实改口**：
pip 模式标「需自行编译 qpdf，未验证」，不冒称支持，**保持 pikepdf 10.x**（不放下界、不移出矩阵）；`docs/support-matrix.json` 是唯一出处，
两份 README 同口径（`tests/test_support_matrix.py` 看护），**网站口径要跟着改**（官网仓库另做，见下一阶段输入 ⑥）。**③ 已被更正**：它依据的「pikepdf 10.x 无 x86_64 wheel」是假阴性，见文末「合入 main 时的更正」。

**下一个无阻塞阶段 / 子切片**：U11（发行资格）。给 U11 的输入：① 退役扫描的 `--sbom` 尺子只在发布链有输入——`release-publish.yml`
生成 SBOM 之后接一步 `retirement_scan.py --sbom out/tavotto-sbom.spdx.json --wheel dist/*.whl`（本阶段没接：那条链每一步都是「第一次
执行就失败」，要随 U11 的演练一起验）；② 冻结产物的 native 名单本机 111 个（`retirement_scan_frozen_dist.json` 的 `sample`），Windows /
macOS 产物腿的扫描步在 PR 上首跑；③ `lab_acceptance.py` 结构检查多了三条（字体 / 覆盖表 / 闭包），lab 首跑看它；④ 打包依赖改装
`requirements.txt`，发行链里任何还手写 `flask + pymupdf` 的地方（grep 过 `.github/`：没有）都要跟着；⑤ `MIN_TAVOTTO_VERSION` 抬到 0.17.0（U08 的 `artifactinspect` 进 bridge import 集；U10 没再动它）；
⑥ ~~官网的 Intel Mac 口径改成「需自行编译 qpdf，未验证」~~（已被更正）：官网要跟 `support-matrix.json` 的 `min_os` 写出「Apple Silicon 需 macOS 14+、Intel 需 macOS 15+」——本仓库管不到那边。

**回退方式、不能假装可回滚的外部副作用**：`git revert` 本 PR 即回到 U09 tip 的形状（候选未启用、旧后端为默认、旧生成物 / 旧用例 /
旧工作流全部回来）；不自动发布、不删用户项目 / 环境、不把旧依赖装进新发行包。用户机器上：预览缓存旧文件不用清（键含后端身份，
自然不命中、按预算淘汰）；已装的 `tavotto[legacy-pymupdf]` 只是测试环境的事。外部副作用只有本机 scratchpad 里的 rc-venv / dist /
build 产物与 `src/tavotto/resources/fonts/`（gitignored）；没有设置写入、没有发布。

## 全量 pytest（rc-venv，macOS arm64，树静止后）

`PYTHONPATH=<worktree>/src rc-venv/bin/python -m pytest -q -p no:cacheprovider -p no:warnings -rs`（2026-09-22，切换后的树，
rc-venv = `pip install -e .[dev,worker]` 的安装闭包，批准字体已取）：**6091 collected，4 failed，48 skipped，2152 s（35.9 min）**。
四条红逐条读过根因，没有一条是 RenderCore 或切换本身的缺陷：

| 红 | 根因 | 归属 / 处置 |
|---|---|---|
| `tests/test_foundation_facade_ledger.py::test_cited_lines_are_information_only_but_inside_the_symbol` | 本阶段最后一次改 `app.py`（`backend_unavailable` 漏斗）之后清单里 `_original_page_pt` 的引用行号漂了 1 行 | **我的**：重生成清单行号 + `U00_FACADE_LEDGER.md`，单跑 12/12 绿 |
| `tests/test_mcp_resolver.py::test_bridge_import_probe_matches_the_bridge` | `codex-plugin/mcp/tavotto_mcp/bridge.py` 自 U08 C（`2e631394`）起 `from tavotto.engine import artifactinspect`，而 `codex-plugin/mcp/server.py::_BRIDGE_IMPORT` 没同步加——resolver 探测放过了一个 bridge 需要的模块 | **U09 tip 上既有**（`git diff 8961883d -- codex-plugin` 为空；#476 是叠栈 PR，PR 级 CI 没跑到它）。本阶段不改别人分支上的缺陷，已报主对话：应在 U08 / U09 的 PR 里把 `artifactinspect` 加进 `_BRIDGE_IMPORT` |
| `tests/native/test_run_cli_integration.py::test_run_messages_only_stderr` | 这台机器的用户配置 `~/Library/Application Support/Tavotto/config.json` 的 `worker.python`（2026-09-22 01:30 由另一会话设）指向 `…/-Volumes-Projects-Tavotto/…/issue435/userenv/bin/python`；用例的探针脚本把 `sys.executable` 打到 stdout，判据 `"Tavotto" not in out` 被**路径里的产品名**打红 | 本机噪音：`TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/bin/python3.13` 单跑绿、不设则红（两次各跑一次）；不动用户配置 |
| `tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan` | 全量里 90 s 超时（stderr 停在 `Waiting for Tavotto desktop…`），单跑绿——与 2026-09-20 main 基线（`baseline/branch_full.log`：同一条全量红、`ctrl_c_single.log` 单跑绿）形状相同 | 本机噪音（既有；#452 家族），本阶段没碰 native |

48 条 skip 全部读过理由，没有一条是 RenderCore 用例：codex 插件真装机模拟不出（12）/ 真插件构建物与联网冒烟（8）/ 私有 Python
真归档要联网（4）/ runtime 未构建（4）/ MCP 画布产物未构建（4）/ `python -m build` 产物（3）/ 平台（Linux /proc、Windows NSIS、3.10
tomllib：4）/ 另一 minor 解释器（4）/ 其它（bridge sitecustomize、RESTORE 表、空参数集、cargo 探针：5）。`tests/test_foundation_cutover.py`
与全部 `test_rendercore_*` / `test_retirement_scan.py` 都是**跑过并通过**，不是 skip。

变异反证（ADR 0072 §4 + 本阶段新增判据）：`evidence/u10/mutations.json`——17 条变异逐条把指定用例打红、还原后全绿；清单与
结果见该文件（默认改回 pymupdf / retired 静默映射 / dependencies 塞回 pymupdf / 源码 `import fitz` / spec 排除 PIL / CI 漏取字体 /
覆盖表 fallback 塞码位 / 基线硬编码 Times / ledger 处置改口 / `--render-child` 分派挪到重定向之后 / renderhost 不预检 pypdfium2 /
spec 漏 `fonts_allowlist.json` / 扫描器阻断器不装 / spec 不铺 `*BACKEND_IMPLS` / 手写模块名 / 留着退役模块的 hidden import / `_IMPL_MODULES` 加回 pymupdf）。

## 合入 main 时的更正（2026-09-24）

本分支按原计划在 U09（#498）合入之后才进 main；这期间 main 又进了 Intel 桌面版（#505，ADR 0076）、RenderCore 性能
P0 / P1 / P2（#506 / #509 / #513，ADR 0077）、#519 等。做法：以 U09 tip `ba32e954` 为基，把本分支相对它的净改动压成
一个提交 cherry-pick 到当时的 main（原始 390 个提交存档在 `archive/foundation-u10-cutover` = `0edf49d3`），逐个解
19 个冲突：`warm()` 保留 main 的 `prewarm()` 交接、`test_glyph_plan._place` 保留 main 的临时目录不泄漏、main 新增的三条
facade 用例改到 rendercore 上；台账越界的 6 个行号按 AST 填回；enrollment 三条 notes 做三方字符级合并。

**一处事实更正**：§「用户拍板」③ 依据的「pikepdf 10.x 没有 macOS x86_64 wheel」是 `u10_wheel_matrix.py` 的假阴性——
Intel 的平台标签只列到 `macosx_14_0`，而 pikepdf 10.x 的 x86_64 wheel 标 `macosx_15_0`；arm64 那组给了 11–15，拿到的
`macosx_14_0` 也记「有」。**两个方向都没量最低系统版本**。复核（PyPI）：pikepdf 10.13 要求 macOS 14（arm64）/ 15（x86_64），
pypdfium2 5.13 要求 13，其余 ≤ 11；而桌面版声明的是 11.0——不改的话 11–13（Intel 11–14）的用户装得上、打得开、渲染时出事。
用户拍板：**如实抬最低版本**。落地：`tauri.conf.json` 14.0、新增 `tauri.intel.conf.json` 15.0（`desktop-tauri.yml` 的 Intel
腿叠它）、`support-matrix.json` 两个 macOS 桌面目标加 `min_os` 并写进发行页英文、两份 README；Intel 桌面版保留（③ 的
「Intel 不支持 / pip 需自编 qpdf」作废）；脚本改成标签列到最新系统、每格记 `macos_min`、汇总 `macos_floor`，wheel 表重生成；
`tests/test_support_matrix.py` 钉住三处一致与 wheel 表的版本新鲜度；`codesign_macos.py scan --expect-min-os` 在真 `.app` 上
逐个 Mach-O 核 minos ≤ 声明值（desktop-tauri 的架构核对步）。ADR 0072 §1 / §2 / §4 / §6、ADR 0076、法务审计附录同步。

**not_run**：`desktop-tauri.yml` 只挂在发版链上，叠 Intel 配置与 `--expect-min-os` 两步在 PR 上不跑——下一次发版之前手动触发
一次 desktop-tauri 验它们；更老系统上真机「打不开」的表现没有机器验证。

**给 U11 的输入 ① 已在合入 main 时提前接上**（Codex #539）：`release-publish.yml` 生成 SBOM 之后紧跟一步
`retirement_scan.py --skip source,deps,block --sbom out/tavotto-sbom.spdx.json --wheel <发行 wheel>`；
`tests/test_retirement_scan.py::test_every_sbom_the_workflows_produce_is_scanned` 看护「每个 SBOM 产出都被扫」。
演练：本机用 anchore/syft 对本仓库 wheel 真生成 SPDX 2.3（与发布链同一种调用），扫描器正例过、注入 PyMuPDF 即红，
那份输出存成 `tests/fixtures/sbom/syft_wheel.spdx.json`。**如实记一条**：syft 对单个 wheel 文件只列出 wheel 本身、
不展开 Requires-Dist，所以发行 SBOM 上这把尺子抓的是「包里混进了 mupdf」，声明依赖由同一步的 wheel 尺子管。
这一步只在真发版时执行，第一次真跑是下一次 release（not_run 至此）。

