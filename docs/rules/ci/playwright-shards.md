# windows-exe-smoke 的 Playwright 分片（CI03c）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **`windows-exe-smoke` 的 Playwright 按 project 分 2 片（CI03c，2026-09-16）**：matrix `include` 两条
  （片 1 `--project=chromium`；片 2 `--project=webkit --project=chromium-en`），每条带 `browsers`（本片要装的引擎）、
  `projects`（本片的 `--project=` 参数，原样进 `pnpm e2e`）与 `others`（其余片的）；值里不能有逗号。两片**各自完整**
  构建产物并都跑三条断言与冒烟①②③——必需步骤一律不加 `if:`。完整性三层：合同测试
  `tests/test_merge_queue_workflows.py::TestPlaywrightShards`（与 `web/playwright.config.ts` 的 project 集比：并集相等、两两不交、
  `others` == 其余片之并、装的引擎 == 本片 project 要的）→ 每片 e2e 之前的自验 `scripts/ci/playwright_shard_check.py`（对着
  Playwright 自己的 `--list`，主语是 (project, file:line:col, title) 集合，不是条数；不完整 rc 1、清单读不懂 rc 2）→ matrix 语义 + Gate
  闭集（不动）。**job id 不变**，显示名 `windows-exe-smoke (1)` / `(2)`（显式 `name:`；不写的话 include 形状会把四个字段全排进显示名），
  required contexts 仍只有三个 Gate。artifact 名一律带 `${{ matrix.shard }}`（upload-artifact v4 同名失败）。加 / 删 / 改名一个 project
  就要回去改那张 matrix，合同测试会红。两条 Playwright 步都有 **step 级** `timeout-minutes`（30 / 20，job 级 60 / 45 不动）：job 级硬杀时 step
  停在 in_progress、`if: failure()` 的收集步骤不跑、日志 blob 与 artifact 都没有（PR #373 attempt 1 实测），step 级超时把挂起变成带日志的失败。
  设计、本机实测、负例与已知边界：`docs/implementation/ci-foundation/CI03C_PLAYWRIGHT_SHARDS.md`。
