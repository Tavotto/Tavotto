# v1.0 发布流程事实审计（2026-08-22）

> 这份文档只记录**核实过的事实**与**从事实推出的判断**，不记录计划。
> 每一条数字都注明取数命令，任何人可以复算。
> 计划在 `docs/engineering/p2-lifecycle.md`、`docs/engineering/p2-fix-train.md`、
> `docs/engineering/codex-review-policy.md`，退出条件在 `docs/1.0-release-readiness.md`。

取数时刻：2026-08-22T14:35Z。取数身份：`gh auth status` → `erwanjun`，
scopes `gist, read:org, repo, workflow`；
`gh api repos/Tavotto/Tavotto --jq .permissions` 回 **`admin: true`**
（用一次幂等 PUT 实测确认过写权限可用）。
**本轮没有修改任何 ruleset**——理由见 `docs/admin/github-ruleset-changes.md` §2。

---

## 1. 当前状态

| 项 | 值 | 取数 |
|---|---|---|
| `origin/main` | `eeb05b342aab5daa447535329e0e86bd4be6ef18`（`eeb05b3`） | `git rev-parse origin/main` |
| 版本号 | `0.9.1` | `src/tavotto/__init__.py:6` |
| 最新 tag | `v0.9.1`、`v0.9.0` | `git tag --sort=-creatordate` |
| **最新已发布 Release** | **`v0.8.0`（2026-08-20T06:22Z）** | `gh release list` |
| 工作区 | 干净 | `git status --short` |

> **v0.9.0 与 v0.9.1 这两个 tag 都存在，却都没有对应的 GitHub Release。**
> 它们是两次正式发布演练的残骸：tag 推上去了，`release.yml` 在
> `lab_release_gate` 挂掉，`github_release` 与 `pypi` 跟着 skip。
> tag ruleset 是 immutable（`update` / `deletion` 均禁），所以这两个 tag
> 现在既不能移动也不能删除——**正式 tag 已经两次被当成发布链的第一次测试**。
> 这是本轮 §7「发布链单一编排 + publish=false 演练」的直接动因。

### 1.1 最近两天的 main 提交

`git log --oneline --since="2 days ago" origin/main` → 31 条。全部是 CI /
发布链修复与版本号提交，**没有产品功能提交**。功能面在 2026-08-21 的
PR #48/#49 之后事实上已经冻结。

---

## 2. 当前 open PR

`gh pr list --state open`（4 条，**全部是 Ready、没有一条是 Draft**）：

| PR | 分支 | 主题 | 类别 | mergeState |
|---|---|---|---|---|
| #53 | `ci/bootstrap-lab-runner-fixes` | 实验室 runner bootstrap 的三个 bug | CI harness（runner bootstrap） | DIRTY |
| #59 | `docs/predicate-subject-discipline` | CLAUDE.md 加「判据的主语」一节 | 文档 + 两条扫描用例 | BEHIND |
| #61 | `ci/summary-step-needs-an-interpreter` | 汇总步骤在有失败要汇总时自己挂掉 | CI harness（release 最后一公里） | DIRTY |
| #62 | `ci/desktop-must-outwait-the-lab-gate` | 桌面链等 Release 的超时 | CI harness（release 最后一公里） | BLOCKED |

`#63`（SBOM glob）**已于本次审计前合并**进 main，提示词里把它列为待处理是过期信息。

`#53` 与 #61/#62 **不是同一类**：#53 改的是 runner 自身的 bootstrap 脚本
（`scripts/ci/bootstrap_lab_runner.sh`、`lab_preflight.py`），
#61/#62 改的是 workflow 里发布链的最后一公里。两者应保持独立（§6）。

---

## 3. 当前 open issue 与里程碑

`gh issue list --state open` → 11 条。`gh api .../milestones` → 1 个里程碑
`1.0-blocker`（open=8 / closed=3）。

| Issue | milestone | 分级（标题里的） | 说明 |
|---|---|---|---|
| #30 | 1.0-blocker | P1 i18n | 非中文系统默认英语；后端错误全量 code |
| #31 | 1.0-blocker | P1 E2E | WebKit/英文 locale + 真 Tauri 窗口验收 |
| #32 | 1.0-blocker | P1 供应链 | tag 可达 main + 最小权限 + provenance |
| #33 | 1.0-blocker | P1 兼容 | Python 元数据与实测范围一致；3.14 |
| #34 | 1.0-blocker | P1 分发 | 支持矩阵收敛到真实承诺 |
| #35 | 1.0-blocker | P1 分发 | 发行构建签名/更新器强制 |
| #37 | 1.0-blocker | P1 可访问性 | 自动化可访问性门禁 |
| #38 | 1.0-blocker | P1 Codex | 真实 Codex Desktop 里验收 MCP 插件与内嵌画布 |
| #39 | **无** | P2 文档 | 插件/发版/支持文档与实现对齐 |
| #40 | **无** | P2 流程 | 每条 PR review thread 必须有 disposition |
| #46 | **无** | Flaky/Windows | 写回 verify 的一次性 worker 目录偶发泄漏 |

**三个事实缺陷**：

1. **11 条 issue 一个标签都没有。** `gh issue list --json labels` 全部返回 `[]`。
   仓库标签集只有 GitHub 默认那 10 个 + `dependencies` / `python`
   （`gh label list`）——**没有 severity / area / disposition 任何一维**。
   于是「unresolved P1 = 0」这条退出条件**没有任何可查询的判据**，只能靠读标题。
2. **#39 / #40 / #46 三条没有里程碑**，即「记下了，但没有下一步」。
3. #30–#35、#37 这些 P1 对应的 PR（#27/#41/#42/#43/#44/#45）**都已经合并**，
   issue 却仍然 open。它们要么该关，要么该说清还差什么——两种都行，
   「合了但不知道算不算完」不行。

---

## 4. 产品 Bug 与 CI harness Bug 的分类

把最近 15 条 PR 按「改的是用户拿到的东西，还是我们验证它的东西」分开：

| 类别 | PR | 结论 |
|---|---|---|
| **产品** | #47（playground 转化/可信度）、#48（Artist family）、#49（CompatBench + 它找出的 9 个产品缺陷）、#41（Magplot 迁移）、#42/#43（i18n/a11y 修复） | 6 条 |
| **CI harness** | #52、#54、#55、#56、#57、#58、#61、#62、#63、#53 | **10 条** |
| **发布/版本** | #51、#60 | 2 条 |

> **本轮的缺陷重心已经完全移出产品。** 最近 10 条 CI harness PR 里，
> 有 6 条的症状是同一个形状：**门禁或诊断在最需要它的时候失灵**
> （#54 空转的 slow 门禁、#55 从没跑过的升级验收、#57 报错信息是空的、
> #58 卡死在写日志、#61 汇总自己挂掉、#63 SBOM 拿到的是 glob 字面量）。
> 这正是 `docs/1.0-release-readiness.md` §1 已经写下的 P1 判据
> 「release CI 门禁**空转**」。产品侧同期的新缺陷是 CompatBench 一次性
> 找出的 9 条，且**已经修完并进了基线**。

---

## 5. 工作流执行真实性

判据：**queued / cancelled 不算一次有效验证。** 只数
`status == completed` 且 `conclusion` 有值的 run。

`gh run list --workflow <f> --limit 100 --json conclusion`：

| workflow | 最近一次**有结论** | 最近一次 **success** | 结论分布（近 100 次） |
|---|---|---|---|
| `ci.yml` | 2026-08-22 ✅ | 2026-08-22 | 健康 |
| `codeql.yml` | 2026-08-22 ✅ | 2026-08-22 | 健康 |
| `telemetry-metrics.yml` | 2026-08-22 ✅ | 2026-08-22 | 健康 |
| `lab-ci.yml` | 2026-08-22 ❌ failure | **从来没有过** | **success=0 / failure=8 / cancelled=17** |
| `release.yml` | 2026-08-22 ❌ failure | 2026-08-20（`v0.8.0`） | v0.9.0、v0.9.1 两次 tag 均失败 |
| `desktop-tauri.yml` | 2026-08-22 ❌ failure | 2026-08-20（`v0.8.0`） | 同上 |
| `nightly.yml` | 2026-08-21 ❌ failure | 2026-08-19（**且是 workflow_dispatch，不是 schedule**） | schedule 腿连续 4 晚失败 |

### 5.1 从未真正执行过的 job / step

- **`lab-ci.yml` 的 `qualify` 从未跑完过任何一次。** 25 次 run 里 17 次是
  cancelled（并发槽 `lab-qualification` 被顶掉），8 次 failure——而 8 次
  failure **全部**停在第一步「开跑前体检」，后面 14 个步骤一次都没执行。
  也就是说 **slow 用例、包验收、升级验收、Golden 视觉回归、CompatBench、
  soak、性能回归、mutation testing 这八道门禁，在这条通道上一次都没有真正跑过**。
- **`release.yml` 的 `github_release` / `pypi` 自 v0.8.0 起没有成功执行过。**
  v0.9.0 / v0.9.1 两次都因为 `lab_release_gate` 失败而 skip。
- **`desktop-tauri.yml` 的「挂到 Release」步骤**同理：它等的那个 Release
  从来没被建出来过。

### 5.2 根因（读日志读出来的，不是推测）

`gh run view 32574417897 --log-failed`：

**根因 A —— 实验室 runner 上有一个上一轮留下的进程，且没有任何自愈。**

```
[FAIL] 上一轮遗留的 Tavotto 进程: 1 个：pid=127486
       /srv/tavotto-ci/tmp/venv-manual-probe/bin/python -m tavotto --port 50415 …
       → 确认无人正在用后 kill 掉，或跑 scripts/ci/cleanup.py --kill-stale
```

体检本身是对的（残留进程确实会污染 soak 与 benchmark）。问题在**顺序**：
`cleanup.py`（会 kill 残留）排在体检**之后**，于是一个手工探测留下的进程
把之后**每一次** lab run 都挡在门外，而唯一的解法是有人 SSH 上去手动清。
从 2026-08-22 05:55 起连续 8 次失败全部是它。

**根因 B —— 汇总步骤在真的有失败要汇总时自己挂掉。**

```
Run  scripts/ci/summarize.py --mode "$LAB_MODE"
/…/ffd831e3….sh: line 1: scripts/ci/summarize.py: Permission denied
##[error]Process completed with exit code 126
```

写法是 `${{ steps.venv.outputs.python }} scripts/ci/summarize.py`。
体检先失败 → 「建验证环境」没跑 → `steps.venv.outputs.python` 是空串 →
命令退化成**直接执行**脚本，而它是 `100644`（没有执行位）→ 126。
**这是 PR #61 的主题，且已被生产日志证实。**

**根因 C —— 跨 workflow 轮询。**
`desktop-tauri.yml:659` `for i in $(seq 1 380); … sleep 30`（190 分钟）
等 `release.yml` 建出 Release。PR #62 把它从 10 分钟调到 190 分钟并补了
最后一探——**都是对的修，但修的是症状**。两个由同一个 tag 独立触发的
workflow 互等，本身就是错的拓扑（§7）。

**根因 D —— SBOM 把 glob 当单值路径喂给 syft。** 已由 #63 修复并合入。

---

## 6. 当前 release pipeline 依赖图

```
git push origin v*                      ← 同一个 tag，两个独立 workflow 各自触发
  │
  ├── release.yml
  │     trust ──────────────► build ──────────────► lab_release_gate
  │     (tag→SHA，验可达 main)  (wheel/sdist)        (self-hosted, tavotto-lab)
  │                                                      │
  │                                          ┌───────────┴───────────┐
  │                                          ▼                       ▼
  │                                    github_release             pypi
  │                                    (建 Release，挂                (OIDC，environment
  │                                     wheel/sdist/SBOM/             `pypi` 有
  │                                     provenance/plugin)            required_reviewers)
  │
  └── desktop-tauri.yml
        trust ──► workerd ──► build (matrix: windows / macos)
                                  │
                                  ├─► 上传 workflow artifact（签名+公证，安全）
                                  │
                                  └─► ⚠️ 轮询 `gh release view` 至多 190 分钟
                                          等 release.yml 建出 Release
                                          │
                                          └─► 挂到 Release ──► updater-manifest
```

**这张图有五个结构性问题：**

1. **跨 workflow 轮询**（虚线那条）。等待时间没有上界——`lab_release_gate`
   的排队时间本身没有上界（等 `lab-qualification` 并发槽 / 等一台带
   `tavotto-lab` 标签的 runner）。#62 的注释自己写明了「没有任何固定上限是够的」。
2. **两条链各自 `trust`，各自 checkout。** 虽然都验了 tag 可达 main，
   但 wheel 与桌面产物**没有任何机制保证来自同一个 SHA**——只是碰巧同一个 tag。
3. **没有 publish=false 演练模式。** `workflow_dispatch` 的 `tag` 是必填，
   且总会走到 `github_release`。想验证这条链，唯一办法就是推一个正式 tag
   ——v0.9.0 与 v0.9.1 就是这么烧掉的。
4. **没有 artifact manifest。** 下游各步骤各自猜文件名
   （`dist/*.whl`、`out/*`），#63 那个 bug 正是这么来的。
5. **资格验证重复定义。** `release.yml` 的 `lab_release_gate`（10 个 step）
   与 `lab-ci.yml` 的 `qualify`（14 个 step）是两份手抄的 shell，
   同名步骤「开跑前体检 / 清理上一轮残留 / slow / 升级验收 / Golden /
   CompatBench / soak / 性能回归 / 汇总」逐条重复。#61 必须**同时改两处**
   才能修好一个 bug，这就是重复的代价。

---

## 7. Codex Review 现状

统计口径：GraphQL 拉全部 PR 的 `reviews`（作者 = `chatgpt-codex-connector`）
与 `reviewThreads`，按 badge 正则 `!\[(P[0-3]) Badge\]` 分级。
脚本见本文件末尾 §10。

**总量：113 次 review 提交，188 条 thread。**

| 严重度 | 条数 | 占比 |
|---|---|---|
| P0 | **0** | 0% |
| P1 | 37 | 19.7% |
| P2 | **151** | **80.3%** |

**轮次分布（每条 PR 上 Codex 提交了几次 review）：**

| PR | 轮次 | commit 数 | thread | unresolved |
|---|---|---|---|---|
| #48 | **18** | 18 | 29 | 0 |
| #53 | **15** | 15 | 28 | **2** |
| #47 | 6 | 6 | 7 | 0 |
| #7 | 6 | 6 | 9 | 2 |
| #49 / #56 / #27 | 5 | 5 | 9 / 6 / 7 | 0 / 0 / 7 |
| #59 | 4 | 4 | 4 | 1 |
| #61 | 3 | 3 | 4 | 1 |
| #62 | 2 | 2 | 3 | 0 |
| 其余 22 条 | 1–3 | — | — | — |

**三个结论：**

1. **轮次 == commit 数，无一例外。** Codex 在**每一次 push** 上都跑一遍。
   #48 与 #53 各自被 review 了 15–18 轮——这不是「深入评审」，
   这是修一条、触发一轮、再修一条的循环。
2. **80% 的发现是 P2，且 P0 是零。** 说明这个工具的边际产出已经从
   「拦住会出事的东西」滑到「补齐边界情况」。它仍然有价值（37 条 P1
   里有真的），但**不该由它决定 PR 什么时候能合**。
3. **已合并的 PR 里有 78 条 thread 至今仍是 unresolved**，分布在 19 条 PR 上
   （#8 有 16 条、#41 有 8 条、#27 / #11 / #10 各 7 条、#15 有 5 条……）。
   全仓库未处置 thread 合计 82 条，其中只有 4 条在还开着的 PR 上——
   也就是说 **95% 的未处置发现挂在已经合并的 PR 上，不会再有人回头看**。
   issue #40 已经写下「每条 thread 必须有 disposition」这条规矩，
   **但它没有任何执行机制**——这正是 §3 说的「记下了，没有下一步」。

**触发方式：** Codex Review 是 GitHub App（`chatgpt-codex-connector`）
在仓库外配置的，**仓库里没有任何文件控制它**
（`grep -rn codex .github/` 只匹配到发布插件的产物名）。
因此关掉「每次 push 自动 review」只能在 ChatGPT / Codex 侧设置，
见 `docs/admin/codex-review-settings.md`。

---

## 8. GitHub 远程治理现状

`gh api repos/Tavotto/Tavotto/rulesets`（**两条 ruleset，均 active**）：

### 8.1 `main: PR + required checks`（id 21121430）

- 目标 `~DEFAULT_BRANCH`，**`bypass_actors: []`——管理员同样受约束** ✅
- `deletion` / `non_fast_forward` 禁止 ✅
- `pull_request`：`required_approving_review_count: 0`、
  **`required_review_thread_resolution: true`** ✅（thread 未 resolve 挡合并）
- `required_status_checks`：`strict_required_status_checks_policy: true`，
  **17 条**必需检查

**核对 17 条 check 是否都真实存在**（对 main 最近一次 CI run 的 job 名逐条比对）：

| 必需 check | main 上是否产出过结论 |
|---|---|
| `backend (ubuntu-latest, 3.10 / 3.13)`、`backend (macos-latest, 3.13)`、`backend (windows-latest, 3.13)` | ✅ |
| `frontend`、`workerd`、`compat-smoke`、`invariants` | ✅ |
| `package (ubuntu / macos / windows-latest)` | ✅ |
| `windows-exe-smoke`、`macos-app-smoke` | ✅ |
| `CodeQL (actions / javascript-typescript / python / rust)` | ✅ |

**17 条全部核对通过，没有幽灵 check。** 这是 §4.7 那条纪律
（先让 job 在 main 上产出结论，再登记）执行到位的结果。

**Lab Qualification 不在必需检查里**——这是对的：它一次都没成功过，
登记进去会锁死整个仓库。但它同时意味着**那八道门禁目前不挡任何东西**。

### 8.2 `release tags: immutable`（id 21121449）

- 目标 `refs/tags/v*`，`deletion` / `update` / `non_fast_forward` 全禁 ✅
- `bypass_actors: []` ✅

**代价已经兑现**：v0.9.0 / v0.9.1 两个失败演练留下的 tag 现在改不动也删不掉。
规则本身是对的（可移动的 release tag 是供应链漏洞），
**要改的是「别拿正式 tag 做第一次演练」**，不是放松这条规则。

### 8.3 备份

审计当时的两份 ruleset JSON 已存档：`docs/admin/rulesets/`
（`ruleset-21121430.json` / `ruleset-21121449.json`，
`gh api repos/Tavotto/Tavotto/rulesets/<id>` 原样输出）。
任何修改都可以用 `scripts/admin/apply_rulesets.sh --restore` 回滚。

### 8.4 遗留的 legacy branch protection

`gh api repos/Tavotto/Tavotto/branches/main/protection` 仍返回一份**几乎全空**
的旧式配置（`enforce_admins: false`、`required_conversation_resolution: false`、
没有 `required_status_checks` 段）。它与 ruleset **并存**，GitHub 取两者的并集，
所以目前不产生错误的放行。但它是一个**读起来会误导人**的对象：
有人查 `branches/main/protection` 会得出「管理员不受约束、thread 不必 resolve」
的结论，而真相在 ruleset 里。建议清掉（§9）。

---

## 9. 需要立即处理 / 安全关闭 / 进 patch train / 进 v1.1

分类判据见 `docs/engineering/review-severity-policy.md` §2。

### 9.1 必须在 v1.0 之前修（release blocker）

| 项 | 类别 | 判据命中 |
|---|---|---|
| 实验室 runner 残留进程无自愈（根因 A） | ci-harness | release pipeline 无法完成 |
| 汇总步骤在有失败时自己挂掉（根因 B，#61） | ci-harness | 诊断在最需要时失灵；release 无法完成 |
| 跨 workflow 轮询（根因 C） | release-infra | release pipeline 无法完成 |
| 没有 publish=false 演练路径 | release-infra | 正式 tag 被迫承担首测；已烧掉两个 tag |
| 资格验证两份手抄定义 | ci-harness | 一个 bug 要改两处，必然漂移 |
| 下游步骤各自猜产物文件名 | release-infra | #63 的根因，会再犯 |
| issue 无 severity/area/disposition 标签 | 流程 | 退出条件「unresolved P1 = 0」不可查询 |

### 9.2 可以安全关闭

| 项 | 理由 |
|---|---|
| #30 / #31 / #32 / #33 / #34 / #35 / #37 | 对应 PR（#27/#41/#42/#43/#44/#45）均已合并。**需要逐条核对验收条件后再关**，不能因为「PR 合了」就关 |
| #63 | 已合并（本审计已确认） |

### 9.3 进 patch train（v1.0.1 / v1.0.2）

| 项 | 理由 |
|---|---|
| #46 写回 verify 的一次性 worker 目录偶发泄漏 | 低频、非静默、不损坏数据、有可见失败 |
| #39 文档与实现对齐 | 非静默、有替代路径 |
| §4.3 `honours_stroke_style` 按类名的例外 | 已有每次重新渲染的复测，例外不会悄悄过期 |

### 9.4 进 v1.1（架构型）

| 项 | 理由 |
|---|---|
| §4.1 图例重建路径不幂等 | 要改图例重建路径 + `ALIAS_GROUPS` 建模；已有边界看护 |
| §4.2 alias/originals/replay 建模 | 1.0 前明确禁止重写；不变式 3 已钉住核心语义 |
| §4.8 多宿主色条只记第一个宿主 | 要把宿主从单 axes 改成 axes 集合，三处按并集算 |
| §4.5 「按对扫」harness 收进仓库 | 新增基础设施，不是修复 |
| §4.6 Collection 能力表剩余 196 格 | 已不再产出新缺陷**类别**，结构性看护就位 |

**§4.8 在本轮增加 guard**（检测多宿主 → 不再给出 orientation 能力 + 稳定
reason），见 `docs/engineering/p2-lifecycle.md` 的 disposition 表。

---

## 10. 复算脚本

本文件 §7 的三张表由这段生成（需要 `gh` 已登录）：

```bash
python3 scripts/admin/codex_review_stats.py --owner Tavotto --name Tavotto
```

§5 的工作流真实性表：

```bash
python3 scripts/ci/check_release_health.py --json
```

§8 的 ruleset 备份与 diff：

```bash
scripts/admin/apply_rulesets.sh --backup            # 存档当前配置
scripts/admin/apply_rulesets.sh --diff              # 与仓库里的期望配置比对
```
