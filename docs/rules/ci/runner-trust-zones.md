# runner 信任区的静态守卫（CI04）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **runner 信任区的静态守卫（CI04，2026-09-16）**：`tests/test_merge_queue_workflows.py::TestRunnerTrustZones` 四条——
  监听 `pull_request` / `pull_request_target` / `merge_group` 的每个 workflow，全部 job 的 `runs-on`（矩阵展开、经本仓库可复用
  workflow 递归）⊆ `{ubuntu-latest, macos-latest, windows-latest}`；`tavotto-lab` 只在 `_lab-qualification.yml`（只可
  `workflow_call`），调用方只有 `lab-ci.yml` / `release.yml`、事件 ⊆ `{push, schedule, workflow_dispatch}`；每个 workflow 的事件
  ⊆ 闭集 `{push, pull_request, merge_group, schedule, workflow_dispatch, workflow_call}`（`pull_request_target` 不在里面，加任何新事件
  先来登记）；`.github/actionlint.yaml` 的自定义标签集合 **==** 实际用到的自托管标签集合——**未部署的池不进配置**，预留一个标签也红，
  所以那份文件里只有 `tavotto-lab`，新池的标签与第一处 `runs-on` 必须同一个 PR 登记。**主语是 main 上的 workflow 文件**：它让
  「把 job 派到 self-hosted」的 PR 合不进 main，**挡不住** PR 自带的 workflow 在 PR 事件上先执行一次——那一半归 runner group 的
  workflow 限制 / fork PR 审批 / 私有 infra 仓库（本轮读到：仓库级 runner 4 台在 Default 组、org 是 free 计划、fork 审批只挡首次
  贡献者、同仓库分支 PR 不经审批），现状、缺口与管理员交接在 `docs/implementation/ci-foundation/CI04_RUNNER_PILOT.md` §2 与
  `ADMIN_HANDOFF_RUNNER_POOL.md`。`runner_pool_ready: not_run`。
