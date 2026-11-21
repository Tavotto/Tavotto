# 来源登记

只有在这里登记过的来源才能被 `principles.md`、`validation-spec.md`、`sciviz_guidance.json` 引用。

| 键 | 来源 | 页码口径 |
| --- | --- | --- |
| `GoG` | Leland Wilkinson, *The Grammar of Graphics*, 2nd ed., Springer 2005 | 印刷页取自页眉；各章偏移不一，无页眉的页按相邻页推算，**可能 ±1** |
| `VAD` | Tamara Munzner, *Visualization Analysis and Design*, CRC Press 2014 | 印刷页 = 电子版页序 − 25，已逐处对过页眉 |
| `FDV` | Claus O. Wilke, *Fundamentals of Data Visualization*, O'Reilly 2019 | 印刷页 = 电子版页序 − 18，已逐处对过页眉；作者在 https://clauswilke.com/dataviz/ 公开全文，可按章名核对 |
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
