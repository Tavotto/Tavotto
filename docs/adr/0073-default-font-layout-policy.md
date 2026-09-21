# ADR 0073：字体默认变更的布局政策与旧项目处理

日期：2026-09-22 · 状态：**Accepted（U10；随 ADR 0072 切默认生效；两处默认取值用户 2026-09-22 已拍板，见 §4）**
相关：[0072 默认后端切换与 PyMuPDF 退役](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、
[0060 字体政策与 allowlist](0060-font-policy-and-allowlist.md)、[0059 Render IR](0059-render-ir-and-render-plan.md)、
[0033 字形回退](0033-scientific-text-and-font-fallback.md)、[0045 CJK 回退链](0045-cjk-font-fallback-chain.md)；
实施包 `01_SCOPE_AND_DECISIONS.md` **D07**、`03_CI_POLICY.md` §6（字体替换后 golden 一次批准迁移）、
`06_ENABLE_AND_RELEASE.md` §3 末段（「不静默重排用户全部画布」）、registry RC-028 / RC-093。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 旧文档里的默认字体是什么 | 画布文字对象只存**通用族**（`font_family ∈ {serif, sans-serif, monospace}`，缺席 = 默认族 serif），从来不存 `Times-Roman` 那类具体脸名——所以**没有需要迁移的文档字段**。「Times-Roman → Liberation Serif」是实现侧的映射：族 → 批准字体集合里对应的脸（`rendercore/fonts.py::FontRegistry.record_for`），旧项目原样打开 |
| 位置 / 内容 / 框尺寸 | **保持**：文字对象的 `x/y/w/h`、内容、字号、对齐、行距一个字节不动；面板 / 形状 / 箭头与字体无关 |
| 换行 | 由同一套算法在新 advance 上算。Liberation 三族与对应 base-14 脸**度量兼容**（advance 相同），所以换行位置不变——这不是假设，`tests/test_compose_text.py` 21 条（含 60 mm / 30 mm 两档的整句换行期望）在切默认后**一字未改仍全过**；`tests/test_rendercore_calibration.py::text` 量墨的左右边 ≤ 1 pt |
| 基线 | 同一条公式 `y0 + size·((line_h − (asc − desc))/2 + asc)`，asc / desc 从 bbox ascender（Times 1.053）换成 OS/2 typo（Liberation Serif 0.693 / −0.216）：12 pt 时基线上移 1.77 pt。**批准的度量变化**（D07），校准用例钉「恰好等于这个量 ±0.6 pt」，不是「差得不多」 |
| 静默重排 | **没有**：不改任何画布尺寸、不改用户脚本、不重写文档；换行变化若有（本轮实测为零）按 U08 / U10 的批准差异表逐例审查，不做「自动重排一遍」 |
| 集合外的字 | 画不出的字是 `missing`：写 .notdef（画面上是方框）+ ToUnicode 回原字（文本层里字还在）+ 问题面板一条 `glyph-missing`——**不换脸冒充**（RC-037）。`auto` 解释档只救「不然就是方框」的上下标（`⁻` → 合成上标 `-`，ActualText 还原原文） |
| 覆盖表 / 向量 / golden | 一次批准迁移（03 §6）：`canvas_coverage.json` 与 `glyph_plan_vectors.json` 重生成，旧表存档为批准资产，差异闭集钉在 `tests/test_rendercore_glyph_vectors.py`（ADR 0072 §2 那张表逐条解释） |
| 字体政策版本 | `identity.fonts_policy_version()`（allowlist 的 sha256 前缀）进 render identity 与预览缓存键：换字体集合 = 换 render 身份，旧预览不命中、导出回执可分辨 |

## 1. 为什么不做「按脸名迁移」

旧后端用 base-14 名字画字，但**文档从来没存过那个名字**——`TextObject.fontFamily` 是三个通用族的闭集
（`web/src/types/document.ts` / `pdfbackend.CANVAS_TEXT_FAMILIES` 严格同源），缺席即默认族。所以「旧项目里的
Times-Roman」是一个只存在于实现里的事实：族 `serif` 在旧实现里解析到 Times-Roman，在新实现里解析到
Liberation Serif（`FontRegistry.record_for("serif", bold, italic)`）。打开旧项目不需要任何转换；
`tests/test_typography_families.py::test_the_family_reaches_the_pdf_font_resources` 钉的「缺省那一条走衬线」
就是「老文档不重排」的直接证据。

## 2. 集合外限制的产品表达（不加脸；已拍板）

批准字体集合能画的码位从 61 238 降到 32 608（ADR 0060 §1 量的）：Hangul（11 172）、阿拉伯 / 天城 / 希伯来、
数学字母（U+1Dxxx）、emoji（U+1Fxxx）、部分古文字不在——旧后端由渲染器自己挑一张 Noto Serif 画出，
用户从没被告知那是换了脸。本轮**如实表达而不冒称支持**：

* 前端「这个字导出后是不是方框」的判据切到新表（`pdfbackend/canvas_coverage.json` 由批准集合出，
  `fallback` 恒空）——预览里的方框提示与导出上的方框从此说同一句话；
* 预检 `glyph-missing` 逐字列出画不出的字符（`tests/test_glyph_plan.py::test_unrenderable_character_is_missing_not_silently_dropped`）；
  `glyph-substituted` 在画布文字上不再发生（没有换脸），规则保留为空档；
* 文档口径：本轮画布文字支持 Liberation 三族的拉丁 / 希腊 / 西里尔 / 常用符号 + Noto Sans SC 的中日文（含假名、
  常用符号），**不支持** Hangul 及上述脚本——在 `docs/rules/backend/pdf-backend-boundary.md` 与 ADR 0060 §1 写明；
  图内文字（matplotlib 画的）不受影响，那一侧仍是 worker 解释器里的字体与 ADR 0045 的回退链；
* **后续路径**（不在本轮）：要救回其中任何一段，就是 allowlist 加一张 OFL 脸——改 `fonts_allowlist.json`（名字 / URL /
  sha256）+ ADR 0060 §1 的限制表 + 重生成覆盖表与向量（`gen_canvas_coverage.py` / `gen_glyph_plan_vectors.py`），
  `fetch_fonts.py` 与冻结 / CI 步骤不用改；加哪张按 RC-028 split 另开决策。

## 3. 前端同源预览（不做；已拍板）

画布预览仍用 CSS 系统字体栈（`web/src/lib/typography.ts` 的 `canvasFontStack`），不把 Liberation / Noto 带进
浏览器：`test_no_web_font_is_fetched_or_embedded` 继续成立，playground 的产物指纹不变。代价是预览里的字形与
导出的字形来自两套脸（一直如此——旧后端时代 base-14 也不在浏览器里）；换行 / 量宽的判据两侧同源
（`glyph_plan_vectors.json` + 覆盖表），所以「预览一行、导出两行」这类分歧不会因此出现。要做同源预览
的三处改动（字体文件进 web 产物、`canvasFontStack` 指向它、`test_no_web_font_is_fetched_or_embedded`
的判据改口）在 U06 交接里写明，本轮不动。

## 4. 用户拍板（2026-09-22，主对话确认）

1. 覆盖收窄：**不加脸**、如实表达（§2）——认可。
2. 前端同源预览：**不做**，只切覆盖表与测量来源（§3）——认可。

两条日后若改口，只动 allowlist / 前端字体栈那几处（§2 的后续路径），本 ADR 的布局政策（§裁决摘要）不变。

## 5. 反证

| 变异 | 红在 |
|---|---|
| 基线公式换回 bbox ascender（或 asc / desc 硬编码成 Times 的数） | `tests/test_compose_text.py::test_baseline_matches_css_line_box`、`tests/test_rendercore_calibration.py::test_old_and_new_backends_agree_within_the_case_thresholds[text]`（基线差不再等于批准量） |
| 缺字换成 CJK 脸 / 系统脸画 | `tests/test_glyph_plan.py::test_no_fallback_face_is_ever_used_and_missing_stays_on_the_primary_face`、`tests/test_rendercore_writer.py::test_a_missing_glyph_is_written_as_notdef_and_reported_not_substituted` |
| 缺省族不走衬线 | `tests/test_typography_families.py::test_the_family_reaches_the_pdf_font_resources`、`test_an_unknown_family_falls_back_to_the_default_instead_of_resolving_it` |
| 覆盖表悄悄给 fallback 层塞码位 | `tests/test_rendercore_glyph_vectors.py::test_the_default_tables_name_the_rendercore_backend_and_have_no_fallback_layer` |
| 换行算法在新 advance 上多断一行 | `test_compose_text.py::test_normal_words_wrap_exactly_as_before_the_force_break_fallback[60/30]` |
