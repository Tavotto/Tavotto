# 离线 repo-v2 原地升级：独立只读审查

结论：在本次冻结源码与合成 SQLite 范围内，未发现可达的数据损坏、错误提交或默认启用问题。离线候选可以作为可审阅的 repo-v2 升级机制；本结论不构成生产数据库识别、生产授权、真实 D1 原子性或运行时可用性证明。

## 审查基线与指纹

- 工作区：`/workspace/Tavotto-cla-v2-upgrade`；HEAD / target #1168：`dc2431f725798bd78c40a3b07e0a8054690fe82c`。
- 来源固定 commit：`77e2cab77597318859f874d9319c47f15da0e1dd`。独立执行 `git show` 验证两份冻结 DDL 的 SHA 与该 commit 下 `src/base.sql`、`src/native/schema.sql` 完全一致；该 commit 的 store 明确 `PREFIX='cla_native_v2_'`（第 7 行）。这是仓库源 v2 的证据，没有读取生产 D1。
- 最终 module：`480b62ea6e3bc81c4f242cd6d343e7ec1b327ce63699edda8b68c6b57bde199f`。
- frozen/base.sql 与 target/src/base.sql：`b7c158869acbd1eb43dcbfee72f85ebf1149716082a66f9140989c56a4927e69`。
- frozen/native-schema.sql：`5e2a63a7560aee08253f942c8ee34a2cf0c99e692ef14417014d6081e348392a`。
- target/native/schema.sql：`0e4cc1672c78837b219eba0c1f54de89fc86169c87ffd26277085a229dfe3a08`。
- target/store.ts：`f56a0d9effb65a44ebb118ee33fd1611340361ea4bce9f5c3a31f77690dfad83`；background.ts：`cf0aec0bcfe02c8c8d51d3e2a4d512625ac9b2d9d10d1a78a92743af7949fb41`；worker.ts：`6b7f1b3c0a03863c99965659f0d7a3d483006dc7ddb36e853836010129731678`。

## 核实的契约

所有 module 行号均指上述最终指纹。

- 第 9–17、24–46 行以固定源/目标 DDL 建立模型，现有对象除了 publication 表的新增字段，其余 SQL 必须逐字对应；新增对象数量必须恰为 7 表、23 trigger。实际语句恰为 31 条：1 条 `ALTER ... send_until INTEGER NOT NULL DEFAULT 0 CHECK(send_until>=0)`，加 30 条 CREATE。没有修改或删除历史 row 的 DML、复制个人签署、选择协议 tuple 或回退版本。
- 第 22 行用 `NOT GLOB 'sqlite_*'`，不会把 `sqliteXfoo` 等额外对象误当内部表忽略。第 60–65 行拒 attached DB、非空 temp schema、外键关闭和 integrity/FK 错误。完整 schema 精确匹配，非预期 schema 留人工映射。
- 第 50–58 行保全所有原表的 rowid、全部列及 SQLite 类型：整数使用 `setReadBigInts(true)`；TEXT/BLOB 再纳入 SQL `hex()` 原字节，因此非法 UTF-8 解码替换不会令不同原数据被判为相同。
- 第 94–99 行默认执行关闭；只有两项严格布尔 opt-in 才进入本地合成 apply。`syntheticFixture` 明确是调用方声明，不是生产来源证明。现有调用方 transaction 先拒绝且不 commit/rollback。随后 `BEGIN IMMEDIATE` 内重新检查源 schema、quiescence、integrity 和完整 typed 数据，并与整个 prepared plan 一致比较（第 108 行）。
- 第 110–117 行执行后重新核 schema、全部旧表数据、send_until=0、7 个新表为空及 integrity。任何异常在仍持有 transaction 时 rollback。未知/部分 schema 不尝试修复。
- 第 100–106 行 uncertain commit 重试只在原数据仍逐字一致、新表仍空、send_until 仍为 0 且 quiet 时返回 already_applied；不重复 DDL 或恢复部分升级。对已经是完整 target 的 fresh plan 只读返回 already_target。
- 第 120–125 行 CLI 仅提供本地内存 synthetic plan，可省略或传 `--plan`；file target、execute 参数拒绝。没有文件目标打开器、D1/HTTP/生产 CLI、凭据路径或部署操作。
- 默认 runtime 的环境开关仍均为 false：worker.ts 第 74–79 行覆盖 JSON 开关；disabled manifest 第 20–24 行关闭 delegated、automatic、agreement activation、confirmation slot。新表全部为空，升级不会建立 slot latch、grant/control/receipt、activation anchor 或 head 请求。

## 实际执行证据

命令：`node /tmp/cla-v2-upgrade-review-probe.mjs`，Node `v24.19.0`，最终实际退出码 **0**，**24 组通过 / 0 失败**。另一个 BEGIN 返回丢失探针也在最终源码独立执行、实际退出码 **0**（详见末节）。主探针在开始和结束比较 module 原字节，最终源码一致。

证据：

- `/tmp/cla-v2-upgrade-independent-probe.json`：最终源码 SHA、逐组结果与明细。
- `/tmp/cla-v2-upgrade-review-probe.log`：最终运行原始输出。
- `/tmp/cla-v2-upgrade-review-probe.mjs`：独立合成探针，可复跑。

覆盖内容：

1. 5 种 default-off/非严格布尔调用，对 null DB 均先返回 disabled，零数据库读取。
2. 19 个旧表的合成历史，包括 immutable 个人签署、audit、持久 outbox/refresh/reconciliation/publication 状态、已有协议来源与内容；保留 `9007199254740993` revision/rowid/int64、REAL、NULL、BLOB、非法 UTF-8 TEXT，保留 confirmed/reconcile/manual/resolved。升级后 send_until 全 0、7 个新表全空。issued/unknown 源状态明确拒绝而不删除或修复。
3. prepared 后修改 mutable row、非法 UTF-8 原字节、超安全整数、rowid，均拒绝，schema/数据不变。
4. sqliteXfoo 额外对象、temp/同名 shadow、attached DB、guard、lease、issued/unknown、foreign_keys OFF 均拒绝；调用方已有 transaction 保持未提交可回滚。
5. 在每一条 DDL 前依次注入异常，共 **31 个独立故障位置**；逐次对比完整原 schema 与旧数据，均完全回滚，transaction 结束。
6. DDL 后注入原表变化或新能力表数据，最终 guard 检出并全部回滚；COMMIT 前失败也全部回滚。
7. COMMIT 已成功但返回丢失的合成异常：重试返回 already_applied，完整 target 数据/schema 不变。部分升级 schema 拒绝；已经升级后旧数据漂移也不被当作原计划成功重试。
8. CLI 2 种 plan 调用退出 0；3 种 file/execute 形态退出 1，指定文件未创建。

首轮旧 metadata 名 `capabilities_enabled:false` 的源码 SHA `c542f61c38ec79aac7c7e6dbc7a2360f789e818e8c82a643cd7fee3b15b7caf8` 实际 21 组通过、退出 0，已另存 `/tmp/cla-v2-upgrade-c542-21-first-run.txt`。该字段可能被误读为对既有 target runtime 的 inactive 认证；父线程已改成 `activation_performed:false` 并收窄文档。扩展探针一次与该重命名重叠，唯一失败是旧字段断言；原始失败输出保留在 `/tmp/cla-v2-upgrade-pre-rename-assertion.{json,log}`，没有计为源码逻辑失败。上述最终 24 组是在新字段和冻结指纹下完整重跑。

未运行其他 agent 的正式测试或变异套件，不将它们计入本审查执行结果。没有改 workspace、调用网络、worker.fetch、publication、生产服务、下载、部署，也没有执行跨 expiry 成功发布。

## 明确留下的人工门槛和 #1168 可用性限制

- SQLite `BEGIN IMMEDIATE` 只能保护该本地 DB 的事务；它不能证明生产 worker、后台进程、外部 writer 已停止。停止外部写入、来源备份和真实 D1 schema/原子 batch 验收仍须独立人工操作和授权。
- `activation_performed:false` 只描述工具未执行能力激活，不证明 existing target 的数据库内容或当前 runtime 全部关闭。这个工具不选择法律文本、协议 tuple、配置或发布版本。
- #1168 的 confirmed 状态仍占全局槽。store.ts 第 81–82 行同时检查 active pointer 以及全部 `success && phase!='resolved'`，无 expiry 自动释放条件；schema.sql 第 106–108 行仅在符合见证校验的 resolved 转换后释放。
- background.ts 第 14 行把 missing 观测记为 manual；store.ts 第 235 行不继续自动处理 manual。unknown 也不能以过期自动释放；不确定状态经过重试耗尽/缺少见证可能转 manual。一个这样的历史 success 可长期阻塞全部正向 publication。这是保守的 fail-closed 可用性限制，不能声称全自动可用或升级后自动恢复。
- 以上可用性判断来自源码，本轮没有发送 GET/撤销/发布请求，也没有假造真实 GitHub 负向见证来释放槽。

## 已修复并独立红→绿复核：BEGIN 返回丢失故障

在父线程追加要求后，对同一 `62fefc...` 源码执行 `/tmp/cla-v2-upgrade-begin-ack-probe.mjs`，真实执行 BEGIN 后由本地 wrapper 抛 `synthetic-begin-ack-lost`。实际退出码 **1**，断言发现 `db.isTransaction===true`。module 第 98 行在 `exec` 返回后才设置 begun，第 117 行因此跳过 rollback。本调用创建的事务留开，可能持续持锁且后续 apply 会被已有 transaction 检查拒绝。该结论限定为合成适配层返回异常；不是已观察到 node:sqlite/生产 D1 自行丢 ACK。

旧红证据保留：`/tmp/cla-v2-upgrade-begin-ack-red.log`、`/tmp/cla-v2-upgrade-begin-ack-red.json`。旧 62fefc 的完整 24 组通过记录也另存 `/tmp/cla-v2-upgrade-62fefc-24.{json,log}`；这些旧通过并未覆盖该额外故障窗口。

父线程随后把第 98 行改为 `begun=true;db.exec('BEGIN IMMEDIATE');`，已有 caller transaction 的第 95 行前置拒绝与第 117 行 `db.isTransaction` 检查仍保留。最终 `480b62...` 上执行同一 `/tmp/cla-v2-upgrade-begin-ack-probe.mjs`，实际退出码 **0**，真实 BEGIN 成功、wrapper 抛错后 `transaction_left_open:false`。绿证据 `/tmp/cla-v2-upgrade-begin-ack-green.log`、`/tmp/cla-v2-upgrade-begin-ack-probe.json`。随后完整 24 组在最终源重新通过，包含 caller transaction 保持、COMMIT 前后故障及全部 31 DDL 回滚；BEGIN 未成功时只在 isTransaction=true 才 rollback 的条件另外由源码核实。没有剩余本项阻塞发现。
