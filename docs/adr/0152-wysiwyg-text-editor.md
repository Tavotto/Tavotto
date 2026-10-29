# ADR 0152：所见即所得文字编辑框——字符串仍是权威，编辑框只是视图；标注加 LaTeX 子集写法

日期：2026-10-29 · 状态：**Accepted（PR 1：片段模型、两种写法、导出按片段换脸；PR 2：编辑框；PR 3：LaTeX 模式与粘贴）**；
PR 4 标注公式排版、PR 5 图内公式字体跟正文各自修订本 ADR
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

## 不做

- 不改图内文字的存法（仍是 mathtext 字符串）。
- 不给 CJK 合成斜体 / 粗体。
- 不支持 mathtext 以外的 LaTeX 环境（`align` 等）；PR 4 对它们给出明确的问题条目。
