# 来源登记

只有在这里登记过的来源才能被 `principles.md`、`validation-spec.md`、`sciviz_guidance.json` 引用。

| 键 | 来源 | 页码口径 |
| --- | --- | --- |
| `GoG` | Leland Wilkinson, *The Grammar of Graphics*, 2nd ed., Springer 2005 | 印刷页取自页眉；各章偏移不一，无页眉的页按相邻页推算，**可能 ±1** |
| `VAD` | Tamara Munzner, *Visualization Analysis and Design*, CRC Press 2014 | 印刷页 = 电子版页序 − 25，已逐处对过页眉 |
| `FDV` | Claus O. Wilke, *Fundamentals of Data Visualization*, O'Reilly 2019 | 印刷页 = 电子版页序 − 18，已逐处对过页眉；作者在 https://clauswilke.com/dataviz/ 公开全文，可按章名核对 |
| `XG` | 一个外部作图实践指引仓库的作图技能目录（地址与版本号不入库，本地留档；2026-11-22 版），根 `LICENSE` 为 Apache-2.0 | 没有页码：`loc` 写仓库内相对路径加小节标题 |
| `MATH` | 数学事实 | — |
| `TAVOTTO` | Tavotto 自己的推导 | — |

## 阅读覆盖（2026-11-21）

没读的章节，其结论不得引用。要用的话先补读，再更新本表。

| 书 | 细读 | 只抽查或没读 |
| --- | --- | --- |
| GoG | 第 1、2、4–7、9、10、12、18、19 章，20.3 | 第 15 章只读了开头与 15.3 开头；第 17 章只读了 17.1 前半；第 3、8、11、13、14、16 章及各章 Examples 只抽查 |
| VAD | 第 2–7、10 章 | 第 1、11、12、13、15 章只读了被引的节；第 8、9、14 章没读 |
| FDV | 第 1–4、6–14、16–29 章 | 第 5、15 章没细读 |

文本提取的损坏：GoG 的图 2.2（管线图）、Table 7.1、8.1、10.1 在提取时损坏，相关结论只取自正文叙述。

## XG 的地位与用法（2026-11-23 登记）

XG 是一份给编码 Agent 用的作图实践指引，不是原著，也不是期刊规定。对照全文见 [`docs/research/external-figure-practice-absorption.md`](../research/external-figure-practice-absorption.md)。

- **证据标记一律 [二手]**。它自述多面板部分是「从语料归纳的单一期刊风格指引，不是官方要求」（`references/multipanel-evidence-architecture.md`），数值是「针对单一期刊系列的保守默认值」（`references/qa-contract.md`）。只靠它支撑的条目，`strength` 不得标 `firm` 或 `strong`。
- **它的数值不进本体系**：5 pt 字号下限、89 / 183 mm 栏宽、1.5 pt 对齐容差、600 dpi 都是它对 单一期刊系列的转述或自定默认值。期刊阈值只来自期刊原文（ADR 0155）；对齐容差以 ADR 0199 §六 为准。
- **不复制代码与文字**：许可证是 Apache-2.0，但仓库内自相矛盾（README_EN.md` 与 仓库内一批第三方素材THIRD_PARTY_NOTICES.md` 称根许可证是 MIT），根 `LICENSE` 的版权人一栏也没填。Tavotto 只吸收做法、自己实现。仓库内一批第三方素材 是没有许可证的第三方材料，不读作依据、不引用。
- **阅读覆盖**：
  - 细读：`SKILL.md`、`static/core/contract.md`、`static/core/stance.md`、`references/qa-contract.md`、`references/asset-adaptation.md`、`references/multipanel-evidence-architecture.md`、`references/chart-types.md`、`scripts/audit_pdf_text.py`、`scripts/figure_safety.py`。
  - 只抽查：`references/design-theory.md`（只看了标题与带硬措辞的行）；`scripts/audit_panel_alignment.py`、`scripts/audit_figure_collisions.py`、`scripts/validate_figure.py`（读了函数表、阈值常量与核心判据段，没有逐行读完）。
  - 没读：其余 `references/`、`assets/`、`evals/`、R 脚本、AI 示意图相关脚本。没读的部分不得引用。
- 它的脚本只当文本读，没有执行，也没有安装它的依赖（PyMuPDF）。

## 三本书都没有的内容（检索确认，别把它们挂在书的名下）

- 期刊规范：字号、dpi、线宽、栏宽。这些的出处是期刊原文（ADR 0155）。
- Matplotlib / R / MATLAB 的具体规则。
- 双 y 轴：只有 GoG 一句「一般应避免」，VAD 和 FDV 都没讨论。
- symlog；对数轴上负值的处理（FDV 只谈到 0）。
- 箱线图和小提琴图的 n 阈值；「图上必须标 n」。
- VAD 里没有不确定性、误差线、置信区间、显著性，也没有零基线、截断轴。

## 版权口径

- 仓库里只放转述、≤ 25 词的逐字引文和页码。
- 原书电子版不入库。
- 研究时用的全文提取只放在会话临时目录。
