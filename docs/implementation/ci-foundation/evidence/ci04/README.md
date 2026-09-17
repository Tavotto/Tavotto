# evidence/ci04/ · CI04 的只读证据（2026-09-15/16）

全部来自 `gh api` **GET**（token scopes `gist, read:org, repo, workflow`；没有 admin:org）与本仓库文件；没有登录任何机器、没有改任何
GitHub 设置。解读在上一级的 [`CI04_RUNNER_PILOT.md`](../../CI04_RUNNER_PILOT.md)。裁剪规则写在每个 JSON 的 `trimmed` 字段里。

| 文件 | 内容 | 产出方式 |
|---|---|---|
| `runners.json` | 仓库级 self-hosted runner 4 台：id / name / os / status / busy / version / labels（名字） | `gh api repos/Tavotto/Tavotto/actions/runners`，2026-09-15T22:03Z（lead 21:57Z 的读数相同） |
| `in_progress_runs_snapshot.json` | 抓取那一刻在跑的两个 run（lab schedule + 合并组 CI） | `gh api 'repos/…/actions/runs?status=in_progress&per_page=20'` |
| `lab_jobs_runner_assignment.tsv` | 最近 30 个 lab-ci run 的每个 job：runner_name / runner_group_name / labels / started_at / conclusion——`qualify` 30/30 落在 tavotto-ci-01、组名 Default | `…/workflows/lab-ci.yml/runs?per_page=30` → 逐个 `…/runs/<id>/jobs` |
| `runs_on_grep.txt` | 全部 workflow 的 `runs-on` 行（41 行；39 处真键，1 处 self-hosted） | `grep -rn runs-on .github/workflows/` @ 944f2b36 |
| `workflow_events_and_runners.json` | 事件 × workflow × 每个 job 的 runs-on 原子集合（矩阵展开、经可复用 workflow 递归） | `tests/test_merge_queue_workflows.py` 的 `_events_of` / `_jobs_of` / `_runs_on_atoms` / `_runners_of_workflow` |
| `org_plan.json` | org 类型 / 计划（free）/ 2FA 要求 / 默认仓库权限 / 创建时间（去掉 billing email 等） | `gh api orgs/Tavotto` |
| `repo_actions_permissions.json` / `repo_actions_permissions_workflow.json` | 仓库 Actions 开关、允许的 actions、默认 token 权限 | `gh api repos/…/actions/permissions[/workflow]` |
| `repo_fork_pr_contributor_approval.json` | fork PR 审批策略：`first_time_contributors` | `gh api repos/…/actions/permissions/fork-pr-contributor-approval` |
| `api_denied.json` | 4 条 403（org runner groups / org runners / org Actions 策略 / org fork PR 审批）与 2 条 422（只对 private/internal 仓库有意义的端点），**原文** | 同上 |
| `runner_downloads_latest.json` | GitHub 当前发行 runner 2.337.0 vs 四台装的 2.336.0 | `gh api repos/…/actions/runners/downloads` |
| `mutations.json` | `TestRunnerTrustZones` 的变异反证：16 条（15 KILLED + 1 NOOP_GREEN），每条锚点命中次数 / 变异落地 / pytest rc / 红的用例 / 还原 sha256 | harness 在会话 scratchpad（`mutate.py`），退出码判；还原态 pytest rc 0、树干净 |
| `runner_budget_plan.json` | 03 §1 示例预算填成**计划**（`status: not_run`，物理宿主容量未核）；`templates/runner_budget.json` 原样 | 手写 |
| `f10_reverse_probe.json` | **F-10 反向实测（2026-09-17，迁移之后）**：草稿 PR #391 里 `runs-on: [self-hosted, tavotto-lab]` 的空 job（run 35175791807 / job 105057089268）十分钟 10 次采样始终 queued、runner 为空；同一时刻公开仓库 runners `total_count` 0、ci-infra 的 tavotto-lab-01 online idle 作对照。CIP-024 由此改 pass | `gh api repos/…/actions/jobs/<id>`、两边 `…/actions/runners`，每 60 s 一次 |

**不进仓库的东西**（会话 scratchpad）：未裁剪的 API 原始 JSON（含 org 的 billing 字段与 runner 标签的 id/type）、变异 harness、pytest 日志。
