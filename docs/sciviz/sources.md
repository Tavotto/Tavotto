# 来源登记

只有在这里登记过的来源才能被 `principles.md`、`validation-spec.md`、`sciviz_guidance.json` 引用。

| 键 | 来源 | 页码口径 |
| --- | --- | --- |
| `GoG` | Leland Wilkinson, *The Grammar of Graphics*, 2nd ed., Springer 2005 | 印刷页取自页眉；各章偏移不一，无页眉的页按相邻页推算，**可能 ±1** |
| `VAD` | Tamara Munzner, *Visualization Analysis and Design*, CRC Press 2014 | 印刷页 = 电子版页序 − 25，已逐处对过页眉 |
| `FDV` | Claus O. Wilke, *Fundamentals of Data Visualization*, O'Reilly 2019 | 印刷页 = 电子版页序 − 18，已逐处对过页眉；作者在 https://clauswilke.com/dataviz/ 公开全文，可按章名核对 |
| `XG` | 一个外部作图实践指引仓库的作图技能目录（地址与版本号不入库，本地留档；2026-11-22 版），根 `LICENSE` 为 Apache-2.0 | 没有页码：`loc` 写仓库内相对路径加小节标题 |
| `KD` | K-Dense AI, scientific-agent-skills: `skills/scientific-visualization`（MIT，`ec59a29a`） | 按文件与小节标题定位 |
| `WCAG` | W3C, WCAG 2.2（经 KD 转述） | 按成功准则编号（SC）定位 |
| `ARS` | imbad0202, academic-research-skills（CC BY-NC 4.0，`d169ec5c`） | 按文件与小节标题定位；只借思路，不引原文 |
| `MATH` | 数学事实 | — |
| `TAVOTTO` | Tavotto 自己的推导 | — |

## 阅读覆盖（2026-11-21）

没读的章节，其结论不得引用。要用的话先补读，再更新本表。

| 书 | 细读 | 只抽查或没读 |
| --- | --- | --- |
| GoG | 第 1、2、4–7、9、10、12、18、19 章，20.3 | 第 15 章只读了开头与 15.3 开头；第 17 章只读了 17.1 前半；第 3、8、11、13、14、16 章及各章 Examples 只抽查 |
| VAD | 第 2–7、10 章；第 12 章 §12.1–12.4.3 与 §12.4.4 开头（2026-11-23 补读，为组图原则 P9） | 第 1、11、13、15 章只读了被引的节；第 12 章 §12.4.4 之后（递归细分、§12.5 叠加）只读了被引的节；第 8、9、14 章没读 |
| FDV | 第 1–4、6–14、16–29 章（第 21 章 2026-11-23 逐页重读并核对页码；第 22、23 章只检索了与小倍数相关的句子） | 第 5、15 章没细读 |

文本提取的损坏：GoG 的图 2.2（管线图）、Table 7.1、8.1、10.1 在提取时损坏，相关结论只取自正文叙述。

## XG 的地位与用法（2026-11-23 登记）

XG 是一份给编码 Agent 用的作图实践指引，不是原著，也不是期刊规定。对照全文见 [`docs/research/external-figure-practice-absorption.md`](../research/external-figure-practice-absorption.md)。

- **证据标记一律 [二手]**。它自述多面板部分是「从语料归纳的单一期刊风格指引，不是官方要求」（`references/multipanel-evidence-architecture.md`），数值是「针对单一期刊系列的保守默认值」（`references/qa-contract.md`）。只靠它支撑的条目，`strength` 不得标 `firm` 或 `strong`。
- **它的数值不进本体系**：5 pt 字号下限、89 / 183 mm 栏宽、1.5 pt 对齐容差、600 dpi 都是它对 单一期刊系列的转述或自定默认值。期刊阈值只来自期刊原文（ADR 0155）；对齐容差以 ADR 0199 §六 为准。
- **不复制代码与文字**：许可证是 Apache-2.0，但仓库内自相矛盾（README_EN.md` 与 仓库内一批第三方素材THIRD_PARTY_NOTICES.md` 称根许可证是 MIT），仓库也没有 `NOTICE` 文件。Tavotto 只吸收做法、自己实现。仓库内一批第三方素材 是没有许可证的第三方材料，不读作依据、不引用。
- **阅读覆盖**：
  - 细读：`SKILL.md`、`static/core/contract.md`、`static/core/stance.md`、`references/qa-contract.md`、`references/asset-adaptation.md`、`references/multipanel-evidence-architecture.md`、`references/chart-types.md`、`scripts/audit_pdf_text.py`、`scripts/figure_safety.py`。
  - 只抽查：`references/design-theory.md`（只看了标题与带硬措辞的行）；`scripts/audit_panel_alignment.py`、`scripts/audit_figure_collisions.py`、`scripts/validate_figure.py`（读了函数表、阈值常量与核心判据段，没有逐行读完）。
  - 没读：其余 `references/`、`assets/`、`evals/`、R 脚本、AI 示意图相关脚本。没读的部分不得引用。
- 它的脚本只当文本读，没有执行，也没有安装它的依赖（PyMuPDF）。

## KD 与 WCAG 的地位与用法（2026-11-23 登记）

`KD` = K-Dense AI 的 [scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills) 里的 `skills/scientific-visualization/`，版本 `ec59a29a03d5c40f73ab40878df04860bc00e876`（2026-11-16），许可证 MIT。它是一份教编码 Agent 用 matplotlib / seaborn / plotly 出投稿图的操作手册，不是原著，也不是期刊规定。

- **证据标记**：KD 自己的话逐字核过，标 [原文]（意思是「这是 KD 原话」，不等于这条结论有原著支撑）；它转述的第三方标准标 [二手]。只靠它支撑的条目，`strength` 用 `inference`，并且带一条「Tavotto 采纳」的推导依据；不得标 `firm` / `strong`。
- **它的出版商规格快照**（`assets/publisher_profiles.json`，8 家）不进本体系：期刊阈值只来自期刊原文（ADR 0155）。
- **`WCAG`**：WCAG 2.2 的对比度数值是经 KD 转述的，本次没有直接核 W3C 原文，所以同样标 [二手]。它是网页无障碍标准，不是期刊要求，只用作可读性参考。
- **阅读覆盖**：
  - 细读：`SKILL.md`、`references/sources.md` 的开头与出版商一节。
  - 只抽查：`scripts/export_plan.py`（函数表）、`assets/publisher_profiles.json`（键名）。
  - 没读：`references/` 的其余文件、`assets/` 的样式表与配色脚本。
- 代码与样式表不复制（Tavotto 有自己的出版规范与期刊库）；它的脚本只当文本读，没有执行。

## ARS 的地位与用法（2026-11-23 登记）

`ARS` = [imbad0202/academic-research-skills](https://github.com/imbad0202/academic-research-skills)，版本 `d169ec5c4bebd4b5ad5e8b8a0559d1d9879a21d7`，许可证 **CC BY-NC 4.0（禁止商用）**。Tavotto 有付费版，所以**它的文字、代码、模板一律不进仓库**，只转述思路，不引原文。

- 它是一整套论文写作流水线，作图部分只有少数几份文件，按 APA 7.0 写。
- 只借两条思路：
  - 「什么时候不该画图」（G-068）；
  - 人为埋缺陷的评测集方法（`validation-spec.md`「评测方法」一节）。
- 它的作图规则与本体系核过的原著有冲突，**不采纳**：
  - 「饼图绝不能用」：FDV 说是品味，见 G-043；
  - 「没有误差线是严重错误」：三本书都没有这条；
  - 「双 y 轴是严重错误」：只有 GoG 说过一般应避免，见 G-041；
  - 「纵横比默认 4:3、时间序列 16:9」：VAD 的依据是按数据把线段倾角调到 45° 附近，FDV 说按要讲的故事定，都不是固定比例。
- 它用多模态模型看渲染图挑错的做法也不采纳：Tavotto 用数据指纹与几何干涉检测做确定性核对。
- **阅读覆盖**：
  - 细读：`academic-paper/references/statistical_visualization_standards.md` 的选图表与常见错误两节、`academic-paper/references/vlm_figure_verification.md` 的前半。
  - 只抽查：`academic-paper/agents/visualization_agent.md`（标题）、`docs/design/2026-08-02-610-statistical-recompute-baseline-spec.md`（开头）。
  - 其余没读。

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
