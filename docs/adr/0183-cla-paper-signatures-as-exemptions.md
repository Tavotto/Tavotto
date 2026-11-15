# ADR 0183：CLA 服务启用后，已双签的纸面协议改记在豁免表里，不迁进服务

- 状态：已接受（2026-11-13，用户批准：mvanhorn 不重签，纸面记录挪进豁免表）
- 修订：`docs/legal/CLA_AUTOMATION_SETUP.md` 与 `CLA_VERSIONING.md` 里「启用服务前先把 `offline_signatures` 迁进服务、两者绝不并存」的规定
- 相关：ADR 0172（实时判定）、#1630（`offline_signatures`）、#826（mvanhorn 首个贡献）

## 背景

服务启用前，唯一的签署途径是纸面双签：贡献者和著作权人在纸面协议上各签一次，执行副本由著作权人私下保存，`cla-policy.json` 的 `offline_signatures` 只放一条索引（账号、不可变账号 id、协议与版本、两个签署日期）。目前只有 mvanhorn 一条。

原规定是：启用服务时，先把这些人迁进服务，再在同一个改动里清空 `offline_signatures`，因为判定器不允许两者并存。这条路实际上走不通：

- 服务没有导入纸面协议的入口。它的签署记录必须是本人经 GitHub 登录、在签署页上留下的加密证据，直接往库里塞行，服务读取时也会拒收。
- 服务只在默认分支 policy 的 `provider` 与自己的配置逐字相同时才肯签署（`github.ts` 的 `trusted_policy_changed`）。也就是说 `configured:true` 先上 main，签署页才能用；可 `configured:true` 又要求 `offline_signatures` 已经清空。所以「先迁、后切换」的顺序本身就不成立。
- 让已经签过、联署过的人再签一次，只是为了迁就工具，对贡献者不合理。

## 决定

**纸面双签记录从 `offline_signatures` 挪进 `exemptions`，新增类型 `countersigned_paper`。** 服务启用后照常保留，不迁进服务，贡献者也不用重签。

```json
{
  "login": "mvanhorn",
  "kind": "countersigned_paper",
  "github_id": 455140,
  "agreement": "individual",
  "version": "1.0",
  "contributor_signed_on": "2026-11-01",
  "countersigned_on": "2026-11-02",
  "reason": "Executed Individual CLA 1.0 (both signatures), held privately by the rights holder; contains the contributor's postal address and is never committed. First contribution: PR #826."
}
```

`offline_signatures` 一节删除，判定器不再认它。

### 为什么放在豁免表

服务读 policy 时只算 `agreements` 与 `exemptions` 两节的摘要，并把 `exemptionIds` 里的账号 id 一律当作不需要服务签名的人（`service.ts`、`group.ts`），不看 `kind`。所以放进豁免表后：

- 单个 PR 只有他时，判定器本地按 id 放行，不问服务。
- 他和别人混在同一个 PR 或合并组里时，服务把他排除在外，只看其余的人是否已签。PR 检查和合并组检查都不会因为「他不在服务数据库里」误判红。
- 服务代码一行不改，生产服务配置的 `exemptionIds` 里加上 `mvanhorn: 455140` 即可。

### 「只有一个权威」改成「每个人只有一个权威」

原来的规定是为了避免同一个法律事实有两份记录、分叉了说不清谁算数。新规定照样守住这一点：

- 一个账号要么是 `countersigned_paper` 豁免（只认纸面执行副本），要么由服务判定（只认服务记录），不会两边都算。判定器在服务模式下发现同一账号又出现在服务的签署里也没关系，它只按豁免放行，服务的记录不参与。
- `countersigned_paper` 只收**已经存在**的纸面双签。服务启用后，新贡献者一律在线签署；要新增这一类条目，须由著作权人另行决定并改这份 ADR。

### 判定器的约束（`cla_gate.py`）

`countersigned_paper` 条目必须且只能有上面这几个字段，不收姓名、邮箱、地址。判定时：

- **按不可变账号 id 认人**：沿用 `offline_signatures` 的规则。可信来源（PR 事件作者 id、GitHub 解析出的提交 `author.id`）给出的 id 全部等于 `github_id`，并且至少有一个，才算本人。`Co-authored-by` 和 noreply 邮箱里的数字不算。
- **只覆盖签过的那一版**：`agreement` 与 `version` 必须等于当前 policy 里那份协议的版本，否则不算豁免。判定器在**任何情形**下都核这一点，包括要问服务的混合 PR 和合并组：只要贡献者里有版本对不上的 `countersigned_paper` 条目，直接判红并提示本人签新版，不把服务的 `eligible` 当作覆盖了他。因为服务只看 id 集合，自己不会按版本区分。
- **不算「已签」的说法**：输出里写「已双签纸面协议（豁免）」，与 `rights_holder`、`bot` 一样走豁免分支，不冒充服务签名。

## 后果

- mvanhorn 不用做任何事。切换 PR 合入后，他的 PR 照常能过。
- 协议升版时，`countersigned_paper` 条目自动失效，本人要在线签新版；签完后由维护者删掉这条豁免，之后由服务判定。
- 生产服务配置（`CLA_CONFIG`）里的 policy 要与改后的 main policy 逐字一致，包括这条豁免和所有豁免的 `github_id`。所以这个改动和 `provider.configured:true` 放进同一个切换 PR。
- `docs/legal/CLA_AUTOMATION_SETUP.md` 第 7 节和启用步骤第 4 条、`CLA_VERSIONING.md` 纸面签署一节、`cla-policy.json` 的注释，按本 ADR 改写。
