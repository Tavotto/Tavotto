# ADR 0152：所见即所得文字编辑框——字符串仍是权威，编辑框只是视图；标注加 LaTeX 子集写法

日期：2026-10-29 · 状态：**Accepted（PR 1：片段模型、两种写法、导出按片段换脸；PR 2：编辑框；PR 3：LaTeX 模式与粘贴；PR 4：标注公式排版；PR 5：图内公式字体跟正文）**。
相关：[0032 排版能力层](0032-typography-capability-layer.md)、[0033 科学文本与字体回退](0033-scientific-text-and-font-fallback.md)
（本 ADR 修订其「画布文字没有 math 档」的边界：公式改由 PR 4 的 worker 排版兑现）、[0060 批准字体集合](0060-font-policy-and-allowlist.md)；
规则全文 `docs/rules/backend/richtext.md`；看护 `tests/test_rich_text_vectors.py`、`web/src/lib/richText.golden.test.ts`、
`tests/test_compose_text.py` 的 `test_latex_*`。

## 维护者裁决（2026-10-29）

起因是用户反馈：「现在对坐标轴和文字只能整体的斜体，但是在使用一列符号或者罗马字符的时候，我们需要把他们单独斜体」。
维护者随后决定把文字编辑做成「和 Word 一样所见即所得的编辑窗口……同时也能切换成 latex 模式」，并且「标注里渲染也能同步完成」。

1. **立项并登记 1.0 豁免**（`docs/1.0-release-readiness.md` §1.8）。
2. 编辑器库用 **ProseMirror**（PR 2）。
3. 标注里的公式由**内置 runtime 的 worker 用 matplotlib mathtext** 排成矢量路径（PR 4）：与图内文字同一套语法、同一种外观；
   代价是只支持 mathtext 那个 LaTeX 子集。
4. 标注的 LaTeX 模式显示**真 LaTeX 子集**（`\textit{}` / `\textbf{}` / `$…$`），不是 Tavotto 自己的标记。
5. 图内公式字体**自动跟正文**（PR 5）。
6. 先行分支 `feat/partial-italic` 的 `*{…}` 标记**不发布**；它「导出按片段换斜体脸」的那一半并入 PR 1。

## 决定

### 一、字符串仍是唯一权威

`TextObject.text`（标注）与图内文字的 `text` override 仍各是一个字符串。编辑框打开时把它解析成片段、每次改动后写回规范形；
存储、导出、写回事务、撤销都不知道编辑框的存在。打开不编辑**不改写**字符串。

### 二、标注的第二种写法：`textFormat: 'latex'`

- 字段可选，**缺省 = 旧标记**（`^{…}` / `_{…}`），读法逐字不变、磁盘格式不升版；导出载荷字段 `text_format` 缺省不发。
- `'latex'` 按 LaTeX 子集读（命令表与宽进规则在 `richtext.py` / `richText.ts` 的「LaTeX 子集」一节，两侧严格同源，
  同一份向量 `tests/golden/rich_text_vectors.json` 两边各跑一遍）。读不懂的命令连同花括号原样是字（`raw` 片段），写回时也原样。
- 片段模型 `TextRun` 加 `italic` / `bold` / `math` / `raw`；旧标记读出来的片段这四个都为假。
- 旧标注**第一次用新编辑框保存**时转换（`legacyToLatex`）：成对的 `$` 写成 `\$`，不会突然变成公式。

### 三、导出按片段换脸

`FaceSet.variant` 给出 (片段斜体, 片段粗体) 对应的正文脸，与对象自身的粗 / 斜取「或」；量宽与落笔读同一张脸，
字形覆盖也按那张脸问（`glyphplan.text_diagnostics` 的 `styled_cov`）。CJK 脸不换。

### 四、PR 1 时公式片段的画法

公式排版（PR 4）接入之前，公式片段在画布与导出上**按源码原样画**（连 `$` 定界符），两侧同一个约定，不吞字。
PR 1 不提供任何能写出 `textFormat: 'latex'` 的界面，用户看不到这一过渡态。

### 五、PR 2：一个编辑框换掉四处纯文本框

`components/ui/RichTextField.tsx`（ProseMirror，`lib/richEditor.ts` 是不含 React 的核心）替换标注属性页、画布上双击就地编辑、
图内文字属性页、右键快捷编辑四处的纯文本框。规则：

- **只在用户真的改了时写回**：只有改了文档的事务才 serialize；外部改了字符串（撤销、换选中对象、大小写菜单）整段替换、
  不进编辑框历史、不回写。输入法组字期间不写回，组字结束补一次。
- **历史**：编辑期内 ⌘Z 归编辑框；宿主照旧在 onFocus 开事务、onBlur 合并成一条（画布就地编辑是失焦时一次 commit）。
  工具条命令先聚焦再改，落进那个事务。
- **键位**：⌘B / ⌘I / ⌘↑ / ⌘↓（沿用原有上下标键位）；画布与图内文字 Enter 提交、⌥/⌘/Ctrl+Enter 换行；标注属性页 Enter 换行。
  样式快捷键与工具条命令执行前把浏览器当下的选区读进编辑器（ProseMirror 异步跟选区，⇧← 后紧接 ⌘↑ 会落在旧光标上——真浏览器实测）。
- **写法**：标注用 `annotationCodec(textFormat)`（第一次改动写成 LaTeX 子集并标 `textFormat`）；图内文字用 `mathtextCodec`
  （只认规范的 `$\mathit{}$` / `$\mathbf{}$` / `$\mathbfit{}$` / `$^{}$` / `$_{}$` 段，其余 `$…$` 原样是公式块）。
- 读不懂的命令（`raw`）与公式块在框里照源码显示；PR 3 / PR 4 接它们的编辑与排版。

### 六、PR 3：LaTeX 源码模式与粘贴识别

- **两种模式改同一个字符串**：属性栏的文字框左下角「TeX」切到源码（画布就地编辑没有这一形）。标注显示 LaTeX 子集的
  规范形（老标注也是，但不改不写），图内文字显示存着的 mathtext 原文。源码里的输入即时写回，与所见即所得同一事务。
- **切回前校验**（`validateSource`）：只查未转义花括号不配对、未转义 `$` 不成对（图内文字按行查）；错了就停在源码模式，
  说出第几个字符。命令拼错不拦——宽进的读法原样当字，切回后看得见。
- **粘贴识别**（`pastedRuns`）：`$…$` / `$$…$$` / `\(…\)` / `\[…\]` 里**像公式**的段（有命令 / 上下标 / 花括号，或单个字母）
  变成公式块；「$5 and $10」这种原样是字。标注另认 `\textit{}` 等文字样式命令。认不出交给默认粘贴；自己复制出去的
  （带 `data-rt` 标签）走默认的 HTML 读入。
- **双击公式块**切到源码模式。
### 七、PR 4：标注公式排版

- **谁排**：`engine/mathtypeset.py` 起一个带 matplotlib 的解释器跑独立脚本 `engine/mathtext_child.py`（内置 runtime →
  源码模式的当前解释器 → 设置里的 worker 解释器），把 `$…$` 排成 **em 单位、基线 y = 0、y 向上**的轮廓（M / L / C / Z，
  二次贝塞尔升三次）与盒子。字母数字用批准集合里的 Liberation（与标注正文同一张脸，`mathtext.fontset = custom`），
  画不了的符号由 matplotlib 自带 STIX 兜底。按 (族, 源码) 缓存到 `data_dir()/cache/mathtext/`；排不出来的（语法错 /
  mathtext 不支持的命令）也缓存，环境问题（没有解释器、子进程崩）不缓存。
- **一份结果两处读**：导出（`_export_produce_rendercore`、单图写回 `annotate_asset`）先把这批对象的公式排好，作为
  `math=` 交给 `plan.compile_page` → `typography.layout_text`——**RenderCore 仍是纯标准库、不起进程**；画布预览经
  `/api/mathtext`（会话认证内）取同一份，`MathFormula` 画 SVG。所以画布与 PDF 逐点相同。
- **排版规则**：一个公式在换行单元里占一个占位字符（补充专用区 B），不可拆；只占宽度、不撑行距（画布上零高的
  inline-block，导出里只算宽度）；公式后面的字另起一段从公式右边落笔。公式用文字颜色填充。
- **排不出来**：两侧都按源码原样画（`$…$`，不吞字）；导出报 `math_unrenderable`（警告里带源码与原因），画布上带红色波浪线、
  悬停看原因。**也进问题面板**：preflight 两侧仍是纯求值器、不起排版进程，事实经规范化 spec 带进来——
  `PreflightTextSpec.math_errors: [{src, why}]`（与面板的 `render_error` / `stale` 同一种喂法），求值器对每条有失败公式的
  可见文字出一条 `math-unrenderable`（两档 profile 都是 `warn`，与导出的 `math_unrenderable` 警告同级；点击选中那条文字）。
  前端 `buildSpec` 读 `lib/mathtext` 缓存里 `ok: false` 的（排版还没回来的不算：「不知道」不是「排不出」），
  `validationStore.startValidation` 订阅 `subscribeMath`，结果一到就重排一轮检查。Python 侧
  求值器同样认这个字段（金向量两侧对齐）；MCP 的 spec 现在不带画布文字，没有要填它的地方。
- **已知限制**：公式是轮廓，PDF 文本层里没有它的字（复制不出来）；图内文字的公式块在编辑框里仍显示源码（图上就是
  matplotlib 画的真身）；缩略图里的公式显示源码。
- 打包：`mathtext_child.py` 进 `packaging/tavotto.spec` 的外部解释器源码清单（第三个根，`test_runtime_build` 按 import
  闭包看护）。

### 八、PR 5：图内公式字体跟正文

- matplotlib 默认公式字体集是 DejaVu Sans：正文 Times 的轴标题里 `$\mathit{T}$` 是无衬线斜体（10-29 实测 PDF 字体名
  `DejaVuSans-Oblique`）。引擎的「字体族」修改本来就连公式一起换（`overrides._set_text_fontfamily`：正文按回退链、
  公式切 custom 字体集指向同一族）——**不加引擎能力**：所见即所得文字框（属性页、右键快捷编辑）写入的文字里有规范段的
  斜体 / 粗体 / 上下标、而这段字的字体族还没被改过时，同一次编辑里顺带写一条字体族 override，值是它**现在的**正文族
  （`lib/figureMathFont.ts`）。写回与重放走字体族那条已有的路。实测：加上之后斜体 T 是 `TimesNewRomanPS-ItalicMT`。
- 代价：字体族那一行显示「已修改」（确实改了：公式的字体集）；rcParams 进程级的 custom 集边界照旧（多段文字各选不同
  字体时后改的赢，见 `_apply_mathtext_custom_set`）。

## 不做

- 不改图内文字的存法（仍是 mathtext 字符串）。
- 不给 CJK 合成斜体 / 粗体。
- 不支持 mathtext 以外的 LaTeX 环境（`align` 等）；PR 4 对它们给出明确的问题条目。
