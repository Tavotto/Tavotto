# D. 对 Tavotto Core 架构的影响

本文区分短期（沿现有路径加字段）与长期（每一步另开 ADR）。现状证据见[研究方案](../research/scientific-visualization-foundations-plan.md) §2。

## 现状里与理论一致、不该改的

- **能力门控**：不开放挪柱子，数据坐标的曲线也不宣称可拖（`engine/overrides.py`、`engine/manifest.py`）。这就是 GoG「数据约束属性」的实现（P4）。
- **写回三段事务**：prepare → verify → commit，以及导出 `_edited.py` 时的重跑核对（P8）。
- **编辑存储**：`(gid, prop)` 全量 override 列表与重放语义。
- **RenderCore**：搬入外来页，不重画（ADR 0065 / 0072）。语法与实现分离，正是 GoG 的立场（p.24、p.633 [原文]）。

## 短期（不改架构）

1. **只读语义事实**：worker 从活 artist 算出，挂在 manifest 元素上，不进 `editable`、不进文档，和 `data_fingerprint` 共用一份预算。首批两项：
   - 每根 log 轴上画不出来的数据个数（V-001）；
   - 柱组的共同基线（V-002）。
2. **编辑三分的不变量**：按 P4 给 `HANDLERS` 的键分成样式、结构、数据三类，配一条测试：样式键与尺度键应用后，所有元素的数据指纹不变。尺度或范围编辑的即时反馈交给 V-001 / V-002 的预检。
3. **统计元数据可读**：数据出图的配方（`summary` 等）现在只写在脚本头注释 `# tavotto-source:` 里，没有下游读它。把解析结果放进项目可读的位置，让「需要你确认」和预检都能读到，从而能显示「误差线为 SD」这类事实（V-004）。
4. **Agent 准则单一出处**：所有提示词都从 `profiles/sciviz_guidance.json` 经 `engine/sciviz_guidance.render()` 取文本。

## 长期方向（只给方向，不排期）

- **语义侧车，而不是替换 manifest**：
  - 元素可以带一个可选的 `semantics` 块，按 GoG 的层次分：data / stat / scale / coord / geom / aesthetic / guide。
  - 每层的值要么是「已知 + 来源」，要么是 `unknown`。
  - matplotlib 路径上大多数层是 `unknown`，少数来自脚本的静态信息。
  - ggplot2 原生路径（#2067 Core v0.2 的后续）天生带 stat 与 scale 层，会是第一个有实际内容的消费者。
  - SVG 的 `ChartModel`（ADR 0182）带数据层。
  - 要统一的是**语义描述**；渲染照旧由各个生态各自负责。
- **显式 schema 版本**：语义侧车一出现就带版本号，沿用 `chartmodel` 的 `name/major` 写法，不再沿用「老引擎不发」这种隐式演进。
- **可复现**：
  - 导出时可选嵌入来源信息：脚本哈希、数据哈希、统计元数据。这一项涉及隐私说明，要拍板。
  - 回执的输入观察逐步覆盖 pandas。

## 明确不做

- 重写渲染器或 manifest。
- 从渲染结果反推统计。GoG 第 19 章（p.612 [原文]）自己承认，没有元数据就复原不了。
- 把 matplotlib 调用强行翻译成 GPL 风格的规格。
- 按 Stevens 指数修正面积映射（GoG p.258–259 [原文]）。
