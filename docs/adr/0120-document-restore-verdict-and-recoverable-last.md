# ADR 0120：文档恢复判决与「可恢复的 last」

日期：2026-10-11 · 状态：**Accepted**
相关：[0023 文档落盘的唯一权威](0023-document-persistence-authority.md)、[0024 保存生命周期](0024-save-lifecycle-and-external-change.md)、
[0039 离线教程项目](0039-offline-tutorial-project.md)、[0101 排版时间线](0101-layout-timeline.md)、
[0108 桌面 origin 稳定与会话状态的后端权威](0108-desktop-stable-origin-and-backend-session-state.md)（本条修订其 §三，并兑现 §四 PR-C）；
规则全文 `docs/rules/frontend/project-document-and-autosave.md`「启动恢复判决（ADR 0120）」、
`docs/rules/backend/layout-versions-and-documents.md`「会话状态以后端为准」。

## 问题

2026-10-11 恢复审计（固定提交 `e090923b4`）在真实 store / 真实 Flask 端点上复现了 8 条路径：**磁盘上有完整、有效、
受支持的自动保存，重新打开项目却落在一份新建排版上**，或把原图重新放进新文档、原来的编辑（overrides）没回来。
原有效文件一个字节没少，丢的是「恢复入口」与「当前活动文档」：

| 编号 | 触发 | 旧结果 |
| --- | --- | --- |
| R1 | 恢复还在等 last / 正文时，页宽 / 参考线 / 外部交接先改了初始空白 | 身份守卫让恢复退出，交接变成重新 `placed`，overrides 丢 |
| R2 | Picker 里 last GET 暂态失败、或只剩全局 currentDoc | reset 把空白 id 写进 currentDoc，`restoreSession` 因 id 相等零请求退出 |
| R3 | 新文档有内容、last 已推、首次正文未落盘就退出 | last 指向不存在的槽位，旧有效文件不再被尝试 |
| R4 | last 正确，正文 GET 一次 500 | 冷启动静默空白，没有任何提示 |
| R5 | last / 缓存 / currentDoc 缺，或 owners 缺且只有 runtime 面板 | 没有候选发现，有效正文躺在磁盘上找不回 |
| R6 | 两个项目保存同一 docId，旧请求的 owner 更新晚于新正文 | 正文是新的、归属被改回旧项目、当前项目 last 被过滤成空 |
| C1 | 本机 docIndex 陈旧记成别的项目，后端 owners 已明确是当前项目 | 本机旧记录否决后端正确 last |
| C2 | 两个后端进程共用 data_dir | 线程锁挡不住跨进程整份读改写，last / owners 丢更新 |

根因不是哪一行写错，而是**五样东西被当成了一样**：正文、恢复指针 last、归属 owners、前端 currentDoc / 项目缓存、
以及「这次异步恢复做完了没有」。

## 决定

### 一、恢复判决与正式文档安装分开（前端）

- **项目 ready ≠ 文档 ready。** documentStore 有一道恢复门：`restoring`（判决在路上）/ `needs_user`（判决结果是要用户处理）/
  `ready`（文档已定，原因：restored / prepared / explicit / empty / blank_requested / continued_new / superseded / initial）。
- 门关着时内存里的是**占位文档**：所有文档写入口拒绝（状态条节流说一句）；不写 currentDoc / docIndex / owner / last / 时间线、
  不排自动保存。**离开旧文档的那次冲刷在设门之前做**，DiskWriter 已排的旧文档写入不清。
- 恢复带**令牌**（`gen` + `pj`）；只有持当前令牌的安装能在门关着时写。显式意图（新建、打开排版、打开最近、教程准备、
  继续新建、选中候选）一律**取代**旧恢复：旧恢复的晚到结果没有提交权。没有跨项目永久的「已恢复」标记。
- 冷启动、Picker、重试走同一个 `resolveProjectDocument`；线索在任何 reset 之前捕获。`restoreSession` 不再按
  `currentDoc === documentId` 判「已经装好」（旧合同「`documentId` 已经是它就不读盘」被门的落定状态替代）。
- 外部交接：native 接收 / 确认不等文档（终端不会被堵到超时）；实际落图、打开 Figure 选择器先 `whenDocumentReady`，
  被取代 / 超时（120 s）有终局（`deferred`）。

### 二、读取结果不提前折叠

`found / missing / unsupported(404) / unavailable(网络、超时、5xx、401、403) / invalid / schema_too_new / index degraded`
各自分开。暂态失败先有界重试原目标（250 ms、1000 ms）；**读不到不自动退回本机**：会话端点重试后仍不可用 → `needs_user{session_unavailable}`，本机线索只作已验证候选；正文读失败（5xx / 401 / 403 / 磁盘上坏）→ `needs_user`，本机崩溃副本只作用户明确选择的「打开本机副本」（它可能比磁盘旧）；只有确认的 404（端点不存在 / 槽位不存在）才按本机线索 / 本机副本自动恢复。每条安装路径都核对正文 revision：读到的与指针 / 扫描 / 提供候选时记下的不同 → 先问 `/owner`，只有 owners 证据且 verified 才装；首选明确缺失 / 坏 / 太新 → 保留失败原因，只列**只读验证过**
的其他候选给用户选，**绝不悄悄打开旧稿后宣布原稿恢复成功**。候选探测（`probeDocument`）零副作用，选中且仍有提交权时才
`acceptProbe`（记修订号与基线、挪恢复副本、推回磁盘都只在这里）。

**产品决定（自动采用）**：没有任何首选目标（后端 last 为空、本机无缓存、currentDoc 无归属证据）、没有失败的首选、
发现列表里**恰好一份**明确属于本项目且有效的候选 → 自动恢复并在状态条说明来源；≥2 份让用户选；只有归属未知的不阻塞
新建（「打开排版」里可找回）。该例外**不适用于**「最后那份失败、只剩旧稿」。

### 三、last 只指向可恢复正文（后端）

每个项目组：`last`（durable，槽位有有效正文）、`prev`（上一份 durable，容量 1）、`pending`（最新打开意图但槽位还没正文，容量 1）。
`PUT /api/layout-session/last` 在槽位验证过（同一把文档锁内）才写 durable，否则只记 pending；正文首次落盘成功时，在同一个
文档锁里把匹配的 pending 晋升。于是：旧前端「early last → 首次 autosave」不再留下悬空 last；A 的迟到保存不会把 last 从 B
拉回（只晋升匹配 pending 的那份）；已有正文的文档被明确打开即刻成为 durable。清理保护覆盖 last + prev + pending。

### 四、owner 与正文同序；元数据失败可见

正文写入、revision、owner + `owner_revisions` + pending 晋升都在同一把 `_document_lock` 里提交；全局锁顺序**文档锁 → 会话锁**。
这只保证并发提交顺序，**不是**跨文件崩溃事务：进程死在两个文件之间时，`owner_revisions` 与槽位 revision 对不上 → 该归属按
「待核对」处理（不当别人的、也不当本项目的）；元数据写失败时 autosave 仍 200，回 `metadata:"failed"` 与真实 revision，
前端在顶栏单独说「下次自动恢复信息没更新」（与保存进度正交），下次保存补记。旧记录（无 owner_revision）按原样信任——兼容边界。

### 五、索引损坏 ≠ 首次空状态

会话文件不存在 = `missing`；坏字节 → 原字节存 `state/layout-sessions.corrupt-<ms>.json`（最多 3 份）再试 `.bak`（上一份好状态）；
读 I/O 失败或未知未来格式 → `degraded` 且**拒绝写回**（不拿空状态盖一份可能完整的文件）；单字段坏只丢那个字段，未知键保留。
旧合同「读坏了当成空的、下一次写整份替换」作废。

### 六、找回入口（兑现 ADR 0108 §四）

只读 `GET /api/layout-session/candidates?scope=project|unknown`（认证下、分页有界、不回路径、坏文件只标自己）按真实槽位枚举。
「打开排版」对话框加两组：「这个项目的本机排版」（原 id 打开）与折叠的「本机上其他排版（归属待确认）」。未知归属**默认恢复为副本**：
新 docId（`d_rc_<源前缀>_<revision 前 8>`，双击 / 重试幂等）、经正常保存链建立本项目归属，源槽位与源归属不动；确认时重取正文比
revision，变了要求刷新；取消零写入。不为列表运行脚本、不编缩略图。

### 七、C2：跨进程互斥

桌面版与浏览器模式（`tavotto` / `run.sh`）用同一个 `data_dir()`，两个后端同时开着是可达状态。会话状态的读改写在线程锁之外再加
`state/layout-sessions.lock` 文件锁（POSIX `fcntl.flock`、Windows `msvcrt.locking`，5 s 拿不到 → 503 `session_unavailable`）。槽位的文档锁同样跨进程（64 条带，`state/doclocks/<条带>.lock`，条带按 `zlib.crc32`，30 s 拿不到 → 503），覆盖判冲突、写正文、读 revision 与元数据提交；所有槽位删除都在会话锁里，发布 durable last 前在会话锁里再核一次槽位签名。

## 不做

- 不引入数据库 / 云同步 / 第二个文档 store；不重写渲染器；不升级正文 schema。
- 未知归属不自动认领到当前项目；脚本同名 / fingerprint / 素材同名都不构成归属转移（只用来解释候选）。
- 不放宽未知归属槽位的清理保护。
- 不声称正文与会话两个文件获得了崩溃原子性。

## 验证

审计夹具转成的正确行为验收（`web/src/store/recovery.acceptance.test.ts`、`tests/test_layout_recovery_acceptance.py`）在
`e090923b4` 上因目标缺陷红、修复后绿；各模块单测与反证表见 PR 正文。Windows 的 `msvcrt` 锁与真窗口重启（A31）只有对应平台
跑过才算数。
