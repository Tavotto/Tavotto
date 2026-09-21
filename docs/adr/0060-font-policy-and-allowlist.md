# ADR 0060：字体政策、来源 allowlist 与可检索文字写入

日期：2026-09-21 · 状态：**Accepted（U06 第二切片；候选栈未默认启用，不切默认后端）**
相关：[0059 Render IR 与 RenderPlan](0059-render-ir-and-render-plan.md)、[0055 render_spike](0055-render-spike.md)、
[0033 字形回退](0033-scientific-text-and-font-fallback.md)、[0045 CJK 回退链](0045-cjk-font-fallback-chain.md)、
[0053 U01 合同](0053-foundation-contracts-and-preparation.md)；实施包 `01_SCOPE_AND_DECISIONS.md` D07 / D15、
`03_CI_POLICY.md` §6；`docs/legal/LICENSING.md`、issue #182（NOTICE）。

## 裁决摘要

| 问题 | 裁决 | 落点 |
|---|---|---|
| 批准字体集合 | **Liberation 2.1.5**（三族 × 四态 = 12 个 TrueType）+ **Noto Sans SC Regular**（Sans2.004 SC 地区子集 OTF，CFF CID-keyed）做唯一一张 CJK 脸；两者 SIL OFL 1.1 | `src/tavotto/rendercore/fonts_allowlist.json` |
| 身份 | **文件字节的 sha256 + face 序号**，不是族名 / PostScript 名（RC-023）；allowlist 里每张脸一条 sha256，注册表扫目录时逐个算、不在表里的一律拒绝（RC-022） | `rendercore/fonts.py`（`FontRegistry.discover` / `FaceRecord.identity`）；`tests/test_rendercore_fonts.py`、`test_font_provenance.py` 第四档 |
| 来源 allowlist | 每张脸：来源 URL（GitHub release tarball 成员 / raw 文件）+ tarball 整体 sha256 + 单文件 sha256 + 许可证 + 许可证全文位置 + 版权声明；改这份表 = 改字体政策，先改本 ADR | 同上；`scripts/fetch_fonts.py` 只读它 |
| 分发 | **字体文件不进 git**（`test_repository_ships_no_font_binaries` 继续成立）；`scripts/fetch_fonts.py` 按 sha256 取到 `src/tavotto/resources/fonts/`（`.gitignore` 挡住），wheel 靠 `[tool.hatch.build] artifacts` 收回，PyInstaller 靠 `tavotto.spec` 已有的 `resources/` datas 一整棵带走；两份 OFL 全文与字体同目录随包分发；#182 的 NOTICE 链要列这两条 | `pyproject.toml`、`.gitignore`、`packaging/AGENTS.md` |
| 运行时定位 | `TAVOTTO_FONTS_DIR`（**排他**覆盖，与 `TAVOTTO_RUNTIME_DIR` 同一条纪律）→ 包内 `tavotto/resources/fonts`；目录不存在 / 缺脸 → `FontsUnavailable(code)`，**不摸系统字体、不用另一张脸冒充**（RC-037） | `rendercore/fonts.py` |
| 四层语义 | primary（请求的族 × 粗斜）/ cjk（唯一一张 Noto 脸）/ **fallback 恒空** / missing；`glyphplan.layer_of` 的四步顺序不变。集合外的字 = missing → `CompiledPage.problems` 的 `glyph_missing`；写入器照样写 .notdef、ToUnicode 照样回原字、`WriteFacts.notdef_codes` 记数——**画面上是方框，问题面板里有一条，文本层里字还在** | `rendercore/typography.py`、`pdfwriter.py`、`job.py` |
| 依赖 | pyproject 可选 extra **`rendercore`**（**候选，未默认启用**）：pikepdf ≥10.13,<11 / fonttools ≥4.65,<5 / uharfbuzz ≥0.56,<1 / pypdfium2 ≥5.13,<6；`requirements-rendercore.txt` 是钉死镜像；`requirements.txt` / 默认 `dependencies` 一字不动；候选包只在 `hbshaper` / `pdfwriter` 的函数里 import | `pyproject.toml`、`requirements-rendercore.txt`、`tests/test_rendercore_fonts.py`（镜像一致 + 不进默认依赖） |
| 可检索文字写入 | U02 受限 emitter 收编为 `rendercore/pdfwriter.py`：TrueType → CIDFontType2 + FontFile2（code = 子集 GID）；CFF CID → CIDFontType0 + FontFile3；子集 fontTools（`recalcTimestamp=False`）；`/W` 按 hmtx；`TJ` 位移 = HarfBuzz advance；ToUnicode 按 cluster；ActualText 三种情形；`y_offset` 经 `Ts`。**一个 ActualText 段里只放一个 `TJ`**（PDFium 对跨多个文字对象的 Span 会吐两遍） | `rendercore/pdfwriter.py`；`tests/test_rendercore_writer.py`、`test_rendercore_evidence.py` |
| 默认切换 | **不切**。PyMuPDF 仍是默认；`canvas_coverage.json` 不重生成（前端的方框判据仍跟默认后端走），新集合的表与 diff 只进 evidence | `evidence/u06/canvas_coverage.rendercore.json`、`coverage_diff.json` |

## 1. 集合外明示限制（量出来的，不是估的）

`docs/implementation/tavotto-foundation/evidence/u06/coverage_diff.json`（生成器 `scripts/dev/u06_evidence.py`）：

| 事实 | 数字 |
|---|---|
| 12 张 Liberation 脸的 cmap **不完全相同**：交集 2305、并集 2336（Mono 缺 U+2000–U+200B 一段空格与 U+0237，Serif 缺 7 个占星符号，PUA F00x 各不同）。旧后端「所有 base-14 脸共用一张覆盖表」的假设在新集合上不成立；evidence 表的 primary 取**交集**——前端摆得出的族 × 样式，每一张都画得出才算画得出。U08 换 `canvas_coverage.json` 时按交集或按脸出表，**不能照抄 serif regular 一张** | 交集 2305 / 并集 2336 |
| primary 层：旧 base-14 653 个码位 → 新 2305（拉丁扩展 / 希腊 / 西里尔 / 常用符号 / `⁵` `₂` `Å` `μ` `±` `≤` `≥` `×` `°`） | +1684 / −16 |
| cjk 层：旧 Droid Sans Fallback 34 012 → Noto Sans SC 子集 30 890；CJK 统一表意（含 `齉`、繁体 `體`）、假名（`の`）、`℃` `∇` `∈` 在；**Hangul（U+AC00–，11 172 个）不在** | −3 122 |
| **没有 fallback 层**：旧后端由 PyMuPDF 自己挑 Noto Serif 画出的 27 610 个码位（阿拉伯 / 天城 / 希伯来 / 数学字母 1Dxxx / emoji 1Fxxx / 古文字 …）在新集合里是 missing | 可画码位 61 238 → 32 608（−36 542，+7 912） |
| 科学文本矩阵（`tests/test_scientific_text_matrix.py` 的六项）：原文里只有 `⁻`（U+207B）哪张脸都没有；`auto` 档把 `⁻²` 折成上标 `-2` 后**零方框**，ActualText 还原 `⁻²`。同类：`⁺` `⁼` `ⁱ` `ₙ` 也走合成 | raw missing `⁻`，rendered missing 空 |

这些是本轮的**能力边界**，不是缺陷：要救回其中任何一段，加一张脸进 allowlist（本 ADR 改一行、
`fetch_fonts.py` 不用改），不是给 `fallback` 层塞一张系统脸。Hangul / 阿拉伯 / 天城这类整段脚本的
取舍留给用户拍板（RC-028 split：首轮支持范围内责任齐备，其它脚本边界测试）。

## 2. 许可与义务（D15：主语是应用 / 发行闭包）

| 组件 | 许可证 | 义务 / 备注 |
|---|---|---|
| Liberation 2.1.5 | SIL OFL 1.1（RFN：Arimo / Tinos / Cousine，不碰） | 随发行物附许可证全文与版权声明；子集嵌入 PDF 被 OFL §1 明确允许；不改字体（子集是派生的 PDF 内嵌程序，不是分发一份改过的字体文件）；不单独出售 |
| Noto Sans CJK Sans2.004 | SIL OFL 1.1（RFN："Noto"） | 同上 |
| pikepdf 10.13.x | MPL-2.0（qpdf Apache-2.0）；硬依赖 **Pillow（HPND）+ lxml（BSD）** + packaging | MPL §3.2：可执行形态分发时告知如何取得 Source Code Form——落在 #182 的 NOTICE 生成里，与既有 5 个 MPL crate 同一类。**Pillow / lxml 是传递依赖，产品不调用**：`tiffwrite.py` 仍是纯标准库 TIFF 编码器 |
| fontTools 4.65 | MIT | — |
| uharfbuzz 0.56（HarfBuzz 14.4） | Apache-2.0（HarfBuzz MIT-old） | — |
| pypdfium2 5.13（PDFium 153） | Apache-2.0 / BSD-3-Clause | U07 的栅格运行时；本轮只在测试 / 证据里当独立读取器 |

**pypdf 替代路线（去掉 Pillow + lxml）**：**本 ADR 不裁决**。代价写明：pikepdf 唯一不可替代的用处是
外来页 → Form XObject（`copy_foreign` + 页盒 / `/Rotate` / 资源搬运），那是 U07 的 `ImportedPage` 写入；
U06 的写入器只用到对象模型 / 流 / 序列化，这一半 pypdf 也做得到。U07 实现导入页时一并裁决：留 pikepdf
（接受 lxml 8.6 MB + Pillow 4.8 MB 进闭包）或换 pypdf（自己拼 Form XObject）。两条路都只改 `pdfwriter.py`
的对象模型调用，纯模型层一个字不动。

## 3. 读取器实测（三家 + 一把纯标准库尺子，macOS arm64，2026-09-21）

| 事实 | PDFium 153 | poppler 26.03 | pdfminer.six 20251107 | `tests/support/pdfread.py` |
|---|---|---|---|---|
| 合成上下标 `m⁻²`（ActualText `⁻²`，ToUnicode `-2`） | `m⁻²` | `m⁻²` | `m-2`（不认 ActualText） | 两层都给 |
| 一个 cluster 两个字形 `x̃`（ActualText + 续字形 ToUnicode `<>`） | `x̃y` | `x̃ y`（位置启发式多一个空格） | `x̃ y` | `x̃y` |
| 同一字形两处原文不同（`e+́` 与 `é`） | 各回各的 | 各回各的 | 都回首次记的 `e+́` | 两层都给 |
| 缺字 `𝔸`（.notdef，code 0，ToUnicode 回原字） | **空格**（PDFium 把 CID 0 当空白） | `𝔸` | `𝔸` | `𝔸` |
| ActualText 段跨两个 `TJ` | **吐两遍**（`x̃y x̃`）→ 写入器改成一段一个 `TJ` | 正常 | 不认 | — |
| `<</ActualText <…>>> BDC` 少写一个 `>` | 宽容 | **Syntax Error** | 宽容 | — |

结论进写入器的三条纪律：一个 Span 一个 `TJ`；ActualText 的字典分隔符按 spec 写全；缺字诚实报告、不换脸。

## 4. D07：会变的旧断言——本轮**不切默认**，所以一条都不删

| 旧断言 | 现值 | 迁移后（切默认那一刻） | 替代证据（已在本 PR 里） |
|---|---|---|---|
| `tests/test_typography_families.py` 的 base-14 名 | `Times-Roman` … | `LiberationSerif` / `LiberationSans-Bold` / `LiberationMono-Italic` …（子集前缀另算） | `test_rendercore_writer.py::test_fonts_are_embedded_subsets_of_both_program_kinds_with_tounicode`、evidence `report.json` 的 `writer_facts.fonts` |
| `tests/test_glyph_plan.py` 的 `pdf_fonts()` | base-14 名 | Liberation PostScript 名 | 同上 |
| `tests/test_compose_text.py` 的 asc / desc 反算 | AFM 上升 / 下降 | Liberation `OS/2` sTypoAscender / Descender（Serif 0.693 / −0.216） | `test_rendercore_typography.py::test_the_first_baseline_is_the_css_line_box_baseline_of_the_primary_face`、`test_rendercore_fonts.py::test_the_stdlib_sfnt_reader_agrees_with_fonttools` |
| `pdfbackend/canvas_coverage.json` + `tests/golden/glyph_plan_vectors.json` | PyMuPDF 三层 | 交集 primary + Noto cjk + 空 fallback | `evidence/u06/canvas_coverage.rendercore.json`、`coverage_diff.json`、`test_rendercore_evidence.py::test_coverage_table_has_no_fallback_layer_and_records_the_known_limits` |
| `test_font_provenance.py` 三档 | PyMuPDF / matplotlib / 用户 | **已加第四档**（本 PR）：allowlist sha256 钉住的 OFL 字体 | `test_packaged_fonts_are_exactly_the_allowlist` + 反证 |
| `_CJK_FACE = china-ss`（Droid Sans Fallback） | Apache-2.0 | Noto Sans SC（OFL）；「换族不换 CJK」语义不变 | `test_rendercore_typography.py::test_degree_sign_below_the_cjk_break_point_still_reaches_the_cjk_face_last` |
| 位置 / 内容 / 框尺寸 | — | **保留**；换行由同一套算法在新 advance 上算，Liberation 与对应 base-14 同 advance（Times ↔ Liberation Serif 度量兼容） | `test_rendercore_typography.py` 的换行 / 对齐用例（合成脸）；真字体换行对拍归 U08 |

`U00_FACADE_LEDGER.json` 的 `migration_evidence` 字段逐项指向上面的用例（`tests/test_foundation_facade_ledger.py`
钉住这些用例名真的存在）。

## 5. 反证（每条变异一次就红）

| 变异 | 红在 |
|---|---|
| 注册表不算 sha256、按文件名收 | `test_files_not_in_the_allowlist_are_rejected_before_being_parsed`、`test_a_same_named_copy_with_different_bytes_is_a_different_face` |
| `face_by_resource` 不核身份、按 face_id 找 | `test_a_same_named_face_with_different_bytes_is_refused` |
| 写入器丢 ToUnicode / 丢 FontFile / code 全 +1 | `test_the_readers_catch_each_broken_file[…]` 三条（读取侧判据对坏文件必红） |
| ActualText 段里放两个 `TJ` | `test_composed_superscript_and_multi_glyph_cluster_get_actualtext_spans` |
| 缺字换成 CJK 脸画 | `test_a_missing_glyph_is_written_as_notdef_and_reported_not_substituted` |
| `read_frozen` 不核 hash | `test_rendercore_job.py::test_a_frozen_source_that_changes_before_writing_fails_the_job` |
| 能力表把 `imported_page` 改成 native | `test_each_declared_capability_matches_what_the_writer_really_does[imported_page]` |

## 6. 没做 / 边界

* 前端画布文字的同源预览（`canvasFontStack` 仍是 CSS 系统字体栈；`@font-face` 被 `test_no_web_font_is_fetched_or_embedded` 挡着）：未做，交接里写明要改的三处。
* `Image` / `ImportedPage` 的写入、透明组里的文字、栅格（PNG / TIFF）、`scope=original`：U07 / U08。
* 多字形 cluster 里标记字形的 `y_offset` 不写（一个 Span 一个 `TJ` 的代价）；竖排 / bidi / 变量 / 彩色字体显式拒绝。
* PDFium 把 CID 0 当空白：缺字在 PDFium 文字层里是空格——读取器规则，不在本轮修。
