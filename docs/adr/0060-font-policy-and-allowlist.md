# ADR 0060：字体政策、来源 allowlist 与可检索文字写入

日期：2026-09-21 · 状态：**Accepted（U06 第二切片；候选栈未默认启用，不切默认后端）**
相关：[0059 Render IR 与 RenderPlan](0059-render-ir-and-render-plan.md)、[0055 render_spike](0055-render-spike.md)、
[0033 字形回退](0033-scientific-text-and-font-fallback.md)、[0045 CJK 回退链](0045-cjk-font-fallback-chain.md)、
[0053 U01 合同](0053-foundation-contracts-and-preparation.md)；实施包 `01_SCOPE_AND_DECISIONS.md` D07 / D15、
`03_CI_POLICY.md` §6；`docs/legal/LICENSING.md`、issue #182（NOTICE）。

> **修订 2026-11-03（[ADR 0164](0164-canvas-text-source-font.md)）**：批准集合仍是**默认与回退**，下面「运行时定位 / 来源 allowlist /
> 身份」三行对**没有 `source_font` 的对象**逐字不变。0164 在它旁边开了**一条**受限通道：画布文字可带一条原图字体的**身份**
> （文件 sha256 + 面序号 + 名字 + 粗斜），导出时若本机有字节一模一样的文件，就用它排版并子集嵌入；找不到 / 许可（OS/2 `fsType`）
> 不让嵌入 / 格式不支持 → 回退成 `fontFamily` 的批准脸、名字保留、报 `source_font_unavailable`。因此「不摸系统字体」这句话的主语改为：
> **批准字体注册表（`FontRegistry`）不摸系统字体**；用户脸只经 `rendercore/userfonts.py` 一个入口、只按 sha256 认、不按名字猜。
> §6 第一条「前端画布文字的同源预览」也由 0164 兑现（用户脸经本机端点供字节）；批准族的预览仍是 CSS 栈。

> **修订 2026-11-04（CJK 脸随族）**：批准集合加第 14 张脸 **Noto Serif SC Regular**（noto-cjk 同一固定提交的
> `Serif/SubsetOTF/SC/NotoSerifSC-Regular.otf`，OFL 1.1，allowlist `family: "cjk-serif"`）；下面「唯一一张 CJK 脸」「`cjk`（唯一一张
> Noto 脸）」与 §4 里「换族不换 CJK」的语义由 §7 取代。理由：画布文字默认衬线，标注里的中文在画布上是宋体
> （`--font-doc` = `'Times New Roman', 'Songti SC', 'Source Han Serif SC', serif`），导出却由黑体 Noto Sans SC 画——
> 画布 ≠ 导出。

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

## 7. 修订 2026-11-04：CJK 脸随族（取代「换族不换 CJK」）

**规则。** 画布文字的 CJK 层按文字的族取脸：`serif` → **Noto Serif SC**（`cjk-serif`）；`sans-serif` / `monospace` →
Noto Sans SC（`cjk`；等宽配黑体是常规做法）。认不出的族走默认族（serif）。唯一出处 `rendercore/fonts.cjk_family_of`；
`FaceProvider.cjk_face(family)` 与 `FontRegistry.cjk_record(family)` 都要带族，没有「不带族的 CJK 脸」。批准集合里缺这张脸时回 `None`
（汉字进 `missing`，**不**拿另一族的 CJK 脸顶）。ADR 0164 的用户原图字体（`user_cjk`）优先级不变：给了就替换，与族无关。
行内样式的粗 / 斜变体、竖排（`vertical.py`）、公式都经 `faces_for` 取脸，随之自动跟族；SVG 的 `font-family` 链给 `cjk-serif`
配 `'Songti SC', SimSun, 'Noto Serif CJK SC', serif`（`svgfonts.py`）。

**覆盖。** 两张脸的 cmap 实测不同：Sans 独有 16 个码位（U+9FEB–9FEF 5 个、U+F900 等 6 个兼容汉字、U+25AD7 / U+27FB7 等 5 个扩展区字），Serif 独有 21
个（多为兼容汉字与 U+2F920）。判据的主语是**导出时画这个字的那张脸**：
* 后端（`typography.coverage(faces)`、`text_diagnostics` 的求值）本来就问 `FaceSet` 里实际那张 CJK 脸，随族后自然问对了脸；
* 前端 / 预检读的 `canvas_coverage.json` 是一张与族无关的表，`primary` 早就取 12 张 Liberation 脸的**交集**，`cjk` 同理取各族实际
  CJK 脸的**交集**（`facade.coverage_ranges`）。表说「画得出」，哪个族的导出都画得出——不会出现判据说能画、衬线脸画出方框；
  代价是 Sans 独有的 13 个（`max_codepoint` 以内：U+9FEB–9FEF、U+F900 / F921 / F938 / F949 / F9B3 / F9DC、U+25AD7、U+27FB7）在前端被保守地
  报成「画不出」（实际黑体能画），它们是 Unicode 10/11 新增字与兼容汉字，实际标注里几乎不出现。不为它们给表加族维度
  （前端 `layerOf(cp)` 没有族参数，加了就是改同源对的签名）。
* 重生成：`gen_canvas_coverage.py --write`：`cjk` 区间 446 → 444，少 13 个码位、多 0 个；`gen_glyph_plan_vectors.py --check` 无变化
  （69 条向量不含这些码位）；`evidence/u06`（改读 `evidence/u10/canvas_coverage.pymupdf.json` 作旧表，因产品表早已是 RenderCore 自己的）：
  `cjk` new 30 887 → 30 874、总可画 new 32 595 → 32 582，其余数字不变，`allowlist_sha256` / 字体表随之换；`evidence/u07`：u07.pdf 里
  「图」改由 Noto Serif SC 画（默认族是 serif），pdf / png / 像素 hash 随之换，30 条核对仍全 ok。
  `test_rendercore_glyph_vectors` 里 `cjk` 的 30 887 → 30 874 同理。

**缓存与预热。** 字体政策版本 = allowlist 文件 sha256 前 16 位（`identity.fonts_policy_version`）：加了一张脸，预览缓存键和 render identity
自动变，老预览不命中。预热（`facade.prewarm`）按默认族 serif 排一段中文，载的是 Noto Serif SC；Noto Sans SC 在第一次用无衬线 /
等宽族写中文时才载。

**体积。** `NotoSerifSC-Regular.otf` 11 607 284 字节（Sans 8 331 336），安装包 / wheel 原始 +11.6 MB，zlib 级别压缩约 +9.9 MB。
打包路径都是整目录收（wheel `artifacts = resources/fonts/**`、PyInstaller `resources/` datas、`fetch_fonts.py` 读 allowlist、
`retirement_scan` 的字体张数取自 allowlist），无需逐文件改清单，只改文字里写死的「13 张」。

**看护。** `test_rendercore_app.py::test_chinese_in_canvas_text_is_drawn_by_the_cjk_face_of_its_family`（导出 PDF 页面里「温」「度」
引用的字体程序，三族各一）、`test_typography_families.py::test_the_cjk_face_follows_the_family`、
`test_rendercore_fonts.py::test_the_two_cjk_faces_differ_only_in_a_few_rare_codepoints`。反证：把 `_CJK_FAMILY_OF` 清空（衬线也取 Sans）
上面几条变红。

---

> **修订 2026-11-04（字母式符号展开）**：`℃`（U+2103）等字符 Liberation 12 张脸**都没有**，导出时按 §4 落到 cjk 层（当时只有 Noto Sans SC，无衬线；§7 之后衬线族是 Noto Serif SC），
> 而画布上浏览器用系统字体画——`℃` 的字形画布与导出不同，宽度也跟着变。在「受控科学文本解释」（`richtext.interpret_runs` ↔
> `richText.ts interpretRuns`）里加第二类解释：**闭集** `LETTERLIKE_EXPANSION`（`℃`→`°C`、`℉`→`°F`、`K`(U+212A)→`K`、`Å`(U+212B)→`Å`(U+00C5)；
> 依据是 fontTools 实测 12 张脸的 cmap：这四个码位全缺、展开后的 `° C F K Å` 全有；数学体字母 / 方块单位 / 全角拉丁语义会丢，不收）。
> 字符不在正文脸、且展开后每个字符都在正文脸才展开，否则原样保留；「正文脸」按批准字体的**静态覆盖表**（`canvas_coverage.json`，与画布 `layerOf` 同一份）判，
> 原图字体（ADR 0164）自带 `℃` 时也照表展开——画布不知道用户字体的 cmap，两边要做同一个决定。**与合成上下标的区别**：上下标「auto 只救方框」是因为折叠改变了文本层字符、
> 要用户明确选；这里 NFKC 等价、文本层仍经 `ShapedPiece.logical` → `GlyphRun.actual_text` 的**同一条** ActualText 路还成用户原文，所以 auto / scientific
> **两档都生效**。raw text 永不改写；量宽 / 换行读展开后的字（`typography.interpreted_pieces` 在用户原文侧补 `ELIDED` 占位保持逐字符对齐，
> 展开出来的字在逐字兜底换行时算一个字符、不被拆开）。**边界（照实）**：PDF 里认 ActualText 的读取器（PDFium / poppler / Acrobat）复制出 `℃`，
> 不认的（macOS PDFKit）读字形文本 `°C`——与既有 `⁻²` → `-2` 同；SVG 的 `<text>` 取自字形簇，写 `°C`（既有合成上下标同样写 `-2`，SVG 不读 ActualText）。
> 看护：`tests/test_glyph_plan.py::test_celsius_sign_in_serif_text_…`（导出页面上这几个字形用哪张脸 + 文本层）、
> `test_rendercore_typography.py` 三条、`tests/golden/rich_text_vectors.json` 的 `interpret` 向量（pytest + vitest 各跑一遍）、表逐字对照
> `test_the_letterlike_expansion_tables_are_identical_on_both_sides`。

## 修订 2026-11-05：加两张粗体 CJK 脸

维护者 2026-11-05：「加入 Noto Sans SC Bold 和 Noto Serif SC Bold」。

批准集合 14 → 16 张：`NotoSansSC-Bold`、`NotoSerifSC-Bold`，与常规脸同一个 noto-cjk 固定提交，OFL 1.1，族名不变（`cjk` / `cjk-serif`）。码位与对应常规脸逐一相同，`canvas_coverage.json` 不变。安装包原始 +20.6 MB。

粗体中文从此不是「只换英文脸」：`cjk_face(family, bold)` 按对象与行内片段取。批准集合仍没有斜体中文，中文斜体由写入器错切模拟（ADR 0169 修订裁决 18）。用户本机字体不受影响，见 ADR 0169 修订。
