# Tavotto AI Foundation（TAF）实施包

决定：`docs/adr/0138-tavotto-ai-foundation.md`（Proposed）。本 README 是**交接页**：换一个会话也能从这里准确接着做。

## 当前状态（2026-10-19）

| 项 | 状态 |
| --- | --- |
| Phase 0 | PR #1187（`feat/taf-phase0`）：审计、ADR、实施包、FigureBench 夹具。`tests/test_figurebench_fixtures.py` 本机 32 passed，六个手工变异全红 |
| Phase 1 | 分支 `feat/taf-phase1-task-core`（叠在 Phase 0 上）：`engine/ai_tasks.py` + `ai_history` 加七列 + `tests/test_ai_tasks.py`（48 条）。AI 相关与架构门禁 13 个文件本机 355 passed / 1 skipped（真 CLI 冒烟，需 `TAVOTTO_REAL_CLI_SMOKE=1`）；十个手工变异全红（PR 正文附表） |
| Phase 2 | 分支 `feat/taf-phase2-verifier`（叠在 Phase 1 上）：`engine/figureverify.py` + manifest 只读 `data_fingerprint` + `tests/test_figure_verifier.py`。19 个探针场景参考解全 verified、反例全 failed（唯一盲区 `FB-C04/n.unshared` 登记在 `KNOWN_BLIND`）；指纹与原生探针逐字节对拍；九个手工变异全红 |
| Phase 3a | 分支 `feat/taf-phase3-figure-edit`（叠在 Phase 2 上）：`engine/ai_figure_edit.py` + `ai_bridge.run` 候选分支 + `ai_tasks.begin_registered` / `drive_registered` + `tests/test_ai_figure_edit.py`（14 条）。R-03 实测见 `08-risk-register.md`；九个手工变异全红 |
| 产品行为变化 | 开关 `ai.taf.enabled` 关（默认）时无：旧路径逐字不变、`ADAPTERS` 为空；旧会话的新列全 NULL；manifest 只多一个只读字段 |
| 未运行 | 全量 pytest / 前端 / e2e（交给 CI，high 档加 `full-ci`）；Windows / Linux |
| 下一步 | Phase 3b：提交后渲染层验证（`on_verify`：改前 manifest 取自已有 worker、改后取自本来就会发生的那次重渲染）+ 前端阶段与结局文案 |

## 待维护者决定

1. **1.0 收敛纪律**：Phase 3 起有用户可见变化（在默认关闭的开关后面）。登记为与统一实施包同类的例外，还是推迟到 1.0 之后？
   Phase 1–2 不受影响（内部契约 + 只读事实）。
2. **数据指纹进 manifest**：Phase 2 要在 worker 侧 manifest 加只读字段 `data_fingerprint`（不参与 gid / identity，
   不进文档与诊断公开面）。这是 manifest 协议的加字段，按 high 档走。是否同意这个落点？
3. **数据出图 v1 不执行模型写的代码**（模型只给列映射 JSON，脚本由模板生成）。这会限制 v1 能画的图形种类
   （折线 / 散点），但避免把 safe 档当沙盒用。是否接受这个边界？

## 文件

| 文件 | 内容 |
| --- | --- |
| `01-existing-system-audit.md` | 代码事实表（运行时 / 验证 / 产品 / 安全 / Harness² / 基准资产），带 ✅ 的已本机复核 |
| `02-architecture.md` | 依赖图、五个子系统落点、流程 A / B / C、候选工作区、与既有 ADR 的张力 |
| `03-contracts.md` | TaskEnvelope / TaskResult / CheckResult / Intent Contract / 预算 / 适配器 / 工具 / 状态映射 / Harness Lab 变更面 |
| `04-security-model.md` | 资产、信任边界、safe 档能与不能、CLI 写入面、注入、路径与提交、隐私、绝不做的事 |
| `05-verification-design.md` | L1–L5、数据指纹、判定规则、反例纪律、证据、性能预算 |
| `06-benchmark-plan.md` | FigureBench 交付、运行方式、指标、防过拟合、不覆盖项 |
| `07-pr-roadmap.md` | Phase 0–6 依赖、改动面、风险档、入口 / 退出标准、性能、回滚 |
| `08-risk-register.md` | 风险登记（R-01…R-14） |

## 接手须知

- 改任何子系统前先读最近的 `AGENTS.md` 与它指向的细则（根 `AGENTS.md` 的子系统索引）。TAF 的后端落点读
  `docs/rules/backend/ai-agent-bridge.md`、`script-edits.md`、`process-boundaries.md`、`writeback-transaction.md`。
- 速查表（根 / `src/tavotto/` / `web/` 的 `AGENTS.md`）距 32 KiB 硬线不到 100 B：TAF 规则全文写进
  `docs/rules/backend/` 新细则，速查表加行前先腾字（`python scripts/dev/agents_budget.py`）。
- 本地跑夹具自证：`PYTHONPATH=$PWD/src .venv/bin/python -m pytest tests/test_figurebench_fixtures.py -q`
  （worktree 里 `PYTHONPATH` 必须是绝对路径；需要一个装了 matplotlib 的解释器，否则 skip = not_run）。
- 新 ADR 编号先查 `docs/adr/` **和在飞 PR**（2026-10-19：0135 在 #1159、0136 在 #1181、0137 已合入）。
