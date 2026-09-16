# 管理员交接 · 独立 PR runner 池试点（CI04，2026-09-16，全部 `not_run`）

按 [`05_ACCEPTANCE_AND_ROLLOUT.md`](05_ACCEPTANCE_AND_ROLLOUT.md) §5 的形状：只读清点与仓库代码已交付
（[`CI04_RUNNER_PILOT.md`](CI04_RUNNER_PILOT.md)），下面是需要外部权限的部分——**作用对象、资源、回退、验收命令**。
本文档**不含任何 token、凭据、IP、主机名、用户名**（文档里写 `github-runner`、另有记录称 `runner`——登录用户名待管理员确认）。
执行之前先读 [`03_RUNNERS_AND_TRUST.md`](03_RUNNERS_AND_TRUST.md) 与 [`../../ci/self-hosted-runner.md`](../../ci/self-hosted-runner.md) §1。

**状态：E 组一栏——全部 `not_run`。** 外部步骤未执行时 hosted 是唯一有效路由；不要为一个还没有实体的池创建 required job。

**2026-09-16 用户拍板（CI05，[`CI_HANDOFF.md`](CI_HANDOFF.md) §13）**：**不建 PR 池**（B / C / D 组整段不批准，只留档）；**注销闲置的 `tavotto-ci-01-2/-3/-4`**（A-2 改成操作项）；**lab runner 迁到私有 ci-infra 仓库**（新加 F 组操作表）。`runner_pool_ready` 仍 `not_run`，且按拍板**本轮不会变 pass**——没有池就没有这个状态的主语。

---

## A. 要管理员回答的问题（先答完 A，再谈 B）

机器可读版在 [`evidence/admin_inventory.json`](evidence/admin_inventory.json)（`needs_admin_input`，18 项）。**最先要答的是 1–3**：
它们决定 PR 池在这个账户上到底能不能形成 03 §3 要求的边界；答不上来，B 组整段不该开始。

| # | 问题 | 为什么问 |
|---|---|---|
| **1** | org `Tavotto` 的 Settings → Actions → Runner groups 里现状如何：只有 Default 组？Default 组的「Allow public repositories」与「Workflow access」（All workflows / Selected workflows）各是什么？账户计划（读到的是 **free**）允许创建额外 runner group、允许把组限制到指定 workflow 吗？ | 本轮 API 403 读不到。按 GitHub 文档（2026-09-15）：free 只有 Default 组，「Selected workflows」只见于 Enterprise Cloud 文档。**没有这个控制，PR 池就不能接 `pull_request` job**（03 §3 末段：「若当前账户能力不能形成该边界，保持 PR 在 hosted」） |
| **2** | **已拍板（2026-09-16）：注销 `tavotto-ci-01-2` / `-3` / `-4`，不建池。** 操作（在那台机器上，每个实例目录各做一次，三次；目录名待管理员按 `ls ~` 确认——另有记录称是 `~/actions-runner-2` / `-3` / `-4`）：`cd <实例目录> && sudo ./svc.sh stop && sudo ./svc.sh uninstall && ./config.sh remove --token <REMOVAL_TOKEN>`（`<REMOVAL_TOKEN>` 在仓库 Settings → Actions → Runners 现取，1 小时有效，**不写进任何文件**）；然后删掉实例目录。**不要碰 `-01`**（它是 lab；由 F 组迁移）。验收（只读）：`gh api repos/Tavotto/Tavotto/actions/runners --jq '[.runners[].name]'` 只剩 `["tavotto-ci-01"]`；`--jq .total_count` = 1。回退：按 `docs/ci/self-hosted-runner.md` §7 重新注册（要新的注册 token）。原问题保留供记录：它们是同一台 lab VM 上的三个额外实例还是三台机器、谁注册的——注销前顺手记一行答案进 `admin_inventory.json` | 它们在线、可接单、只等一行 `runs-on: [self-hosted, tavotto-ci]`；在可信主机上就是三个闲置入口（CI04 §1.1）。用户拍板不建池，所以没有任何理由留着 |
| **3** | 今天一个同仓库分支的 PR 加一行 `runs-on: [self-hosted, tavotto-lab]`，那个 job 会不会真的派到 `tavotto-ci-01`？管理员是否愿意用一个**空 job**（只 `echo`）在一个临时分支 PR 上实测一次并记录 run 号与 `runner_name`？ | CI04 §2.2 按规则推的答案是「会」；实测把「很可能」变成「是 / 否」。实测本身安全：job 体为空、PR 立刻关闭。**不要用 fork 做这个实验** |
| 4 | org 级 fork PR 审批策略（Settings → Actions → General）是什么？仓库级读到的是 `first_time_contributors` | 决定回头客的 fork PR 会不会自动跑在 self-hosted 上 |
| 5 | 谁有仓库写权限（能开同仓库分支 PR）？是否都开了 2FA（org `two_factor_requirement_enabled=false`）？ | 信任区 C 的边界是「有写权限的人」，这群人有多大、账户多硬，决定上面第 3 问的风险大小 |
| 6 | CI00 的 11 项：16 vCPU / 32 GiB 是宿主总量还是 lab VM 的 guest 配额；hypervisor 是什么、谁有管理面；同宿主还有哪些 VM / 常驻负载；还能分配多少 vCPU / 内存（不用 swap 硬撑）/ 磁盘（IOPS、是否共享盘）；CPU 型号、物理核、超分配比 | 03 §1：预算表「不是已证足够的配置」；宿主容量未核之前 B 组的每个数字都不是可分配量 |
| 7 | 新 VM 的出站规则：GitHub api / pipelines / objects、PyPI、npm、crates、nodejs.org、浏览器 CDN 各通不通；`github.com:443` 是否仍不可达；是否与 lab VM 同网段；是否禁止访问其它实验室主机与 metadata 服务 | 03 §5：文档里的网络限制是历史记录，不是本次实测 |
| 8 | lab VM 现状：uptime、`/srv/tavotto-ci` 各子目录占用、runner 版本（读到四台都是 2.336.0，当前发行 2.337.0——自更新在工作吗？看 `_diag/`）、最近一次 `lab_preflight.py --json` | 分清「lab 机器自身的债」与「新池的需求」 |
| 9 | 有没有从模板克隆 / 快照回滚 / 销毁 VM 的自动化入口（CLI / API）？JIT 注册（`generate-jitconfig`）用谁的凭据、放在哪个控制面？ | 03 §3：一 job 一 VM 要外部控制面保证清洁；凭据不进 VM |
| 10 | runner 服务的登录用户名是 `github-runner`（文档）还是 `runner`（另有记录）？ | `bootstrap_lab_runner.sh --user` 用错会 `useradd` 出新账号并 chown 整个 state root（CI00 已列） |

## B. 若批准试点：操作表（每步写作用对象 / 资源 / 回退 / 验收）——**未批准：用户 2026-09-16 拍板不建池**

> 本段整段**留档不执行**。拍板理由见 CI04 §4（Linux 池对当前瓶颈贡献有限）与 CI05 §5–§7（真正的瓶颈是账户并发上限 20 与 Windows 腿，Linux 池两者都解不了）。将来若重新考虑，从 A-1 / A-6 / A-7 重新答起，不沿用本轮的任何「很可能」。

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

## C. 本仓库侧要配合的改动（只描述，本轮不做）——**随 B 组一并未批准**

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

## D. 演练清单（B-9 之前每条各做一次，记录结论；D-1/D-2 参考 03 §6「不因本地离线就跳过资格」）——**随 B 组一并未批准**

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
| A 组问题 | 18 项待答（`admin_inventory.json`）；A-2 已变成操作项（注销 -2/-3/-4，已拍板待执行）；A-1 / A-3 的答案被 F 组绕开（迁私有仓库后公开仓库不再有 runner） |
| B-1 … B-10 | **未批准**（用户 2026-09-16 拍板不建池），留档 |
| C-1 … C-5 | **未批准**（随 B），留档 |
| D-1 … D-7 | **未批准**（随 B），留档 |
| F-1 … F-12 | F-3 **PR A 已开**（分支 `ci/lab-runner-private-repo-a`）；其余 `not_run`（已拍板，待管理员 + PR B / PR C） |
| `runner_pool_ready` | **`not_run`**；按拍板本轮不会变 pass（没有池，就没有这个状态的主语） |
| 本轮已做 | 只读清点（`evidence/ci04/`）、静态守卫（`TestRunnerTrustZones`，变异 15/15）、`actionlint.yaml` 去掉预留标签、本交接；CI05 把三条拍板落进本文 |

## F. lab runner 迁移到私有仓库（已拍板 2026-09-16，待管理员执行 + 两个后续 PR）

拍板：采用 [`docs/ci/self-hosted-runner.md`](../../ci/self-hosted-runner.md) §1「更安全的备用形态」——runner **注册在一个私有仓库**
（下文叫 `Tavotto/ci-infra`，org 内、private），公开仓库的 PR 天然够不到它；同一台 VM、标签不变（`self-hosted, linux, x64, tavotto-lab`）。
这一刀关掉的是 CI04 §2.2 那个洞（同仓库分支 PR 加一行 `runs-on` 就派到 -01）：runner 不在公开仓库里，PR 写什么 `runs-on` 都只会永远排队。
本节**不含任何 token / 主机名 / IP / 用户名**；所有 token 都是「在设置页现取、只进仓库 secret、不写进文件」。

### F.0 今天哪一段在动（先读代码再动手）

| 文件 | 今天 | 迁移后 |
|---|---|---|
| `.github/workflows/_lab-qualification.yml`（`workflow_call`，`runs-on: [self-hosted, linux, x64, tavotto-lab]`） | 被同仓库的 `lab-ci.yml::qualify` 与 `release.yml::lab_release_gate` 调用；`actions/checkout` 用 `ref: inputs.sha`（默认 `repository` = 调用方仓库）；发行档 `download-artifact` 取**同一 run** 的 `dist` | 仍是唯一定义，但调用方变成私有仓库的 workflow（`uses: Tavotto/Tavotto/.github/workflows/_lab-qualification.yml@main`——reusable 的 job 在**调用方**上下文里跑，`runs-on` 在 ci-infra 的 runner 里解析；`uses:` 不能带表达式，所以 workflow **定义**取 main、被验的**代码**取 `inputs.sha`）。要改两处：① checkout 显式 `repository: Tavotto/Tavotto`（两种调用方都成立）；② 发行档的 `download-artifact` 加 `repository` / `run-id: inputs.source_run_id \|\| github.run_id` / `github-token: secrets.TAVOTTO_PUBLIC_TOKEN`（跨仓库取 `dist`，v4 支持；token 是 ci-infra 的 secret，经 `workflow_call` 的 `secrets:` 传入，公开仓库这一侧不需要它）。**`github-token` 刻意不兜 `\|\| github.token`**：download-artifact@v4 只在 token 非空时切到 REST 路径（源码 `if (inputs.token)`），空串走今天的同 run 内部路径；而 reusable 的 GITHUB_TOKEN 只有 `contents: read`、没有 `actions: read`，兜上去同仓库调用的发行档会在下一次发版时 403（合同 `test_the_reusable_pins_the_public_repository_for_cross_repository_callers` 钉的是三个字段的字符串相等）。`permissions: contents: read` 不动（`test_the_reusable_workflow_needs_no_write_permission`） |
| `.github/workflows/lab-ci.yml`（push main / schedule / dispatch → `trust-check` → `qualify`） | `qualify` 直接 `uses: ./.github/workflows/_lab-qualification.yml` | `trust-check` 不动（ancestry 判断留在公开仓库 hosted 上）；`qualify` 改成 **`dispatch`** job（hosted）：`gh workflow run lab-qualification.yml -R Tavotto/ci-infra -f sha=<trust 的输出> -f mode=… -f baseline_tag=…`，token 是公开仓库的 secret（fine-grained，只对 ci-infra 的 `actions: write`）。**它不等结果**：lab-ci 是合并后的深度观察，不是资格；结论回到公开仓库的方式见 F.1 ③。两条 cron 可以留在这里（由 dispatch 转发）或搬去 ci-infra——建议留在这里，`trust-check` 仍是唯一入口 |
| `.github/workflows/release.yml::lab_release_gate`（`needs: [build, trust]` → 同步 `uses` reusable → `validate_artifacts` needs 它） | 发行门禁**同步**等 lab 结论；`validate_artifacts` / `github_release` / `pypi` / `plugin_stable` 都在它后面 | **公开仓库不能等私有仓库**——本仓库禁止轮询（`tests/test_release_workflow_contract.py::test_no_sleep_polling_loops_anywhere_in_the_release_chain` / `test_no_workflow_polls_for_a_github_release`，理由：lab 排队时间没有上界）。所以发布链要**在 lab 门禁处切成两段**（F.2）：第一段 `trust → build / desktop → dispatch lab`，第二段由 ci-infra 在 lab 绿了之后 dispatch 回来（`release-publish.yml`，只有 `workflow_dispatch`），第二段先重跑 trust（ancestry + tag + **blocker 签字**）再 `validate_artifacts` → 发布 |
| `.github/actionlint.yaml` | 登记 `tavotto-lab` | **公开仓库不再有任何 `runs-on` 用它**（reusable 里那一行仍在——它是被 ci-infra 调用时才解析的）。合同 `TestRunnerTrustZones` ④ 要求「登记的 == 用到的」，reusable 里的那一行算「用到」，登记不变；② 要求 `tavotto-lab` 只在 reusable、调用方只有 `lab-ci.yml` / `release.yml`——迁移后公开仓库里**没有**调用方，这条与 `test_qualification_is_defined_exactly_once` / `test_every_caller_gates_the_sha_through_a_trust_job` 都要改主语（见 F.3） |

### F.1 操作表

| 步 | 操作 | 作用对象 / 资源 | 回退 | 验收命令（只读） |
|---|---|---|---|---|
| F-1 | 在 org 内建**私有**仓库 `Tavotto/ci-infra`：默认分支保护（只维护者可写）、Actions 开启、`default_workflow_permissions = read`、fork PR 审批无关（私有仓库没有 fork PR）；仓库里只放 workflow + 文档，**不放产品代码** | 新仓库 | 删仓库 | `gh repo view Tavotto/ci-infra --json visibility,isPrivate`（private）；`gh api repos/Tavotto/ci-infra/actions/permissions/workflow`（read） |
| F-2 | ci-infra 的 `lab-qualification.yml`：`on: workflow_dispatch`（inputs `mode` / `sha` / `baseline_tag` / `use_prebuilt_dist` / `source_run_id`）。job ① `trust-check`（hosted）：checkout `Tavotto/Tavotto`（`fetch-depth: 0`）、`git merge-base --is-ancestor <sha> origin/main` 或 tag 指向——与今天 `lab-ci.yml::trust-check` 同一段 shell（**照抄，不是引用**：调用方的信任判断必须在调用方自己手里，`test_every_caller_gates_the_sha_through_a_trust_job` 的理由）；job ② `qualify`：`uses: Tavotto/Tavotto/.github/workflows/_lab-qualification.yml@main`（`uses:` 不接受表达式，钉不到被验的那个 SHA；定义取 main 上那份——main 已过 review；被验的**代码**仍是 `inputs.sha`，由 reusable 里的 checkout 取；公开仓库可读，不需要 token），`secrets: inherit`；job ③ `report`（hosted，`needs: qualify`，`if: always()`）：用 ci-infra 的 secret（fine-grained，只对 `Tavotto/Tavotto` 的 `statuses: write`）给那个 SHA 打一条 commit status（context `lab/<mode>`，state = qualify 的结论，target_url = 本 run），并上传汇总 artifact。reusable 仍是 `contents: read`，写权限只在 ci-infra 的 ③ 里 | ci-infra 仓库 + 一个 secret `TAVOTTO_PUBLIC_TOKEN`（fine-grained PAT，只对 `Tavotto/Tavotto`：Actions read/write + Commit statuses write；reusable 靠它跨仓库取 `dist`，`report` 靠它回写 status；**名字被两边 workflow 共同钉死**——reusable 的 `workflow_call.secrets` 就叫这个名，**值不进任何文件**）；公开仓库那侧对应 `TAVOTTO_CI_INFRA_TOKEN`（只对 `Tavotto/ci-infra`：Actions write） | 删 workflow | `gh workflow list -R Tavotto/ci-infra`；一次手动 dispatch（`mode=main`，sha = main 当前）在 ci-infra 有结论 run，且 `gh api repos/Tavotto/Tavotto/commits/<sha>/status` 里出现 `lab/main` |
| F-3 | 公开仓库 PR A（**先合**）——**PR 已开（分支 `ci/lab-runner-private-repo-a`）**：`_lab-qualification.yml` 的两处改动（F.0 第一行）+ 并发槽名加 `inputs.mode`；`lab-ci.yml::qualify` → `dispatch`（secret 为空退 1，不许静默跳过）；合同测试改主语（F.3）。**这个 PR 合入时 runner 还在公开仓库上**，所以 `lab-ci.yml` 的 dispatch 会打到 ci-infra、而 ci-infra 的 runner 还没注册——F-4 之前 lab-ci 会排队；顺序上 F-4 紧接着做。**合入前公开仓库要先配好 secret `TAVOTTO_CI_INFRA_TOKEN`**（没配的话合入后第一次 push main 的 lab-ci 就红在 dispatch——那是设计如此，不是故障） | `.github/workflows/` + `tests/` | `git revert` | 合同测试全绿；`actionlint` 0 |
| F-4 | **在同一台 VM 上给 ci-infra 注册第二个 runner 实例**（新目录，例如 `~/actions-runner-infra`；`./config.sh --url https://github.com/Tavotto/ci-infra --token <注册 token> --name tavotto-lab-01 --labels self-hosted,linux,x64,tavotto-lab --work _work --unattended`；装成服务）。公开仓库的 `-01` **先不动**：并行期两个实例各只接自己仓库的 job，同机互斥靠 `/srv/tavotto-ci` 的 flock 与 `lab_preflight.py --reap-stale`（`self-hosted-runner.md` §7「并发度」）。**A-2 的注销（-2/-3/-4）在这之前做完**，机器上别再有第三个入口 | 同一台 lab VM | `./svc.sh stop && ./svc.sh uninstall && ./config.sh remove` | `gh api repos/Tavotto/ci-infra/actions/runners --jq '.runners[] | [.name, .status, ([.labels[].name] | join(","))]'`（online，含 `tavotto-lab`） |
| F-5 | 在 ci-infra 手动 dispatch 一次 `mode=nightly`（sha = main 当前）；再等一次 cron（或由 `lab-ci.yml` 的 push main 触发一次 dispatch）。**判据**：ci-infra 的 run 结论 success，`compat.json` / soak / benchmark 报告与迁移前那台机器上最近一次 nightly 的形状一致（同一台机器，`/srv/tavotto-ci` 的基线不变，benchmark 不该漂） | ci-infra run | — | `gh run list -R Tavotto/ci-infra --workflow lab-qualification.yml`；公开仓库 `gh api repos/Tavotto/Tavotto/commits/<sha>/status --jq '.statuses[] | select(.context | startswith("lab/"))'` |
| F-6 | 公开仓库 PR B：发布链切两段（F.2）+ 合同测试。**合入前用 `publish=false` 在 PR 分支上不能演练**（release.yml 只认 tag / dispatch 且 dispatch 要 main 祖先）——所以 PR B 合入后立刻做 F-7 | `.github/workflows/release.yml` + 新 `release-publish.yml` + `tests/test_release_workflow_contract.py` | `git revert` | 合同测试全绿 |
| F-7 | **发布演练**：`workflow_dispatch(ref=<main 当前 SHA>, publish=false)` → 第一段跑完并 dispatch ci-infra（`mode=release`, `use_prebuilt_dist=true`, `source_run_id=<第一段 run id>`）→ ci-infra 取到 `dist` artifact、`lab_release_gate` 绿、`report` 回写 `lab/release` status 并 dispatch `release-publish.yml(sha, source_run_id, lab_run_id, publish=false, ack_open_blockers=<原样>)` → 第二段 trust 重验 + `validate_artifacts` 全部产物真实存在并校验、最后不发布。**三个 run 都要有结论** | 三条 run | — | 第一段 `gh run view <id> --json conclusion`；ci-infra run success；第二段 run success 且 summary 里有「演练（publish=false）到此为止」 |
| F-8 | **注销公开仓库的 `tavotto-ci-01`**（F-5 与 F-7 都绿之后）：在它的实例目录 `sudo ./svc.sh stop && sudo ./svc.sh uninstall && ./config.sh remove --token <REMOVAL_TOKEN>`；删掉那把只给公开仓库用的 Deploy key（`self-hosted-runner.md` §9「停用 / 移除」） | lab VM 上公开仓库那个实例 | 重新注册（§7） | `gh api repos/Tavotto/Tavotto/actions/runners --jq .total_count` = **0**（-2/-3/-4 已在 A-2 注销）；ci-infra 的 runners 仍 online |
| F-9 | 公开仓库 PR C：`.github/actionlint.yaml` 与守卫 ② ④ 的主语按 F.3 收口；`docs/ci/self-hosted-runner.md` §7 的注册命令改成 ci-infra 的 URL、§1 把「备用形态」改成「现行形态」；`docs/ci/release-qualification.md` 加两段链路 | 文档 + 测试 | `git revert` | `pytest tests/test_merge_queue_workflows.py tests/test_release_workflow_contract.py tests/test_docs_references.py` 0 |
| F-10 | 一次 A-3 的反向实测（可选，安全）：临时分支 PR 里一个 `runs-on: [self-hosted, tavotto-lab]` 的空 job（只 `echo`）→ 预期 **永远 queued**（公开仓库没有这个标签的 runner）→ 关 PR。这是 CI04 §2.2 那个洞关掉的直接证据；把 run 号记进 `evidence/ci04/` | 临时 PR | 关 PR | `gh api …/runs/<id>/jobs --jq '.jobs[].status'` = queued；关 PR 后 run 被 cancel |
| F-11 | 更新 `admin_inventory.json`：runner 注册层级（仓库级 → 私有仓库级）、实例数（1）、A-1 / A-3 标「被 F 绕开」 | 文档 | — | JSON 解析 |
| F-12 | `runner_pool_ready` 保持 `not_run`（这不是池）；CIP-024「PR 无法通过修改 runs-on 接触可信 lab」在 F-8 + F-10 之后可以改 pass——证据 = 公开仓库 runners `total_count` 0 + F-10 的 queued run | `acceptance.json` | — | — |

### F.2 发布链切两段（PR B 的设计；不实施，只写清）

```
release.yml（tag push / workflow_dispatch）                       ci-infra lab-qualification.yml            release-publish.yml（只 workflow_dispatch）
trust（ancestry + tag + blocker 签字）                            trust-check（同一段 ancestry 判断）         trust2（同一段 ancestry + tag 判断 +
  → build / desktop（hosted，产物 upload）          ──dispatch──▶  qualify（reusable@sha，取 dist by run-id）     再核一次 open blocker 与传回来的 ack）
  → dispatch_lab（hosted：gh workflow run …）                      report（status lab/release + ──dispatch──▶  → validate_artifacts（按 source_run_id 取
                                                                    gh workflow run release-publish.yml）        第一段的产物，同仓库、无需 token）
                                                                                                             → github_release / pypi / plugin_stable（publish 门）
```

- **哪些不变**：`trust` 的三条判断（精确 SHA、`origin/main` 祖先或 tag、release-blocker 签字）、`build` / `desktop`、`validate_artifacts` 之后的全部步骤、`publish=false` 演练语义、`plugin_stable` 通道。
- **blocker 签字怎么保持**：`ack_open_blockers` 在第一段 `trust` 消费一次（今天的行为）；第二段 `trust2` **再核一次**——输入里带着第一段传下来的 ack 列表，对着**当时** open 的 `release:blocker` 重新跑 `scripts/ci/release_blockers.py`：lab 跑的那一两个小时里新开的 blocker 会让第二段停在 trust2，而不是无声发出去。第二段不新增签字入口——它只认第一段的 ack；要签新 blocker 就从第一段重来。
- **publish 怎么保持**：tag 触发 → 第一段 `trust` 解析出 `publish=true`，随 dispatch 载荷传到 ci-infra 再传回第二段；第二段 `trust2` 不信载荷里的 `publish`，**按同一规则重算**（有 tag 指向该 SHA 且 tag 名与 `pyproject` 版本一致 → true；dispatch 的 `publish=false` 只能把 true 压成 false，不能反过来）。`test_every_publishing_job_is_gated_on_publish` 的主语扩到 `release-publish.yml`。
- **谁能 dispatch 第二段**：只有持 `TAVOTTO_PUBLIC_TOKEN`（ci-infra 的 secret，fine-grained，对 `Tavotto/Tavotto` 的 `actions: write` + `statuses: write`——与取 `dist` / 回写 status 是同一枚）的 ci-infra `report` job；但第二段**不信任「谁触发」**——它重验 SHA、重验 blocker、重读 `lab/release` status（一次 API 调用，不是循环）并要求 state = success 且 `target_url` 指向 `lab_run_id` 那个 run。手工 dispatch 第二段而 lab 没绿，就停在 trust2。
- **失败形状**：ci-infra 的 lab 红 → `report` 打 `lab/release` = failure、**不 dispatch** 第二段——第一段的 run 已经 success 结束（它只负责到 dispatch），发布「没有发生」是靠第二段从未开始；所以 release 的可观测结论从「一个 run 红」变成「`lab/release` status 红 + 没有第二段」。`docs/ci/release-qualification.md` 要把这一点写给读结论的人。
- **代价**：release 相关合同测试要重写主语（下一行）；`release.yml` 从「一条链」变成「一条链 + 一个回调」，`test_the_tag_has_exactly_one_entry_point` 仍成立（`release-publish.yml` 没有 `push: tags`）。

### F.3 合同测试要一起改的主语（PR A / PR B / PR C 各自的红灯）

| 用例 | 今天钉的 | 迁移后钉什么 |
|---|---|---|
| `test_qualification_is_defined_exactly_once` | `lab-ci.yml::qualify` 与 `release.yml::lab_release_gate` 都 `uses` reusable、不带自己的 steps | reusable 仍唯一；判两个**集合相等**：调用方（`uses` reusable）与派发方（`gh workflow run lab-qualification.yml -R Tavotto/ci-infra`）。**PR A（并行期）**：调用方 == {`release.yml::lab_release_gate`}，派发方 == {`lab-ci.yml::dispatch`}；**PR B**：调用方 == ∅，派发方加 `release.yml::dispatch_lab`——两张表 `_REUSABLE_CALLERS` / `_DISPATCHERS` 一起改 |
| `test_every_caller_gates_the_sha_through_a_trust_job` | 每个 `uses` reusable 的 job 的 `sha:` 来自带 `--is-ancestor` 的 job | 主语 = **调用方 ∪ 派发方**（PR A 已改）：`uses` 的 `sha:`、派发的 `-f sha=`（允许经**一层** job `env:` 间接，再多就抛）都要解析到 `needs.<job>.outputs.sha`，`<job>` 含 `--is-ancestor`；ci-infra 那边的 trust-check 由 ci-infra 自己的合同测试钉（私有仓库要有自己的 `tests/`，把这条用例抄过去） |
| `test_the_release_gate_cannot_be_evicted_by_a_routine_lab_run` | reusable 的 `concurrency.group` 含 `github.workflow` | ci-infra 的 `lab-qualification.yml` 一个 workflow 服务四档——槽名**同时**含 `github.workflow` 与 `inputs.mode`（PR A 已改：`lab-qualification-${{ github.workflow }}-${{ inputs.mode }}`），并行期同仓库的 release.yml 仍各排各的、ci-infra 上 release 与 nightly 不共槽；用例判 group 恰好一处且两个片段都在 |
| `TestRunnerTrustZones` ② | `tavotto-lab` 只在 reusable，调用方 == {lab-ci.yml, release.yml}，事件 ⊆ {push, schedule, workflow_dispatch} | `tavotto-lab` 只在 reusable；reusable 的事件仍 == {workflow_call}；`uses` 它的 workflow 集合 == `_REUSABLE_CALLING_WORKFLOWS`、派发 ci-infra 的 == `_DISPATCHING_WORKFLOWS`（PR A：{release.yml} / {lab-ci.yml}；**PR B 后：∅ / {lab-ci.yml, release.yml}**——F-8 注销公开仓库 runner 的前提），两类的事件都 ⊆ {push, schedule, workflow_dispatch} |
| `TestRunnerTrustZones` ④ | `actionlint.yaml` 登记 == 实际用到的自托管标签 | 不变（reusable 里那一行仍算用到）；若最终把 reusable 也搬去 ci-infra，登记与 reusable 一起走 |
| `test_no_sleep_polling_loops_anywhere_in_the_release_chain` | release / desktop / lab / reusable 里无 `sleep N` / `seq 1 NN` | 不变，且要把 `release-publish.yml` 加进被扫的文件集合——第二段读 status 只许一次 API 调用 |
| `test_every_publishing_job_is_gated_on_publish` / `test_release_only_uses_the_sha_that_trust_resolved` / `test_the_dry_run_still_exercises_every_verification_step` | `release.yml` 一个文件 | 主语扩到 `release-publish.yml`：发布 job 在第二段，`publish` 由 `trust2` 重算，SHA 只用 `trust2` 的输出 |

### F.4 切换顺序与回退（一句话版）

顺序：A-2 注销闲置实例 → F-1 建仓库 → F-2 ci-infra workflow + 两个 secret → F-3 PR A 合入 → **F-4 第二个实例注册（并行期开始）** → F-5 nightly 在 ci-infra 绿 → F-6 PR B 合入 → F-7 `publish=false` 三段演练绿 → **F-8 注销公开仓库实例（并行期结束）** → F-9 PR C 收口 → F-10 反向实测 → F-11 / F-12 记账。
每一步的回退都是「上一步的逆」：删仓库 / 删 workflow / `git revert` / `config.sh remove` / 重新注册。**F-8 之前任何一步失败，公开仓库的 lab 都还在原样工作**——这是并行期的意义。
