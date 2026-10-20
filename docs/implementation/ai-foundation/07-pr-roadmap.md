# 07 · PR 路线图（Phase 0–6）

每阶段一个独立 PR（可再拆小）；每个 PR 正文按任务书 §16 的十二项写（问题、范围、复用、架构变更、兼容、安全、
测试命令与退出码、反例、未验证项、回滚、性能、下阶段依赖）。风险档按 `docs/ci/pr-review-tiers.md`。

```
P0 ──▶ P1 ──▶ P2 ──▶ P3 ──▶ P4
              │       └────▶ P5
              └──────────────▶ P6（依赖 P2 的验证器；真实模型部分依赖 P3）
```

| Phase | 内容 | 主要改动面 | 风险档 | 入口条件 | 退出标准 |
| --- | --- | --- | --- | --- | --- |
| **0** | 审计、ADR 0138、实施包、FigureBench 夹具 | `docs/`、`tests/fixtures/figurebench/`、`tests/test_figurebench_fixtures.py` | low（纯文档 + 纯测试） | — | 夹具自证绿 + 变异证据；维护者读过 ADR |
| **1** | Task Core 最小实现 | `engine/ai_tasks.py`（新）、`engine/ai_history.py`（加列）、测试 | high（改会话 schema） | ADR 0138 被接受（或明确「按 Proposed 推进」） | Fake Adapter + Fake Executor 跑通：持久化、去重、取消、超时如实、重启识别中断、Schema 完整；旧 AI 路径零行为变化（现有 `tests/test_ai_*.py` 全绿）；父进程无科学栈可 import；不引入新依赖 |
| **2** | Figure Verifier + 数据指纹 + FigureBench 接验证器 | `engine/manifest.py`（只读新字段）、`ai_tasks` verify 段、抽出重放检查、测试 | high（manifest 协议加字段） | P1 合入 | 19 个探针场景：参考解 verified、反例 failed；与探针事实对拍；CompatBench / 不变式 / 等价性矩阵不回归；时延实测 |
| **3** | 改图助手接入 TAF（开关后面） | `app.py` `/api/ai/run` 分支、`ai_bridge`（候选 cwd）、`ai_tasks` figure_edit 适配器、前端 `ai.stage` 与结局文案 | high | P2 合入；1.0 例外已登记（ADR 0138 §五）；两个 CLI 写入面实测（R-03） | 开关关：逐字回到旧路径（现有 e2e + `tests/test_ai_*.py`）；开关开：Fake Executor 下 FigureBench 改图场景按期望结局；旧历史可读；不双重执行；项目切换不串；失败 / 取消不污染项目 |
| **4** | 有界修正与进度体验（4a 后端修正准入与预算；4b 前端阶段展示） | `ai_tasks` 修正准入、预算、`ai_bridge` 同候选再起 CLI、前端阶段展示 | normal–high | P3 合入 | 可修复缺陷一次修正后 verified；不可修复明确退出；取消后不提交；模型失联不丢原脚本；不绕开运行政策 |
| **5** | Data-to-Figure 最小垂直切片 | `ai_tasks` data_to_figure 适配器、模板生成器、`inspect_data_source`、前端入口 | high | P2 合入（P3 的提交路径复用） | FB-D01–D08 按期望结局；生成图可编辑、可导出、可被 figure_edit 修改；不覆写 CSV；**未新增**调度器 / 历史 / 模型发现 / 提交机制（结构测试证明两个适配器走同一 orchestrator） |
| **6** | Harness Lab | `scripts/dev/taf_bench.py`、`taf_optimize.py`、Prompt Pack 版本化（v1 逐字节等于当前提示词） | normal | P2；真实模型实验需 P3 | 基线可复现；候选可记录与比较；holdout 泄漏守卫；一次最小真实实验（基线 vs 一个改进 Prompt）带报告；产品默认 Pack 不由工具直接改 |

## 性能影响（逐阶段）

- P1：每次 AI 请求多几次 sqlite 写（同一事务内），无新进程。
- P2：manifest 计算多一次数据哈希（worker 内，O(数据量)）；改图任务多一次 manifest 读取。实测后写进 P2 PR。
- P3：候选目录复制（单文件级）+ 原项目有界指纹；不增加模型调用。
- P4：最多一次额外 Agent 调用（仅在验证给出具体失败条件时）。
- P5：数据摘要读取（标准库 csv，行数有上限）；不必然调用模型。
- P6：只在开发侧。

## 回滚

P1–P2 不改用户可见行为，回滚即 revert PR（新列保留为 NULL 无害）。P3 起一律在 `ai.taf.enabled`（默认 false）后面：
关掉即回到旧路径；PR revert 不需要数据迁移（新列只追加）。
