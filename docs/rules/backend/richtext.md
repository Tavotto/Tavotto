# 文字：行内上下标与大小写

> 原文出自 `src/tavotto/AGENTS.md`「文字：行内上下标与大小写」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_compose_text.py`、`test_glyph_plan.py`、`test_rich_text_vectors.py`

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 250
改到: `richtext.py`
主题: 文字：行内上下标与大小写 → `richtext.md`
必守要点: ↔ `web/src/lib/richText.ts` 严格同源（三常量 + parse）
-->

- **画布标注**用行内标记 `^{…}` / `_{…}`（`web/src/lib/richText.ts` ↔
  `src/tavotto/richtext.py` **严格同源**：`SCRIPT_SIZE/SUP_RISE/SUB_DROP`
  三个常量与 parse 规则改一边必须同步另一边，pytest 用真实 PDF 的字形
  字号与基线做几何级看护）。只有 `^{`/`_{` 才触发，正文里孤立的 `^`/`_`
  原样显示；`\^`、`\_`、`\\` 是转义。序列化**按需转义**——无脑加反斜杠会让
  用户点一次「大小写」正文里就凭空多出 `\`。
- **第二种写法 `latex`**（ADR 0152，2026-10-29）：标注带 `textFormat: 'latex'`（载荷 `text_format`）时按
  LaTeX 子集读：`\textit{}` `\emph{}` `\textbf{}` `\textup{}` `\textmd{}` `\textnormal{}`
  `\textsuperscript{}` `\textsubscript{}`、`$…$` / `$$…$$` / `\(…\)` / `\[…\]` 公式、`{…}` 分组、
  `\$ \% \& \# \_ \{ \}` `\^{}` `\~{}` `\textbackslash{}` 字面量、`~` 不换行空格、`\\` 换行。**宽进严出**：读不懂的
  命令连同花括号原样是字（`raw` 片段，写回也原样）；写回只出规范形（粗体 → 斜体 → 上下标逐层分组）。
  没有该字段的标注一律按旧标记读，逐字不变。两侧解析 + 规范形 + 可读文本由同一份向量
  `tests/golden/rich_text_vectors.json` 看护（`python scripts/gen_rich_text_vectors.py --write` 生成；pytest 与 vitest 各跑一遍）。
  导出按片段换斜体 / 粗体脸（`FaceSet.variant`，与对象自身粗 / 斜取「或」），量宽、落笔、覆盖判据读同一张脸。
  公式片段在公式排版接入（PR 4）之前两侧都按源码原样画。
- **编辑只经一个文字框**（ADR 0152 PR 2）：标注与图内文字的内容一律经 `components/ui/RichTextField`
  （核心 `web/src/lib/richEditor.ts`）编辑，不再有纯文本框直接改标记。它只在用户真的改了时写回规范形；
  图内文字的写法是 `web/src/lib/mathtextCodec.ts`（只认规范段，其余 `$…$` 原样是公式块，`$` / `\$` 按 matplotlib
  的逐行规则读写——实测见该文件注释）。
- **标注公式排版**（ADR 0152 PR 4）：`$…$` 由 `engine/mathtypeset.py` 起 matplotlib 子进程排成 em 轮廓、按内容缓存；
  导出把结果作为 `math=` 交给 `plan.compile_page`（RenderCore 不起进程），画布经 `/api/mathtext` 读同一份。新增导出 /
  写回路径要先 `mathtypeset.layouts_for(objects)` 再编译，不然公式只按源码画。看护 `tests/test_mathtypeset.py`、
  `test_rendercore_plan.py` 的 `test_math_*`、`test_export_endpoint.py` 的 `test_export_*math*`。
- **图内元素文字**走 matplotlib mathtext（`cm$^{-1}$`），不是上面那套标记；
  大小写转换要 `protectMath`（`$…$` 里是 `\alpha` 这类命令，改大小写就废）。
- 大小写是**一次性动作**，直接改文本内容（可撤销），不新增字段、导出零改动。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- ↔ `web/src/lib/richText.ts` 严格同源（三常量 + parse）
- 图内文字走 mathtext、大小写要 `protectMath`
