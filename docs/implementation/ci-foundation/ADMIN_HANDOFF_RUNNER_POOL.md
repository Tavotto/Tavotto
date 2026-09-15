# 管理员交接 · 独立 PR runner 池试点（CI04，2026-09-16，全部 `not_run`）

按 [`05_ACCEPTANCE_AND_ROLLOUT.md`](05_ACCEPTANCE_AND_ROLLOUT.md) §5 的形状：只读清点与仓库代码已交付
（[`CI04_RUNNER_PILOT.md`](CI04_RUNNER_PILOT.md)），下面是需要外部权限的部分——**作用对象、资源、回退、验收命令**。
本文档**不含任何 token、凭据、IP、主机名、用户名**（文档里写 `github-runner`、另有记录称 `runner`——登录用户名待管理员确认）。
执行之前先读 [`03_RUNNERS_AND_TRUST.md`](03_RUNNERS_AND_TRUST.md) 与 [`../../ci/self-hosted-runner.md`](../../ci/self-hosted-runner.md) §1。

**状态：E 组一栏——全部 `not_run`。** 外部步骤未执行时 hosted 是唯一有效路由；不要为一个还没有实体的池创建 required job。

---

## A. 要管理员回答的问题（先答完 A，再谈 B）

机器可读版在 [`evidence/admin_inventory.json`](evidence/admin_inventory.json)（`needs_admin_input`，18 项）。**最先要答的是 1–3**：
它们决定 PR 池在这个账户上到底能不能形成 03 §3 要求的边界；答不上来，B 组整段不该开始。

| # | 问题 | 为什么问 |
|---|---|---|
| **1** | org `Tavotto` 的 Settings → Actions → Runner groups 里现状如何：只有 Default 组？Default 组的「Allow public repositories」与「Workflow access」（All workflows / Selected workflows）各是什么？账户计划（读到的是 **free**）允许创建额外 runner group、允许把组限制到指定 workflow 吗？ | 本轮 API 403 读不到。按 GitHub 文档（2026-09-15）：free 只有 Default 组，「Selected workflows」只见于 Enterprise Cloud 文档。**没有这个控制，PR 池就不能接 `pull_request` job**（03 §3 末段：「若当前账户能力不能形成该边界，保持 PR 在 hosted」） |
| **2** | `tavotto-ci-01-2` / `-3` / `-4` 是什么：同一台 lab VM 上的三个额外 runner 服务实例，还是三台别的机器？谁、什么时候、为了什么注册的？为什么没有任何 workflow 用 `tavotto-ci` 标签？ | 它们在线、可接单、只等一行 `runs-on: [self-hosted, tavotto-ci]`。若在可信主机上，三个闲置入口本身就是风险——建议**注销**或写明用途。它们也让文档「每台机器一个 runner」的假设失效（CI04 §1.1） |
| **3** | 今天一个同仓库分支的 PR 加一行 `runs-on: [self-hosted, tavotto-lab]`，那个 job 会不会真的派到 `tavotto-ci-01`？管理员是否愿意用一个**空 job**（只 `echo`）在一个临时分支 PR 上实测一次并记录 run 号与 `runner_name`？ | CI04 §2.2 按规则推的答案是「会」；实测把「很可能」变成「是 / 否」。实测本身安全：job 体为空、PR 立刻关闭。**不要用 fork 做这个实验** |
| 4 | org 级 fork PR 审批策略（Settings → Actions → General）是什么？仓库级读到的是 `first_time_contributors` | 决定回头客的 fork PR 会不会自动跑在 self-hosted 上 |
| 5 | 谁有仓库写权限（能开同仓库分支 PR）？是否都开了 2FA（org `two_factor_requirement_enabled=false`）？ | 信任区 C 的边界是「有写权限的人」，这群人有多大、账户多硬，决定上面第 3 问的风险大小 |
| 6 | CI00 的 11 项：16 vCPU / 32 GiB 是宿主总量还是 lab VM 的 guest 配额；hypervisor 是什么、谁有管理面；同宿主还有哪些 VM / 常驻负载；还能分配多少 vCPU / 内存（不用 swap 硬撑）/ 磁盘（IOPS、是否共享盘）；CPU 型号、物理核、超分配比 | 03 §1：预算表「不是已证足够的配置」；宿主容量未核之前 B 组的每个数字都不是可分配量 |
| 7 | 新 VM 的出站规则：GitHub api / pipelines / objects、PyPI、npm、crates、nodejs.org、浏览器 CDN 各通不通；`github.com:443` 是否仍不可达；是否与 lab VM 同网段；是否禁止访问其它实验室主机与 metadata 服务 | 03 §5：文档里的网络限制是历史记录，不是本次实测 |
| 8 | lab VM 现状：uptime、`/srv/tavotto-ci` 各子目录占用、runner 版本（读到四台都是 2.336.0，当前发行 2.337.0——自更新在工作吗？看 `_diag/`）、最近一次 `lab_preflight.py --json` | 分清「lab 机器自身的债」与「新池的需求」 |
| 9 | 有没有从模板克隆 / 快照回滚 / 销毁 VM 的自动化入口（CLI / API）？JIT 注册（`generate-jitconfig`）用谁的凭据、放在哪个控制面？ | 03 §3：一 job 一 VM 要外部控制面保证清洁；凭据不进 VM |
| 10 | runner 服务的登录用户名是 `github-runner`（文档）还是 `runner`（另有记录）？ | `bootstrap_lab_runner.sh --user` 用错会 `useradd` 出新账号并 chown 整个 state root（CI00 已列） |

## B. 若批准试点：操作表（每步写作用对象 / 资源 / 回退 / 验收）

前提：A-1 的答案是「能把一个 runner group 限制到指定 workflow」**或**决定采用「私有 ci-infra 仓库持有 runner」形态；A-6/7 已核。
否则停在这里，PR 保持 hosted（03 §3）。

| 步 | 操作 | 作用对象 / 资源 | 回退 | 验收命令（只读） |
|---|---|---|---|---|
| B-1 | 从**额外**资源创建 1 台 VM 作为模板：Ubuntu 24.04，3 vCPU / 6 GiB / 40 GiB（`evidence/ci04/runner_budget_plan.json` 的 fast-01）；独立磁盘、独立账号、独立网段；**不挂宿主目录、无 Docker socket、无实验室共享盘、无 hypervisor 凭据** | 新 VM（模板） | 删 VM | `nproc` / `free -g` / `df -h`；`mount \| grep -v '^/dev'`（确认没有宿主挂载）；`ls /var/run/docker.sock`（应不存在） |
| B-2 | 镜像内容：`scripts/ci/bootstrap_lab_runner.sh --check` 先看它会装什么（**不要**直接跑它建 `/srv/tavotto-ci`——那是 lab 的持久化根，PR 池不需要）；手工装 Python 3.10 / 3.13 / 3.14 tool cache、Node 22 + pnpm 11、Rust stable + clippy + rustfmt、Playwright chromium 系统依赖、`fonts-noto-cjk` + `fonts-dejavu-extra`；记录每个工具的版本与镜像 hash | 模板 | 回滚快照 | `python3.13 --version` / `node --version` / `pnpm --version` / `cargo --version` / `fc-list \| grep -c Oblique`（≥ 1） |
| B-3 | 模板里**不存在**：已注册 runner 的 `.runner` / `.credentials`、任何 token、SSH 私钥（lab 的 deploy key 不复制）、`~/.cargo/credentials`、`.npmrc` 带 token | 模板 | — | `find / -name .credentials -o -name .runner 2>/dev/null`（空）；`ls ~/.ssh`（无私钥） |
| B-4 | 控制面（**不在 VM 里**）写注册脚本：`POST /repos/Tavotto/Tavotto/actions/runners/generate-jitconfig`，body `{"name": "<池名>-<随机后缀>", "runner_group_id": <A-1 决定的组>, "labels": ["<新标签>"], "work_folder": "_work"}` → 把 `encoded_jit_config` 注入新克隆的 VM → VM 内 `./run.sh --jitconfig "$JIT"`；跑完一个 job runner 自动注销；控制面销毁 VM。凭据只在控制面 | 控制面 + 每次一台一次性 VM | 停控制面；已注销的 runner 不留痕 | `gh api repos/Tavotto/Tavotto/actions/runners`（job 结束后该 runner 应**消失**）；`gh api repos/…/actions/runs/<id>/jobs --jq '.jobs[].runner_name'`（每个 job 的 runner 名都不同） |
| B-5 | 标签：新标签只有一个（例如 `tavotto-pr-fast`），**不复用 `tavotto-ci` / `tavotto-lab`**；同一天把它登记进 `.github/actionlint.yaml`（合同测试 ④ 会要求相等） | GitHub runner 标签 + 仓库配置 | 删标签 + 删登记 | `tests/test_merge_queue_workflows.py::TestRunnerTrustZones` 绿 |
| B-6 | runner group（若 A-1 允许）：新建组 → 「Allow public repositories」按需要 → **Workflow access = Selected workflows**，只列 `Tavotto/Tavotto/.github/workflows/ci.yml@refs/heads/main`（或按 SHA 钉）；**lab 的 runner 同样应进一个只允许 `lab-ci.yml` / `release.yml` 的组**——这是 A-3 那个漏洞的正解 | org runner group | 删组（runner 回 Default） | `gh api orgs/Tavotto/actions/runner-groups`（需要 admin:org）→ `selected_workflows` 字段 |
| B-7 | 网络验证（每条真跑，不抄文档）：`git ls-remote https://github.com/Tavotto/Tavotto`；`curl -sI https://api.github.com`；`curl -sI https://pipelines.actions.githubusercontent.com`；`curl -sI https://objects.githubusercontent.com`；`pip download --no-deps numpy -d /tmp/x`；`npm view pnpm version`；`cargo search serde --limit 1`；`curl -sI https://nodejs.org/dist/`；`npx playwright install chromium --dry-run`（或直接装一次看时长）；记每条的耗时 | 新 VM 网络 | — | 上面每条 rc 0；耗时进 `evidence/ci04/`（之后的轮次） |
| B-8 | 固定公开合成 benchmark 与 hosted 对拍：同一个 SHA、同一份 workflow、同一 job（建议 `python-lint` + `invariants`），在池与 `ubuntu-latest` 各跑 ≥ 5 次**交错**（不是先 5 次后 5 次），比 `runner_wait` / setup / test / 清理 / 网络下载各段 | 池 + hosted | — | `scripts/ci/ci_baseline.py analyze`（CI00 的口径）对两组 run |
| B-9 | 验收判据（全部满足才进 B-10）：正确性同 hosted（同 SHA 结论一致）；一 job 一 VM（B-4 的验收）；VM 内无凭据（B-3）；B-7 全通；B-8 的 feedback 不比 hosted 差；D 组演练全过 | — | — | 见各行 |
| B-10 | **生产路由只在 B-9 之后**：先 1 个 job 类型、1 个槽；用 C 组的开关路由；观察一周再谈第二个槽 | 仓库 workflow（C 组） | 变量改回 hosted | Gate 闭集不变（`TestGates` 绿） |

## C. 本仓库侧要配合的改动（只描述，本轮不做）

1. **标签登记**：新标签进 `.github/actionlint.yaml`（B-5）；合同测试 ④ 要求「声明的 == 实际用的」，所以**登记与第一处
   `runs-on` 使用必须同一个 PR**，早一天登记就红。
2. **哪些 job 可以路由**：fast 类（`python-lint` / `invariants` / `compat-smoke` / `frontend` / `plugin-candidate` / `workerd` 与
   `desktop-shell` 的 ubuntu 腿）；heavy 类（`backend-fast` 的一片、`package` 的 ubuntu 两档、`posix-e2e`）。**不路由**
   Windows / macOS 腿、CodeQL、任何拿签名 / 发布凭据的 job；不路由整个矩阵（池只有 1 台时 5 片同时要 1 台）。
3. **路由开关怎么做才「未部署不进 required」**：不改 Gate、不改 required contexts、不加新 job。在被路由的 job 上写
   `runs-on: ${{ vars.TAVOTTO_PR_FAST_RUNNER || 'ubuntu-latest' }}`——repo variable 缺省时表达式求值为 `ubuntu-latest`，
   池上线才设变量；池离线删变量即回 hosted，**不需要改 yml**。合同测试 ① 要按这个形状扩：`||` 右侧的缺省值必须在托管枚举里，
   左侧变量名进一张枚举。这一步等 B-9 之后再做，本轮不改 ci.yml。
4. `merge_group` 上**不**路由到池（合并组的领取等待中位 2–9s，池解决不了任何东西，只增加一个可信任务的暴露面）。
5. `docs/ci/self-hosted-runner.md` §1 的「更安全的备用形态」若被采用（私有 ci-infra 仓库），lab 的 runner 也要一并迁过去；
   那是另一个 PR。

## D. 演练清单（B-9 之前每条各做一次，记录结论；D-1/D-2 参考 03 §6「不因本地离线就跳过资格」）

| # | 演练 | 期望 | 验收 |
|---|---|---|---|
| D-1 | 池的 runner 离线（关 VM），推一个会路由到池的 PR | job 在 GitHub 上 **queued**，不会静默改跑 hosted；管理员改 C-3 的变量后**重跑同一 SHA**取得资格 | `gh api …/runs/<id>/jobs` 里该 job `status=queued`；改变量后 attempt 2 成功 |
| D-2 | 控制面停止接单（不再克隆 VM） | 同 D-1；不产生半注册的 runner | `gh api …/actions/runners` 里没有残留 |
| D-3 | job 中途取消（PR 再 push 触发 cancel-in-progress） | VM 被销毁；runner 消失；没有孤儿进程留在别处 | runners 列表干净；控制面日志有销毁记录 |
| D-4 | JIT 配置过期（生成后等超过有效期再启动） | runner 启动失败、控制面报错、VM 被销毁；**不会**永久排队 | 控制面告警；job 仍 queued 等下一台 |
| D-5 | 同宿主并发噪声：池的 VM 与 lab 的 qualification 同时跑 | lab 的 benchmark 数字与安静基线的差异被记录；若超阈值，池的 heavy 槽不与 lab 同宿主 | 对比 `LAB_PERF_GATE` 报告 |
| D-6 | 有写权限的人在 PR 里写 `runs-on: [self-hosted, tavotto-lab]`（A-3 的实测） | **被 runner group 拒绝**（B-6 做完之后）；B-6 之前则会跑——那就是要修的东西 | jobs API 的 `runner_name` |
| D-7 | 磁盘清洁：在 job 里往 `$HOME` 与 `/tmp` 写一个标记文件，下一个 job 找 | 找不到（每 job 新 VM） | 第二个 job 的 `ls` 为空 |

## E. 状态

| 项 | 状态 |
|---|---|
| A 组问题 | 18 项待答（`admin_inventory.json`） |
| B-1 … B-10 | `not_run` |
| C-1 … C-5 | `not_run`（本轮不改 ci.yml） |
| D-1 … D-7 | `not_run` |
| `runner_pool_ready` | **`not_run`** |
| 本轮已做 | 只读清点（`evidence/ci04/`）、静态守卫（`TestRunnerTrustZones`，变异 15/15）、`actionlint.yaml` 去掉预留标签、本交接 |
