# CI 分层：PR / merge_group / full-ci / push main / nightly 各自的时机

> 原文出自 `.github/AGENTS.md`「CI 分层（1.0 稳定化，2026-08-21 起）」（2026-09-18 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`.github/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_merge_queue_workflows.py`（含 `TestMergeGroupFailFast`）、`tests/test_aggregate_gate.py`（含 `TestMergeGroupFailFastAttribution`、`TestE2eLegsOnPullRequest`）、`tests/test_docs_scope.py`（含 `TestE2eSpecsChanged`）
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
`UNRELEASED.md` 与 `unreleased/` 片段目录：`test_claude_plugin.py` / `test_dsh_bundle.py` / `test_install_commands_from_brand.py` 读它们；只此一条，不扩到整个
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

**改到 e2e 用例的 PR 在 PR 上也跑 e2e 腿（`e2e_specs_changed`，2026-11-04）。** 重型腿平时在 PR 上整体
skipped、合并组里才第一次跑，这对「改的正是这些腿上的用例」的 PR 等于没验：#1736（`risk:low`，只把
`keyboard-golden-path.spec.ts` 的一步从 click 改成键盘 Tab）PR 上 `posix-e2e` 与 `windows-exe-smoke`
都 skipped，进合并组才第一次跑，Windows webkit 上红了被踢（merge group run 41817554523）；#1717 改
`golden-paths.spec.ts` 同形，运气好才没出事。规则：PR 改动里**任一路径**（改名取新旧两条，删除也算）属于 e2e 腿
的输入——`web/e2e/` 下任何文件（含 `fixtures.ts` 等共用支撑）、`web/playwright.config.ts`、`web/tsconfig.e2e.json`
（`web/playwright.perf.config.ts` 不算：它只驱动 nightly 的 perf 用例，`testIgnore` 已把 perf 用例挡在这两条腿之外）——
就在 PR 上跑 `windows-exe-smoke` 与 `posix-e2e`（与 full-ci 时同一套分片），**不管 risk 档、有没有 `full-ci`**；
`backend-platforms` / `package` / `macos-app-smoke` 仍按原样只在 merge_group / full-ci 上跑（macOS 腿不跑 e2e）。
实现：`docs_scope.py` 另输出 `e2e_specs_changed`（与 `docs_only` 同一次分类、同样取默认分支上的脚本，路径判据宁宽勿窄：
漏判才是 #1736 的老路，多判只是多跑一遍），两条腿的 `if:` 在原条件上多一个
`|| needs.docs-scope.outputs.e2e_specs_changed == 'true'`。**Gate 同步认**：integration gate 在普通 PR（`--allow-deferred`）
且该输出为 `true` 时多传 `--e2e-classifier docs-scope --e2e-legs windows-exe-smoke,posix-e2e`，`aggregate_gate.py` 核对分类
job success + 输出字面 `true` 后，要求两条腿**真实 success**（红 / 取消 / 本该跑却 skipped 都判红），其余三条重型须全
skipped（status `success`、reason `e2e_legs_only`，仍推迟到 merge_group）或全 success，半跑半跳照旧判失败；证据不成立时参数
不传，e2e 腿若仍跑了就是「半跑半跳」判红，不留 `if:` 与 Gate 互相以为的缝。**merge_group / push main / full-ci 不变**
（merge_group 本来五条全跑、全必需；full-ci 走 `--require-heavy`，e2e 输出不参与）。**bootstrap**：默认分支上的 `docs_scope.py`
还没有这个输出时它恒为空、两条腿不加跑，Gate 参数与旧版逐字相同；判定器是旧版时遇到 e2e 输出为 true 的 PR 会按「半跑半跳」
红（fail-closed，不是假绿）。看护：`tests/test_docs_scope.py::TestE2eSpecsChanged`、
`tests/test_aggregate_gate.py::TestE2eLegsOnPullRequest`、`tests/test_merge_queue_workflows.py::TestPullRequestEventTypes` 的
`test_e2e_spec_changes_run_the_two_e2e_legs_on_a_plain_pull_request` / `test_gate_requires_the_e2e_legs_when_specs_changed`。
新增 e2e 腿的输入目录（比如把 fixtures 挪到 `web/` 别处）要同步改 `docs_scope.py` 的 `E2E_PREFIXES` / `E2E_FILES`。

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

**同 SHA 不互相取消（2026-11-04）。** 规则：`ci.yml` 的 concurrency 组带 `pull_request.head.sha`、
`cancel-in-progress` 恒为 `false`；同 PR 旧 SHA 的 run 由 job `cancel-superseded` 取消。
事实：旧写法按 PR 号分组、PR 事件取消在跑的 run，同一 SHA 上出现第二个事件（推提交后补打 `full-ci`；
`gh pr create --label a --label b` 的 opened + labeled；连打两个标签）就把已起 job 的第一个 run 整套取消，
被取消 run 里 `if: always()` 的 CI fast gate / CI integration gate 会以 FAILURE / CANCELLED 留在该 SHA
上，check 列表里出现误导性的红，容易被当成真失败，进而触发误判、无谓的空提交与重跑。GitHub 最终按哪一次判定
并未坐实（#1745 在 72b52b2c9 上第二个 run 的 gate 后完成、成功，PR 变 CLEAN；#1714 / #1748 当时卡住是在成功
run 跑完前就动了手，不作证据）。实证：#1664 #1714 #1745 #1748 #1751 都出现过这种红；#1745 在 72b52b2c9 上
13:44 的 run 41840570035（29 job cancelled、gate FAILURE）被 13:43:15 labeled 的 run 41840677929 取消。
Gate 侧修法（#1666，已关）
无效：被取消 run 里的 Gate 被记为 CANCELLED 而非 skipped。为什么现在的写法成立：同 SHA 的事件进同一个组，
在跑的跑完，后来的至多一个 pending，更新的 pending 顶掉旧 pending；被顶掉的 run 从没起过 job，不产生
check run，SHA 上不留红。不同 SHA 不同组，新 push 立刻起跑。代价与补偿：
旧 SHA 的 run 不再被 concurrency 取消，由 `cancel-superseded`（只 pull_request、同仓库 PR、
`continue-on-error`、不进任何 Gate 的 needs）取消同 PR 里 `head_sha` 不同且 run id 小于自己的未完成 run（连推两次时旧 run 的这个 job 可能晚起跑，
不限「更早」会反过来取消新提交）；它失败只是旧 run 多跑一会儿。
探针实证（草稿 PR #1753，已关）：开 PR 带两个标签再补打 `full-ci`，同一 SHA 3ccb1fe 上共 4 个 CI run：
2 个被顶掉的 pending（41845526864、41845527390）cancelled、0 个 job、没有 check run；run 41845526908 全绿
（integration gate 14:24:21、fast gate 14:56:41 success，CodeQL gate success）；SHA 上没有任何失败的 gate。
之后推新提交 ac808f167：新 run 里的 `cancel-superseded` success，旧 SHA 上已起 job 的 run 41845535389 被取消
（旧 SHA 的 gate 15:02 failure，只落在被取代的旧 SHA 上，不影响新 head）。merge_group / push 行为不变（本来就不取消）；`codeql.yml` 未改（默认三种 PR 事件类型，同 SHA
不会有第二个事件）。看护：`tests/test_merge_queue_workflows.py::TestPullRequestEventTypes::
test_same_sha_events_queue_and_never_cancel_each_other`、`TestConcurrency`。

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

**main 红时修复插到队首（2026-11-03）。** 事实：10-13 ~ 11-02 Windows 腿红的 194 个合并组 run 里约 67%
是**连坐**——一个坏提交（两个各自绿的 PR 的组合、或没带 full-ci 的 Windows-only 缺陷）进了 main 或排在队列前面，
之后每个组都带着它红；只对应 84 个独立事件。10-31 两个 PR 撞了同一个 ADR 号 0154，合入后 main 红，修复 PR 排在
队尾，7.5 小时里连踢 12 次。队列同时构建 4 组（`maximumEntriesToBuild`），main 不修好，排在前面的每一组都白跑。
规则：
* **「main 红」的判据**：main 的 push run 红在同一条用例，或**同一条用例**在 ≥ 2 个不同 PR 的合并组上红（只一组红是
  那个 PR 自己的事，正常踢出即可，不插队）。
* 满足时，**只修这条红**的那个 PR 门槛满足后插到队首：GraphQL `enqueuePullRequest(input: {pullRequestId, jump: true,
  expectedHeadOid})`（管理员；`expectedHeadOid` 防止入的不是审过的 head）；GitHub 界面是 PR 页合并框的「Jump to
  front」。插队不降门槛：Codex / CI / 线程照档位全满足才插。
* 不为别的理由插队（赶发版、「这个 PR 很小」）：插队会让排在前面的组全部重建。

**碰 Windows 路径 / 进程 / 安装的 PR，开 PR 时就打 `full-ci` 并看 Windows 腿（错题本 L-096 升级，
2026-11-03）。** PR 级 CI 不跑 Windows 后端，只在 Windows 显形的缺陷（fsync 只读句柄、cp1252 编码、
非法文件名、文件占用）到合并组才第一次红；入队前确认 Windows 后端腿真跑过且绿，只在 Windows 生效的
改动另在 Lab 整文件跑相关测试。为什么：六个不同日子各被合并组踢一次，每次白烧约一小时
（#214、#296、#314、#317、#319、#360、#725、#1587、#1604）。
