# U07 · 完整合成与 PNG/TIFF 同源 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U07.compose_raster，两个叠栈 PR：**A**（#463）= 合成（ImportedPage / Image 写入、页盒 / Rotate /
UserUnit、crop / 翻转 / 旋转顺序合同、透明组、同名资源、不可信源、pikepdf 裁决，ADR 0065）；**B** = render child 收编 +
RasterBuffer 栅格 + PNG / TIFF 同源 + 预览缓存 + 旧新后端校准对拍 + spike 退役 + evidence/u07 + 最小 freeze
（ADR 0066）。本文件记的是 **B 之后**的状态（A 的三轮 Codex 评审处置也在 A 分支上：像素预算两级且在解码前记账、
JPEG 直通先核 SOF 再真解、退化页盒）。

**开始 HEAD / 结束 HEAD / 用户原有工作区改动**：开始 `7d312482`（`foundation/u06-ir-text-b` 当时的 head，即 U06 的 PR B；
下面依次是 U06 A #458 → U02 #455 → U01 #451 → main）；结束 = PR A 的 head（合并后以 `git log origin/main` 里 PR 号为准）。
全部在 worktree `tavotto-wt/foundation-u07` 里做，用户主工作区一个字节没碰；候选包只装在 scratchpad 的独立 `rc-venv`
（`pip install -e '<worktree>[rendercore,dev]' pdfminer.six pypdf`），主仓库 `.venv` 零改动；字体经 `scripts/fetch_fonts.py`
取到 `src/tavotto/resources/fonts/`（gitignored，`git ls-files` 零字体二进制）。

**默认路径 / 候选路径 / 本阶段拟启用能力**：默认路径不变（PyMuPDF 后端、`app.py` 的 `_export_produce_canvas`、
`/api/render` 缓存、`canvas_coverage.json`）；候选路径 = `src/tavotto/rendercore/`（IR → RenderPlan → pdfwriter → PDF，
现在含面板；只由测试驱动，`app.py` 不 import 它）；拟启用能力**无**（`plan.json` `new_default_capabilities_enabled: []`）。

**已读规则与复用的权威模块**：根 `AGENTS.md`、`src/tavotto/AGENTS.md`、`packaging/AGENTS.md`、`.github/AGENTS.md`；
`docs/rules/backend/` 的 rendercore / pdf-backend-boundary / export-pipeline / runtime-figure-assets / process-boundaries /
preview-complexity-budget / layout-versions-and-documents / writeback-transaction；`docs/rules/repo/same-origin-pairs.md`、
`predicate-subject.md`；ADR 0046 / 0049 / 0053 / 0055 / 0059 / 0060；实施包 00 / 01（D07 / D08 / D12）/ 03 §6 / 04 §4 /
05 §5 / 07 / `phases/U07_render_output.md` / registry RC-038 ~ RC-062、RC-094 / RC-096；U02 / U06 交接。复用（权威不动）：
`ir` / `plan` / `sources` / `typography` / `geometry`（U06）、`exportjob.prepare / run`（作业生命周期一字不改）、
`sources.read_frozen()`（冻结字节的第一道核）、U02 spike 的 `import_page` 形状（收编成 `_imported_page` + `placement`）、
旧 facade `_place_panel` / `_crop_clip` / `_obj_morph` 与前端 `PanelView` 的变换顺序（当**用户合同**取语义，不取实现细节）、
`tests/support/pdfread.py`（纯标准库读取器）、U00 夹具 `pdf_png_assets/`（非零原点页盒 + 带 alpha 的 PNG）。

**实际代码与 API / 数据结构变更（PR A + PR B）**：

| 层 | 变更 |
|---|---|
| `rendercore/placement.py`（新，纯模型） | `place(source, rect, crop, rotate_cw_deg, flip_h, flip_v) → Placement(matrix, clip, bbox)`：crop → 翻转 → 顺时针旋转 → 填满的唯一出处；`visible_box(bbox, matrix)`：form BBox 经 /Matrix 的包围盒 |
| `rendercore/raster.py`（新，纯模型） | `RasterBuffer`：8 bit RGB / RGBA、stride ≥ 行字节、**alpha 一律 straight**、sRGB、自持 `bytes`；`rows()` / `packed()` / `split_alpha()` / `pixel()` |
| `rendercore/rasterio.py`（新，native 适配） | Pillow 解码位图源 → `RasterBuffer`（灰度 / 调色板 / 16 bit / CMYK 归一，密度只取文件声明过的）；`jpeg_passthrough()`：8 bit RGB / 灰度 JPEG 从 SOF 段读尺寸，原字节直通 |
| `rendercore/ir.py` | `Image.rotate_cw_deg`；`CAPABILITIES["pdf"]` 的 `image` / `imported_page` / `flip` 翻成 **native**（理由写明 Form XObject / Image XObject / 负缩放）；PNG / TIFF / EPS 仍全 unsupported |
| `rendercore/pdfwriter.py` | `PdfWriter(page, provider, files)`：`_imported_page`（qpdf `as_form_xobject(handle_transformations=True)` + `copy_foreign`，同 (资源, 页) 只搬一次，外来 Pdf 活到 save 之后；透明组包装；`WriteFacts.imported_pages`）、`_image`（Image XObject：Flate + /SMask 或 DCTDecode 直通，按资源 key 去重；`WriteFacts.images`）、`_source_bytes`（再核 sha256）；新错误码 `source_bytes_missing` / `source_identity` / `source_unreadable(why=encrypted|broken|page_index|import|raster_*)` |
| `rendercore/plan.py` | 位图面板也带 `rotate_cw_deg`（四舍五入到 90 倍数，与 PDF 同一条） |
| `rendercore/job.py` | 冻结源经 `read_frozen()` 读进内存后作为 `files` 交给写入器（写入器只吃这一份字节） |
| `pyproject.toml` / `requirements-rendercore.txt` | `rendercore` extra 加 `pillow>=10,<13` / `pillow==12.3.0`（U07 起直接依赖，ADR 0065 §2） |
| `tests/support/importgraph.py` | `placement.py` / `raster.py` 进 `rendercore_model` 层 |
| `tests/test_rendercore_placement.py`、`test_rendercore_raster.py`（新，纯模型） | 手算落点 / 可见框 / 顺序；RasterBuffer 的 stride / alpha / 拒绝 |
| `tests/test_rendercore_compose.py`（新，rc-venv） | 56 条：页盒、Rotate ×4、UserUnit、32 组 crop × 翻 × 转 + 框外一圈白、透明组 ×2、opacity=0、镜像保矢量、同名资源、两实例一份 form、继承资源、不可信源 / 加密 / 坏文件 / 缺页、字节身份、PNG alpha + /SMask、JPEG 直通 vs CMYK 解码、位图 crop / 翻 / 转、扩展名与字节不符、8 线程并发、确定性字节 |
| `tests/test_rendercore_writer.py` / `test_rendercore_job.py` / `test_rendercore_ir.py` / `test_rendercore_plan.py` | 能力交叉核对给面板真源；面板作业 → `done` + `vector: True` + 文字层在；能力表用例改成「PDF 十个操作全 native」；plan 的 `opacity: 0` 与旋转四舍五入用例 |
| `rendercore/renderchild.py`（新，native：pypdfium2 / pikepdf 只在 `child_main()` 里 import） | 应用自己的 PDFium 子进程：行分隔 JSON 协议，op `ping / probe / render / inspect / close`；probe 含 /UserUnit（PDFium 忽略它，child 用 pikepdf 读一次乘上）；render 把 RGB（白底）/ RGBA（透明底，straight）原样字节写进父进程给的文件；像素预算 child 侧再判；doc / page / bitmap `finally` 关、像素先复制；`RLIMIT_AS`（Linux 生效）；`child_argv()` frozen 下 `--render-child` |
| `rendercore/renderhost.py`（新，纯标准库） | `RenderHost`：一把锁串行 + `max_waiting` 有界队列（`render_queue_full`）+ deadline kill / `wait()` reap / 下一次重启 + `close()`；`render()` 回 `RasterBuffer`（父进程自己的字节）；`shared()` / `shutdown_shared()` 一个进程一个 child |
| `rendercore/raster.py` | `SOURCE_MAX_PIXELS` / `DOCUMENT_MAX_PIXELS`（A 评审）；`encode_png()`（纯标准库，色型 2 / 6，pHYs 只在 dpi 已知时写）、`write_tiff()`（复用 `tiffwrite.py`） |
| `rendercore/preview.py`（新） | `PreviewCache`：键 = `sha1(源 id \| 内容 sha256 \| 宽 \| 背景 \| rendercore 版本 \| PDFium 版本 \| 字体政策版本)`，同键锁表、临时发布 + Windows 退让、零字节重建、异常抛出、`prune()`；**不接 app.py** |
| `rendercore/job.py` | 一定写 Canonical PDF；要了 PNG / TIFF 就交给 child 栅格一次，两种容器从同一个 RasterBuffer；child 失败 → 该格式 `format_failed(raster_code)`，PDF 照常；PDF 没写出来 → 位图说「Canonical PDF 未写出」；EPS 逐项 unsupported；`host=None` 用 `renderhost.shared()` |
| `rendercore/ir.py` | `CAPABILITIES["png"] / ["tiff"]` 十个操作全 `rasterized`（理由：PDFium 栅格化 Canonical PDF） |
| `scripts/dev/u07_evidence.py`、`u07_freeze_child.py`（新） | evidence 生成器（30 条核对）；最小 PyInstaller freeze（U02 `freeze_spike.py` 收编） |
| `scripts/dev/u02_spikes/` | **render 半边删除**：`fonts.py` shim / `pdfwrite.py` / `render_spike.py` / `render_child.py` / `freeze_spike.py`；留 `hashcheck.py` / `runtime_spike.py` / `requirements.txt`（U05 收编时清） |
| `tests/test_foundation_u02_render_child.py` | 删除（由 `tests/test_rendercore_renderchild.py` 取代）；`test_foundation_u02_render.py` 留（纯标准库读 evidence/u02） |
| `tests/test_source_hygiene.py` | `stdout=PIPE` 判据的单文件例外删除（scripts/ 里不再有流式客户端） |
| `.github/workflows/foundation-u02-spikes.yml` | 只剩 runtime 半边（fonts / render_spike / render 用例 / freeze 步骤与 paths 删除） |
| `.github/workflows/foundation-u06-rendercore.yml` | 加 U07 用例、`u07_evidence.py`（PDF 逐字节判、PNG 只记）、`u07_freeze_child.py`（每腿冻结 + 自起）；装 pyinstaller；45 min |
| `tests/support/pdfread.py` | `decode_png_any()`（RGB / RGBA）；旧 `decode_png` 保留 |
| `tests/test_rendercore_renderchild.py` / `test_rendercore_rasterize.py` / `test_rendercore_preview.py` / `test_rendercore_calibration.py`（新） | 假 child 控制流（任何机器）+ 真 child（rc-venv）；PNG / TIFF 同源（纯模型 + 经真实 ExportJob）；预览缓存合同（假 host）；旧新后端校准（按 case 阈值） |
| `tests/test_rendercore_evidence.py` | U07 段：进了 git 的 evidence/u07 与 report 互钉、PNG 解码像素 == report 记的 TIFF 像素 sha256、像素采样、freeze 报告 |
| `tests/test_rendercore_ir.py` / `_plan.py` / `_job.py` / `_model.py` / `test_foundation_harness.py` / `test_windows_regressions.py` | 能力表用例改「PNG / TIFF rasterized」；job 用例改正向（PNG 真出、空页空位图、EPS 拒）；懒 import 守卫扩到 rasterio / renderchild / renderhost / preview；enrollment 计数 + observing 1；evidence/u07 两份 JSON 进 LF 表 |
| `.gitattributes` | evidence/u07 的 JSON / freeze 报告 LF，PDF / PNG binary |
| `docs/implementation/tavotto-foundation/evidence/u07/` | `truth.json`（手写）、`u07.pdf`、`u07_pdfium.png`（macOS arm64 基线）、`report.json`、`freeze/report-darwin-arm64.json` + `frozen-render-darwin-arm64.png` + `pyinstaller-log.txt` |
| 文档 | ADR 0065 / 0066；`docs/rules/backend/rendercore.md` 合成 + 栅格 + 预览缓存 + 跨平台基线四节；`src/tavotto/AGENTS.md` 速查行；`U00_FACADE_LEDGER.json` 的 `compose` / `render_preview_png` / `probe_asset` 加 `migration_evidence`、`canvas_methods` 加 U07 注；`enrollment.json` 加 `U07-R1`（observing）；`plan.json` U07 `done`；README U07 段；本文件；PACKAGE_CONTENTS 重算 |

**关联旧要求 ID / 场景 ID**：R07（RC-038 ~ RC-046）、R08（RC-047 ~ RC-053、RC-061 / 062）、R13（RC-094 / 096）。
逐条处置：

| ID | 处置 | 证据 |
|---|---|---|
| RC-038 非零原点页盒 | 做了 | `test_the_visible_box_is_the_crop_box_not_the_media_box`（U00 夹具 CropBox [15 10 285 170]，解析式落点 + 像素） |
| RC-039 Rotate / UserUnit 只应用一次 | 做了 | `test_source_rotate_is_applied_exactly_once[0/90/180/270]`、`test_userunit_scales_the_visible_box…`；反证「忽略」与「重复」各红 |
| RC-040 镜像保矢量 / 文字 | 做了 | `test_a_mirrored_import_keeps_its_vector_and_text_layer`（墨左右互换 + 无 image 对象 + 文字抽得到） |
| RC-041 面板 opacity 是透明组 | 做了 | `test_panel_opacity_is_a_transparency_group_not_per_object_alpha`（(255,128,128) vs (191,64,128)）、U00 夹具那一对 (191,128,191) vs (160,96,191) |
| RC-042 透明度零值 | 做了 | `test_opacity_zero_paints_nothing_but_the_vector_object_is_still_there`、`test_panel_opacity_zero_is_a_value_not_an_absence`（变异 `or 1.0` 红） |
| RC-043 同名资源不冲突 | 做了 | `test_same_named_resources_in_two_sources_do_not_collide`（/F1 / /X1 / /GS0 各不同）、两实例一份 form |
| RC-044 透明 / 镜像 / 裁切组合 | 做了 | 32 组组合矩阵 + 框外一圈白 |
| RC-045 白底 / 透明画布 | 做了：白底 RGB / 透明底 RGBA（child 从全 0 起算，straight alpha），PNG 与 TIFF 各自表达 | `test_rendercore_rasterize.py::test_transparent_background_yields_rgba_with_straight_alpha_in_both`、`test_rendercore_renderchild.py::test_real_child_transparent_render…` |
| RC-046 不可信 PDF 政策 | 做了（不带注释 / 动作 / JS；加密 / 坏 / 缺页结构化拒绝） | `test_actions_annotations_and_javascript_of_the_source_are_not_imported`、加密 / 坏文件 / 缺页三条 |
| RC-014（第二道核） | 做了 | `test_source_bytes_must_match_the_resource_identity` |
| RC-015 / 016 实例隔离 | 做了（写入器级） | `test_concurrent_writers_do_not_share_any_state`；跨项目并发真图仍归 U08 |
| RC-047 PDFium 进程内串行 | 做了（native 根本不在父进程；一个 child 一把锁） | `test_requests_are_serialized…`、`test_concurrent_probe_render_inspect_all_go_through_one_child_in_order`（seq 严格递增） |
| RC-048 child 属于应用运行时 | 做了（`child_argv()` = 本解释器 / 冻结 exe；不装进用户 .venv） | 结构性 + freeze 报告 |
| RC-049 冻结程序正确起子进程 | 做了（最小 freeze，macOS arm64；其它平台看 workflow 工件）；**真产品包（tavotto.spec）归 U10 / U11** | `evidence/u07/freeze/report-darwin-arm64.json`（7/7） |
| RC-050 native 显式释放 + timeout / crash 恢复 | 做了 | 真 child 超时 kill → reap → 恢复、坏 PDF 不死、崩溃 / 外杀恢复 |
| RC-051 RasterBuffer 通道 / stride / alpha | 做了 | `test_rendercore_raster.py`、`test_rendercore_rasterize.py`（padding stride / alpha 边缘 / 同灰度异色） |
| RC-052 PDF / PNG / TIFF 同一 canonical PDF | 做了（交付的 PDF 再栅格 == 交付的 PNG 逐字节） | `test_pdf_png_and_tiff_come_from_one_canonical_pdf_and_one_raster`；变异「另画一张页」红 |
| RC-053 同参数 PNG / TIFF 像素逐个相同 | 做了（精确） | 同上 + 纯模型两容器用例 + evidence 的 `png_tiff.same_pixels` |
| RC-061 cache 身份 / 并发发布 / 旧版本失效 | 做了（键含内容 sha256 / 后端 build / 字体政策；同键去重；Windows 退让；零字节重建） | `test_rendercore_preview.py` 全部 |
| RC-062 像素 / 内存 / 队列预算 | 做了（父子两侧像素预算、有界队列背压、`RLIMIT_AS` 只 Linux 生效并如实记录） | `test_a_bounded_queue_pushes_back…`、预算用例 |
| RC-094 RGBA 比较覆盖灰度漏检 | 做了（同灰度异色 + 纯 alpha 差在两个容器里都活着；校准对拍逐 RGBA 通道） | `test_same_luminance_colors_and_alpha_edges_survive_both_encoders`、`test_rendercore_calibration.py` |
| RC-096 旧新差分不把旧缺陷当真值 | 做了（opacity 面板：旧位图 vs 新透明组只比落位框；文字基线差 = 批准的度量差） | `test_rendercore_calibration.py` |

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `<rc-venv>/bin/python -m pytest tests/test_rendercore_*.py tests/test_font_provenance.py tests/test_import_architecture.py -rs`（候选包 + 字体） | 同上 | 0 | **314 通过 / 0 skip** |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest <同一批 + test_tutorial / test_source_hygiene / test_docs_references / test_agents_rules_index / test_foundation_facade_ledger>`（主仓库 `.venv`，没装候选包） | 同上 | 0 | 通过；rendercore 的候选包用例 skip 并写明理由（skip 不是绿，真跑在 rc-venv 与 `foundation-u06-rendercore.yml`） |
| 变异反证 16 条（`scratchpad/u07/mutate_a.py`，ADR 0065 §5） | 同上 | 每条非零 | 「`plan` 的 opacity 用 `or 1.0`」第一版绿——没有面板 `opacity: 0` 的 plan 用例；补了 `test_panel_opacity_zero_is_a_value_not_an_absence` 后红 |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest`（全量，主仓库 `.venv`，后台 + 日志） | 同上 | 见 PR 正文 | 见 PR 正文 |
| **PR B** `<rc-venv>/bin/python -m pytest tests/test_rendercore_*.py tests/test_font_provenance.py tests/test_import_architecture.py tests/test_windows_regressions.py`（含真 child） | 同上 | 0 | **见 PR B 正文的数字 / 0 skip** |
| **PR B** 主 `.venv`：假 child / 假 host / 纯模型 + hygiene / workflow 合同 / harness / ledger / docs 索引 | 同上 | 0 | 通过；真 child / 候选包用例 skip 有理由 |
| `scripts/dev/u07_evidence.py --out evidence/u07` | 同上；`u07.pdf` 8 179 B、`u07_pdfium.png` 945 × 709 RGB、`report.json` | 0 | **30/30**；两次运行 PDF 与 PNG 逐字节相同 |
| `scripts/dev/u07_freeze_child.py --pdf page.pdf --out evidence/u07/freeze` | 同上；PyInstaller 6.19 onedir 202 文件 87 MB（不进 git） | 0 | **7/7**：冻结 exe 以 `--render-child` 自起、libpdfium + libqpdf + 13 字体在包里、probe 270 × 160、渲染 400 × 237 |
| PR B 变异反证 19 条（`scratchpad/u07/mutate_b.py`，ADR 0066 §2） | 同上 | 每条非零 | 「PNG 不从 Canonical PDF 出」第一版变异写了一张**相同**的 PDF（语义 no-op）绿——换成不同的页才红 |
| Linux / Windows / 3.10 | `foundation-u06-rendercore.yml`（PR 上随 rendercore 文件变动自动触发） | 见 PR 正文 | run 号与四条腿结论填在 PR 正文与下面「其它目标」 |
| 前端 `pnpm test && pnpm build` | — | — | **not_run**：本阶段没有改 `web/src` |

**本切片的正例、负例、旧行为回归**：正例 = 上表；负例 = 加密 / 坏文件 / 缺页 / 字节身份不符 / 没交字节 / 扩展名与字节
不符 / 位图解不开，全部结构化拒绝且不落文件；旧行为回归 = 产品默认路径零改动（`app.py` / `pdfbackend/` / `web/src` 一字未动），
U06 用例一个不删（能力表用例按 U07 状态改写）。

**本次是否改变 case enrollment（planned / observing / enforced / later）及理由**：**加了一条 `U07-R1`（observing）**——
RenderBench 实例：evidence/u07 的合成 + 栅格经三平台 `foundation-u06-rendercore.yml`（已有的具名非 required 任务，不新开）
真跑 30 条核对与最小 freeze，main .venv 里 `test_rendercore_evidence.py::test_u07_evidence_is_current` 只核进了 git 的字节。
不经产品入口（`app.py` 不 import rendercore），所以不是 FO 场景的资格、不进 required；提升 enforced 要等 U08 接 facade
再走 05 §3 的整条用户链。台账计数 planned 31 / later 1 / enforced 1 / observing 1（`test_foundation_harness.py` 跟着改）。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run）：Linux / Windows / 3.10 上的 render child 与 freeze（workflow 每腿给，结论填「其它目标」）；macOS x86_64；
  `RLIMIT_AS` 超限触发试验（Linux 只验 set，没做超限）；跨项目并发真图 / `scope=original` / `annotate_asset` / facade 接线
  （U08）；产品包 `tavotto.spec` 带 child + 签名（U10 / U11）；registry 220 条产品实例；前端。
* 基础设施问题：主仓库 `.venv` 没装候选包，真 child / 候选包用例在必需矩阵里是 skip（有理由；假 child / 假 host / 纯模型
  用例在那里真跑）。
* 真正产品失败：无新增。顺带发现的事实：① PDFium `get_size()` 忽略 `/UserUnit`（child 的 probe 用 pikepdf 读一次乘上，
  与旧 `probe_asset` 是有意差异）；② qpdf 把 `/Rotate` + `/UserUnit` 折进 form 的 /Matrix，写入器不能再自己应用；③ 旧后端
  在 opacity < 1 / flip 时退位图——新写入器保矢量（校准对拍：只比落位框 + 框内像素差 3.1%）；④ 旧后端文字基线用的是 PyMuPDF
  给 base-14 的 **bbox** ascender（Times 1.053）套在 typographic 公式里，Liberation 用 OS/2 typo ascender 0.693——12 pt 时
  基线差 1.77 pt，是 ADR 0060 §4 批准过的 D07 迁移，校准用例钉的是「差恰好等于这个量」；⑤ Python `round()` 是四舍六入
  五成双（270 × 150 / 72 = 562.5 → 562），与旧 app 报 `width_px` 的同一个函数——报出去的数就是 buffer 的尺寸。

**批准的字体 / 视觉差异，及未授权变更检查**：本阶段没有改任何产品输出（默认路径零改动）。有意差异（记进 U08 对拍表，
校准用例已钉）：opacity < 1 / flip 的面板从位图变矢量；probe 按 UserUnit 乘；文字基线差 = 批准的度量差；PNG / TIFF 尺寸用
`round()`（与旧报数同函数，旧 PyMuPDF 位图本身是 ceil）。`git diff --stat` 只含上表的文件；`LICENSE`、ruleset、
`aggregate_gate.py`、required job、默认后端、`requirements.txt`、默认 `dependencies`、`packaging/tavotto.spec` 一个都没动。

**当前可合并依据（不等于可以默认启用 / 发行）**：两个 PR 都是中高风险档（改 `src/` / `tests/` / `scripts/` / `.github/` /
`pyproject.toml`）→ `@codex review`（叠栈 PR 按 runner 纪律先不打 `full-ci`，轮到 base 改 main 再加）；ruff 两条 0；rc-venv
0 skip；主 `.venv` 针对性 0；A 16 + 4、B 19 条变异逐条红；全量 pytest 见各 PR 正文；两个 workflow 的合同用例
（`test_merge_queue_workflows`）全过、actionlint 0。

**仍缺哪些默认启用 / 精确安装物资格**：全部。候选栈未默认启用；freeze 只证明「候选 native 库 + 字体 + child 自起」过
PyInstaller（本机 macOS arm64 + workflow 三平台），**不是** `packaging/tavotto.spec` 的产物；签名 / 公证 / 干净机器归 U11。

**下一个无阻塞阶段 / 子切片**：U08（依赖 U07）。给 U08 的输入：
* facade 19 项的对照：`compose` 面 → `job.produce`（Canvas 的 place / save_pdf / save_png / save_tiff / size_pt 全在里面）；
  `render_preview_png` + `app.py` 的 `_write_render_cache` / `_publish_render_cache` / `_cache_write_lock` / `source_sha1` →
  `preview.PreviewCache`（把 memo 收编进 `source_identity()`）；`probe_asset(kind="pdf")` → `RenderHost.probe()`（**含 UserUnit**，
  记进对拍表）；`probe_asset(kind="raster")` → `rasterio.header_size()`；`pdf_fonts` → `RenderHost.inspect()` 要加字体名
  （PDFium raw `FPDFText_GetFontInfo`）或 pikepdf 读；`compare_png` 不动（它是比较器不是后端）。
* `ExecutionSourceResolver`（worker + 回执 → `origin=execution`）接 `_serialize_figure`；`scope=original` 的 PDF 整页搬运可以
  直接用 `pdfwriter._foreign_form`（copy_foreign）——**不重画**。
* `job.produce(host=None)` 用进程级 `renderhost.shared()`；app 关停时调 `shutdown_shared()`；`RenderHost.max_waiting` 是
  HTTP 并发的背压，`/api/render` 撞到 `render_queue_full` 该回 503 而不是 500。
* 错误码若要用户可见：`WriterError.code` / `RenderChildError.code` / `PreviewError.code` 进 i18n（本轮走既有的
  `export_render_failed` / `format_failed`）。
* `canvas_coverage.json` 切表：primary 取 12 张 Liberation 脸交集（U06 交接）。

**回退方式、不能假装可回滚的外部副作用**：revert 两个 PR 即回退（新模块 / 用例 / 文档 / extra 一行依赖 / 台账状态；
spike 的 render 半边可从 git 历史找回）；没有设置写入、没有发布。外部副作用只有本机 scratchpad 里的 rc-venv、
`src/tavotto/resources/fonts/`（gitignored）与 PyInstaller 的临时目录（已删）。

## 其它目标（`foundation-u06-rendercore.yml` 的四条腿，PR B 上随 rendercore 文件变动自动触发）

（结论按 run 号逐腿填；没跑出来的腿保持 **not_run**，不预填。判的是 `u07.pdf` 逐字节与 30 条核对 + freeze 7/7；
PNG 的 sha256 只记不判。）

| 腿 | run | rendercore 用例 | u07.pdf 与 git 逐字节 | u07 30 条 | freeze 7/7 | PNG sha256（信息） |
|---|---|---|---|---|---|---|
| ubuntu-latest py3.10 | not_run | — | — | — | — | — |
| ubuntu-latest py3.13 | not_run | — | — | — | — | — |
| windows-latest py3.13 | not_run | — | — | — | — | — |
| macos-latest py3.13 | not_run | — | — | — | — | — |
