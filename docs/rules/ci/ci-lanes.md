# CI 分层：PR / merge_group / full-ci / push main / nightly 各自的时机

> 原文出自 `.github/AGENTS.md`「CI 分层（1.0 稳定化，2026-08-21 起）」（2026-09-18 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

CI 按**发生时机**分工（`.github/workflows/ci.yml` 抬头有全图，2026-08-25
Merge Queue 定版）：PR = 快速反馈（python-lint / invariants / backend-fast /
frontend / workerd / **desktop-shell** / compat-smoke / CodeQL）；merge_group = 完整合并资格的唯一常规执行
点（backend-platforms / package ×3 / 两个真产物冒烟，Merge Queue 对「最新
main + 前序 PR + 当前 PR」的组合提交验证）；`full-ci` 标签 = 在 PR 自己的
SHA 上提前跑全套；push main = 轻量落地审计（main-landing-audit，不重复打
包）**+ 缓存种子（cache-seed，非门禁，2026-09-16 起）**——它不产生任何结论、
不在任何 Gate 的闭集里，只是在 main 上把 pnpm store / CPython 归档 / rust-cache
各种一份；为什么 push main 要多这一个 job，见下面「门禁纪律」的缓存那一段；
nightly / lab / release 照旧。**覆盖面一条没减，改的是时机**。ruleset
的 required checks 只有三个稳定 Gate（CI fast gate / CI integration gate /
CodeQL gate），判定收敛在 `scripts/ci/aggregate_gate.py`——普通 PR 上
integration gate 显式 deferred，merge_group 与 full-ci 永远不许 deferred。
迁移顺序与 Ruleset 工具见 `docs/ci/merge-queue-rollout.md`；受管生成物
（canvas.html 等）的冲突域治理与 stack / train 协作见
`docs/ci/parallel-prs.md` + `.github/conflict-domains.json`。ci.yml 与
codeql.yml 的 `cancel-in-progress` **只对 PR 开**：merge_group 候选与 main
的唯一验证记录都不许被取消，tag / release 链路不在分组里。

四条 workflow 的顶层 env 钉 `TAVOTTO_NO_TELEMETRY=1`——**CI 绝不产生真实的
产品事件**（细节见 `src/tavotto/AGENTS.md` 的遥测一节）。
