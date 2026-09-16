# CI04 · 独立 runner 池试点——本轮无部署权限：只读清点 + 静态守卫 + 管理员交接（2026-09-16）

- **`runner_pool_ready: not_run`**（§5）。本轮没有注册 / 注销 / 改标签任何 runner，没有改任何 GitHub 设置，没有 SSH 到任何机器，
  没有创建 VM；`gh api` 只有 GET。`_lab-qualification.yml` / `lab-ci.yml` / `bootstrap_lab_runner.sh` / `lab_preflight.py` /
  `cleanup.py` 一个字节没动（§7 的 `git diff --stat` 为空）。
- 改动对象：`tests/test_merge_queue_workflows.py`（新增 `TestRunnerTrustZones` 四条 + 九个 helper）、`.github/actionlint.yaml`
  （删掉从没被任何 `runs-on` 用过的预留标签 `tavotto-trusted`——它在文档里的形态是 runner group 的**组名**，不是标签；
  「未部署的池不进配置」现在是机器判据，见 §2.4 ④）、本文档、[`ADMIN_HANDOFF_RUNNER_POOL.md`](ADMIN_HANDOFF_RUNNER_POOL.md)、
  `evidence/ci04/`、`evidence/admin_inventory.json`（补上能读到的、加 7 个新问题）、`acceptance.json`（CIP-022…026）、
  `evidence/README.md`（登记 ci04/）、`.github/AGENTS.md`（门禁纪律加一段）。`templates/runner_budget.json` 不动，
  计划版在 `evidence/ci04/runner_budget_plan.json`。
- 叠在 CI02 `777dd3c8` 之上；栈：#372 CI00 → #373 CI01 → #374 CI03a → #375 CI03c → #376 CI03b → CI02（待开 PR）→ 本 PR。
- **本轮最要紧的一条结论在 §2.2**：仓库里没有任何 PR 事件的 workflow 用 `tavotto-lab`（静态答案：不能触到 lab），
  **但**四台 runner 都是仓库级、jobs API 报它们在 `Default` 组、org 在 free 计划上——按 GitHub 文档，
  「把 runner group 限制到指定 workflow」只见于 Enterprise Cloud。于是**同仓库有写权限的人在 PR 里加一行
  `runs-on: [self-hosted, tavotto-lab]`，那个 job 会被派到 tavotto-ci-01**；本轮的合同测试只能让这样的 PR 合不进 main，
  挡不住它在 PR 事件上先执行一次。这一半归管理员（交接文档 A 组问题 1–3）。
- 回退：不部署就没有运行时回退（§6）。代码侧回退 = 删掉 `TestRunnerTrustZones` 与九个 helper、把 `tavotto-trusted` 加回
  `actionlint.yaml`；文档与证据是只读产物，留着无害。

## 1. 现状清点（每条带命令与时间；原样在 [`evidence/ci04/`](evidence/ci04/README.md)）

| # | 事实 | 命令 | 时间（UTC） | 来源 |
|---|---|---|---|---|
| 1 | 仓库级 self-hosted runner **4 台在线**：`tavotto-ci-01`（labels `self-hosted, Linux, X64, tavotto-ci, tavotto-lab`，busy=true）、`tavotto-ci-01-2` / `-3` / `-4`（labels `self-hosted, Linux, X64, tavotto-ci`，idle）；runner 版本全部 **2.336.0** | `gh api repos/Tavotto/Tavotto/actions/runners` | lead 2026-09-15 21:57Z；本轮复核 22:03Z，结果相同 | `runners.json` |
| 2 | -01 当时在跑 `Lab Qualification (schedule)` run 35028176892（21:54Z 起）；同一刻还有一个合并组 CI run（#357 候选）在 hosted 上 | `gh api 'repos/…/actions/runs?status=in_progress'` | 22:03:26Z | `in_progress_runs_snapshot.json` |
| 3 | **最近 30 个 lab-ci run 的 `qualify` 全部由 `tavotto-ci-01` 执行**；jobs API 报 `runner_group_id=1` / `runner_group_name=Default`；-2/-3/-4 从没出现过 | `gh api repos/…/actions/workflows/lab-ci.yml/runs?per_page=30` → 每个 run 的 `/jobs` | 22:03Z | `lab_jobs_runner_assignment.tsv` |
| 4 | 仓库里**没有任何 workflow 用 `tavotto-ci` 标签**；`runs-on` 全表只有 `_lab-qualification.yml:57` 一处 self-hosted（`[self-hosted, linux, x64, tavotto-lab]`），其余 38 处全是 `ubuntu-latest` / `windows-latest` / `macos-latest` / `${{ matrix.os }}`（展开后也是这三个） | `grep -rn runs-on .github/workflows/`；`evidence/ci04/workflow_events_and_runners.json`（守卫自己的读法，矩阵展开） | 22:02Z | `runs_on_grep.txt` |
| 5 | 仓库属于 Organization `Tavotto`，public；**plan = free**；`two_factor_requirement_enabled=false`；`default_repository_permission=read` | `gh api orgs/Tavotto` | 22:03Z | `org_plan.json` |
| 6 | 仓库 Actions：`enabled=true, allowed_actions=all, sha_pinning_required=false`；workflow 默认权限 `read`，`can_approve_pull_request_reviews=false` | `gh api repos/…/actions/permissions`、`…/permissions/workflow` | 22:03Z | `repo_actions_permissions*.json` |
| 7 | **fork PR 审批策略（仓库级）：`first_time_contributors`**——首次贡献者的 fork PR 要人工批准才跑 workflow，回头客的 fork PR 自动跑；**同仓库分支的 PR 从不经审批** | `gh api repos/…/actions/permissions/fork-pr-contributor-approval` | 22:03Z | `repo_fork_pr_contributor_approval.json` |
| 8 | org 级 runner group / org runners / org Actions 策略 / org fork PR 审批：**HTTP 403**「You must be an org admin or have the runners and runner groups fine-grained permission.」（token scopes `gist, read:org, repo, workflow`，缺 `admin:org`） | `gh api orgs/Tavotto/actions/runner-groups` 等四条 | 22:03Z | `api_denied.json` |
| 9 | `…/actions/permissions/access` 与 `…/fork-pr-workflows-private-repos`：**HTTP 422**「only applies to internal and private repositories」——这两层设置对 public 仓库不存在 | 同上 | 22:03Z | `api_denied.json` |
| 10 | GitHub 当前发行的 runner 是 **2.337.0**，四台装的都是 2.336.0（一个小版本落后；runner 自更新是否在工作待管理员看 `_diag/`） | `gh api repos/…/actions/runners/downloads` | 22:03Z | `runner_downloads_latest.json` |
| 11 | CI00 的 11 项待填（§10、`admin_inventory.json`）**仍待填**；本轮补了「能从 GitHub 读到的」并新增 7 问（§1.2） | — | — | `admin_inventory.json` |
| 12 | 文档假设复核：标签 `[self-hosted, linux, x64, tavotto-lab]` ✔；「带该标签的 runner 只有一台」✔（只有 -01 带）；「每台机器一个 runner 进程」（`self-hosted-runner.md` §7）**很可能不成立**——四个名字共享前缀 `-01`；state root / flock / `ssh.github.com:443` 通路是文档记录，本轮未复测 | 读 `docs/ci/self-hosted-runner.md`、`_lab-qualification.yml` L57–75 | — | — |

### 1.1 -2 / -3 / -4 是什么（待管理员确认；设计按最坏情况）

按名字（同一前缀 `tavotto-ci-01`、后缀 `-2/-3/-4`）**很可能是同一台可信 lab VM 上的四个 runner 服务实例**；另有会话记录
（2026-08-22）称那台 VM 上装了 `~/actions-runner`、`-2`、`-3`、`-4` 四个并行实例、登录用户名是 `runner`——与文档写的
`github-runner` 不一致，`admin_inventory.json` 早已把这一条列为待确认。本轮不把它当事实，只当**最坏情况**来设计：

- 三台空闲的 `tavotto-ci` runner **在可信主机上**。把任何 PR job 路由到 `tavotto-ci`，等于把公开 PR 的代码放到信任区 C 的机器上
  （03 §3）；标签不是访问控制。**本轮不做、交接里也不建议做。**
- 四个实例共享一台 16 vCPU / 32 GiB 的 VM，意味着 `self-hosted-runner.md` §7「每台 runner 的 job 并发为 1」这条对**机器**已不成立
  ——今天 lab 仍串行只是因为没有 workflow 用 `tavotto-ci`。一旦有人用了，qualification 的 benchmark / soak 会与 PR job 同机并发，
  性能基线的「安静机器」前提当场失效（03 §5）。
- 闲置的入口本身是风险：它们在线、可接单、只等一行 `runs-on: [self-hosted, tavotto-ci]`。交接文档建议管理员**要么注销、要么说明用途**。

### 1.2 本轮补进 `admin_inventory.json` 的东西

- `observed_readonly_2026_09_15` 段：上表 1–10 的机器可读版。
- `runner_registration_scope` 等三项加了 `observed`（能读到的部分），`value` 仍为 null——**观察不等于回答**。
- 新增 7 问：-2/-3/-4 的身份 / 注册人 / 为何闲置、org runner group 现状与「Workflow access」、org 级 fork PR 审批、
  「同仓库分支 PR 加一行 runs-on 会不会真派到 -01」的实测意愿、VM 模板与 JIT 签发方。

## 2. 三个信任区在本仓库的实际落点

### 2.1 静态：事件 × workflow × runner（`evidence/ci04/workflow_events_and_runners.json`，守卫自己的读法）

| workflow | 事件 | 全部 job 的 `runs-on` 之并（矩阵展开、经可复用 workflow 递归） | 信任区 |
|---|---|---|---|
| `ci.yml` | push(main) / **pull_request** / **merge_group** | `ubuntu-latest, macos-latest, windows-latest` | A |
| `codeql.yml` | push(main) / **pull_request** / **merge_group** / schedule | `ubuntu-latest` | A |
| `pr-conflict-domains.yml` | **pull_request** | `ubuntu-latest` | A |
| `nightly.yml` | schedule / workflow_dispatch | `ubuntu-latest, windows-latest` | A |
| `metrics-freshness.yml` / `telemetry-metrics.yml` / `plugin-stable.yml` | schedule / push / workflow_dispatch | `ubuntu-latest` | A |
| `desktop-tauri.yml` | workflow_call / workflow_dispatch | `ubuntu-latest, macos-latest, windows-latest` | A（发布凭据留在 hosted） |
| `lab-ci.yml` | push(main) / schedule / workflow_dispatch | `ubuntu-latest`（trust-check）+ `self-hosted, linux, x64, tavotto-lab`（经 `_lab-qualification.yml`） | **C** |
| `release.yml` | push(tags v*) / workflow_dispatch | 三个 hosted + `self-hosted, linux, x64, tavotto-lab`（`lab_release_gate`） | A + **C** |
| `_lab-qualification.yml` | workflow_call | `self-hosted, linux, x64, tavotto-lab` | **C** |

**静态答案：PR 与 merge_group 今天触不到 lab**——监听这三种事件的三个 workflow，runner 集合 ⊆ {三个 hosted 名}；`tavotto-lab`
只出现在 `_lab-qualification.yml`，调用它的只有 `lab-ci.yml` / `release.yml`，两者的事件 ⊆ {push, schedule, workflow_dispatch}，
SHA 另经 trust job 验 `origin/main` 祖先（`test_release_workflow_contract.py::test_every_caller_gates_the_sha_through_a_trust_job`）。
信任区 B（可销毁 PR 池）**在本仓库不存在**：没有它的标签、没有它的 job、没有它的 runner。

### 2.2 动态：同仓库有写权限的人在 PR 里加一行 `runs-on: [self-hosted, tavotto-lab]` 会怎样

按 GitHub 的规则逐条推（引文抓取于 2026-09-15，见 §2.3）：

1. **`pull_request` 事件执行的是 PR 那一版的 workflow 文件**（合并提交上的 `.github/workflows/`），不是 main 上的。PR 写什么
   `runs-on`，调度器就按什么找 runner。`merge_group` 同理——组合提交带着 PR 的 yml。
2. **仓库级 runner 对本仓库的任何 workflow 可见**。按标签匹配：`[self-hosted, tavotto-lab]` 匹配 `-01`；
   `[self-hosted, tavotto-ci]` 或裸 `self-hosted` 匹配全部四台。仓库这一层**没有**「只允许指定 workflow」的开关——那个开关
   （runner group 的 Workflow access → Selected workflows，格式 `owner/repo/.github/workflows/file.yml@ref`）只在 org / enterprise
   的 runner group 上，且**只出现在 Enterprise Cloud 版文档**里；org `Tavotto` 是 **free** 计划（§1 表 5），文档原话：
   「All organizations have a single default runner group. Organization owners using the GitHub Team plan can create additional
   organization-level runner groups.」——所以现状很可能是：只有 Default 组、没有 workflow 限制、限制也建不了。**这一条要管理员
   在 org Settings 里确认**（403，本轮读不到）。
3. **审批只挡 fork**：仓库级策略 `first_time_contributors`（§1 表 7）意味着首次贡献者的 fork PR 要人批、回头客的 fork PR
   自动跑；**同仓库分支的 PR 根本不进审批**。GitHub 自己在设置页上的警告是：「If you are using self-hosted runners, potentially
   malicious user-controlled workflow code will execute automatically if the user is allowed to bypass approval in the set approval
   policy or if the pull request is approved.」
4. 于是：**有写权限的人（或被批准 / 曾合入过的 fork 作者）开一个 PR，加一行 `runs-on: [self-hosted, tavotto-lab]`，那个 job
   会在 PR 事件上被派到 `tavotto-ci-01`——在 `qualify` 的空档立刻跑，否则排队等它。** 它拿到的是 `contents: read` 的
   GITHUB_TOKEN（默认 `read`，§1 表 6）——但 03 §3 与 `self-hosted-runner.md` §1 说得对：攻击者要的是**机器**（`/srv/tavotto-ci`
   的基线与缓存、内网位置、`~/.ssh` 的只读 deploy key、`.credentials`），不是仓库 secret。
5. **本轮的合同测试挡的是「合进 main」，不是「在 PR 上执行」**：`TestRunnerTrustZones` 跑在 `backend-fast`（PR / merge_group）
   与 `main-landing-audit`（push main）里，它会在那个 PR 自己的 CI 里红，让它进不了合并队列；但 (a) 那个 self-hosted job
   与 `backend-fast` 是并行的，红灯亮起时它已经在 -01 上跑了；(b) PR 可以连测试一起改。**它是防误用的门，不是防恶意的门。**
   真正的门只能是 PR 改不动的东西：runner group 的 workflow 限制、或把 runner 注册在私有 infra 仓库里（03 §3、`self-hosted-runner.md`
   §1「更安全的备用形态」）。

### 2.3 哪一层在挡、哪一层挡不住（现状）

| 层 | 现状（来源） | 对同仓库分支 PR | 对 fork PR |
|---|---|---|---|
| 仓库里的 workflow 文件（main 上） | 没有 PR 事件的 workflow 用 lab 标签（§2.1） | **挡不住**——PR 自带 yml | 挡不住 |
| 合同测试 `TestRunnerTrustZones` | 本轮新增（§2.4） | 挡合并，**不挡执行** | 同左 |
| fork PR 审批 `first_time_contributors` | 仓库级读到（§1 表 7）；org 级 403 | **不适用** | 只挡首次贡献者；回头客自动跑 |
| runner group「Selected workflows」 | 403 读不到；free 计划按文档只有 Default 组、限制只见于 Enterprise Cloud 文档 | **很可能不存在** | 同左 |
| 私有 ci-infra 仓库持有 runner | **已拍板（2026-09-16）**：lab runner 迁到私有仓库，操作表 [`ADMIN_HANDOFF_RUNNER_POOL.md`](ADMIN_HANDOFF_RUNNER_POOL.md) F 组；本轮未执行 | 挡得住（公开仓库的 PR 够不到私有仓库的 runner） | 挡得住 |
| `default_workflow_permissions=read` | 读到（§1 表 6） | 只限 token，不限机器 | 同左 |

结论：**今天挡「PR 触到可信 lab」的只有一条线——没人这么写**。这是 CIP-024 只能 `not_run` 的原因（§5）。交接文档 A 组的前三个问题
就是让管理员把这张表的「很可能」变成「是 / 否」。

### 2.4 合同测试：`TestRunnerTrustZones` 四条（全部正面形式；变异 15/15 打红 + 1 条 no-op 保持绿，`evidence/ci04/mutations.json`）

| # | 判据（主语） | 反证（变异 → 红的用例） |
|---|---|---|
| ① `test_untrusted_events_reach_only_hosted_runners` | 监听 `pull_request` / `pull_request_target` / `merge_group` 的每个 workflow，**全部 job** 的 `runs-on` 原子集合（矩阵轴与 include 展开；job 级 `uses: ./.github/workflows/x.yml` 递归到被调用方）⊆ `{ubuntu-latest, macos-latest, windows-latest}`；前提：ci.yml 与 codeql.yml 必须在监听集合里 | M01 ci.yml `python-lint` → `[self-hosted, tavotto-lab]`；M02 → `[self-hosted, tavotto-ci]`；M03 裸 `self-hosted`；M04 `backend-platforms` 的 os 轴加 `self-hosted`；M05 `package` include 加 `os: self-hosted`；M09 lab-ci.yml 加 `pull_request`（经递归看到 lab 的 runs-on）；M10 新建一个 `pull_request` 直接 `uses: _lab-qualification.yml` 的文件；M13 映射形式 `group:`+`labels:`；M14 codeql.yml 派到 lab；M15 pr-conflict-domains 派到 `tavotto-ci`；M16 未登记的托管名 `ubuntu-24.04-arm` |
| ② `test_the_lab_label_reaches_only_the_reusable_qualification_and_its_trusted_callers` | `tavotto-lab` 出现的文件集合 == `{_lab-qualification.yml}`；该文件事件 == `{workflow_call}`；调用它的文件集合 == `{lab-ci.yml, release.yml}`；两者事件 ⊆ `{push, schedule, workflow_dispatch}` | M01 / M09 / M10 / M11（`_lab-qualification.yml` 加 `workflow_dispatch`）/ M13 / M14 |
| ③ `test_every_workflow_event_is_in_the_known_closed_set` | 每个 workflow 的事件 ⊆ 闭集 `{push, pull_request, merge_group, schedule, workflow_dispatch, workflow_call}`，且闭集里每个事件今天都真的有人用（表不许宽过头）。`pull_request_target` 不在闭集里（03 §3） | M06 ci.yml 加 `pull_request_target` |
| ④ `test_actionlint_custom_labels_are_exactly_the_self_hosted_labels_in_use` | `.github/actionlint.yaml` 的 `self-hosted-runner.labels` 集合 == 全部 workflow `runs-on` 里实际出现的自托管标签集合（剪掉三个托管名与注册时自动打的 `self-hosted/linux/windows/macos/x64/arm64/arm`）；前提：`tavotto-lab` 在用到的集合里 | M07 actionlint 多登记 `tavotto-fast`；M08 把 `tavotto-lab` 换名；M02 / M15（用了没登记的 `tavotto-ci`）；M16 |
| no-op | M12：只在 ci.yml 的注释里写 `runs-on: [self-hosted, tavotto-lab]` → **四条全绿**（判据不被散文咬到） | — |

判据的读法与 `test_release_workflow_contract.py` 同一条纪律：不用 PyYAML，按本仓库缩进形状切；`on:` / `jobs:` 切不出、
`runs-on` 写法认不出、`${{ matrix.os }}` 却读不出任何 os 取值，一律抛，不猜。docstring 里写明它量的是 **main 上的 workflow 文件**，
挡不住 PR 自带的 workflow。

**为什么 ④ 要求相等而不是 ⊆**：`actionlint.yaml` 原来登记着 `tavotto-trusted`「预留：组织升级到支持 runner group 之后用」——一个没有
任何 runner、没有任何 job 的标签，正是 03 §6「尚未部署 / 离线池不进入强制默认路由」在配置层的反面。预留标签是下一个人「顺手
用一下」的入口，而 actionlint 不会拦它。且在文档写的形态 `runs-on: { group: tavotto-trusted, labels: [tavotto-lab] }` 里，
`tavotto-trusted` 是组名，actionlint 根本不按标签查它。删掉，等真有池了再登记——那时 ④ 会红一次，逼着人来这里看一眼。

## 3. 新池的设计（03 §1–§6 落到本仓库；全部 `not_run`，只描述）

计划版预算 [`evidence/ci04/runner_budget_plan.json`](evidence/ci04/runner_budget_plan.json)；`templates/runner_budget.json` 原样。

| 项 | 设计 | 依据 |
|---|---|---|
| 资源来源 | **额外**资源，不拆现有 lab VM；lab 的 16/32 已被 qualification 占用，四个 runner 实例还在它上面（§1.1） | 03 §1；CI04 phase 第 1 条 |
| 拓扑 | 可销毁 VM，**一 job 一 VM**：模板克隆 → 启动时 JIT 注册 → 跑一个 job → 外部控制面销毁 / 回滚磁盘。`--ephemeral` 只注销 runner，不清磁盘 | 03 §3 [W02/W03] |
| 注册 | `POST /repos/Tavotto/Tavotto/actions/runners/generate-jitconfig`（body：`name` / `runner_group_id` / `labels` / `work_folder`）→ `encoded_jit_config` → `./run.sh --jitconfig …`。凭据留在控制面，**不进模板、不进 VM**；模板里没有 `.credentials` / `.runner` / token / SSH 仓库写密钥 | GitHub REST（2026-09-15 抓取）；03 §3 |
| 镜像内容 | Ubuntu 24.04；Python 3.10 / 3.13 / 3.14 tool cache（本网络 `setup-python` 现下不了，`self-hosted-runner.md` §6）；Node 22 + pnpm 11；Rust stable + clippy + rustfmt；Playwright chromium 的系统依赖（`--with-deps` 那份 apt，浏览器本体按锁文件版本现装，CI02 §2 的结论：**不缓存浏览器**）；`fonts-noto-cjk` + `fonts-dejavu-extra`（issue #229 那条：缺 Oblique 时 italic 静默退回 regular）。镜像记版本与 hash，升级独立于产品科学依赖 | 03 §4；`self-hosted-runner.md` §4 |
| 网络验证清单 | checkout（`github.com:443` 在本网络不可达，历史对策是 `ssh.github.com:443`——**PR 池不能复用 lab 的 deploy key**，要么给池自己的只读 key，要么证明池所在网段 `github.com` 可达）；actions 下载（`api.github.com` / `pipelines.actions.githubusercontent.com` / `objects.githubusercontent.com`）；artifacts 上传下载；PyPI；npm registry；crates.io；`nodejs.org`；Playwright 浏览器 CDN。**每条实测，不抄文档** | 03 §5 [G08]；`self-hosted-runner.md` §2、§5 |
| 隔离 | 无宿主挂载、无 Docker socket、无实验室共享盘、阻断 metadata 服务与其它内网主机；VM 内无 hypervisor 凭据；不与 lab VM 同网段（或至少互不可达） | 03 §3；CI04 phase 第 3 条 |
| 标签 | 新标签（例如 `tavotto-pr-fast` / `tavotto-pr-heavy`）**只在池真上线那天**进 `actionlint.yaml`（④ 会逼着来）；`tavotto-ci` 不复用（它在可信主机上，§1.1） | 03 §2、§6 |
| 可承载 job | fast：`python-lint` / `invariants` / `compat-smoke` / `frontend` / `plugin-candidate` / `workerd` 与 `desktop-shell` 的 ubuntu 腿；heavy：`backend-fast` 的一片 / `package` 的 ubuntu 两档 / `posix-e2e`。**不承载**：Windows / macOS 腿、CodeQL、任何持有签名 / 发布凭据的 job | 03 §1、§2 |
| `max-parallel` | 池只有 1 台时，路由到它的矩阵要么 `max-parallel: 1` 要么不路由矩阵；它只限那一个矩阵，不限总量、不建 runner、不排优先级 | 03 §2 |
| 路由开关 | 未部署时 **hosted 是唯一路由**；`runs-on` 不是「先等本地几分钟再换 hosted」的备选列表。开关的形状见交接文档 C 组：在 workflow 里用一个 **repo variable** 选 runner 名，变量缺省就是 hosted 名——池离线时改一个变量回到 hosted，不改 yml、不动 required contexts | 03 §6；05 §5 |
| 退出路线 | = hosted。已失败 / 取消的必需实例仍在同一 SHA 上重跑取得资格；不把旧结果拷成成功 | 03 §6；CI04 phase 第 9 条 |
| lab 独占 | `lab_preflight` / `cleanup` 的共享根与 flock **不沿用**到 PR 池（它们假设长期状态与单机独占）；PR 池的 VM 每次都是新的，不需要它们 | CI04 phase 第 7 条 |

## 4. 值不值得：新 Linux 池对当前瓶颈的贡献有限

CI01–CI03 之后（数字来源标在括号里）：

| 瓶颈 | 数字 | Linux 自托管池能不能解 |
|---|---|---|
| 合并组上托管 runner 的领取等待 | 中位 ubuntu 2s / macOS 9s / Windows 3s（CI00 §4.2） | **不用解**——合并组被队列串行化，等待几乎为零 |
| 合并组上 Windows 分片的领取等待 | #375 / #376 的 run（`35011613925`）里 205s / 321s——`windows-exe-smoke` 两片 + `backend-platforms (windows)` 两片 + `package (windows)` 同时要 5 台 Windows（CI02 §1 第 6 行） | **不能**——Linux 池不产 Windows（03 §1：「单纯增加 Linux 槽不会让 Windows/macOS 原生资格更快」） |
| macOS 腿（`macos-app-smoke` / `backend-platforms (macos)` / `desktop-shell (macos)`） | 关键路径之一 | **不能**——不用 Linux 容器假冒 |
| PR 突发时 ubuntu 领取等待 | `pull_request` 上 `runner_wait` 中位 331s、max **3050s**（51 分钟，`34949318428`）；六次「几秒内推 5–6 个 stacked PR」= ~78 个并发 job（CI00 §4.3） | **能解一部分**——但这是账户并发上限的问题；1–2 台 Linux 槽在 78 个并发 job 面前是 1–3%，且只对 fast 类 job 有效 |
| 合并组上缓存 0% 命中、每候选写 ≈ 1.8 GB、仓库缓存超 10 GB 上限 | CI02 §4.1 | **不能**——作用域问题，不是容量问题；修法在 CI02 §4.1（归 CI05 拍板） |
| `backend-fast` 全量 pytest ~33 分钟 → 分片后每片一半 | CI03a | 池不改变测试时长；只在 hosted 排队时省等待 |

**「先做什么才有净收益」的顺序**（每一步都不需要新 runner）：

1. **CI05 拍板缓存作用域修法**（CI02 §4.1 (a)：push main 种子 job + rust-cache 对齐 `shared-key`）——合并组关键路径上的
   `pnpm install` / cargo 重建是每个候选都在付的钱，与容量无关。
2. **Windows 关键路径**：CI03c 已把 Playwright 分两片、CI02 去掉 `--with-deps`（−200s / 片，实验由 full-ci run 判）。下一刀是
   `backend-platforms (windows)` 40 分钟顶着 45 上限那件事（#345 / #346），仍在 hosted 上做。
3. **PR 突发的 78 并发**：先量账户并发上限（API 不给，问管理员 / 看 billing 页），再决定是「减少同时推 PR 的习惯」（stacked PR 用
   merge queue 串行推）还是加容量。**加 Linux 池是这一步的候选之一，不是前提**。
4. 以上做完还剩「PR 反馈中位 44.7 分钟里的 ~10 分钟领取等待」时，再回到 §3 的池——那时试点才有可对拍的 A/B 基线
   （固定公开合成 benchmark，交接文档 B-8）。

CI00 §11 H4 的裁决「自托管只有条件齐备才有净收益——证据不足」本轮**没有被推翻**，只是把「条件」写具体了。

**拍板结果（2026-09-16，用户，[`CI_HANDOFF.md`](CI_HANDOFF.md) §13）**：**不建 PR 池**（ADMIN_HANDOFF B / C / D 组留档不执行）；
**注销闲置的 `tavotto-ci-01-2/-3/-4`**（A-2 改成操作项）；**lab runner 迁到私有 ci-infra 仓库**（F 组）；账户并发上限查明是
free 计划的 **20 个并发 job（macOS 5）**，处置是改推送习惯而不是加容量（CI05 §5）。`runner_pool_ready` 保持 `not_run`，且按拍板本轮不会变 pass。

## 5. `runner_pool_ready: not_run`，以及每个验收项为什么是现在这个状态

CI04 phase「验收」要求真实正反测试：隔离、一次性身份 / 磁盘、并发有效、版本与网络、可信资源不可达、故障回退。**本轮一项都没跑**
——没有 VM、没有 runner、没有部署权限。按 05 §5：只读清点与仓库代码先交付，外部步骤生成管理员操作表。

| ID | 要求 | 状态 | 主语与理由 |
|---|---|---|---|
| CIP-022 | 现有 lab 职责和资格基线不被 PR 池悄然改变 | **pass** | 主语 = 本 PR 栈（`8b95256c..HEAD`）对 lab 五个文件的 diff（`_lab-qualification.yml` / `lab-ci.yml` / `bootstrap_lab_runner.sh` / `lab_preflight.py` / `cleanup.py`）**为空**（§7）+ 守卫 ①②（PR 事件触不到 lab 标签；lab 标签只有一处、调用方只有两个、事件只有可信三种）。**不是**「lab 机器没被改」——那一半（-2/-3/-4 的存在本身就是变化）待管理员 |
| CIP-023 | 新池真实 VM 资源和网络已测；未知项不冒充可用 | not_run | 没有 VM；网络清单在 §3 / 交接 B-7，一条都没测。宿主容量未核（`runner_budget_plan.json`） |
| CIP-024 | PR 无法通过修改 runs-on 接触可信 lab / 签名 / 真实数据 | **not_run** | 静态半边有守卫（①②，挡合并）；**动态半边读不到**：runner group 的 workflow 限制 403、free 计划按文档没有这个控制、fork 审批只挡首次贡献者、同仓库分支 PR 不经审批。缺的那半边是「PR 改不动的控制」，本轮没有任何证据它存在（§2.3）。签名 / 发布凭据留在 hosted 这一条成立（`desktop-tauri.yml` / `release.yml` 的相关 job 都是 hosted，§2.1） |
| CIP-025 | 执行 VM 无管理面凭据，单 job 身份与磁盘清洁得到验证 | not_run | 没有执行 VM |
| CIP-026 | 多槽真并行、离线 route 退出和同等 hosted 恢复有效 | not_run | 没有槽；退出路线的设计在 §3 与交接 C / D |

## 6. 回退

- **运行时**：不部署就没有回退——hosted 今天是、明天仍是唯一路由。没有任何 job 的 `runs-on` 指向不存在的池
  （守卫 ④：配置里没有未部署池的标签）。
- **代码**：删掉 `TestRunnerTrustZones` 与九个 helper（`_top_level_block` / `_events_of` / `_jobs_of` / `_matrix_os_values` /
  `_runs_on_atoms` / `_workflow_texts` / `_local_reusable` / `_runners_of_workflow` / `_actionlint_custom_labels`）与四个模块常量；
  `actionlint.yaml` 加回 `- tavotto-trusted`。文档、证据、`admin_inventory.json` 的补充是只读产物，留着无害。
- **lab**：本轮没动，没有可回退的东西。

## 7. 验证命令与退出码（2026-09-16，worktree `ci-foundation`，本 PR 的树）

| 命令 | 结果 |
|---|---|
| `git diff --stat 8b95256c..HEAD -- .github/workflows/_lab-qualification.yml .github/workflows/lab-ci.yml scripts/ci/bootstrap_lab_runner.sh scripts/ci/lab_preflight.py scripts/ci/cleanup.py` | 输出 0 行（CIP-022 的主语） |
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | `All checks passed!` / `375 files already formatted`，rc 0 |
| `.venv/bin/python -m pytest tests/test_merge_queue_workflows.py tests/test_release_workflow_contract.py tests/test_docs_references.py -q` | 127 passed，rc 0（守卫提交前）；文档与证据落地后复跑见 commit 信息 |
| `actionlint` | 无输出，rc 0（yml 没改；`actionlint.yaml` 少了一个标签之后仍无未知标签告警） |
| 变异反证 `evidence/ci04/mutations.json`（harness 在会话 scratchpad） | 16 条：15 KILLED + 1 NOOP_GREEN，0 UNEXPECTED；每条锚点恰好一次 → 变异落地 → pytest 退出码 → `git checkout` 还原 → sha256 核对；还原态 pytest rc 0、`git status --porcelain` 为空；harness rc 0 |

## 8. 证据索引（[`evidence/ci04/README.md`](evidence/ci04/README.md)）

`runners.json` · `in_progress_runs_snapshot.json` · `lab_jobs_runner_assignment.tsv` · `runs_on_grep.txt` ·
`workflow_events_and_runners.json` · `org_plan.json` · `repo_actions_permissions.json` · `repo_actions_permissions_workflow.json` ·
`repo_fork_pr_contributor_approval.json` · `api_denied.json` · `runner_downloads_latest.json` · `mutations.json` · `runner_budget_plan.json`。
上一级：`admin_inventory.json`（更新）、`acceptance.json`（CIP-022…026）。
