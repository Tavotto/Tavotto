# docs/sciviz/ — Tavotto 的科学可视化知识体系

把三本著作的原则变成 Tavotto 可执行、可测试、可维护的工程知识，供以下工作使用：
- 需求分析、架构设计、代码评审；
- 预检规则；
- 数据出图推荐；
- Tavotto Agent。

立于 2026-11-21（维护者拍板见 [研究方案](../research/scientific-visualization-foundations-plan.md) §7）。

| 文件 | 内容 | 权威性 |
| --- | --- | --- |
| [`sources.md`](sources.md) | 来源、版本、阅读覆盖、页码口径、版权口径 | 新来源先在这里登记才能被引 |
| [`principles.md`](principles.md) | P1–P8：来源、适用范围、例外、工程意义、实现与验证方式 | 原则的唯一全文 |
| [`decision-framework.md`](decision-framework.md) | 数据抽象 → 任务 → derive → 编码 → 理由三档 | 数据出图与 Agent 选图的骨架 |
| [`validation-spec.md`](validation-spec.md) | V-xxx 规则规格：检测逻辑、严重度、误报、测试 | 规则规格的唯一全文；判据实现仍以 `publication.json` + 两侧求值器为准 |
| [`core-implications.md`](core-implications.md) | 对 Core 模型、编辑、跨语言、可复现的影响 | 架构方向，落地时另开 ADR |
| [`../../src/tavotto/profiles/sciviz_guidance.json`](../../src/tavotto/profiles/sciviz_guidance.json) | G-xxx 指导条目，**含品味条目**，每条一句给 Agent 的准则 | Agent、插件技能、数据出图准则取文本的唯一出处 |

## 证据标记（全体系通用，不许混写）

| 标记 | 含义 |
| --- | --- |
| **[原文]** | 在原著全文里定位并核对过：章节 + 印刷页；引文 ≤ 25 词，逐字核对 |
| **[二手]** | 原著转述的他人研究，或我们没读原著的来源 |
| **[推导]** | Tavotto 自己的工程推断；**不能升格为 [原文]** |

`sciviz_guidance.json` 里对应的是 `evidence[].tag`：`原文` / `二手` / `推导`。

## 问题的五种依据（`basis`）

| basis | 含义 | 去向 |
| --- | --- | --- |
| `encoding` | 图与数据在数学或编码上不一致，可以客观判定 | 预检 `warn`（维护者 2026-11-21 定）；不用 error 阻断 |
| `statistics` | 统计表达的风险，依赖上下文 | 预检 `suggestion`，或只作 Agent 准则 |
| `visual` | 感知建议，有例外 | 预检 `suggestion`，或只作 Agent 准则 |
| `undecidable` | 仅凭图和数据判不了 | 不出 issue；进问题面板的「需要你确认」，或由 Agent 提问 |
| `taste` | 作者偏好、「matter of taste」 | **永不进预检**（`tests/test_sciviz_guidance.py` 看护）；给 Agent 作参考：按用户或期刊，不当问题报告 |

期刊格式不在本体系里，它的出处是期刊原文（ADR 0155）。

## 维护纪律

- 改原则：先改 `principles.md`，再看 `sciviz_guidance.json` 里引用它的条目要不要跟着改。
- 加规则：先写 `validation-spec.md` 的 V 条目（schema 必填项齐全），再实现判据，在 G 条目的 `spec` / `checks` 里登记指向。
- 加 Agent 准则：只改 `sciviz_guidance.json`。代码通过 `engine/sciviz_guidance.render()` 取文本，**不在提示词里另抄**。
- 引文只放转述加 ≤ 25 词的原文；原书文件不入库。
- 看护：`tests/test_sciviz_guidance.py` 钉住以下几件事：
  - 闭集取值；
  - 品味条目不进判据；
  - 指向的 P / V / 预检 id 都真的存在；
  - 渲染结果确定。
- 开发与评审时用的问题清单：[`docs/rules/repo/scientific-figure-review.md`](../rules/repo/scientific-figure-review.md)。
