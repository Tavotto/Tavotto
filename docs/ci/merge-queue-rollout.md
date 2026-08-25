# Merge Queue 迁移手册（2026-08-25）

目标：多 session 并行开 PR 时，普通 PR 只跑快速反馈；完整跨平台资格验证
（package ×3 / windows-exe-smoke / macos-app-smoke / 平台 backend）只对
「最新 main + 队列前序 PR + 当前 PR」的**最终组合提交**（merge_group）执行；
main 不再重复完整打包。**验证覆盖面一条没减，改的是验证发生的时机。**

三个稳定 Gate 是 ruleset 收敛后的唯一 required contexts：

| Gate | 出处 | 语义 |
|---|---|---|
| `CI fast gate` | ci.yml | 快线闭集（invariants / backend 快线 / frontend / workerd / compat-smoke）全部 success |
| `CI integration gate` | ci.yml | 重型资格闭集全部 success；**仅**普通 PR 上允许整体 skipped → deferred（summary 与 JSON 明确标注） |
| `CodeQL gate` | codeql.yml | 四语言矩阵整体 success |

判定逻辑只有一处：`scripts/ci/aggregate_gate.py`（单测
`tests/test_aggregate_gate.py`）。cancelled / skipped / 缺失 / 认不出的
结论一律失败，绝不依赖「skipped 的必需检查算不算通过」这类随 GitHub
行为变化的缝隙。

## 顺序（不可交换）

```
1. 合并 PR 1（infra/merge-queue-foundation：merge_group 触发 + 三个 Gate +
   本手册 + 迁移脚本。不改任何 job 的运行时机）
2. 等 main 上那次 push 的 CI / CodeQL 跑完，确认三个 Gate 都真实产出 success
   （scripts/ci/merge_queue_ruleset.py 的 apply 会再机器核对一遍，但先人眼看到）
3. python scripts/ci/merge_queue_ruleset.py plan  --phase enable-queue
   python scripts/ci/merge_queue_ruleset.py apply --phase enable-queue --yes
   （加 merge_queue rule + 关 strict；旧 17 个 required contexts 一个不删）
4. 用一个小 PR 做 Merge Queue canary：正常绿了之后点「Merge when ready」，
   确认 merge_group run 出现、旧 contexts + 三个 Gate 全部在组合提交上产出
   结论、PR 被队列合入
5. python scripts/ci/merge_queue_ruleset.py plan  --phase switch-to-gates
   python scripts/ci/merge_queue_ruleset.py apply --phase switch-to-gates --yes
   （required contexts 收敛为且仅为三个 Gate）
6. 再合并 PR 2（infra/merge-queue-qualification：重型 job 收敛到
   merge_group / full-ci，main 改轻量落地审计）
```

## 为什么顺序是硬的

> **禁止先把旧 required checks 从 Ruleset 删除、再合入产生新 Gate 的
> workflow。** required context 只能登记 main 上**已经产出过结论**的名字；
> 反过来，所有 PR（包括那个本该救场的 PR）都会等一个永远不会出现的
> context——仓库整体锁死，而且没有 bypass actor 可用。这正是
> `required-checks-ordering` 那条教训的 Merge Queue 版。

同族的其他不可交换点：

* **strict 只能与强制 merge_queue 同时关**。只关 strict 而不强制队列，
  在旧 main 上绿过的 PR 仍可直接合并——组合从未被验证。
  `merge_queue_ruleset.py` 把两件事做成一个原子阶段（enable-queue）。
* **PR 2 只能在 switch-to-gates 之后合**。它把 package / smoke 收敛到
  merge_group；如果旧 contexts 还在 ruleset 里，普通 PR 上它们永远没结论，
  所有 PR 进不了队列。
* enable-queue 之前，workflow 必须已在 **main 上**监听 merge_group
  （即 PR 1 已合并）。否则每个队列候选都白等 90 分钟超时。apply 会核对。

## 脚本的安全设计

`scripts/ci/merge_queue_ruleset.py`（纯标准库，经 `gh api`，默认只读）：

* 按「名称 + target=branch + 条件含默认分支」定位 ruleset，不写死 ID；
  找不到或同名多个都拒绝。tag ruleset（`release tags: immutable`）
  target 不是 branch，结构上就选不中。
* 只改三样：merge_queue rule、strict 开关、required contexts。
  pull_request / deletion / non_fast_forward / bypass_actors / conditions /
  未来新增的任何 rule 原样带回（`_assert_untouched` 硬断言）。
* plan 记下当时 ruleset 全文的 SHA-256；apply 前重读线上比对，
  并发漂移即拒绝——绝不拿旧 JSON 盖掉别人刚做的修改。
* apply 不带 `--yes` 只演练；switch-to-gates 的 apply 自动核对：
  三个 Gate 已在 main 最新 commit 上 conclusion=success、
  两个 workflow 在 main 上监听 merge_group、merge_queue rule 已存在、
  strict 已关。任何一项不满足都拒绝写入。

## Merge Queue 参数

| 参数 | 值 |
|---|---|
| merge_method | SQUASH |
| grouping_strategy | ALLGREEN（即 UI 的 "Only merge non-failing entries"） |
| max_entries_to_build | 2 |
| max_entries_to_merge | 1 |
| min_entries_to_merge | 1 |
| min_entries_to_merge_wait_minutes | 0 |
| check_response_timeout_minutes | 90 |

REST API 的 merge_queue rule 参数名与上表逐字对应；GitHub 网页把
`grouping_strategy: ALLGREEN` 呈现为 "Only merge non-failing entries"，
两者是同一个开关的两种表达。

## 迁移后的最终 ruleset

* 必须走 PR + review thread resolution（原样）
* 禁止删除 / non-fast-forward（原样）
* 无 bypass actor（原样）
* **必须走 Merge Queue**（新增）
* strict "branch must be up to date" 关闭（队列对最终组合负责）
* required checks = `CI fast gate` + `CI integration gate` + `CodeQL gate`

## 回滚

任一阶段出问题，用同一个脚本反向操作是**不安全**的（会遇到同样的顺序
约束）。正确的回滚是：在网页 UI 把 required contexts 改回旧清单（那些
context 仍在每个 PR 上产出——PR 1/PR 2 没有删它们的产出，只有 PR 2 改了
package/smoke 的触发条件，所以 **PR 2 合并之后不能再整体回滚到旧 contexts**，
只能修 Gate 本身）。这也是 PR 2 必须最后合的另一个理由。
