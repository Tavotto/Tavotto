# CI 分层：PR / merge_group / full-ci / push main / nightly 各自的时机

> 原文出自 `.github/AGENTS.md`「CI 分层（1.0 稳定化，2026-08-21 起）」（2026-09-18 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`.github/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_merge_queue_workflows.py`（含 `TestMergeGroupFailFast`）、`tests/test_aggregate_gate.py`（含 `TestMergeGroupFailFastAttribution`）
> `.github/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：时机分工与覆盖面不变；三个稳定 Gate；`cancel-in-progress` 只对 PR 开；顶层 `TAVOTTO_NO_TELEMETRY=1`；目录里每个监听 `pull_request` 的 workflow 都带 `branches: [main]`（`TestPullRequestBaseFilter`，算出来的集合、不用登记）

<!-- agents-route
sheet: .github/AGENTS.md
order: 10
改到: `.github/workflows/ci.yml` 的事件 / `needs` / Gate 闭集、`scripts/ci/aggregate_gate.py`
主题: CI 分层 → `ci-lanes.md`
必守要点: 时机分工与覆盖面不变；三个稳定 Gate；`cancel-in-progress` 只对 PR 开；监听 `pull_request` 的 workflow 都带 `branches: [main]`
-->

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

**docs-only 快车道（`docs-scope` job，2026-10-31）。** 只改 `docs/release-notes/**.md`（**不含**
`UNRELEASED.md`：`test_claude_plugin.py` / `test_dsh_bundle.py` 读它；只此一条，不扩到整个
`docs/`）的 PR 与队列候选不再跑 invariants / backend-fast / frontend / plugin-candidate / workerd /
desktop-shell / compat-smoke 和五个重型资格；python-lint / cla-check / release-note-gate / `docs-scope`
照跑，`docs-scope` 在 docs-only 时另跑 `tests/test_docs_references.py`（发行说明相对链接）。
**Gate 怎么有据地接受这些 skipped**（CI01 §4 ① 说的「前置 filter + 短路会让 Gate 判红」的解法）：
`docs-scope` 本身在 fast gate 的 `--required` 里（失败 / skipped = 红）；只有它 `success` 且输出
`docs_only == "true"` 时，两个 Gate 才带 `--classifier docs-scope --docs-only-skippable <写死的名单>`，
`aggregate_gate.py` 再从 `toJSON(needs)` 里核一遍 result 与 outputs（调用方的布尔值不算证据）。
integration gate 把 `docs-scope` 放进 needs 但不进 `--required`：非 docs-only 时先从 needs JSON 里剔掉，
参数与引入前逐字相同。分类依据：PR 用 `pulls/{n}/files`（条数与 `changed_files` 对账，改名连旧路径一起算），
merge_group 用 `merge_group.base_sha` → `head_sha` 的 `git diff --no-renames`，队列里任一 PR 改了别的就不是
docs-only。判定器与分类器都取默认分支。**bootstrap**：引入本机制的 PR 合入前，默认分支上是旧判定器——
`docs_only` 恒 false 时两个 Gate 的调用与旧版逐字相同，不会判红；docs-only 的 run 在旧判定器下按
fail-closed 红（不是假绿）。看护：`tests/test_docs_scope.py`、`tests/test_docs_only_fast_lane.py`
（含冻结的旧判定器 `tests/fixtures/ci_gate/`）、`tests/test_aggregate_gate.py::TestDocsOnlyFastLane`。

**PR 级 CI 只对 base 是 main 的 PR 触发**（2026-09-21 用户拍板）：监听 `pull_request`
的四个 workflow（`ci.yml` / `codeql.yml` / `pr-conflict-domains.yml` / `private-python-targets.yml`）
都带 `on.pull_request.branches: [main]`（U05 那份在 `paths` 之外再加，两者是 AND）。叠栈 PR（base 是上一层分支，`docs/ci/parallel-prs.md`
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
`tests/test_merge_queue_workflows.py::TestPullRequestBaseFilter`（目录里**每一个**监听
`pull_request` 的 workflow——算出来的集合，`.yaml` 也算，三个常驻的必须在里面——`branches` ==
`[main]`；`edited` 不在任何一个的 types 里）。刻意**不是**一张要人登记的名单：名单是共享序列
的读改写，两条并行的叠栈链各带一个新证据 workflow 时谁后合谁的合并组就红。新加一个 PR 级
workflow 只要带同一行过滤（叠栈分支上的 `foundation-u06-rendercore.yml` /
`private-python-targets.yml` 已经带了）。

**合并组快速失败（2026-11-03）。** 事实：2026-10-13 ~ 11-02 的 525 个合入 PR 里 27% 被队列踢过，
81% 的踢出是合并组检查红；抽样 46 条失败 run，第一个真实红 job 中位 38.6 分钟出现，而
`CI integration gate` 要等五条重腿全结束，中位 69 分钟才红——每个红组白占 runner 约 30 分钟
（免费计划总共 20 个并发槽）。做法：`backend-platforms` / `package` / `windows-exe-smoke` /
`macos-app-smoke` / `posix-e2e` 各自的**最后一步**（`if: failure() && github.event_name == 'merge_group'`）
执行 `gh run cancel $GITHUB_RUN_ID`，其余 job 被取消，Gate 照跑。**为什么不会让 required check 缺席**：
两个 Gate 都是 `if: always()`，官方 workflow-cancellation 文档写明取消开始时服务端重新求值条件，
`always()` 为真的 job 继续运行；它们的上游此时是 failure + cancelled，`aggregate_gate.py`
一律判红（cancelled 从来不算通过），verdict 另给 `root_failures` / `cancelled_by_fail_fast`
让人先看真红的那条（`fail_fast_attribution()`，纯函数；workflow 里没有任何判定）。
**与「merge_group 不许被取消」不矛盾**：那条防的是**外部**取消（Gate 没机会给结论、候选白等
90 分钟）；这里是 run 自己判红后的收尾，Gate 一定产出红结论。边界与取舍：
* 只在 `merge_group`：PR / push / full-ci 的 run 不变（步骤的 `if` 钉死事件）；required check 名与两个
  Gate 的 `needs` / `--required` 闭集不变，ruleset 不用动（也就无迁移顺序可言）；
* 取消放在各腿最后一步，诊断产物上传（`if: failure()`）先做完；取消请求失败（`|| true`）不改变本 job 已红；
* 权限：只有这五个 job 在 job 级加 `actions: write`（顶层仍 `contents: read`）；`actions: write` 也会出现在
  同仓库 PR 的 full-ci run 里（permissions 不能按事件条件写），fork PR 的 token 本就只读；
* 代价：同组里另外几条腿若也红，它们被取消、看不到——先看 Gate summary 的 `root_failures`，要全貌就重跑。
  取消后其余腿显示 cancelled 是**预期**（L-114 的「裸 cancelled 先核时长」只对没有任何 failure 的 cancelled 适用）；
* **红腿自己也显示 cancelled**：`gh run cancel` 取消的是整个 run，红腿的 job 结论也被改成 cancelled，
  needs 里看不出谁真红（2026-11-03 本 PR 进队的第一个红组实测：`backend-platforms (macos-latest, 6)` 的
  pytest 红，job 却是 cancelled；Gate 在取消后约 2 分钟以 failure 完成，机制本身成立）。所以 integration
  Gate 在 merge_group 上先读本 run 各 job 的**步骤**结论（步骤结论不受取消影响；Gate 为此有 `actions: read`、
  没有 write），经 `RUN_JOBS_FILE` 环境变量交给判定器，`root_failures` 写「job 显示名 · 失败步骤」。走环境变量
  而不是新参数：判定器取自默认分支，新参数在合入前会让旧判定器 argparse 报错。取不到就留空，不改变结论；
* 生效判据（合入后核）：红组里 Gate 在红腿之后几分钟内出现 failure、summary 写出真红的「job · 步骤」、其余腿
  cancelled；若 Gate 没出结论（候选超时），回滚 = revert 本提交，Gate 与闭集本就没动。
看护：`TestMergeGroupFailFast`（最后一步、只在 merge_group、不含判定、只有五腿拿 `actions: write`、
check 名与闭集不变、`cancel-in-progress` 仍只对 PR、Gate 只读 actions、归因步骤只在 merge_group 且取不到不打红、
归因经环境变量）、`TestMergeGroupFailFastAttribution`、`TestFailFastRootFromRunJobs`。
