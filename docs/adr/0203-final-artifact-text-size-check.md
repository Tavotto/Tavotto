# ADR 0203：成品 PDF 的实际字号接成一项产物检查（`text_size`）

日期：2026-11-23 · 状态：**Accepted（2026-11-23 维护者裁决「3.接」：按本稿实施；1.0 豁免并入科学图形检查族那一行，见 `docs/1.0-release-readiness.md` §1.8）**
**编号：原拟 0201，因 0201 / 0202 已分别被 #2125 / #2127 占用，顺延为 0203（2026-11-23）。**
相关：[0068 产物检查器与 manifest](0068-artifact-inspector-and-manifest.md)（四值判据、两档政策、`checks` 闭集）、[0155 期刊标准库](0155-journal-catalog.md)（阈值只来自期刊）、[0072 PyMuPDF 退役](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、[0152 文字编辑](0152-wysiwyg-text-editor.md)（公式画成轮廓 + 不画的文本层）；`docs/sciviz/validation-spec.md` V-009；`docs/research/external-figure-practice-absorption.md` §4.1。

## 问题

预检的字号规则按引擎 manifest 的 `fontsize × scale` 判（`engine/preflight.py` 的 `_iter_font_elements`）。它量的是「这个文字元素设的字号」，看不到一段文字内部更小的字：matplotlib 的 mathtext 把上下标排成约 0.7 倍的另一段字，7 pt 的 `$R^2$` 在导出的 PDF 里有一段 4.9 pt 的「2」，对数轴的 `10^4` 同理。`preflight._advise_font` 的注释自己写着「上下标求值器也无法区分」。

期刊对最小字号有规定时，被拒的正是成品里量得到的那个数。产物检查器（ADR 0068）已经在重新打开成品、遍历内容流，只是没记字号。

## 已经做了的（不需要本 ADR）

`inspector.observe_pdf()` 现在多带一块 `observed.text_size`：每段看得见的文字的有效字号（`Tf` × 文字矩阵 × 累计 CTM，含 Form 的 `/Matrix`）、最小值、段数、量不了的段数。另有纯函数 `text_size_verdict(observed, min_pt, tol_pt=…)`，阈值由调用方给。

这一半**只是观测**：不进 `checks`、不进 `summary()` 与 `public_projection()`、不改任何裁决，界面上看不到。同一份密集 PDF（16 个面板、931 段文字、26,732 条指令）遍历耗时的中位数改动前 54.2–55.8 ms、改动后 54.5–56.4 ms，在噪声之内。

## 决定

1. **`CHECKS_PDF` 加一项 `text_size`**，四值照 0068：
   - `failed`：成品里有一段看得见的文字小于规范下限；
   - `verified`：量到的都不小于下限，并且没有量不了的段；
   - `unknown`：有下限，但量不了——预算耗尽；没有读取器；文件损坏；或有量不了的段（不画只进文本层的文字、em 不明的 Type 3、退化矩阵）；
   - `not_applicable`：没有下限（这一项没被要求检查：standard 不带规范、中性底下限为 0）；或成品里没有可量的文字段。
   （2026-11-23 修订：初稿把「没有下限」记作 `unknown`，那会让原本全 verified 的普通导出整份降成「未核验」、MCP 文本
   多一行「未核验 text_size」；没被要求的检查不该拖低总体结论。）
2. **阈值只取出版规范的 `min_effective_font_size_pt`**，容差取 `profiles.FONT_FLOOR_TOL_PT`，由 `engine/artifactinspect.py` 交给检查器；检查器里不出现任何 pt 数。中性底下限为 0、或没有规范时结果是 `not_applicable`（没被要求检查，只记录不判）——与 `image_ppi` 没有最低 dpi 时记 `unknown` 的口径**有意不同**：那一项在 strict 是必需项，这一项两档都不是。期刊单独给了上下标下限（`advisories` 的 `script_min`）时怎么用，留给期刊库那条线定，首版不区分。
3. **两档政策里都不是必需项**。`standard` 与 `strict` 的 `REQUIRED` 都不加它：这一项 failed 只写进说明，不拦导出。理由：
   - 预检的 `font-too-small` 已经在导出前拦过「整段文字太小」；本项多抓到的主要是上下标，许多期刊对上下标另有更低的下限或没有规定，直接拦会误伤；
   - Tavotto 自己的公式是轮廓加不画的文本层（0152），这类文件天然是 `unknown`，进 `strict` 的必需项会把所有带公式的导出拦住。
4. **界面**：导出结果里的检查项列表多一行（`web/src/lib/artifactInspection.ts` 的闭集与两份 `dialogs.json` 各加一个键）。不新增卡片、横条或文案块；`failed` 时沿用现有那一行的说明位，一句话「成品里最小的文字是 4.9 pt，低于规范的 5 pt」。截图稿先过 agy 再给维护者看。
5. **SVG 不做**：SVG 里文字的实际大小取决于 CSS 与嵌套变换，静态结构检查量不准（0068 对 SVG 的既有边界），首版 `CHECKS_SVG` 不加这一项。
6. **不引入 PyMuPDF**，也不做文字包围盒级的碰撞检测：那需要字形宽度，检查器现在没有，也不该为此加依赖（0072）。

## 同步面（实施时一起改）

| 位置 | 改动 |
| --- | --- |
| `rendercore/inspector.py` | `CHECKS_PDF` 加 `text_size`；`_check_pdf` 调 `text_size_verdict`；`inspect()` 多收一个容差 |
| `engine/artifactinspect.py` | `inspection_profile()` 把字号下限与容差带进去 |
| `web/src/lib/artifactInspection.ts` | 检查项闭集 |
| `web/src/i18n/locales/{zh-CN,en-US}/dialogs.json`、`resources.d.ts` | 一个键 |
| `tests/test_export_inspection.py` 等 | `set(checks) == set(CHECKS_PDF)` 一类断言自动跟上；补 strict 下不拦的用例 |
| `docs/adr/0068-…`、`docs/rules/backend/rendercore.md` | 检查项清单 |

`MANIFEST_VERSION` 不升：`checks` 多一个键，老读者按未知键忽略（实施前核一遍前端与 MCP 读者是否都按闭集过滤）。

## 不做

- 把这一项做成预检规则：预检的输入是 manifest，没有成品；硬塞一个估计值（`fontsize × 0.7`）进去就成了猜。
- 在检查器里写 5 pt 之类的默认下限。
- 最终 PDF 上的面板对齐与文字碰撞审计：见研究报告 §4.1 的取舍。

## 后果

- 正面：字号这件事第一次有了成品上的独立读数；上下标太小在导出时就能看到，而不是等期刊退回。
- 代价：检查项闭集多一项，前端与 i18n 各多一个键；勾了严格核验时，带公式的 Tavotto 导出这一项长期是 `unknown`——界面不说（核验只说失败），只在 manifest / MCP 文本里列为未核验。
- 未做（NOT_RUN）：R / Cairo 产物的实测（只按写法造了手工 PDF）；界面稿（截图过 agy）。

## 实施记录（2026-11-23）

- `CHECKS_PDF` 末尾加 `text_size`；`_check_pdf` 经 `_check_text_size` 调 `text_size_verdict()`，下限取 `profile` 的
  `min_effective_font_size_pt`，容差是 `inspect(text_size_tol_pt=…)`，`artifactinspect.inspect_produced` 交
  `profiles.FONT_FLOOR_TOL_PT`。`REQUIRED` 两档都没动。
- standard 政策下 `inspection_profile()` 照旧不解析规范（请求也不带 `profile_id`），所以这一项在普通导出里恒为
  `not_applicable`（`no_threshold`），总体结论与加这一项之前一致；只在用户勾了严格核验时出结论。
- `summary()` 多一个 `text_size: {reason, min_pt, floor_pt}`（界面那一句话的依据）；`public_projection()` 只经
  `checks` 带四值，不带读数。
- 界面：`artifactInspection.textSizeNote()` 是唯一解读。failed 时「核验未通过」那一行里换成
  「成品里最小的文字是 4.9 pt，低于规范的 5 pt」；通过、没有规范、量不全都不画（2026-11-06「核验只说失败」，
  初稿里那行「公式量不全」的灰字已按这条裁决去掉）。
- 多页 PDF 的边界：`inspect_produced` 对各页只留拦截码、回执的 manifest 来自合并文件的检查，而检查器只读首页；
  `text_size` 不是必需项，所以第 2 页起的小字既不拦也不进回执——这一项的结论只覆盖首页（未实测）。
- 真实导出路由的端到端读数（`tests/test_export_text_size_e2e.py`，worker 重画的面板作为 Form 嵌进版面）：7 pt 的
  `$R^2$` 量到 4.9 pt；版面放大 1.25 倍量到 6.12 pt；画布公式标注是 `unknown`（`unmeasured_runs`），同一处换成普通文字是 `verified`（对照）。
- 读者核对：前端 `inspectionState()` 与 MCP `_inspection_summary()` 都**不按闭集过滤**，遍历 `checks` 的全部键，
  未知键照样计入并以原键名显示（不会崩）。两者与后端同一份构建发出，没有版本错位，所以 `MANIFEST_VERSION` 不升。
