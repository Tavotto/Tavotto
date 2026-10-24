# runner 信任区的静态守卫（CI04）

> 原文出自 `.github/AGENTS.md`「门禁纪律」（2026-09-18 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`.github/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_merge_queue_workflows.py::TestRunnerTrustZones`
> `.github/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：PR / merge_group / review / issue_comment 只许 hosted；审查和评论事件只由只读的 `review-budget.yml` 消费；`tavotto-lab` 只在 `_lab-qualification.yml`；事件闭集；标签集合 == 实际用到的

<!-- agents-route
sheet: .github/AGENTS.md
order: 100
改到: `runs-on`、workflow 事件、`.github/actionlint.yaml`、可复用 workflow
主题: runner 信任区（CI04）→ `runner-trust-zones.md`
必守要点: PR / merge_group / review / issue_comment 只许 hosted；审查和评论事件只由只读的 `review-budget.yml` 消费；事件闭集
-->

- **runner 信任区的静态守卫（CI04，2026-09-16）**：`tests/test_merge_queue_workflows.py::TestRunnerTrustZones` 五条——
  监听 `pull_request` / `pull_request_target` / `merge_group` / `pull_request_review` /
  `pull_request_review_comment` / `issue_comment` 的每个 workflow，全部 job 的 `runs-on`（矩阵展开、经本仓库可复用
  workflow 递归）⊆ 托管 runner 枚举 `{ubuntu-24.04, ubuntu-latest, macos-latest, windows-latest, macos-26-intel}`（`ubuntu-24.04` 与 `ubuntu-latest` 并列：ci.yml 钉 24.04，其它 workflow 仍用 `ubuntu-latest`；`macos-26-intel` 是 GitHub
  托管的 Intel 镜像，2026-09-23 为 ADR 0076 登记；只有不监听 PR 事件的 desktop-tauri.yml 用它）；`tavotto-lab` 只在 `_lab-qualification.yml`（只可
  `workflow_call`），调用方只有 `lab-ci.yml` / `release.yml`、事件 ⊆ `{push, schedule, workflow_dispatch}`；每个 workflow 的事件
  ⊆ 闭集 `{push, pull_request, pull_request_review, pull_request_review_comment, issue_comment, merge_group, schedule, workflow_dispatch, workflow_call}`（`pull_request_target` 不在里面，加任何新事件
  先来登记）；`.github/actionlint.yaml` 的自定义标签集合 **==** 实际用到的自托管标签集合——**未部署的池不进配置**，预留一个标签也红，
  所以那份文件里只有 `tavotto-lab`，新池的标签与第一处 `runs-on` 必须同一个 PR 登记。**主语是 main 上的 workflow 文件**：它让
  「把 job 派到 self-hosted」的 PR 合不进 main，**挡不住** PR 自带的 workflow 在 PR 事件上先执行一次——那一半归 runner group 的
  workflow 限制 / fork PR 审批 / 私有 infra 仓库（本轮读到：仓库级 runner 4 台在 Default 组、org 是 free 计划、fork 审批只挡首次
  贡献者、同仓库分支 PR 不经审批），现状、缺口与管理员交接在 `docs/implementation/ci-foundation/CI04_RUNNER_PILOT.md` §2 与
  `ADMIN_HANDOFF_RUNNER_POOL.md`。`runner_pool_ready: not_run`。

- **审查/评论事件的额外信任界**：`issue_comment`（默认分支执行）及
  `pull_request_review` / `pull_request_review_comment`（PR 载荷）都按
  **不可信来源**归类，与 `pull_request` 一样只能使用 hosted runner。
  三种事件目前只允许 `review-budget.yml` 消费，并在契约测试中限定为
  `ubuntu-latest`、只读 `contents/pull-requests/issues` 权限、checkout
  可信默认分支、关闭 checkout 凭据持久化，并且 `issue_comment` 只接受 PR
  而非普通 issue。新消费者必须重新审核并修改闭集契约，不能仅在事件集合里追加字符串。
