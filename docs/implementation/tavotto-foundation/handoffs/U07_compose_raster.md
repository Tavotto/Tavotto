# U07 · 完整合成与 PNG/TIFF 同源 — 交接（按 [`../07_HANDOFF.md`](../07_HANDOFF.md) 模板）

**阶段 / 子切片**：U07.compose_raster，两个叠栈 PR：**A** = 合成（ImportedPage / Image 写入、页盒 / Rotate / UserUnit、
crop / 翻转 / 旋转顺序合同、透明组、同名资源、不可信源、pikepdf 裁决，ADR 0065）；**B** = render child 收编 +
RasterBuffer 栅格 + PNG / TIFF 同源 + 预览缓存 + spike 退役（ADR 0066，**本文件在 B 里再更新**）。
本文件此刻记的是 **A 之后**的状态；B 未落地的部分在「未运行」一节如实标 not_run。

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

**实际代码与 API / 数据结构变更（PR A）**：

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
| 文档 | ADR 0065；`docs/rules/backend/rendercore.md` 合成一节；`src/tavotto/AGENTS.md` 速查行；`U00_FACADE_LEDGER.json` 的 `compose` 加 6 条 `migration_evidence`、`canvas_methods.place` 加 U07 注；`plan.json` U07 `in_progress`；README U07 段；本文件 |

**关联旧要求 ID / 场景 ID**：R07（RC-038 ~ RC-046）；R08 / R13 的 RC-047 ~ RC-053、RC-061 / 062、RC-094 / 096 归 PR B。
逐条处置（PR A）：

| ID | 处置 | 证据 |
|---|---|---|
| RC-038 非零原点页盒 | 做了 | `test_the_visible_box_is_the_crop_box_not_the_media_box`（U00 夹具 CropBox [15 10 285 170]，解析式落点 + 像素） |
| RC-039 Rotate / UserUnit 只应用一次 | 做了 | `test_source_rotate_is_applied_exactly_once[0/90/180/270]`、`test_userunit_scales_the_visible_box…`；反证「忽略」与「重复」各红 |
| RC-040 镜像保矢量 / 文字 | 做了 | `test_a_mirrored_import_keeps_its_vector_and_text_layer`（墨左右互换 + 无 image 对象 + 文字抽得到） |
| RC-041 面板 opacity 是透明组 | 做了 | `test_panel_opacity_is_a_transparency_group_not_per_object_alpha`（(255,128,128) vs (191,64,128)）、U00 夹具那一对 (191,128,191) vs (160,96,191) |
| RC-042 透明度零值 | 做了 | `test_opacity_zero_paints_nothing_but_the_vector_object_is_still_there`、`test_panel_opacity_zero_is_a_value_not_an_absence`（变异 `or 1.0` 红） |
| RC-043 同名资源不冲突 | 做了 | `test_same_named_resources_in_two_sources_do_not_collide`（/F1 / /X1 / /GS0 各不同）、两实例一份 form |
| RC-044 透明 / 镜像 / 裁切组合 | 做了 | 32 组组合矩阵 + 框外一圈白 |
| RC-045 白底 / 透明画布 | 模型层已有（`Page.background = None` 不画底）；**PNG 带 alpha 的验证归 PR B**（栅格） | `test_rendercore_writer.py` 的 `page_background` 交叉核对 |
| RC-046 不可信 PDF 政策 | 做了（不带注释 / 动作 / JS；加密 / 坏 / 缺页结构化拒绝） | `test_actions_annotations_and_javascript_of_the_source_are_not_imported`、加密 / 坏文件 / 缺页三条 |
| RC-014（第二道核） | 做了 | `test_source_bytes_must_match_the_resource_identity` |
| RC-015 / 016 实例隔离 | 做了（写入器级） | `test_concurrent_writers_do_not_share_any_state`；跨项目并发真图仍归 U08 |

| 命令 | 目标平台 / 环境 / 产物 | 退出码 | 结果与必要证据 |
|---|---|---|---|
| `ruff check .` / `ruff format --check .` | macOS arm64，worktree | 0 / 0 | 全绿 |
| `<rc-venv>/bin/python -m pytest tests/test_rendercore_*.py tests/test_font_provenance.py tests/test_import_architecture.py -rs`（候选包 + 字体） | 同上 | 0 | **314 通过 / 0 skip** |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest <同一批 + test_tutorial / test_source_hygiene / test_docs_references / test_agents_rules_index / test_foundation_facade_ledger>`（主仓库 `.venv`，没装候选包） | 同上 | 0 | 通过；rendercore 的候选包用例 skip 并写明理由（skip 不是绿，真跑在 rc-venv 与 `foundation-u06-rendercore.yml`） |
| 变异反证 16 条（`scratchpad/u07/mutate_a.py`，ADR 0065 §5） | 同上 | 每条非零 | 「`plan` 的 opacity 用 `or 1.0`」第一版绿——没有面板 `opacity: 0` 的 plan 用例；补了 `test_panel_opacity_zero_is_a_value_not_an_absence` 后红 |
| `PYTHONPATH=$WT/src .venv/bin/python -m pytest`（全量，主仓库 `.venv`，后台 + 日志） | 同上 | 见 PR 正文 | 见 PR 正文 |
| Linux / Windows / 3.10 | `foundation-u06-rendercore.yml`（PR 上随 rendercore 文件变动自动触发） | 见 PR 正文 | run 号与四条腿结论填在 PR 正文 |

**本切片的正例、负例、旧行为回归**：正例 = 上表；负例 = 加密 / 坏文件 / 缺页 / 字节身份不符 / 没交字节 / 扩展名与字节
不符 / 位图解不开，全部结构化拒绝且不落文件；旧行为回归 = 产品默认路径零改动（`app.py` / `pdfbackend/` / `web/src` 一字未动），
U06 用例一个不删（能力表用例按 U07 状态改写）。

**本次是否改变 case enrollment（planned / observing / enforced / later）及理由**：**没有**（PR A）。合成用例是切片自己的
单元 / 短链路，不经产品入口；RenderBench 实例的登记随 PR B 一并决定。

**未运行 / 基础设施问题 / 真正产品失败，分别说明**：

* 未运行（not_run，PR B 的范围）：render child 收编、RasterBuffer 栅格、PNG / TIFF 同源、预览缓存、spike 退役、evidence/u07、
  按平台分基线、旧新后端校准对拍、冻结最小 child；跨项目并发真图（U08）；`scope=original`（U08）；registry 220 条产品实例。
* 基础设施问题：主仓库 `.venv` 没装候选包，`test_rendercore_compose.py` 的 56 条在必需矩阵里是 skip（有理由）。
* 真正产品失败：无新增。顺带发现的事实（进 ADR 0065）：① PDFium `get_size()` 忽略 `/UserUnit`（PR B 的 probe 要自己乘）；
  ② qpdf 把 `/Rotate` + `/UserUnit` 折进 form 的 /Matrix（`[0 −2 2 0 0 540]`），写入器不能再自己应用；③ 旧后端在 opacity < 1 /
  flip 时退位图——新写入器保矢量，像素**不会**逐个相同，几何相同（U08 对拍要按此校准，RC-096）。

**批准的字体 / 视觉差异，及未授权变更检查**：本阶段没有改任何产品输出。有意差异（记进 U08 对拍表）：opacity < 1 / flip
的面板从位图变矢量；probe 将按 UserUnit 乘。`git diff --stat` 只含上表的文件；`LICENSE`、ruleset、`aggregate_gate.py`、
required job、默认后端、`requirements.txt`、默认 `dependencies` 一个都没动。

**当前可合并依据（不等于可以默认启用 / 发行）**：中高风险档（改 `src/` / `tests/` / `pyproject.toml`）→ `full-ci` +
`@codex review`；ruff 两条 0；rc-venv 314 / 0 skip；主 `.venv` 针对性 0；16 条变异逐条红；全量 pytest 见 PR 正文。

**仍缺哪些默认启用 / 精确安装物资格**：全部。候选栈未默认启用；Pillow 进 `rendercore` extra 只是声明，冻结产物里带 Pillow /
libpdfium 的证据归 PR B（freeze）与 U10 / U11。

**下一个无阻塞阶段 / 子切片**：PR B（同阶段）。给 PR B 的输入：`RasterBuffer` 已定形（PNG / TIFF 编码器接它）、`ir.CAPABILITIES`
的 PNG / TIFF 仍全 unsupported（render child 收编时翻成 `rasterized`，`test_u07_pdf_declares_every_operation_native_and_raster_formats_not_yet`
要跟着改）、`WriteFacts.imported_pages / images` 给 evidence 用。

**回退方式、不能假装可回滚的外部副作用**：revert PR A 即回退（新模块 / 用例 / 文档 / extra 一行依赖）；没有设置写入、没有发布。
外部副作用只有本机 scratchpad 里的 rc-venv 与 `src/tavotto/resources/fonts/`（gitignored）。
