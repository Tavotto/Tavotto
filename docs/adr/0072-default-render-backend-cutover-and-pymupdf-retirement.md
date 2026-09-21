# ADR 0072：默认渲染后端切换为 RenderCore 与 PyMuPDF 退役

日期：2026-09-22 · 状态：**Accepted（U10；默认已切、旧实现已删；发行资格未取得——那是 U11）**
相关：[0067 候选切换开关](0067-render-backend-switch-and-execution-sources.md)、[0060 字体政策](0060-font-policy-and-allowlist.md)、
[0073 字体默认变更的布局政策](0073-default-font-layout-policy.md)、[0055 render_spike](0055-render-spike.md)、
[0059 Render IR](0059-render-ir-and-render-plan.md)、[0065 合成](0065-imported-page-composition.md)、
[0066 render child](0066-render-child-and-raster-buffer.md)、[0068 产物验证](0068-artifact-inspector-and-manifest.md)、
[0070 回执与身份](0070-execution-receipt-and-manifest-identities.md)、[0053 合同与准备](0053-foundation-contracts-and-preparation.md)；
实施包 `docs/implementation/tavotto-foundation/`（`phases/U10_cutover.md`、`00_MASTER_PROMPT.md` §4、
`01_SCOPE_AND_DECISIONS.md` D03 / D07 / D15、`03_CI_POLICY.md` §3–§4 / §6、`06_ENABLE_AND_RELEASE.md` §3；
registry R13 / R14 / R15 / CP08 / CP08-E）；`docs/legal/COMMERCIALIZATION_DEPENDENCY_AUDIT.md` 的 2026-09-22 附录。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 默认后端 | **`pdfbackend.BACKEND_DEFAULT = rendercore`**，`BACKENDS` 闭集只剩它；`TAVOTTO_RENDER_BACKEND=pymupdf` 是明确的 `BackendSelectionError(backend_retired)`（`app.py` 漏斗 → 500 + 稳定 code + 双语文案），不认识的取值仍 `backend_unknown`；**不静默回退、不猜** | `pdfbackend/__init__.py`；`tests/test_rendercore_facade.py::test_the_retired_pymupdf_name_is_a_loud_error_not_a_fallback`、`tests/test_rendercore_app.py::test_the_retired_backend_name_is_refused_at_the_real_entry_not_swapped` |
| 旧实现 | **删除** `pdfbackend/pymupdf_backend.py`（D03 的另一条路——移到隔离迁移路径——没选：留着它就留着「悄悄 import 回来」的入口）；`app.py` 的旧分支（`_export_produce_canvas` / 旧 `/api/render` 键 / `_write_render_cache` / `_publish_render_cache` / `source_sha1` memo）一并删除，`/api/render` 只剩 `PreviewCache` 一条路 | `app.py`；`tests/test_render_cache.py`（真实端点的用户合同）|
| 依赖 | pikepdf / fonttools / uharfbuzz / pypdfium2 / pillow **从可选 extra 变成 `dependencies`**（`requirements.txt` 钉死镜像同名同序；`requirements-rendercore.txt` 与 `rendercore` extra 删除）；`pymupdf` 只在 **`legacy-pymupdf`** extra（`dev` 经自引用 extra 带进来，给测试的独立读取器与 `scripts/dev/` / 维护者资产脚本），绝不进运行时闭包 | `pyproject.toml`、`requirements*.txt`；`tests/test_rendercore_fonts.py` 两条镜像用例 |
| 退役扫描（06 §3「切换后正式发行闭包没有旧库」） | `scripts/ci/retirement_scan.py` **五把尺子**：应用源码 AST（含 `import_module` / `__import__` 字面量）/ 发行版声明的运行时依赖闭包（递归、无 extra）/ 干净新进程（`-I`）装 meta_path 阻断器跑主要路径（契约层 14 条 + doctor + MCP bridge；阻断器先在标准库模块上证明会咬）/ 产物 native 名单 + wheel METADATA + 字体 13 张 / SBOM（发布链才有）。**主语是应用 / 发行 / runtime 闭包**（D15）：`tests/**` 读取器、`scripts/dev/**`、维护者资产脚本、用户科学脚本的 `import fitz`、git 历史与文档字样按类别写明为例外，`--selftest` 七条正负例 | `tests/test_retirement_scan.py`（前三把随 pytest）；ci.yml `package` 扫 wheel、`windows-exe-smoke` / `macos-app-smoke` 扫冻结产物；切换前在旧闭包上判红的记录 `evidence/u10/pre_cutover/retirement_scan_old_closure.json` |
| 生成物 | `pdfbackend/canvas_coverage.json` 与 `tests/golden/glyph_plan_vectors.json` 由批准字体集合出（生成器只剩一个落点，`--backend` 删除）；旧表 / 旧向量存档为批准资产 `evidence/u10/*.pymupdf.json`，差异闭集由 `tests/test_rendercore_glyph_vectors.py` 钉着（§2） | 两个生成脚本、`tests/test_rendercore_glyph_vectors.py` |
| 旧行为的参照 | 只在批准资产：`tests/fixtures/legacy_pymupdf/`（退役前一提交 `8961883d` 上旧后端跑出的 `oracle.json` / `preview_300.png` / `calibration/<case>.pdf`）与 `tests/golden/shape_geometry_vectors.json`（旧 facade 记下的多边形 / 虚线向量，前端与 rendercore 各跑一遍）。对拍运行器 `scripts/dev/u08_parity.py` 与 `foundation-u06-rendercore.yml` 的对拍步删除——普通测试就是「在新实现上跑」 | `tests/test_rendercore_facade.py`、`test_rendercore_calibration.py`、`test_rendercore_geometry.py`、`web/src/lib/shapeGeometry.golden.test.ts` |
| 旧测试 | **用户合同一条不删**：直接驱动旧私有函数的排版 / 箭头 / 裁剪用例改走契约层 `compose()` + 独立读取器（PDFium 字符级文字层 `tests/support/pdftext.py`、内容流坐标）——期望值一字未改（§3）；U08 的 14 条 deselect 逐条处置（ledger `candidate_parity.deselected[*].u10_disposition`：deleted 2 / migrated 9 / kept 3） | `tests/test_compose_text.py`、`test_compose_arrow.py`、`test_paths_and_baked.py`、`test_typography_families.py`、`test_glyph_plan.py`、`tests/test_foundation_facade_ledger.py::test_candidate_parity_deselections_were_each_dispositioned_at_cutover` |
| 打包 | `tavotto.spec`：缺批准字体拒绝打包（判据与 `fetch_fonts.py --check` 同源）、`collect_dynamic_libs("pypdfium2_raw")` + `collect_all("pikepdf")`、`PIL` 出 excludes、`pymupdf` / `fitz` 进 excludes；`packaging/entry.py` 最先分派 `--render-child`；`build_desktop.py` / ci.yml / nightly / desktop-tauri 在 PyInstaller 前取字体、打包依赖装 `requirements.txt`；`lab_acceptance.py` 结构检查加字体 / 覆盖表 / 闭包零 pymupdf | `tests/test_runtime_build.py::test_spec_ships_the_rendercore_closure_and_refuses_to_freeze_without_the_fonts`、`tests/test_merge_queue_workflows.py::TestApprovedFontsAndRetirementScan` |
| CI | 每条装项目 / 打 wheel / PyInstaller 的腿先 `fetch_fonts.py`（不上 actions/cache：CI02 把它钉成 CPython 归档的闭集，12 MiB 两个文件不值得开第二类）；rendercore 用例从此在必需矩阵里真跑（U08-R1 提升 enforced，§5）；`foundation-u06-rendercore.yml` 只剩三平台 evidence 复现 + 冻结 child + FO32 观察腿 | `.github/workflows/*.yml`、`enrollment.json` |
| 仓库规则修订 | 根 `AGENTS.md` 两条不变量正式改写（「pymupdf 唯一 import 点」→「渲染闭包零 PyMuPDF，扫描门禁看护」；1.0 收敛纪律加实施包登记例外）；`docs/rules/backend/pdf-backend-boundary.md` 重写为 RenderCore 边界；同源对表加 `dependencies ↔ requirements.txt` 一对、shapeGeometry 一对换共享向量 | 各文件；`tests/test_agents_rules_index.py` |
| 回退 | `git revert` 本切换变更即回到「候选未启用」（U09 tip 的形状）：不自动发布、不删用户项目 / 环境、不把旧依赖装进新发行包；预览缓存旧文件不用清（键含后端身份，自然不命中、按预算淘汰） | §6 |

## 1. 切换前核验（都在退役前的 U09 tip `8961883d` 上做，evidence/u10/pre_cutover）

* **facade 覆盖 × 入口**：U08 的 19 项 + Canvas 面 + 真实入口（HTTP 同步 / 异步 / SSE、MCP、`/api/render`、写回）
  在候选下逐字重跑：`u08_parity.py` **654 通过 / 0 失败 / 13 skip / 14 deselect**（skip 全是 workerd 二进制与画布产物
  没建，与后端无关）；退役扫描的 block 尺子在 `TAVOTTO_RENDER_BACKEND=rendercore` 下 17 步全过；`smoke_app.py`
  全路径（启动 → 版本 → 环境自检 → 开项目 → 渲染 × 2 → 导出 × 2 → 干净退出）退出 0 且阻断器在父进程装上；
  U09 的 `existing_env_join` 出口已在候选下通过（ADR 0070）。
* **负例**：同一把扫描器在旧闭包上 source / deps / block 三尺**全红**（`retirement_scan_old_closure.json`）——
  切换前「候选不依赖旧库」与切换后「闭包没有旧库」是两个判据，不是循环前置。
* **依赖闭包 wheel 表**（`scripts/dev/u10_wheel_matrix.py`，`evidence/u10/wheel_matrix.json`）：五个原生包 ×
  Python 3.10–3.14 × 三个正式目标（win x64 / macOS arm64 / Linux x86_64）**75/75 有 wheel**；informational：
  Linux aarch64 全有；**macOS x86_64：pikepdf 10.x 五档全部没有 wheel**（9.11.0 是最后一版有 Intel wheel 的）。
  这不是本 ADR 的裁决范围（支持矩阵里 Intel Mac 本来就是 unsupported 桌面版），但 `support-matrix.json` 那条
  「Intel 用 pip 浏览器模式」的出路从此要多一句：pip 装 pikepdf 得自己编译（qpdf + C++ 工具链）。**待用户拍板**
  的两条路：如实改口（本 ADR 先按这条写进 note），或把 pikepdf 下界放到 9.x（要另验 API 与 U02 的钉死版本）。
* **许可与 NOTICE**（D15 主语是应用 / 发行闭包）：pikepdf MPL-2.0（qpdf Apache-2.0；传递 lxml BSD-3 / Pillow HPND）
  / fontTools MIT / uharfbuzz Apache-2.0（HarfBuzz MIT-old）/ pypdfium2 Apache-2.0 + BSD-3（PDFium）/ Liberation 与
  Noto Sans SC OFL 1.1。义务：MPL §3.2 告知源码获取方式（与既有 5 个 MPL crate 同一类，#182 的 NOTICE 链）；
  OFL 全文与版权随字体同目录分发（已做）。法务文档的处理：`LICENSING.md` / 权利政策改口，审计文档加附录
  不重基线——重基线与律师复核是另一项工作。
* **冻结候选闭包**：`scripts/dev/u07_freeze_child.py` 本机 macOS arm64 **7/7**（PDFium 库进产物、13 张脸、
  child 以同一 exe `--render-child` 自起、真 probe / 真渲染）；其它目标 not_run（不借宿主）。

## 2. 用户可见变化（一张表，全部是批准过的迁移，不是缺陷）

| 变化 | 旧（PyMuPDF 1.28.2） | 新（RenderCore） | 出处 |
|---|---|---|---|
| 画布文字的脸 | base-14（Times-Roman / Helvetica / Courier 三族）+ Droid Sans Fallback CJK + 渲染器自选的 Noto Serif 回退 | Liberation Serif / Sans / Mono（12 张脸，子集嵌入）+ Noto Sans SC 子集；**没有回退脸** | ADR 0060 §1、0073 |
| 覆盖表（`canvas_coverage.json`） | primary 653 / cjk 34 012 / fallback 27 610 个码位 | primary 2305（+1668 / −16）/ cjk 30 887 / fallback 0；可画码位 61 238 → 32 608 | `tests/test_rendercore_glyph_vectors.py::test_the_coverage_table_lost_only_the_fallback_layer_and_gained_only_approved_primary` |
| 字形归属向量（69 条）里变的 7 条样例 | `⁵` / `₂` fallback、`⁻` / `😀` / `𝛼` fallback | `⁵` / `₂` → primary；`⁻` / `😀` / `𝛼` → missing（`⁻` 由 auto 档合成成上标 `-`，画面零方框、ActualText 还原原文；`😀` / `𝛼` 是方框 + 问题面板一条 + 文本层里字还在） | `test_vectors_differ_from_the_retired_backend_only_in_the_approved_ways` |
| 预检 `glyph-substituted` | `×10⁵` 报一条「⁵ 换了脸」 | 不报（没有换脸这回事）；`canvas-text-glyph-substituted` 向量期望改成空 | `tests/golden/preflight_vectors.json` |
| 预检 `bitmap-embed` 与检查器「翻转 / 半透明将嵌入为位图」 | 翻转 / opacity < 1 的面板退位图，矢量文字不可选 | **保矢量**（透明组 / 负缩放，ADR 0065）；规则与三段文案**删除**（它们说的事不再发生） | `preflight.py` / `preflight.ts` / `PanelSection.tsx` |
| `scientific` 解释档 | `×10⁵ H₂O` 折成 `×105 H2O`（Helvetica 没有 `⁵` / `₂`） | 主脸自带就原样落笔、文本层不降级；只合成主脸没有的（`⁻`），且 ActualText 还原原文 | `tests/test_glyph_plan.py::test_scientific_mode_draws_everything_with_one_face` |
| 文字基线 | Times bbox ascender 1.053 | Liberation OS/2 typo ascender 0.693（12 pt 差 1.77 pt）；advance 相同、换行不变、位置 / 内容 / 框尺寸不变 | ADR 0073；`tests/test_compose_text.py`（全部换行期望值一字未改仍过） |
| 零修改往返的像素 | `Two` 样例 < 2% / mean 0.5 | PDFium 对 Type 3 字形的抗锯齿不同：3.28% / mean 1.33，阈值按 case 记 4% / 1.6（另外两个 case 不动） | `tests/test_mcp_normalize.py` |
| PNG 的 pHYs | 矢量源栅格化的 PNG 恒 96 dpi | 写真实 ppi | ADR 0067 §2 |
| 写回带标注 | incremental save | 整份重写（原内容流与资源不动） | ADR 0067 §2 |
| `/api/render` 缓存 | 键 `sha1(id|内容 sha1|宽|后端-版本)` + (mtime, size) memo | `PreviewCache`（键多 PDFium 版本 / 字体政策 / 背景；身份从抄出来的字节上算）；旧缓存文件不命中、按预算淘汰 | `docs/rules/backend/pdf-backend-boundary.md` |
| 错误 | — | `TAVOTTO_RENDER_BACKEND=pymupdf` → `backend_retired`（500 + 双语文案） | `errors.json` |
| 产物核验 | PDF 的 `fonts_embedded` / PNG 的 `dpi_tag` 在默认路上是可选项 failed | 都 verified（子集嵌入、真实 ppi） | ADR 0068 |

## 3. 旧测试的处置（D07 / RC-093：用户合同一条不删）

直接驱动旧私有函数的用例改走契约层，独立读取器换成 PDFium 字符级文字层（`tests/support/pdftext.py`：原点 /
字号 / 字体 / 包围盒，与旧 `rawdict` 同一口径）与内容流坐标（`test_compose_arrow.py`）——`test_compose_text.py`
21 条的换行 / 对齐 / 行距 / 上下标 / 超宽单词期望值**一字未改**仍全过（advance 兼容成立的直接证据）；
`_crop_clip` → `placement.place().clip`；`hex2rgb` 经契约层。按 D07 改口的断言（base-14 名 → Liberation 名、
`₂` 层归属、`×10⁵` 层集合、scientific 档）逐条写在用例 docstring 里。测试文件里 33 处 `import pymupdf` 保留：
它们是**独立读取器 / 夹具制造者**（D15），经 `legacy-pymupdf` extra 进测试环境，不在任何发行闭包里；
`scripts/ci/compat_matrix.py` 的「解得开」判据改用 pypdfium2（在闭包里，不再有「没装就跳过」的档）。

## 4. 反证（每条变异一次就红）

| 变异 | 红在 |
|---|---|
| `BACKEND_DEFAULT` 改回 `pymupdf` | `test_the_default_backend_is_rendercore_…`；`scan_deps` 不红（主语是依赖）但 `scan_block` 红（import 被阻断） |
| `selected()` 把 `pymupdf` 静默映射成 `rendercore` | `test_the_retired_pymupdf_name_is_a_loud_error_not_a_fallback`、`test_the_retired_backend_name_is_refused_at_the_real_entry_not_swapped` |
| 往 `dependencies` 塞回 `pymupdf` | `test_pymupdf_is_only_the_legacy_extra_never_a_runtime_dependency`、`scan_deps`（`retired_in_closure`）、wheel 尺子 |
| 应用源码里加一行 `import fitz` / `import_module("pymupdf")` | `scan_source`（selftest 两条正例） |
| 扫描器忘了装阻断器 | `blocker_selftest`（标准库靶子不咬 → 后面的绿不算数） |
| 产物目录里出现 `libmupdf.so` / 少字体 | `scan_dist`（selftest） |
| spec 把 `PIL` 放回 excludes / 不收 pdfium | `test_spec_ships_the_rendercore_closure_…` |
| ci.yml 哪条腿漏了取字体 / 装了 `pymupdf` | `TestApprovedFontsAndRetirementScan` |
| 覆盖表多出一个 fallback 码位 / 少一条批准差异 | `test_the_coverage_table_lost_only_…`、`test_vectors_differ_…` |
| 排版基线换回 bbox ascender | `test_compose_text.py::test_baseline_matches_css_line_box`、`test_rendercore_calibration.py::text` |
| ledger 里把 deleted 的用例标成 migrated | `test_candidate_parity_deselections_were_each_dispositioned_at_cutover` |

## 5. enrollment 与门禁

* `U08-R1`（facade 19 项 + 真实入口 + 产物验证）**observing → enforced（pr）**：它的用例现在在 backend-fast /
  backend-platforms 的必需矩阵里真跑（依赖在 `dependencies`、字体每条腿取），有正负例（本 ADR §4）。
* `U07-R1`（三平台 evidence 复现 + 冻结 child）仍 observing：主语是别的平台的字节，留在 `foundation-u06-rendercore.yml`。
* 没有新增 required job；新增的都是接在现有 job 上的步骤（字体、退役扫描）。`03_CI_POLICY` §4 的提升条件
  ——预期、合法输入、fixture 先验、命令、产物、证据、负例、预算——在 `enrollment.json` 的 notes 里逐项。

## 6. 没做 / 边界 / 待拍板

* **发行资格未取得**（U11）：最终签名安装物、no-system-Python 目标、SBOM 尺子的第一次真跑（发布链演练）。
* **Intel Mac 的 pip 渠道**（§1 的 wheel 表）——待用户拍板；本 ADR 只如实记事实。
* **覆盖收窄**（61 238 → 32 608 个可画码位；Hangul / 阿拉伯 / 天城 / 数学字母 / emoji 不在）默认**不加脸**：
  产品如实表达（覆盖表 / 预检 / 问题面板），文档写明集合与集合外行为（ADR 0073 §2）；加脸 = allowlist 加一张
  OFL 脸，随时可做——待用户拍板。
* **前端同源预览**（把字体带进浏览器、改 `canvasFontStack`）默认**不做**：画布预览继续用 CSS 字体栈，只切覆盖表
  与测量来源；`test_no_web_font_is_fetched_or_embedded` 继续成立。
* `glyph-substituted` 规则与 `fallback` 层保留为空档（ADR 0060 的四步顺序不变、两侧闭集常量不变），不删。
* `MIN_TAVOTTO_VERSION`：桥的 import 集本阶段没变，仍按 U08 的备注在下次发版抬。
