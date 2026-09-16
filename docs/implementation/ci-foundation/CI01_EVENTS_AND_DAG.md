# CI01 · 事件表、DAG 与取消 / 并发合同（2026-09-16）

- 改动对象：`.github/workflows/ci.yml`，commit `7d77494c`（叠在 CI00 `37fb89a1` / 源码基线 `8b95256c` 之上）。
- **改了什么**：`package` / `windows-exe-smoke` / `macos-app-smoke` / `posix-e2e` 四个 job 的 `needs` 由
  `[backend-fast, frontend]` 改为 `[frontend]`；抬头与 `package` 段的注释写清理由。
- **没改什么**：`scripts/ci/aggregate_gate.py` 一行不动；两个 Gate 的 `needs` / `--required` 闭集不动；
  `ci-integration-gate` 的 needs 本来就不含 backend-fast；任何 `timeout-minutes`、顶层 `concurrency`、任何 `if:`、
  CodeQL / CLA、`plugin-candidate ← frontend` 的 artifact 边、ruleset、runner、凭据都没动。
  机器比对见 [`evidence/ci01/dag_diff.json`](evidence/ci01/dag_diff.json)：除 4 条边删除、4 条边改标之外，17 个 job
  的 name / if / runs_on / timeout / matrix / 步骤数逐字段相同。
- **本轮没有任何真实 run**（不能 push）。下面凡是「调度会怎样」的句子都是读 workflow 与 GitHub 文档得出的合同，
  由静态合同测试看住；实机验证归 CIP-008…010，状态 `not_run`。
- 回退 = 把四行 `needs` 改回 `[backend-fast, frontend]`，并改掉
  `tests/test_merge_queue_workflows.py::TestHeavyLaneDependencies` 的前两条用例；不删 job、不换 required context。

## 1. DAG：改前 / 改后

画法同 `02_EVENTS_DAG_GATES.md` §2。`══▶` = artifact/data 边（下游真下载字节）；`──▶` = verdict-only（只等结论）；
`··▶` = 短预筛（只等结论，但便宜且不在关键路径上，留着省白跑）。时长是 CI00 的 29 个 merge_group 中位（`CI_BASELINE.md` §4.2）。

### 改前（`8b95256c`，23 条边）

```
python-lint 12s ─────────────────────────────────────────────┐
cla-check 4s ────────────────────────────────────────────────┤
invariants 501s ─────────────────────────────────────────────┤
workerd 23s ─────────────────────────────────────────────────┤
desktop-shell ×2 238s ───────────────────────────────────────┤
compat-smoke 132s ───────────────────────────────────────────┼──▶ CI fast gate（--required 9 个）
frontend 252s ═══════▶ plugin-candidate 46s ─────────────────┘
   │ ╲
   │  ╲──────────────┐（verdict-only，与 backend-fast 串在同一条 needs 里，预筛从未真正起作用）
   │                 ▼
backend-fast ×3 1865s ──▶ package ×4 ~110s ─────────────────┐
                     ├──▶ windows-exe-smoke 1411s ──────────┤
                     ├──▶ macos-app-smoke 325s ─────────────┼──▶ CI integration gate（--required 5 个）
                     └──▶ posix-e2e 541s ───────────────────┤
backend-platforms ×2 2443s ─────────────────────────────────┘

关键路径（28/29）：backend-fast 1865 → windows-exe-smoke 1411 → gate；资格中位 3412s（56.9 分钟）
```

### 改后（`7d77494c`，19 条边）

```
python-lint ─────────────────────────────────────────────────┐
cla-check ───────────────────────────────────────────────────┤
invariants ──────────────────────────────────────────────────┤
workerd ─────────────────────────────────────────────────────┤
desktop-shell ×2 ────────────────────────────────────────────┤
compat-smoke ────────────────────────────────────────────────┤
backend-fast ×3 1865s ───────────────────────────────────────┼──▶ CI fast gate（--required 9 个，含 backend-fast：不变）
frontend 252s ═══════▶ plugin-candidate 46s ─────────────────┘
   ┊
   ┊··▶ package ×4 ~110s ────────────────────────────────────┐
   ┊··▶ windows-exe-smoke 1411s ─────────────────────────────┤
   ┊··▶ macos-app-smoke 325s ────────────────────────────────┼──▶ CI integration gate（--required 5 个：不变）
   ┊··▶ posix-e2e 541s ──────────────────────────────────────┤
backend-platforms ×2 2443s ─────────────────────────────────┘

关键路径（模型）：backend-platforms (windows) 2443 → gate；frontend 252 + windows-exe-smoke 1411 = 1663 不在关键路径上。
模型资格中位 3412s → 2476s（Δ −892s，`CI_BASELINE.md` §5.1）。合并资格 = ruleset 三个 context 全绿
∧ fast gate 闭集（含 backend-fast）∧ integration gate 闭集——与改前逐项相同。
```

保留 `frontend ··▶ 重型` 的决定（02 §2「是否保留短质量预筛是明确的成本 / 延迟决策」）：留着对资格时长零成本，
前端坏掉（`pnpm install` / lint / i18n / vitest / build / 插件候选组装；CI00 样本里 `34966709766` 就是
`pnpm install --frozen-lockfile` 15 秒红）时省下每个候选约 45 runner 分钟（Windows 25 + macOS 5 + posix 9 + package ×4）。
代价：backend-fast 红时四个重型 job 白跑一轮（约 66 Windows 分钟 + 35 macOS 分钟）——这是把 35 分钟的等待换成并行的必然代价。

## 2. 事件 × 层级表（当前实际行为）

02 §1 的六行落到今天的 ci.yml / codeql.yml。**本轮没做 T1 / T2 草稿分层**（02 §3 允许「本轮保留所有 PR 相同的小代表集」）：
草稿与非草稿 PR 跑同一套快线，重型档只在 merge_group / full-ci 上跑。

| 事件（触发字段） | ci.yml 跑什么 | `CI fast gate` | `CI integration gate` | codeql.yml | 02 §1 对应层 | 看住它的测试 |
|---|---|---|---|---|---|---|
| `pull_request: opened / synchronize / reopened`（含草稿） | 快线 9 个：python-lint / cla-check / invariants / backend-fast ×3 / frontend → plugin-candidate / workerd / desktop-shell ×2 / compat-smoke；重型 5 个整体 skipped | `--mode fast`，9 个全 success 才绿 | `--allow-deferred`：5 个全 skipped → deferred（绿，summary/JSON 写明推迟到 merge_group）；任一跑过就按真实结果判 | `pull_request`（默认 types）→ analyze ×4 → CodeQL gate | T1（今天 = 全部快线，≈35 分钟，无草稿区分） | `TestGates::test_fast_jobs_cover_pr_and_merge_group_but_not_push`、`test_every_fast_lane_job_actually_runs_on_a_plain_pull_request`、`test_heavy_jobs_do_not_run_on_plain_prs_or_push`、`test_integration_gate_defers_only_on_plain_pull_requests`；`test_aggregate_gate.py::TestIntegrationGate::test_plain_pr_all_skipped_is_deferred` / `test_partial_skip_is_failure_not_deferred`；`test_cla_workflow_contract.py::TestClaWorkflowContract::test_runs_on_pull_request` |
| `pull_request: ready_for_review` | 与上一行相同（新 run，同一 head SHA） | 同上 | 同上（仍 deferred——没有标签） | **不触发**（codeql 未列 types）；该 SHA 上已有的 CodeQL 结论继续有效 | T2 的接入点（本轮未接） | `types:` 里含 `ready_for_review`：**无测试**（`test_full_ci_label_still_triggers_the_heavy_layer` 只钉 `labeled`） |
| `pull_request: labeled`（加 `full-ci`） | 快线 9 个 + 重型 5 个，在 PR 自己的 head SHA 上 | 同上 | `--require-heavy --full-ci`：5 个全 success 才绿，skipped 即失败，deferred 是配置错误 | 不触发 | T3a 提前到 PR | `test_full_ci_label_still_triggers_the_heavy_layer`、`test_heavy_jobs_run_on_merge_group`（同一折叠条件）、`test_integration_gate_defers_only_on_plain_pull_requests`；`test_aggregate_gate.py::TestIntegrationGate::test_full_ci_pr_may_not_defer` |
| `pull_request: labeled / unlabeled`（**任意**别的标签） | 快线 9 个重跑一遍（同 SHA） | 同上 | deferred（若 `full-ci` 仍在则同上一行） | 不触发 | — | **无测试**；见 §4 现存问题 ① |
| `merge_group: checks_requested` | 快线 9 个 + 重型 5 个，在队列的组合提交上 | `--mode fast`，同上 | `--require-heavy`：deferred 是配置错误（`aggregate_gate` 直接拒绝） | `merge_group: checks_requested` → analyze ×4（SARIF 不上传）→ CodeQL gate | T3a 完整合并资格（唯一常规执行点） | `TestMergeGroupTrigger::test_ci_listens_to_merge_group_checks_requested` / `test_codeql_listens_to_merge_group_checks_requested`、`TestGates::test_heavy_jobs_run_on_merge_group`、`test_codeql_skips_the_sarif_upload_only_on_merge_group`；`test_aggregate_gate.py::TestIntegrationGate::test_merge_group_may_not_defer` / `test_require_heavy_rejects_skipped`；`test_cla_workflow_contract.py::…::test_runs_on_merge_group_too`；`TestHeavyLaneDependencies::test_a_red_backend_fast_still_blocks_the_merge_even_when_every_heavy_job_is_green` |
| `push: main` | 只有 `main-landing-audit`（结构契约 pytest + 生成物不进索引 + 落地信息）；快线与重型都不跑，两个 Gate 也不跑 | 不跑 | 不跑 | `push: main` → analyze ×4（上传 SARIF）→ CodeQL gate | 轻量落地审计 | `TestLandingAudit::test_main_push_runs_only_the_landing_audit` / `test_landing_audit_structural_tests_exist`、`test_fast_jobs_cover_pr_and_merge_group_but_not_push`、`test_heavy_jobs_do_not_run_on_plain_prs_or_push` |
| `schedule` | ci.yml **不监听**；codeql.yml 每周一 04:23 UTC；nightly.yml 每日 18:00 UTC；lab-ci.yml 每日 19:00 + 每周日 20:00；telemetry-metrics / metrics-freshness 各自 | — | — | 周扫描 → CodeQL gate（不是 required 场景） | T3b 深度观察 | `TestMergeGroupTrigger::test_non_required_workflows_do_not_join_the_queue`（nightly / lab / release 不进队列）；codeql 的 schedule 本身：**无测试** |
| `release`（`push: tags v*` / `workflow_dispatch`） | ci.yml 不监听；release.yml：trust → build / desktop（reusable `desktop-tauri.yml`）→ lab_release_gate（reusable `_lab-qualification.yml`）→ validate_artifacts → github_release / pypi / plugin_stable（publish 门） | — | — | — | 精确发行资格 | `tests/test_release_workflow_contract.py`（`test_the_tag_has_exactly_one_entry_point`、`test_every_publishing_job_is_gated_on_publish`、`test_release_only_uses_the_sha_that_trust_resolved`、`test_the_release_gate_cannot_be_evicted_by_a_routine_lab_run` 等） |

Gate 的共同合同（与事件无关）：`if: always()`、判定器取默认分支副本并以 `python3 -I` 执行、`needs` 与 `--required` 同一闭集、
needs 指向的 job 都存在——`TestGates::test_gates_run_on_always` / `test_gates_run_the_trusted_copy_of_the_verdict` /
`test_fast_gate_needs_matches_required_closed_set` / `test_integration_gate_needs_matches_required_closed_set` /
`test_every_gate_needs_is_a_real_job`；ruleset 三个 context 的名字与 workflow 逐字相同——
`test_merge_queue_ruleset.py::…::test_gate_names_match_the_workflow_files` / `test_contexts_become_exactly_the_three_gates`。
merge_group payload 里没有 `pull_request.draft / labels`，任何读 PR 字段的表达式都先按事件分支——
`TestEventFieldAccess::test_pull_request_fields_are_guarded_by_event_checks` / `test_no_bare_head_ref_or_label_event_usage`。

## 3. 取消与并发合同：真值表

ci.yml 与 codeql.yml 的顶层：

```yaml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.event.merge_group.head_sha || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

GitHub 的组语义（本轮不改、只依赖）：同一组**最多一个运行中 + 一个待定**；`cancel-in-progress: true` 时新来的取消运行中的；
`false` 时新来的排到待定位，**第三个进来会取代待定的那个**（`_lab-qualification.yml` L59-75 的注释与
`tests/test_release_workflow_contract.py::test_the_release_gate_cannot_be_evicted_by_a_routine_lab_run` 都是围绕这一条写的）。

| 场景 | 组名（实例） | cancel | 结果 | 是不是想要的 | 看住它的测试 |
|---|---|---|---|---|---|
| PR A 新 push（`synchronize`） | `ci-CI-pull_request-<A>` | true | A 上旧 SHA 的运行中 run 被取消；旧 SHA 全绿也没有 merge value | 是 | `TestConcurrency::test_cancel_in_progress_only_for_pull_request`、`test_group_distinguishes_events` |
| PR A 新 push 时 PR B 在跑 | B 在 `ci-CI-pull_request-<B>` | — | B 不受影响：组名带 PR 号 | 是 | `test_group_distinguishes_events`（组名含 `pull_request.number` 那一段由 `\|\|` 链保证） |
| PR A 加 / 减任意标签（`labeled` / `unlabeled`） | `ci-CI-pull_request-<A>` | true | 同 SHA 新 run 取消同 PR 的运行中 run（若还在跑） | 对 `full-ci` 是；对无关标签见 §4 ① | **无测试**（组名对所有 `pull_request` 子类型同形） |
| merge_group 候选 X 与 Y 同时构建（`max_entries_to_build: 2`） | `ci-CI-merge_group-<head_sha_X>` / `…-<head_sha_Y>` | false | 各自一组，互不排队、互不取消 | 是 | `test_group_distinguishes_events`（`merge_group.head_sha` 必须在组名里）、`test_cancel_in_progress_only_for_pull_request` |
| 同一候选被队列重新 `checks_requested`（同 head_sha） | 同一组 | false | 第二个排待定；若出现第三个，取代第二个 | 可接受（同一 SHA 的重复验证互相取代不丢资格） | 无测试；实机才知道队列会不会这样做 |
| push main 连着两次（队列连续合两个 PR） | `ci-CI-push-refs/heads/main` | false | 第二次排待定，两次都会跑 | 是 | `test_cancel_in_progress_only_for_pull_request`（push 不取消） |
| push main 连着**三次**（第一个还在跑时第二、三个到达） | 同上 | false | **第二次的待定被第三次取代**：中间那个 commit 没有 `main-landing-audit`；codeql.yml 同形，中间那个 commit 没有 SARIF 上传 | **不是**，但见 §4 ②：它不是合并资格 | **无测试**（GitHub 侧行为，静态钉不住） |
| PR run 与 merge_group / push main 之间 | 组名含 `event_name`，三种 run 绝不同组 | — | 谁也取消不了谁 | 是 | `test_group_distinguishes_events`（`github.event_name` 在组名里） |
| ci.yml 与 codeql.yml 之间 | 组名含 `github.workflow`（`CI` / `CodeQL`） | — | 各自一套槽 | 是 | `test_ci_and_codeql_use_distinct_namespaces` |
| release.yml（tag push / dispatch） | 顶层**无** concurrency；`lab_release_gate` 经 reusable 的 job 级 `lab-qualification-Release` | false | 两次发布同时来：第二次的 lab 门禁排待定；第三次取代第二次。build / desktop 等其它 job 并行不互斥 | 是（手动、低频） | `test_release_workflow_contract.py::test_the_release_gate_cannot_be_evicted_by_a_routine_lab_run`（槽名含 `github.workflow`，日常 lab 与发布链不共槽） |
| lab-ci.yml（push main / schedule / dispatch） | 顶层刻意无；`qualify` 经 reusable 的 `lab-qualification-Lab CI` | false | 同一条链上第三个取代待定的（注释明写「本来就该取代」） | 是 | 同上；`test_release_workflow_contract.py::test_qualification_is_defined_exactly_once` |
| reusable `_lab-qualification.yml` 被两条链调用 | `lab-qualification-${{ github.workflow }}` 取**调用方**名字 | false | 两条链各排各的队；机器独占靠只有一台 `tavotto-lab` runner + flock | 是 | 同上 |
| plugin-stable.yml（dispatch） | `plugin-stable-publish` | false | 连发三次，第二次的待定被第三次取代 | 可接受（手动、有 `expected_remote_sha` 复核） | 无测试 |
| pr-conflict-domains.yml | `conflict-domains-<PR>` | true | 同 PR 新 push 取消旧的 | 是 | 无测试 |
| nightly.yml / desktop-tauri.yml | **无** concurrency | — | dispatch 与 schedule 撞上会并行跑两份 | 可接受（只读、不发布） | 无测试 |

**同组 pending 覆盖风险，逐事件结论**：`pull_request` → 想要的；`merge_group` → 每个候选 key 不同，无风险；
`push main` → 三连推时中间一次的落地审计 / SARIF 会被挤掉（§4 ②，本轮只记录不改）；`schedule` → 每天 / 每周一次，无重叠；
`release` / `plugin-stable` / lab → 手动低频，文档已写明取代规则；`workflow_call` → key 取调用方，不互相误取消。
02 §4 提到的 `queue: max` 多 pending 策略本轮**不引入**（先确认工具链支持再说，[W01]）。

## 4. 现存问题（只记录，不改；2026-09-16 用户逐条拍板：修 ①③⑥、接受 ②④⑤⑦）

1. **任意 `labeled` / `unlabeled` 都重跑整条快线并取消同 PR 运行中的 run**。`types:` 里的 `labeled, unlabeled` 是为 `full-ci`
   加的，但表达式不区分标签名——加一个 `docs` 标签也会让 35 分钟的 backend-fast 从头来过。另外**去掉 `full-ci` 标签会在同一
   SHA 上产出一个新的、deferred（绿）的 `CI integration gate`**，覆盖此前那个真实失败的结论；候选进队列后 merge_group 仍会真跑一遍，
   所以不是合并资格的洞，但「策略变化到同一 SHA 要正确失效」（02 §4）在 PR 层面并不成立。02 §4 要求先清点所有 label consumer
   再减少无关重跑——本轮不动。
   **拍板：修（后续 PR）**——`labeled` / `unlabeled` 只在标签名是 `full-ci` 时进快线（其余标签事件按 02 §4 先清点 consumer 再过滤），并让去掉 `full-ci` 不产出同 SHA 的 deferred Gate。
2. **push main 三连推时中间一次的 `main-landing-audit` 与 codeql push run 会被待定替换**。它们不是 required context、不是合并资格
   （树在 merge_group 上验过），丢的是那个 commit 的落地记录与 SARIF 账本；下一个 commit 的 run 覆盖了同一棵树的后继。
   若要保留每一次：把 push 的组名换成 `github.sha`（每个 commit 一组，互不排队），代价是并发 runner。本轮不改，留给 CI05 一并评估。
   **拍板：已接受**——不是合并资格，丢的只是中间 commit 的落地记录 / SARIF 账本，下一个 commit 覆盖同一棵树的后继；账户并发只有 20（CI05 §5），不值得为它多占 runner。
3. **`ready_for_review` 在 `types:` 里没有测试看住**；今天草稿与非草稿跑同一套，删了它只会少一个多余的 run。哪天做 T1/T2 分层
   （02 §3），它就成了「作者点 Ready 之后重活永远不跑」的那个洞——分层之前必须先给它加判据。
   **拍板：修（后续 PR）**——给 `types:` 里的 `ready_for_review` 加合同用例（与 ① 同一个 PR：两者都是 `types:` 的判据）。
4. **codeql.yml 的 `pull_request` 没写 `types`**：`ready_for_review` / `labeled` 不产生新的 CodeQL run。对同一 head SHA 无影响
   （check run 按 SHA 存在），记录以免将来有人以为 CodeQL 也会「按标签重跑」。
   **拍板：已接受**——CodeQL 结论按 SHA 存在，标签 / Ready 不改变代码，重跑只是浪费。
5. **`ci_baseline.py analyze` 不校验 `--workflow` 是不是那些 run 真正执行时的那份**：用改后的 ci.yml 分解 CI00 的 86 个旧 run，
   windows-exe-smoke 的 `dependency_wait` 从 2120s 变成 ~256s、差额进 `dispatch_gap`、关键路径被记成 frontend → windows-exe-smoke
   （[`evidence/ci01/analyze_after_check.json`](evidence/ci01/analyze_after_check.json)）。`tests/test_ci_baseline.py` 的四条计时
   用例原先读 HEAD 的 ci.yml，本轮改读 `tests/fixtures/ci_baseline/ci_8b95256c.yml` 快照（并加 `test_the_snapshot_is_the_workflow_the_fixture_runs_executed_under`
   钉住前提）。CI05 做前后对照时按 run 记 workflow 的 SHA。
   **拍板：已接受**——CI05 已按 run 用它执行时那份 ci.yml 快照分解（`evidence/ci05/workflows/`），流程上守住；`analyze` 不加校验（它没有可靠的信号知道 run 用的是哪份 yml）。
6. **ci.yml 抬头 L8「backend-fast（Linux 3.10+3.13）」已陈旧**（矩阵是 3.10 / 3.13 / 3.14）；CI00 §12 还列了
   backend-fast「实测 20–25 分钟」（实测中位 29–35、上限 40 余量 4 分钟）等几处。与本刀无关，不动；40 分钟上限的余量归 CI03 分片解决。
   **拍板：修（后续 PR）**——抬头与各 job 段的时长注释按 CI05 的实测改（分片后 backend-fast 每片 ~1000–1170s，40 分钟上限余量充足）。
7. **草稿 PR 与非草稿跑同一套 35 分钟快线**——不是缺陷，是本轮明确不做 T1/T2 的决定（02 §3 的「先取得 DAG / 分片收益，Ready 分层可后置」）。
   **拍板：已接受**——分片后快线已到 19 分钟，T1/T2 分层的收益变小；③ 修好之后再议。

## 5. 本轮的合同测试与变异反证

`tests/test_merge_queue_workflows.py::TestHeavyLaneDependencies`（四条）+ `tests/test_ci_baseline.py` 的快照前提（一条）。
每条写完立刻变异一次：先断言目标串存在 → 断言变异落在预期 job 块 → pytest 退出码判红 → 按备份还原并核 md5。
13 条变异全部按预期（哪条该红哪条该绿）落地；清单与退出码在交付报告里，脚本在会话 scratchpad（不进仓库）。

现有测试在改动后仍绿（它们是「CIP-006 / CIP-007 保持」的现成证据）：`test_integration_gate_needs_matches_required_closed_set`、
`test_fast_gate_needs_matches_required_closed_set`、`test_heavy_jobs_run_on_merge_group`、`test_heavy_jobs_do_not_run_on_plain_prs_or_push`、
`test_gates_run_the_trusted_copy_of_the_verdict`、`test_codeql_gate_depends_on_analyze`、`test_cla_workflow_contract.py` 全部。

## 6. 验证命令与退出码（2026-09-16，worktree，commit `7d77494c` 之后）

| 命令 | 退出码 |
|---|---:|
| `/opt/homebrew/bin/actionlint .github/workflows/ci.yml` | 0 |
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0 |
| `.venv/bin/python -m pytest tests/test_merge_queue_workflows.py tests/test_aggregate_gate.py tests/test_ci_tooling.py tests/test_ci_baseline.py tests/test_merge_queue_ruleset.py tests/test_cla_workflow_contract.py tests/test_ci_qualification.py tests/test_docs_references.py`（288 passed, 1 skipped：test_ci_tooling 的「非 Linux 无 /proc」） | 0 |
| `python scripts/ci/ci_baseline.py dag --workflow .github/workflows/ci.yml --edge-kinds …/evidence/ci01/dag_edge_kinds_after.json --out …/evidence/ci01/dag_after.json`（17 job / 19 边） | 0 |
| `python scripts/ci/ci_baseline.py analyze --compact --workflow .github/workflows/ci.yml --evidence …/evidence/actions --edge-kinds …/evidence/ci01/dag_edge_kinds_after.json --out <scratchpad>`（解析通过；输出不是证据，见 §4 ⑤） | 0 |
| 实机：一个正常候选 + 一个故意失败候选的 DAG 时间与 Gate（phases/CI01 第 7 条） | **not_run**（不能 push） |
