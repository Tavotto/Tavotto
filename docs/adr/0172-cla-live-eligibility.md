# ADR 0172：CLA 由 CI 直接问服务的实时判定，不再走已发布的检查与短命凭证

- 状态：已接受（2026-11-06，用户拍板方案 B）
- 取代：`services/cla-cloudflare/deployment/ci-proposal.md` 里「`ci-candidate.mjs` + 两次实时确认」的接线方案
- 相关：ADR 0137（有限联署授权）、#1771、#1774、#1786、#1791

## 背景

原方案是服务在 GitHub 上发一条 check，里面放 120 秒有效的凭证，CI 校验凭证后再向服务确认两次。沙盒实测（2026-11-06，Actions run 42114869043，PR #4，30 分钟 262 次）说明这个模型放到开着的外部 PR 上走不通：

- PR 开着、签名有效，服务发 4 轮后 `refresh_retry_exhausted`，之后不再发凭证，这个目标还被标成 manual。沙盒里每个目标最后都是这个结局。
- 确认要等下一轮 2 分钟的定时任务，而凭证只有 120 秒，可用窗口可能只有几秒。
- 凭证共用一个确认槽位，排在外部 PR 后面的合并组要排队。

Tavotto 一个月一两个外部 PR，为这点流量维护发布、确认、撤回、重发一整套状态机不值得。

## 决定

服务新增两个只读接口，CI 当场问，服务当场算：

```
GET /eligibility/pr?pr=<号>&head=<sha>&nonce=<随机串>
GET /eligibility/group?base=<sha>&head=<sha>&nonce=<随机串>
```

`cla-check` 仍是唯一入口，分支规则不新增必需检查：

1. 全员按不可变账号 id 豁免：本地放行，不问服务（`_id_exempt`）。
2. 其余情形：`cla_eligibility_fetch.py` 问一次，`cla_gate.py --eligibility-json` 逐字段核对。
3. 没签：立刻红，给签署地址。签完推新提交或重跑这个检查。

## 三条硬约束

**不可达时必须红。** 取回答的脚本从不让检查变绿：连不上、超时、限流、5xx 在约 2 分钟内重试，之后把错误码写进文件，判定器照样判红。判定器自己不联网。

**接口只读，认证方式明确：不认证。** 接口公开、不带任何凭据；CI 只发 PR 号、sha 和 nonce，不发 GitHub 令牌。接口不写库、不发检查，走现有限流器（10 次/分钟）。公开意味着回答不能泄露个人签署状态：只有一个 `eligible`，贡献者列表是 PR 上本来就公开的账号，不标注谁签了谁没签。

**回答绑定这次判定，防错配和重放。** 判定器要求字段集合恰好相符，并核对：仓库、PR 或合并组 base、head、协议、策略摘要；`contributors` 覆盖 CI 自己看到的每一个人（作者、提交作者、Co-authored-by，账号 id 也要对上）；`nonce` 是这次校验生成的；`observed_at` 落在这次校验之内。`eligible` 只有是布尔 `true` 才算过。

地址写在默认分支 policy 的 `authority_origin`，只认 https 根地址，不跟随跳转，PR 和事件都改不了。回答没有签名，信任来自 HTTPS 与写死的地址，和原方案对服务的信任相同。

**合并组先独立收齐，再判豁免（2026-11-07 补充）。** CI 用自己的只读 GitHub 令牌分页读取 `repository.mergeQueue(branch:"main").entries`，对账 `totalCount`、分页结束、唯一 PR、连续位置，用候选 base/head 唯一定位从队首到该候选的前缀。逐个 PR 从 GitHub 取前后元数据（发起人 login/不可变 id、原始 head、base、状态和提交数），另取每个 PR 的完整 compare 提交列表；收集完再读一次完整队列。由可信默认分支 `cla_gate.py --group-snapshot-json` 离线校验这些原始数据，缺失、截断、重复、前后变化或仓库/base/head 错配一律拒绝。先合并全部 PR 发起人、提交作者与 Co-authored-by，再做 id 豁免预判；不能只因提交作者全豁免而漏掉外部 PR 发起人。任何无法解析的贡献者，即使服务回答 `eligible=true` 仍拒绝。服务回答的有序 `members` 必须与这个独立前缀的每一项 `{pr,headSha}` 精确相同，不能反过来拿服务的成员表决定 CI 检查哪些 PR。

这里的 GitHub 令牌只给 GitHub API，调用 authority 的 fetch 子进程不继承该令牌。队列读权限不可用、候选已离队或队列变化都按失败处理，不能退回解析分支名或信任事件里不完整的成员信息。

## 后果

- 撤回在下一次判定时生效：PR 重跑和入队时的合并组都会重新查，入队后到合入之间的几秒不覆盖。
- 外部贡献者签完不会自动变绿，要推新提交或由维护者重跑。
- 服务不可用时外部 PR 红，恢复后重跑；全员豁免的 PR 不受影响。
- 生产只需登录、签署页和这个接口；webhook、定时任务、发检查、自动发布都不开，App 只需读权限（`checks: read`，令牌申请时固定要这一项）。
- 合并组判定仍需 `CLA_MERGE_PREFIX_ASSESSMENT_ENABLED` 与 `CLA_MERGE_PREFIX_READ_SESSION_ENABLED`。
- 发布、确认、撤回、重发的代码留在仓库、不启用，要不要删以后再定。`_service_verdict` 与 `ci-candidate.mjs` 同样保留。
