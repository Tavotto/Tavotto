# singleton active_attempt 单槽：触发器反证设计（未执行）

本报告依据父线程明确的新方案：publication 表格式保持原样；`slot_policy` singleton 的 nullable `active_attempt` FK 是唯一槽；同一 issued batch 增加 `_guards` 标记，journal AFTER INSERT 绑定 pointer 并消费标记；负向 JSON 放既有 observation，journal AFTER UPDATE 清 pointer。当前主树 DDL/derived field 尚在适配，以下不是对未稳定实现的失败判定，也不声称任何测试通过。未写工作区、未执行 transport/HTTP/worker.fetch/发布或新槽代码。

## 1. 最小触发顺序

一次成功占用必须作为同一 D1 batch：原 guards → `confirmation-slot:<new attempt>` marker → journal INSERT → 原 head-dispatched/outbox（refresh 则 consumed decision/outbox）→ commit。

journal BEFORE INSERT：检查 positive 模式/latch、全局 active 是否空、遗留未解决 positive、同 head owner/history 保护、marker 对应本次 new id。不能把“marker 存在”当槽已经分配。

journal AFTER INSERT：

1. 首次时建立 latch，保留 immutable protocol/first_attempt/created；
2. 在 pointer 仍空时把 active_attempt 设为刚插入的 NEW.id；
3. 明确核实 pointer=NEW.id；
4. 最后删除该 marker。

不得先删 marker 再设置 pointer，因为 policy 的 acquire guard 此时仍要验证 marker。既有 singleton 的插入保护会在 INSERT OR IGNORE 上也运行，故后续 acquire 不应盲目重插已存在 policy；可用 INSERT…SELECT WHERE NOT EXISTS + 独立内部 UPDATE，或等价明确分支。

同一 batch 后续的 head/outbox/cursor guard 失败，必须连 journal、pointer、首次 latch、marker 一起回滚。trigger 内部 changes() 不应误当外层 UPDATE 的 CAS 结果；实现后用真实 SQLite 验证外层 changes()=1 与 rollback 链。

## 2. policy 自身必须保护的边界

| 操作 | 允许条件 |
|---|---|
| 无行→首次 singleton | 仅绑定本次实际存在的 positive issued journal 和对应 marker；不能凭空安装空 latch 或指任意历史 row |
| active NULL→new id | exact marker；new row issued/success；live send fence；没有其它未解决 positive；保持 latch metadata |
| active id→同 id | 仅无害 no-op，不能顺带改 metadata 或延长其它状态 |
| active old→active new | 拒绝；不能以 new row 看起来合法替换仍被占用的 old owner |
| active old→NULL | 仅旧 owner 已完成本次 fresh negative 结算；看 OLD.active_attempt 对应的 journal，不能看任意别的负向 row |
| policy DELETE | 永远拒绝，active 已空也不能删 latch |
| policy REPLACE/UPSERT | 不得隐式删除或换 metadata；BEFORE INSERT 在 singleton 已存在时先拒绝 replacement |

SQLite 的 INSERT OR REPLACE 在 recursive_triggers=0 时可跳过隐式 DELETE trigger。只做 policy BEFORE DELETE 不够；journal owner 的同 id REPLACE 也能在语句结束时仍满足 FK（同 id 已被新行替换），FK 无法证明原 proof/attempt row 未被替换。建议 journal BEFORE INSERT 拒绝已有 publication id 的重插，至少严格保护 active、first_attempt 及已归档负向 row。

`active_attempt` 的 FK 应为 NO ACTION/RESTRICT，不可用 ON DELETE SET NULL/CASCADE 释放槽。另加显式 owner journal BEFORE DELETE 保护，避免仅依赖 foreign_keys pragma。policy 的 first_attempt/created/protocol 保持不可变，pointer 的清空不删除 latch。

## 3. OLD tuple 与 AFTER pointer 清理的两层分工

journal BEFORE UPDATE 在原 owner 即 OLD.id=policy.active_attempt 时检查：

* id/target/head/original revision/expires/conclusion/proof/created 身份不漂移；
* 负向 JSON 的 `attempt/target/head/original_revision/proof_text/expires/observed_phase` 对照 **OLD**；
* JSON.old_run_id 使用 NULL-safe `IS OLD.run_id`；已知 run 不可变；未知 run 可由 NULL 发现为 JSON.run_id；
* NEW.run_id=JSON.run_id>0，NEW.phase=resolved；
* live send_until 已结束或被合法 sender settlement 清零；
* 当前 state revision 和 outbox target/lease/generation/lease_until 仍匹配；
* DB_NOW 严格小于 JSON.valid_until，observed_at 不在未来。

接着 journal AFTER UPDATE 才请求 policy active→NULL。policy BEFORE UPDATE 此刻读到的是 **更新后的 journal**：run_id 可能已经 NULL→101。因此它应复核当前 row resolved、当前 run/JSON/current state/outbox、pointer 身份；不能再把 JSON.old_run_id 跟当前 row.run_id 比较，否则合法 unknown-run 发现会被拒绝。OLD tuple 的证明应由 journal BEFORE UPDATE 完成。

runtime opaque consumer 是“本 invocation 的实际 GET”来源边界；SQL/trigger 是原行与当前恢复权限边界。不能只在 policy trigger 中检查 observation 文本为旧 binary 已会写的 `remote_failure_verified`。canonical JSON 必须由有效 opaque witness 消费产生，并由上述 OLD/NEW 检查绑定。

原 publication revision 与当前 recovery revision 必须分开：grant revoke/协议切换后，原成功应在新 revision 下被负向清理。相等要求会堵住正确撤回。

目前 producer 的消费期限是 now>=valid_until 即拒绝；SQL 要用 **DB_NOW<valid_until**。反例：consumer 在截止前 1 秒消费，DB 结算刚到截止秒；若 SQL 写 valid_until>=DB_NOW，则会比 consumer 多放行一个边界点。

## 4. observation 复用和历史保护

既有 observation 经常是 `budget_deferred`、`retry_pending`、`remote_failure_observed` 等非 JSON。不能给所有行直接 json_extract(observation,…)，也不能假设普通 AND 一定短路。建议把 JSON 提取放在明确 CASE(json_valid) 分支，legacy 字符串应继续走旧路径。

释放后 observation 中的 negative JSON 是归档事实，不能被旧 retry/defer/manual cleanup 覆盖；但它也不是下一 attempt 的释放令牌。released row 不可被重插/改 id、head、proof、original revision，pointer 不可重新指回已 resolved 的旧 row。

policy pointer 已释放、另一个 attempt 已取得槽后，旧 witness/旧 observer 只能命中旧 row，不能清新 pointer。清 pointer 的 WHERE 必须是 `active_attempt=旧 attempt`，并且 journal/lease/phase CAS 仍要生效；不得按 target 或“当前全局 active”清。

## 5. derived projection 与 resolved-held 恢复

`confirmation_slot` 是读取投影，非 owner 应为 **NULL**，owner 为 1；不能生成 0 后继续使用原先 `IS NULL`/`?? null` 语义。policy 缺失、active NULL、active 指别人三种情况均要返回 NULL。

所有读取 owner 的路径应使用同一表达式/投影视图：publicationBatch、confirmed/latest publication、refresh decision/predecessor、claim/finish/defer、recordReconciliation。仅 SELECT p.* 加别名不意味着 WHERE 里的 p.confirmation_slot 已变成物理列；避免有的路径仍引用旧列或看到不一致的 ownership。

若允许旧 binary 把 held owner 改 resolved 而 pointer 不变，eligible 必须仍选到它，至少绕过 resolved、reconcile_due=NULL、latest-per-head 这些排除条件；finishBatch 不能删 outbox，due 应安排 fresh-negative 观察。manual 保持不自动解除，已有失败上限不应凭槽无限重试。

另一路合法选择是直接拒绝无 fresh-negative JSON 的 held→resolved 更新，这时不需要为这种旧 binary 状态做特殊选取；两种策略都可以，但不能只“指针仍占着”却永久漏选 owner 而不说明人工边界。

无论采用哪种策略，其他 head 的新 failure 不应阻止原 head 负向清理；同 head 新 journal 不应改写 owned run external_id 并遮蔽原 attempt。原 attempt withdraw 保持可用。

## 6. marker 与预算口径

新增 marker 是 +1 外层 SQL，预计委托 N=1 的 new-head/fresh journal 从 16 work/20 total 变为17/21；释放仍折入原 UPDATE，+0 外层 SQL。确认/负向恢复最贵 work 前缀仍须实际保持19以内，不能因 pointer 查询再加多条读取。

投影中的 scalar lookup、policy acquire/release 与 marker DELETE 属数据库内部工作，按当前 meterDatabase 调用口径不增加外层 query 次数；和既有 revision triggers 相同，但并非平台零成本。需要执行后实测 outer statement trace，并另留 D1 内部读取/CPU/事务行为验收。

marker 是事务关联标记，不是秘密服务凭据。防线假定 DB 任意写权限不交给不可信调用者；拥有任意 SQL 写权限者可伪造 _guards，这不是通过 brand 或 FK 能解决的新认证问题。旧 binary 的随机 guard ID 不会意外形成 `confirmation-slot:<attempt>`，但任意不相关 guard 不能作为 acquire 标记。

## 7. 待实现稳定后的精确反证列表

以下只列测试，当前没有执行，不把旧 DDL 的两列方案当新设计结论：

1. 第一次 acquire 后故意让同 batch outbox guard 失败：pointer/latch/marker/journal/head 全回滚。
2. 后续 acquire 证明 singleton BEFORE INSERT 不会误伤合法分支；无 marker/错 id/标记已消费的 INSERT 拒绝。
3. recursive_triggers=0/1 下分别尝试 REPLACE policy、owner journal、first_attempt history；都拒绝且原 tuple 不变。
4. 直接 policy DELETE、active→NULL、active→other、改 protocol/first_attempt/created 拒绝；active NULL 后旧 success/NULL INSERT 仍被 latch 拒绝。
5. 原run=NULL 的 fresh failure发现101能释放；old_run错误、run在GET后变化、original revision/proof/head/attempt错、当前lease/generation/revision错均拒绝。
6. consume截止前但DB在valid_until执行，必须拒绝；不提前释放 pointer。send_until/live outbox lease 的相同边界固定时刻拒绝。
7. 允许的旧 phase=resolved+held 状态确实仍选中 owner；或该 UPDATE 被明文拒绝。manual不自动恢复。
8. legacy observation 非JSON仍正常；释放后的JSON不被defer/retry覆盖，不可重放清后来的pointer。
9. 非owner投影NULL；无policy/空pointer/指别人三分支；所有读取路径无物理confirmation_slot列依赖。
10. 同head failure新journal被挡，其他head failure可提交，原attempt撤回不被正向slot挡；旧row结算不清新owner。
11. 同一batch执行order实测；外层changes() guard在trigger acquire/release后仍看正确外层row count。
12. 跑实际 meter 计数覆盖acquire失败、opaque消费拒绝、fresh负向释放、resolved-held、cleanup分支，仍守20/40 SQL、35/40 external、120秒fence≥90秒cleanup。

下一步应等父线程完成 DDL/projection 适配并固定源码，再把上述列表缩成实际反向测试与唯一落点变异。
