# 06 · Tavotto-FigureBench 计划

夹具与场景：`tests/fixtures/figurebench/`（README 是场景清单）；夹具自证：`tests/test_figurebench_fixtures.py`。

## 一、Phase 0 交付（已实测）

- 7 个合成 matplotlib 夹具 + 8 份 CSV 输入，真值手写、标准库核对。
- 45 个场景：dev 32 / holdout 13（`sha256(id) % 5 == 0`）；单元素 9、复杂图 4、科学正确性 7、范围纪律 8（`scope_discipline`，只许改一处、最自然的写法会顺手改到别处）、数据出图 8、可靠性 9。
- 27 个改图场景可由原生探针核验：基线不满足目标、参考解满足全部断言、每个反例让点名的断言全红。
- 可靠性场景：5 个指向现有用例（幂等、过期回滚、脚本锁、中断识别、超时 / 取消），4 个登记为后续 Phase 的缺口。
- 本机：40 passed（27 个探针场景，含每条保持断言的反例；2026-10-21 重跑）；六个手工变异全部按预期变红（`01-existing-system-audit.md` §七）。

**Phase 0 不产生任何「通过率」。** 被测系统（TAF）还不存在；所有指标在 Phase 2 起才有基线。

## 二、运行方式（随阶段增长）

| 阶段 | 跑什么 | 谁来执行 | 进普通 CI？ |
| --- | --- | --- | --- |
| 0 | 夹具自证 | `_probe.py`（原生 matplotlib） | 是（随 pytest；无解释器则 skip = not_run） |
| 2 | Figure Verifier 在全部探针场景的参考解上判 verified、反例上判 failed，并与探针事实对拍 | 验证器 + safe worker | 是 |
| 3–4 | 改图任务端到端 | Fake Executor（按场景的 `reference_solution` / `negative_variants` 回放编辑） | 是 |
| 5 | 数据出图端到端 | 确定性映射（无模型） | 是 |
| 6 | 真实模型基准 | `scripts/dev/taf_bench.py --real --budget-usd N` | **否**：单独命令、显式计费、结果作为报告附在 PR |

Fake Executor 的形状：实现 `AgentDefinition` 的测试适配器（`tests/test_ai_agents.py::_FakeAgent` 同一注入方式），它
「改脚本」的方式是对候选目录应用场景里登记的编辑——这样基础设施（候选、L1、提交、回滚、验证）全部真实运行，只有
「理解指令」被替换。

## 三、指标（Phase 2 起记录；先建基线再谈改善）

| 指标 | 定义 |
| --- | --- |
| First Attempt Verified Rate | 第一轮提交前检查就过（`rounds` = 1）且 outcome = completed、figure verdict = verified 的场景比例 |
| Final Verified Completion Rate | 不论几轮，最终 outcome = completed 且 figure verdict = verified 的比例（6b 起与首发分开出） |
| Scientific Preservation Rate | 所有 L2 保持断言 verified 的比例（分母含 unknown，unknown 不算保持） |
| Unintended Modification Rate | L1 发现非目标文件变化或保持断言 failed 的比例 |
| Recovery Success Rate | 可靠性场景按期望结局收尾的比例 |
| 时延 | 中位数 / P95（墙钟，按阶段拆） |
| Tokens / Cost per Verified Task | 只在真实模型基准上记，来源是 CLI 自报的用量 |
| User Intervention Rate | outcome = needs_input / partial 的比例 |
| TAF Overclaim Rate（6b 新增） | 基准判反例违反了目标 / 保持断言、TAF 却给 completed 的比例——任务结局对用户「说多了」 |

报告字段：基线 / 候选策略版本、场景集与夹具版本（git sha）、模型与 CLI 版本、OS、matplotlib 版本、执行次数、
每个场景的 outcome 与 verdict、未运行与不可比的场景（独立状态，不并进分母）。

**Phase 6b 基线**（`scripts/dev/taf_bench.py --negatives`，回放执行器，Pack v1，macOS / matplotlib 3.11.2；
`evidence/bench-6b-baseline.json`）：dev 14 个可回放场景 + 16 个反例全部跑完（not_run 0）。参考解首发 verified 1.0、
最终 verified completion 1.0（回放参考解全部一轮过，rounds = 1）、科学保持 1.0、非预期修改 0（含保持断言 failed）；反例被基准抓住 1.0。**TAF Overclaim 0.625**（16 个被抓住的反例里 10 个 TAF 自己判
completed）：提交后验证只核数据指纹，标题 / 单位 / 配色 / 刻度类型 / 图例的改动它看不见——这是 Harness Lab 与后续
验证层要压低的数，不是基准的问题。

## 四、防过拟合

- Harness Lab 只读 `scenarios.json`（dev）；`scenarios_holdout.json` 只在复核时整体运行、只出汇总分。Phase 6 加一条
  守卫：优化轨迹、经验条目、Prompt Pack 候选里不得出现 holdout 场景的 id、指令原文或断言（Harness² 的
  `assert_no_leak` 同一思路）。
- 场景只增不改；改一个已有场景的断言要在 PR 里说明，且该场景的历史结果作废。
- 扩到 100+：优先从 CompatBench 的 149 个 case 派生（它们已有 `semantic_expectations` 与 `mutations`），每个新场景
  同样要有参考解与反例。

## 五、已知不覆盖

- 本机字体与 Tavotto 批准字体不同：探针不判字形（`FB-S08` 的 unverified）。
- 探针读的是原生 matplotlib，不是 Tavotto 的 worker；两者在 override 重放上的差异由 Phase 2 的对拍负责发现。
- Windows / Linux 上夹具自证尚未单独跑过（CI 会跑；本机只有 macOS）。
