# P2 生命周期：怎么让 P2 有下一步，而不是有一份档案

> 问题不是「P2 太多」，是 **P2 没有出口**。
> 2026-08-22 的审计（`docs/audit/2026-08-22-v1-release-process-audit.md` §7）
> 数出来：188 条 Codex 发现里 151 条是 P2，其中 **82 条至今未处置，
> 而 78 条挂在已经合并的 PR 上**——那些 thread 不会再有人回头看。
> 同一份审计还发现，11 条 open issue **一个标签都没有**，
> 于是「unresolved P1 = 0」这条退出条件根本无法查询。
>
> 这份文档定义 P2 从发现到关闭的完整路径，每一步都要落成**可查询的状态**。

---

## 1. 状态机

```
        发现（Codex thread / 用户 issue / 自测）
                     │
                     ▼
              ┌─────────────┐
              │  reproduce  │  ← 必经。复现不出来就是 disposition:false-positive
              └──────┬──────┘     （**要附实测证据**，不是「我觉得不会」）
                     ▼
              ┌─────────────┐
              │  triage     │  ← 按 review-severity-policy §2 判是否 release:blocker
              └──────┬──────┘
                     ▼
   ┌────────────┬────┴─────┬──────────────┬─────────────────┐
   ▼            ▼          ▼              ▼                 ▼
fix-now      guard    patch-train   minor-release   accepted-limitation
（本轮修）  （本轮加   （v1.0.1）     （v1.1）        （不修，但三处
             护栏 +                                   如实表达）
             长期 issue）
   │            │          │              │                 │
   └────────────┴────┬─────┴──────────────┴─────────────────┘
                     ▼
              ┌─────────────┐
              │   closed    │  ← 只有 §3 那四种理由能关
              └─────────────┘
```

**每一条 open 的 P2 issue 必须同时有**：
`severity:*` + `area:*` + `disposition:*` + milestone + acceptance test + next action。
少任何一项，它就是一份档案，不是一个待办。

---

## 2. Deferred P2 必须保持 open

`disposition:patch-train` 与 `disposition:minor-release` 的 issue
**保持 open 并挂 milestone**（`v1.0.1` / `v1.1`）。

不许「先关掉，到时候再开」——关掉的 issue 不出现在任何列表里，
而「到时候」没有触发器。

---

## 3. 允许关闭 P2 的四种理由（**没有第五种**）

| 理由 | 前提 | 关闭时必须留下 |
|---|---|---|
| **fixed** | 修好了 | acceptance test 的**文件名 + 用例名**，以及它见红过的证据 |
| **guarded** | 加了护栏 | 护栏的测试 + **长期修复 issue 的编号**（必须已经建好） |
| **accepted limitation** | 决定不修 | **UI、支持矩阵、文档三处都已如实表达**，各给出改动的 commit |
| **invalid / false positive** | 不成立 | **实测证据**（复现脚本 / 日志 / 像素数），不是推理 |

> **「已记录」不是关闭理由。**
> 「写进 `docs/1.0-release-readiness.md` §4 了」也不是——那份文档是 backlog，
> 不是 issue tracker。backlog 里的每一条都应当有一个 open issue 与之对应。

---

## 4. 三个「看起来像关闭」的反模式

1. **合并 PR 时顺手关掉一批 issue。**
   PR 合了不等于验收条件满足。本仓库现在就有这个待办：
   #30–#35、#37 对应的 PR（#27/#41/#42/#43/#44/#45）全部已合并，
   issue 却仍然 open——要么逐条核对验收条件后关，要么说清还差什么。
   **两种都行，「合了但不知道算不算完」不行。**
2. **把 P2 降级成 P3 再关。**
   降级要有理由，而理由必须是「重新测量发现它比原判轻」，
   不能是「我们不想修它」。
3. **在 thread 里回一句「已知问题」然后 resolve。**
   resolve 的前提是有 disposition，而 disposition 的四种形态写在
   `docs/engineering/codex-review-policy.md` §4，每一种都带一个可验证的产物。

---

## 5. 本轮的 disposition 表

判据：`docs/engineering/review-severity-policy.md` §2。
「Reproduced」= 有人真的跑出来过；「Silent」= 用户会不会拿着错结果继续走。

| 来源 | Area | Reproduced | Silent | 当前 PR 引入 | Disposition | Milestone | Guard | Acceptance Test |
|---|---|---|---|---|---|---|---|---|
| lab runner 残留进程无自愈（审计 §5.2 根因 A） | ci-harness | ✅ 生产日志 8 次 | ❌（明确报错） | ❌ | **fix-now** | 1.0-blocker | preflight 自愈 + `--kill-stale` 排到体检之前 | `test_ci_qualification.py::test_preflight_self_heals_stale_processes` |
| 汇总步骤自己挂掉（根因 B，PR #61） | ci-harness | ✅ 生产日志 | ✅ **诊断说谎** | ❌ | **fix-now** | 1.0-blocker | 解释器兜底 + 只认本轮报告 | `test_always_steps_do_not_depend_on_a_step_that_may_not_have_run` |
| 跨 workflow 轮询（根因 C，PR #62） | release-infra | ✅ v0.9.0/v0.9.1 | ❌ | ❌ | **fix-now** | 1.0-blocker | 改成 `workflow_call`，删掉轮询 | `test_release_workflow_contract.py::test_no_cross_workflow_polling` |
| 下游猜产物文件名（根因 D，#63 已修一处） | release-infra | ✅ | ✅ SBOM 静默产出空清单 | ❌ | **fix-now** | 1.0-blocker | artifact manifest 成为唯一出处 | `tests/test_artifact_manifest.py` |
| 资格验证两份手抄定义 | ci-harness | ✅（#61 要改两处） | ❌ | ❌ | **fix-now** | 1.0-blocker | 收敛成 reusable workflow | `test_release_workflow_contract.py::test_qualification_is_defined_once` |
| issue 无 severity/area/disposition 标签 | 流程 | ✅ | ✅ 退出条件不可查询 | ❌ | **fix-now** | 1.0-blocker | `.github/labels.yml` + 同步脚本（已应用） | `tests/test_governance_contracts.py` |
| #46 写回 verify 一次性 worker 目录泄漏 | engine | 偶发（Windows） | ❌（只是垃圾目录） | ❌ | **patch-train** | v1.0.1 | — | 需要先写一条稳定复现 |
| #39 文档与实现对齐 | docs | ✅ | ❌ | ❌ | **patch-train** | v1.0.1 | 清单生成 | 由清单脚本对拍 |
| §4.8 多宿主色条只记第一个宿主 | engine | ✅ 实测数字在 §4.8 | ❌（肉眼可见缩到一边） | ❌ | **guard** → **minor-release** | v1.1 | **本轮加**：多宿主时不给 orientation + `multi_host_colorbar` reason | `test_colorbar_orientation.py::test_multi_host_colorbar_does_not_offer_orientation` |
| §4.1 图例重建路径不幂等 | engine | ✅ 实测像素 | ❌（manifest 不变，写回安全） | ❌ | **minor-release** | v1.1 | 已有 `test_legend_rebuild_drift_stays_where_it_is` | 同左 |
| §4.2 alias/originals/replay 建模 | engine | n/a（不是缺陷，是形态） | — | ❌ | **minor-release** | v1.1 | 不变式 3 已钉住语义 | `test_invariants_engine.py` |
| §4.3 `honours_stroke_style` 按类名的例外 | engine | ✅ | ❌ | ❌ | **patch-train** | v1.0.1 | 已有每次重渲的复测 | `test_the_mesh_stroke_style_table_still_holds` |
| §4.5 「按对扫」harness 收进仓库 | ci-harness | n/a（新增能力） | — | ❌ | **minor-release** | v1.1 | — | — |
| §4.6 Collection 能力表剩余 196 格 | engine | 部分 | ❌ | ❌ | **accepted-limitation** | — | 能力真实不变式按**运行时实况**判，不按白名单 | `test_invariants_engine.py`（能力真实） |
| §4.4 CompatBench 分类是声明出来的 | ci-harness | ✅ | ⚠️ 报不出「变好了」 | ❌ | **patch-train** | v1.0.1 | 基线 schema 已要求写 reason/follow_up | `scripts/ci/compat_matrix.py` 的 schema 校验 |

**这张表里凡是写了 Guard 的，本轮都要落成代码**，不是落成计划。
落到哪个 PR 见 `docs/engineering/p2-fix-train.md` §4。
