# 属性能力层与 Typography 控件（2026-09-01，ADR 0032）

> 原文出自 `web/AGENTS.md`「属性能力层与 Typography 控件（2026-09-01，ADR 0032）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

完整版在 `docs/adr/0032-typography-capability-layer.md`，改动前先读。
**「一段文字长什么样」只有一套词汇**：

```text
lib/typography.ts          规范属性名 · 取值语义 · 能力表 · property path · 校验
  → components/inspector/typographyAdapter.ts
       useFigureTypography（能力问 manifest，写 setOverride(s)）
       useCanvasTypography（能力看 TextObject 字段，写 updateObjects）
  → components/inspector/controls/TypographyControls.tsx（一份控件）
       属性页图内文字 / 图内批量 / 画布标注 / 浮动工具条 —— 四个入口
```

* **不许在组件里按对象类型 switch 着写属性。** 要新属性就加进
  `TYPOGRAPHY_PROPS`，三张表（支持 / path / 校验）一起改；在第二个组件里抄
  一份换算就是埋一次分叉——`ContextBar` 的文字快捷编辑以前正是那样，
  没有斜体、没有字体、`o.bold = !o.bold` 与属性页的 `!bold` 在多选下算出不同
  的结果。
* **值有四档，一档都不许压扁**：`uniform` / `mixed` / `inherit` / `unsupported`。
  字号 mixed 画成 9 pt、字体 inherit 画成一次显式设置，都是数据损坏级的误导。
* **能力有两层**：静态支持表只答「值不值得问引擎」，图内真正能改什么由
  manifest 的 `editable` 说了算。
* **property path 只有 `propertyPathOf(kind, prop)` 一份**：检查报的字段名、
  控件挂的 `data-prop`、`issueFocus` 查的选择器同源。`TEXT_BAR_PROPS`
  （「平铺列表要让出哪几条」）**从这张表算出来，不手抄**。
* **画布文字的字体族是闭集**（`serif` / `sans-serif` / `monospace`），与
  `pdfbackend.CANVAS_TEXT_FAMILIES` 严格同源（顺序也比）。合成跑在没有
  matplotlib 的 Flask 进程里，画得出来的就是 PyMuPDF 的 base-14；摆一个画不
  出来的选项 = 静默替换。`TextObject.fontFamily` 是**可选字段**，缺席 = 没设过
  = 继承默认族，回到默认值时**删字段**。
* **写入**：invalid 输入不开事务、不 commit、不进历史，校验**不 clamp**；
  连续输入合并成一条历史，且 `write()` **自己会开一轮**——打字那条路没有
  `onScrubStart`。
* **字形归属与科学文本（ADR 0033）**：`lib/glyphPlan.ts` 回答「这个字符导出
  后由哪张脸画、会不会是方框」，判据是**生成的覆盖表**（`@glyphcoverage`
  别名，与 `src/tavotto/glyphplan.py` 严格同源）——**不是浏览器自己的字体
  栈**。拿浏览器画得出当判据的结果正是「预览好好的、导出上是个方框」。
  `TextObject.interpretation` 两档（`auto` 默认 / `scientific`），合成只生成
  渲染表示，**raw text 一个字符不改**；`scientific` 的代价是 PDF 文本层里的
  `⁵` 变成 `5`，所以它必须由用户明确选。缺字形与「换了脸」是**两条规则、
  两句话**（`glyph-missing` / `glyph-substituted`）。
* 装不上的字体：`manifest` 的 `options_unavailable` → 界面**保留名字 +
  warning**，绝不换掉再改文档。
* 图内中文（ADR 0045）：引擎给每段文字接了本机的中日韩回退链，manifest 用
  `cjk_family` 报是哪张脸画的；预检 `cjk-fallback-missing` 的主语是它（不是正文
  族名），`preflight.ts` 与 Python 侧同源，golden 向量看护。
* 看护：`lib/typography.test.ts` / `components/inspector/typographyAdapter.test.tsx`
  / `lib/canvasTextFont.test.ts` / `TextSection.test.tsx` / `textStyleBar.test.tsx`
  / `canvas/TextView.test.tsx` / `canvas/contextBar.test.tsx`；Python 侧
  `tests/test_typography_families.py`。
