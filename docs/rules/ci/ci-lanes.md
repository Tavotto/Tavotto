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

**PR 级 CI 只对 base 是 main 的 PR 触发**（2026-09-21 用户拍板）：监听 `pull_request`
的四个 workflow（`ci.yml` / `codeql.yml` / `pr-conflict-domains.yml` / `foundation-u02-spikes.yml`）
都带 `on.pull_request.branches: [main]`（U02 那份在 `paths` 之外再加，两者是 AND）。叠栈 PR（base 是上一层分支，`docs/ci/parallel-prs.md`
「Stacked PR」）在合入 main 之前**不跑**这套 CI——统一实施包的 14 层叠栈每层 push 都起
~17 个 job，逐级 rebase 一次 ≈180 个 ubuntu job，合并队列的候选（#462 / #455）被挤到 90
分钟 `checks_timed_out` 踢出；叠栈 PR 靠 Codex 评审 + 本地验证，retarget 到 main 成为链头
之后才跑全套，那时它才有资格进队列。选的是 workflow 级过滤而不是 job 级 `if:`：后者仍
会每 push 起十几个 job 且要改 Gate 的闭集语义；前者一刀切、三个 Gate / `aggregate_gate.py`
/ `concurrency` / 任何 `if:` 一行不动，`merge_group` 事件不受影响（队列候选的 base 永远是
main）。三个 Gate 的 required 语义（缺失 = 失败）让 base ≠ main 的 PR 合不进 main——本来
就该如此。**retarget 本身不产生 run**：改 base 是 `pull_request.edited`（GitHub webhook
文档原句「The title or body of a pull request was edited, or the base branch of a pull request
was changed」），它不在 `ci.yml` 的 types 闭集里、也不在其余几个的默认三个里（加进去 = 每次
改标题 / 正文都重跑整条快线）。所以顺序是**先 retarget、后 push**：下层合入后仓库的
`delete_branch_on_merge` 让 GitHub 自动把上层 base 改到 main（时间线事件
`automatic_base_change_succeeded`），随后 `rebase --onto origin/main` 的 push 才是那个带着
base = main 的 `synchronize`，四个 workflow 一起跑；反过来（先 push 后 retarget）两头落空，
PR 上没有任何结论，补救是再推一个空提交（`git commit --allow-empty`）。实证：PR #371 在
#370 合入（2026-09-16 09:34:29Z）后 09:34:30Z 自动 retarget，之后没有任何 `pull_request`
run，09:35:34Z 的 push 才起了 CI / CodeQL / conflict domains 三个。看护：
`tests/test_merge_queue_workflows.py::TestPullRequestBaseFilter`（监听 `pull_request` 的
workflow 集合 == 登记的四个；每一个 `branches` == `[main]`；`edited` 不在任何一个的 types
里）——新加一个 PR 级 workflow 要到 `PR_WORKFLOWS` 登记并带同一条过滤（叠栈分支上的
`foundation-u06-rendercore.yml` / `private-python-targets.yml` 进 main 时就是这样）。
