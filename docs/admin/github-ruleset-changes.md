# GitHub ruleset 现状、建议变更与回滚

> **备份先于修改。** 审计当时的两份 ruleset 原样存档在
> `docs/admin/rulesets/`（`gh api repos/Tavotto/Tavotto/rulesets/<id>` 的输出），
> 标签现状存档在 `labels-before-2026-08-22.json`。
> 任何修改都可以用 `scripts/admin/apply_rulesets.sh --restore` 回滚。

---

## 1. 现状（2026-08-22 实测，两条 ruleset 均 `active`）

### 1.1 `main: PR + required checks`（id `21121430`）

| 项 | 值 | 判断 |
|---|---|---|
| target | `~DEFAULT_BRANCH` | ✅ |
| `bypass_actors` | `[]` | ✅ **管理员同样受约束** |
| `deletion` / `non_fast_forward` | 禁止 | ✅ |
| `required_approving_review_count` | `0` | ⚠️ 单人仓库，可接受；见 §2.3 |
| `required_review_thread_resolution` | `true` | ✅ 未处置的 thread 挡住合并 |
| `strict_required_status_checks_policy` | `true` | ✅ 必须与最新 main 对齐 |
| required checks | **17 条** | ✅ **逐条核对过，全部真实存在** |

17 条：`backend (ubuntu-latest, 3.10 / 3.13)`、`backend (macos-latest, 3.13)`、
`backend (windows-latest, 3.13)`、`frontend`、`workerd`、`compat-smoke`、
`invariants`、`package (ubuntu / macos / windows-latest)`、`windows-exe-smoke`、
`macos-app-smoke`、`CodeQL (actions / javascript-typescript / python / rust)`。

**没有幽灵 check。** 这是 §4.7 那条纪律（先让 job 在 main 上产出结论、
再登记成必需）执行到位的结果——反过来会把整个仓库锁死。

### 1.2 `release tags: immutable`（id `21121449`）

target `refs/tags/v*`，`deletion` / `update` / `non_fast_forward` 全禁，
`bypass_actors: []`。✅

**代价已经兑现**：v0.9.0 与 v0.9.1 两次失败的发布演练留下的 tag 现在
既不能移动也不能删除。**规则是对的**（可移动的 release tag 是供应链漏洞），
要改的是「别拿正式 tag 做第一次演练」——这正是
`ci/release-orchestrator` 引入 `publish=false` 的动因。

---

## 2. 建议变更

### 2.1 清掉遗留的 legacy branch protection（**建议做**）

`gh api repos/Tavotto/Tavotto/branches/main/protection` 仍返回一份几乎全空的
旧式配置：`enforce_admins: false`、`required_conversation_resolution: false`、
**没有 `required_status_checks` 段**。

它与 ruleset **并存**，GitHub 取两者的并集，所以**目前不会错误放行**。
问题在于它**读起来会误导人**：任何人去查 `branches/main/protection`
都会得出「管理员不受约束、thread 不必 resolve」的结论，而真相在 ruleset 里。
本轮审计自己就先被它误导了一次。

```bash
# 备份（apply_rulesets.sh --backup 已经包含这一步）
gh api repos/Tavotto/Tavotto/branches/main/protection \
  > docs/admin/rulesets/legacy-branch-protection-2026-08-22.json
# 删除
gh api -X DELETE repos/Tavotto/Tavotto/branches/main/protection
```

**权限是有的，本轮仍然没做**——理由是**回滚不可靠**，不是没权限。
`gh api repos/Tavotto/Tavotto --jq .permissions` 实测返回 `admin: true`，
用一次幂等 PUT（原样写回 ruleset `21121449`）确认过写权限真实可用
（`updated_at` 没变，确实是 no-op）。

不做的理由：**legacy branch protection 的 GET 响应不是 PUT 的请求体格式**
（GET 回的是一堆 `url` 字段，PUT 要的是另一套 schema）。
`legacy-branch-protection-2026-08-22.json` 存下来的是 GET 的原样输出，
它能证明**删之前是什么样**，但不能保证一条命令还原回去。

在「有备份才动」这条纪律下，这不算有备份。而它当前的实际危害是**零**
（union 语义，ruleset 已经覆盖了它的每一条），只是读起来误导人。
所以：交给管理员在**能确认还原步骤**的时候做，或者接受它一直留着。

### 2.2 把 `Codex Review Gate` 登记为必需检查（**等它先在 main 上产出结论**）

顺序不能反（§4.7 的教训）：

1. `chore/review-and-p2-governance` 合进 main；
2. 确认 main 上出现过一次 `gate` 的 conclusion；
3. **然后**才加进 ruleset。

```bash
scripts/admin/apply_rulesets.sh --add-check "gate" --apply
```

反过来做的话，那个 check 在合并之前永远不会出现，而规则要求它通过——
**整个仓库会被锁死**。

### 2.3 不建议改的

| 项 | 为什么不改 |
|---|---|
| `required_approving_review_count: 0` | 单人仓库。改成 1 会让所有 PR 停住，而 `required_review_thread_resolution: true` 已经提供了实质约束 |
| tag immutable | 见 §1.2。要修的是流程，不是规则 |
| `bypass_actors: []` | 管理员受约束正是这套规则的价值所在 |
| 把 `Lab Qualification` 加成必需 | **它一次都没成功过**（success = 0）。现在登记会锁死仓库。等 `ci/release-harness-stabilization` 让它真正跑通、并在 main 上绿过之后再说 |

---

## 3. 回滚

```bash
scripts/admin/apply_rulesets.sh --diff       # 远程 vs 存档，先看差异
scripts/admin/apply_rulesets.sh --restore    # dry-run，打印将要还原成什么
scripts/admin/apply_rulesets.sh --restore --apply
```

存档文件就是 `gh api` 的原样输出，所以**任何时候都可以手动**：

```bash
gh api -X PUT repos/Tavotto/Tavotto/rulesets/21121430 \
  --input docs/admin/rulesets/ruleset-21121430.json
```

---

## 4. 当前身份能做什么

`gh auth status` → `erwanjun`，scopes `gist, read:org, repo, workflow`。

| 动作 | 能否 | 实测 |
|---|---|---|
| 读 ruleset | ✅ | 本文件 §1 的数据就是这么来的 |
| 建 / 改 label | ✅ | 18 个标签已建好（`sync_github_labels.py --apply`） |
| 建 / 改 milestone、issue | ✅ | |
| 改 ruleset | ✅ | `permissions.admin = true`，并用一次幂等 PUT 实测确认过。**本轮仍未改任何 ruleset**——§2.2 那条要等 `gate` 先在 main 上产出结论 |
| 删 legacy branch protection | ⚠️ 权限有，**没做** | 见 §2.1：GET 的输出不是 PUT 的请求体，还原不可靠。「有备份才动」这条纪律下不算有备份 |
