# windows-exe-smoke 的 Playwright 分片（CI03c）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`.github/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_merge_queue_workflows.py::TestPlaywrightShards`、`tests/test_playwright_shard_check.py`、`tests/test_feature_registry.py`
> `.github/AGENTS.md`「必守要点」压缩前原文（2026-10-23，与正文同等有效）：Windows 三片完整构建（chromium 内部原生 `--shard=1/2`、`2/2` + webkit/chromium-en 一片，`others` 用 `\|` 隔开）；POSIX 三片按 planned list 对拍 active 用例；必需步骤不加 `if:`；`others` == 其余各片的 projects；artifact 名带 shard；step 级 `timeout-minutes`

<!-- agents-route
sheet: .github/AGENTS.md
order: 60
改到: `windows-exe-smoke` matrix、`posix-e2e` 原生 `--shard`、`web/playwright.config.ts` 的 project 集
主题: Playwright 分片（CI03c）→ `playwright-shards.md`
必守要点: 各片 projects 并集 == 全集、POSIX 按 planned list 对拍；必需步骤不加 `if:`；artifact 名带 shard；step 级 `timeout-minutes`
-->

- **`windows-exe-smoke` 的 Playwright 分 3 片（CI03c，2026-09-16 按 project 分 2 片；2026-10-19 chromium 内部再拆一刀）**：
  matrix `include` 三条（片 1 `--project=chromium --shard=1/2`；片 2 `--project=webkit --project=chromium-en`；片 3
  `--project=chromium --shard=2/2`），每条带 `browsers`（本片要装的引擎）、`projects`（本片的参数，原样进 `pnpm e2e`；可带一个
  Playwright 原生 `--shard=i/n`）与 `others`（**其余每一片**的 `projects`，按 matrix 顺序、用 `|` 隔开）；值里不能有逗号（`|` 可以）。
  三片**各自完整**构建产物并都跑三条断言与冒烟①②③——必需步骤一律不加 `if:`。完整性三层：合同测试
  `tests/test_merge_queue_workflows.py::TestPlaywrightShards`（与 `web/playwright.config.ts` 的 project 集比：并集相等；同一 project
  被多片声称时必须每片都带 `--shard=i/n`、n 相同、i 恰好 1..n 各一次；`others` == 其余各片的 `projects`；装的引擎 == 本片 project 要的）→ 每片 e2e 之前的自验
  `scripts/ci/playwright_shard_check.py`（对着 Playwright 自己的 `--list`，对本片与其余每一片各列一次；主语是 (project, file:line:col, title)
  集合，不是条数：各片两两不交、并集 == 全集；不完整 rc 1、清单读不懂 rc 2）→ matrix 语义 + Gate 闭集（不动）。**job id 不变**，
  显示名 `windows-exe-smoke (1)` / `(2)` / `(3)`（显式 `name:`；不写的话 include 形状会把四个字段全排进显示名），required contexts 仍只有三个
  Gate（`CI fast gate` / `CI integration gate` / `CodeQL gate`），**新增的 `(3)` 不用登记成必需检查**。artifact 名一律带 `${{ matrix.shard }}`
  （upload-artifact v4 同名失败）。加 / 删 / 改名一个 project、或改 chromium 的 `--shard` 分法，就要回去改那张 matrix，合同测试会红。
  **为什么 chromium 用原生 `--shard` 而不是文件清单**：新增 spec 不需要人工维护清单；`workers: 1` + `fullyParallel: false` 下分组 == 文件，
  原生按文件顺序、按条数等分（2026-10-19 实测 151 条 / 32 文件 与 141 条 / 43 文件；用 run 39235718807 的逐条耗时累加约 23.4 / 20.0 分钟）。
  代价是按条数等分不看时长，随 spec 增减两半会慢慢偏；偏到某半 e2e 逼近 30 分钟再重估。
  两条 Playwright 步都有 **step 级** `timeout-minutes`（45 / 35，job 级 60 / 45 不动；posix-e2e 单 worker 约 19.7 分钟、209 条，20 分钟上限在 run 36734896556 上误杀过，所以放到 35；
  windows chromium 一个 project 单片曾 27.4–49 分钟，30 分钟上限在 PR #828 / #833 上误杀过，所以放到 45——拆成两半后各约 20–23 分钟，上限刻意不动）：job 级硬杀时 step
  停在 in_progress、`if: failure()` 的收集步骤不跑、日志 blob 与 artifact 都没有（PR #373 attempt 1 实测），step 级超时把挂起变成带日志的失败。
  回退：删 matrix 里的片 3、把片 1 的 `projects` 改回 `--project=chromium`、`others` 各改回整 project 形状（自验与合同测试都认）；撤掉前先别让别处依赖 `(3)` 这个 check 名。
  设计、本机实测、负例与已知边界：`docs/implementation/ci-foundation/CI03C_PLAYWRIGHT_SHARDS.md`（该文成稿于两片时期，三片的增量记在本节）。


## POSIX Playwright 三路分片（Issue #768）

`posix-e2e` 的 job id 不变，`strategy.matrix.shard: [1, 2, 3]`，
三个独立 `ubuntu-24.04` Runner 使用 Playwright 原生 `--shard=i/3` 分配
Chromium + chromium-en 所有用例（不改 project 集合）。2026-10-14 #1000 full-ci
单 Runner 的 step 级 35 分钟上限只跑到约 274 条用例即被杀；原先止血余量已耗尽。

每个 shard 先让 Playwright `test --list --shard=i/3 --reporter=json` 产生计划，
随后相同项目和相同分片实际运行；`feature_registry.py verify-run --expected-list`
严格核验该 shard **计划中的 active 用例与 project** 均有真实成功结果。
其他 shard 的注册用例允许不在本报告内，但未分片的 frontend 静态资格已确认
全部注册用例存在；GitHub matrix 聚合语义要求三片全部成功，
`needs.posix-e2e` 才是 success。故无需修改 Gate job id、needs 或 required 闭集。
失败 trace artifact 名须包含 shard 号，防止 artifact 冲突。



## WebKit-only 注册用例防假绿（#1021 审核补强）

Chromium / chromium-en 的各 shard 真跑前，额外生成一次两者**未分片**的
`playwright test --list`，由 `feature_registry.py verify-run --all-planned-list`
检验每一条 active 注册引用至少被这两个 POSIX project 中的一项以
`expectedStatus=passed` 收集。只有 WebKit 拥有的用例不能只凭 frontend
“全浏览器有登记”就被错判为真实运行通过；须改变注册策略或在另一条腿补齐
运行期验证后才放行。任何缺少全量清单的分片调用都返回 rc=2，三个 Runner
不得拿局部 JSON 假冒完整覆盖。
