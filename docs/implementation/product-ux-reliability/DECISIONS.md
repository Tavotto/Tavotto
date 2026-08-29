# DECISIONS — 本改造轨道的架构决策

> **单一权威原则**（根 `AGENTS.md`）：一条规则只有一个出处。
> 因此本文件里**大部分决策已经是仓库既有 ADR**，这里只写"本轨道怎么用它"
> 并指向权威；只有轨道内新产生的决策才在 `docs/adr/` 新开一份。
>
> 编号 `T-nn` 只是本文件内的索引，不是 ADR 编号。

---

## T-01 自动刷新只做静态扫描，绝不运行用户脚本

**权威**：`docs/adr/0014-safe-native-execution-profiles.md`、
`docs/adr/0020-native-matplotlib-bridge.md`、根 `AGENTS.md` 安全边界段。

**背景**：打开项目、autosave、watcher、readiness、问题检查、自动刷新都想
"知道图现在长什么样"，而最直接的做法是跑一遍用户的脚本。

**决定**：这些路径只允许静态扫描、读 AST、合并 registry、算 fingerprint、
校验文档。**执行**（probe / 渲染 / native 会话 / 依赖安装）一律由用户显式触发，
并显示目标环境与结果。

**现状已落实**：`POST /api/registry/probe` 是显式动作；watcher 只比 mtime；
`/api/native/*` 全部要用户批准（`engine/nativeperm.py`）。

**禁止的替代实现**：在 readiness / watcher / 打开项目里"顺手 probe 一下"；
为了拿一个尺寸就跑一次 `main()`。

---

## T-02 项目文档必须版本化、可迁移、原子写入

**权威**：本轨道新增 → `docs/adr/0023-document-persistence-authority.md`（Prompt 02 落地）。

**背景**：起始 commit 上有 6 处以上各自手写的 "tmp + replace"，其中
`POST /api/layouts/<name>`（用户的"另存为"，最显眼的一次保存）**根本不是原子写**；
没有一处做 fsync，没有一处在失败时清理临时文件，没有一处返回结构化错误。

**决定**：Python 侧只保留**一个**原子写实现，所有文档类写入经它；
文档格式有显式 schema version 与显式 migrator；写入前拒绝非有限数
（NaN / Infinity）。

**后果**：新增写入点必须复用它；`ruff`/测试会挡住第二份实现。

**禁止的替代实现**：在新端点里再抄一遍 tmp+replace；用 `write_text` 直接盖；
用 `json.dumps` 默认参数（它会写出非标准的 `NaN` 字面量，浏览器 `JSON.parse` 解不动）。

---

## T-03 用户文档与派生元数据严格分离

**权威**：本文件 + `UX_CONTRACTS.md` §3 的所有权表。

**决定**：派生数据（registry 映射、fingerprint、worker id、缓存路径、SSE 状态）
的刷新**不得**把文档标脏，也不得进普通撤销历史。出口只有
`documentStore.silent(recipe)` 一个。

**理由**：一旦派生刷新能置 dirty，autosave 就会在用户什么都没做的时候
写出新的 `updatedAt`，多标签页立刻互撞 `stale_write`（这条路径在
`documentStore.ts:892` 的注释里已经被踩过一次并写进了代码）。

---

## T-04 快速编辑与画布排版共享一个文档和编辑底层

**权威**：`docs/adr/0001-project-canvas-tab-object.md`。

**决定**：Prompt 09 引入的"快速编辑"是**同一个 `ProjectDocument` 上的一种模式**，
不是第二个文档模型。模式差异只体现在：默认页面尺寸取原图规格、
导出默认按原图规格、UI 隐藏画布相关控件。

**禁止的替代实现**：给快速编辑单开一套 store / 序列化 / 导出路径；
把 `FigureDocument` 复制成 `FastEditDocument`。

---

## T-05 Style / Spec / Validation / Export 分层

**权威**：`src/tavotto/profiles/publication.json`（Spec 规则的唯一出处）+
根 `AGENTS.md` 的严格同源对表。

**决定**：四层边界见 `UX_CONTRACTS.md` §4。Validation 只读 Spec 求值，
两个求值器靠 `tests/golden/preflight_vectors.json` 对齐。
文档里只存 `profile.id` 与期刊覆盖。

**禁止的替代实现**：在 `preflight.ts` 或 `preflight.py` 里硬编码任何阈值；
把规则快照冻进 `.tavotto` 文档。

---

## T-06 Validation 结果通过稳定对象引用定位

**权威**：`web/src/lib/preflight.ts` 的 `PreflightIssue`。

**决定**：问题项持有 `objectIds` + `gids`（+ 后续补 `canvasId`），
不用数组下标 / 文件名 / matplotlib 内部 id。`issue.id` 是稳定的判据身份，
措辞变化不影响 golden vectors。

**已有的等价类型就是 ObjectRef，不另造第二套。**

---

## T-07 Codex / AI / 包管理复用统一服务

**权威**：`docs/adr/0015`（Coding Agent 注册表）、`docs/adr/0018`（项目 Python 环境解析）、
`docs/adr/0019`（受控依赖修复）、`docs/adr/0005`/`0006`/`0012`（Codex 插件与 MCP）。

**决定**：Prompt 19/22 只做 UI 外壳与接线，不新建第二套 agent 发现、
环境解析或安装实现。包管理只操作 Tavotto 管理的环境，
package spec 结构化校验，禁止 shell 拼接。

---

## T-08 中英文、无障碍、数据恢复是完成条件

**权威**：`docs/i18n.md`、`pnpm i18n:check` 门禁、根 `AGENTS.md`。

**决定**：任何阶段的"完成"都包含：中英文文案齐全、键盘可达、
`prefers-reduced-motion` 生效、以及"这个阶段引入的数据在崩溃后可恢复"。
缺任何一条视为未完成，不得靠"后续阶段补"通过 Gate。

---

## T-09（本轨道自加）不为了本轨道推翻已通过测试的架构

**背景**：起始仓库已经有 1338 条前端用例与百余个 pytest 文件，
写回事务、几何权威、遥测白名单等都有专门的不变量看护。

**决定**：本轨道遇到"现状与 Prompt 描述不符"时，**以现状为准并记录**，
只补缺口。禁止把仓库恢复成 Prompt 编写时的旧版本，禁止趁机重写已稳定模块
（与根 `AGENTS.md` 的 1.0 收敛纪律一致）。

---

## T-10（Session 03）保存状态是文档的字段，但不是一个大枚举

**权威**：`docs/adr/0024`、`web/src/store/documentStore.ts`。

**决定**：`saveState` 只装保存生命周期的六个值
（`clean` / `dirty` / `saving` / `saved` / `save_error` / `conflict`）。
Prompt 03 §二 与它们并列的 `recovery_available` / `read_only` 走正交的
`docNotice`。

**理由**：它们与保存进度是**两根互不相干的轴**。塞进同一个枚举，两者会互相
顶掉——恢复副本还在本机躺着，一次成功的自动保存把状态推成 `saved`，横幅没了，
副本要等到下次启动才再被想起来（实现过程中真的撞见过这一下）。`read_only`
更明显：schema 太新的那份**根本没有打开**，当前文档是一份能存能改的空白文档。

**与 Prompt 字面不同是有意的**：Prompt 的退出条件是行为（恢复可预览可恢复、
只读不伪装可保存），不是枚举里必须出现那两个字面量。

---

## T-11（Session 03）判据要把两条边都钉住

**权威**：`app.py::_revision_conflict`、`web/src/lib/api.ts::REVISION_ABSENT`。

**决定**：外部修改检测的基线有 `absent` 哨兵（「我读过，磁盘上没有这份文件」），
且基线缺席时**一律**先 GET 确认一次，没有例外。

**理由**：只挡「hash 对不上」的话，两个标签页同时新建同一份文档时双方都拿不出
hash，后写的整份盖掉先写的——而这正是这条判据要挡的事。同理，「只有读盘失败
时才确认」看着更省一次 GET，但新建 / 载入 / 启动初始文档三种情况一样是两个基线
都没有。**判据留了例外就会从例外那一侧漏。**

反方向也要说清：hash 基线遇上磁盘无文件（被外部删了）**放行**。这条判据挡的是
「覆盖别人的内容」，不是「重建一个被删掉的文件」。两侧不对称是结论，不是疏漏。

---

## T-12（Session 03）「缺席」不许补默认值

**权威**：`web/src/lib/versionTarget.ts`、`app.py::_version_meta`。

**决定**：旧版本检查点没有 `canvasId`，读到的地方**不补**当前画布 id，
也不补空串；它是独立的一档 `unknown`，恢复前必须用户点头。

**理由**：补出来的身份恰好总是「允许直接覆盖」。同一形状在本次还出现两次：
修订号「读到了但没有 hash」不能并进「磁盘上没有」，摘要「读不出来」不能并进
「各项为 0」——每一次图省事的合并，都把一个「不知道」说成了一个具体的断言。

---

## T-13（Session 04）刷新的编排只有一份，副作用出口靠注入

**权威**：`docs/adr/0025`、`engine/project_refresh.py`、`app.refresh_project()`。

**决定**：registry 合并 / 重载 / diff / worker 失效 / 事件发布的编排只在
`refresh_project_index()` 里；SSE 与 watcher 重挂由 app 层作为 `RefreshSink`
注入。`app.py` 的 `reload_registry()` 删除，三个老调用点全部改走统一入口。

**理由**：改造前这条链路在 `app.py` 里有三份，而它们的**答案不一样**——
三份里没有一份作废过 worker（改了 entry、挪了 stem，热会话手里还是老的），
没有一份说得出"什么变了"，素材根本不在视野里。第四条路径（Prompt 05 的
watcher）照着任意一份再抄一遍，分叉就有四份。

**注入而不是回头 import**：`engine/project_refresh.py` 要能被 Flask 父进程
安全 import（`.venv` 没有 matplotlib），而 SSE / watcher 回调是 app 层的东西。
它连 `engine/documents` 都不 import——"派生刷新不碰文档"因此是结构性的，
不是一条注释。

---

## T-14（Session 04）恒等成立的 diff 与"没变化"长得一模一样

**权威**：`engine/project_refresh.refresh_project_index()` 里那段注释、
`RefreshState.assets`。

**决定**：素材 diff 与**上一轮的清单**比（基线存在 `RefreshState`，项目打开时
`seed_state()` 落一份），不与同一次调用里的"刷新前快照"比。

**理由**：Prompt 04 §四 的流程写的是"刷新前素材快照 → …… → 刷新后素材快照"。
照着实现，这条 diff **永远是空的**：刷新会改注册表，所以 registry 的前/后有
内容；素材它一个字节都不碰，同一次调用里的两张快照必然逐项相同。

这是"对拍的尺子量不了那个维度"那一族——判据没错，它只是恒等成立，而恒等
成立的 diff **看起来和"什么都没变"一模一样**，没有任何信号提醒你它坏了。
本次是靠一条真的加了图片的用例红出来的（先写用例、后发现设计错）。

**顺带**：没有基线的那一轮报 `assets.baseline=true`，不报"没变化"——第一次
刷新报空比不报还糟，它是一句**错的断言**而不是"我还不知道"（同 T-12）。

---

## T-15（Session 04）"没跑过"与"跑了没发现"是两档

**权威**：`refresh_project_index()` 的 `registry.conflicts`。

**决定**：没跑静态扫描的那些刷新（probe / 手工登记，`allow_static_merge=False`）
返回 `conflicts=null`；跑了且没有冲突返回 `{}`。

**理由**：合并成 `{}` 的话，调用方会把"不知道"读成"已确认没有冲突"，而
RegistryDialog 正是靠这个字段决定要不要显示冲突区。同一形状 Session 03 已经
踩过三次（T-12），这次在写之前就分开了。

---

## T-16（Session 04）watcher 认自己写的那一下靠内容，不靠时间窗口

**权威**：`project_refresh.is_self_written()`、`RefreshState.registry_revision`。

**决定**：Prompt 05 的 watcher 用**内容修订号**判"这份注册表我已经消化过"，
不用"写完之后忽略 registry 事件 N 秒"。修订号在**装载成功之后**更新。

**理由**：时间窗口两头都错——慢磁盘上 N 不够，快机器上 N 会吞掉用户真实的
外部修改。内容比较两头都对：用户把文件改回原样 = 内容没变 = 确实不用刷新。

**顺带把源头也堵上**：无变化的刷新**不回写**注册表（按字节比）。以前
`/api/registry/scan` 无条件重写，文件内容一样、mtime 变了——而 mtime 一变，
watcher 就会看到一次"外部修改"，于是刷新自己触发下一次刷新。

---

## T-17（Session 04）挂不上的桩会让用例恒绿

**权威**：`tests/test_discover.py::test_registry_write_is_atomic`。

**决定**：`discover.write_config()` 并进 `atomicio` 的同时，把那两条看护用例的
故障注入点从 `Path.replace` 挪到 `os.replace`。

**理由**：`atomicio` 调的是 `os.replace`。注入点不挪的话，故障根本打不中，
用例会**恒绿**——而"没红"只说明桩没挂上，不说明失败路径是对的。这次它是以
`DID NOT RAISE` 红出来的（那条用例的注释早就写明了这个红法），换一条形状
（比如只断言"文件没坏"）就会静默变成一条空门禁。

---

## T-18（评审回合 1）对的判据放错了位置，一样挡不住

**权威**：`app.py::api_autosave_put` 的锁段、`docs/adr/0024` §外部修改检测。

**决定**：自动保存的「读修订号 → 判冲突 → 写 → 读回修订号」整段进锁
（按落盘路径取的固定 64 条锁带，不是 doc_id→锁 的表）。

**理由**：`_revision_conflict` 两条边都钉住了（T-11），但**判完到写完之间没有
互斥**，于是它只在请求串行时成立。两个标签页同时新建同一份文档：双方都在对方
落盘之前读到「磁盘上没有」、双方都判没冲突、后写的整份盖掉先写的，**而两边都
收到 200**——这正是 `absent` 哨兵要挡的那个场景，被交错执行绕了过去。

**这是一族**：T-11 说的是判据的内容要对，这条说的是判据的**位置**要对。
判据与它守护的动作之间只要有一个可以被别人插进来的间隙，判据就退化成
「在没人跟我抢的时候成立」。同一形状还有：交回去的修订号也必须在锁里读，
否则 A 拿到的可能是 B 刚写下的那份的 hash，而 A 的下一次写会带着它当基线
——后端一比「和磁盘一致」就放行，A 于是能静默盖掉 B。

**锁带而不是锁表**：锁表要自己治理生命周期，而「什么时候能删掉一把锁」没有
可靠信号（同 04 的 `RefreshState` 挂在 ctx 上的理由）。固定条数的锁带内存是
常数，不同文档偶尔共用一把只是多串行一点点。

---

## T-19（评审回合 1）两个 `except` 挡的不是同一件事，就不能同一个处置

**权威**：`engine/atomicio._fsync_dir()`。

**决定**：`os.open` 目录失败（Windows）继续忽略；`os.fsync` 失败只放过
EINVAL/ENOTSUP（确实没有这一步的文件系统），其余抛 `dir_fsync_failed`。

**理由**：以前两个 `except` 都是 `pass`，而它们挡的是两件不同的事——前者是
「这一步在这里不存在」，后者是「这一步失败了」。吞掉后者的后果不是少一次
fsync：调用方收到成功，前端据此把本机兜底副本删掉，用户手上从此只剩一份
可能撑不过掉电的文件。docstring 当时只解释了第一个 `except`，**第二个连
解释都没有**——那正是「被吞掉的 except 让门禁变空」的典型形状。

**反方向也要量**：把 EINVAL 也当成失败的话，那些文件系统上**每一次保存都会
报错**。判据宽过它要守的东西同样是缺陷（同 T-15 的「范围」维度）。

---

## T-20（评审回合 1）按钮要么做它说的事，要么说清为什么做不到

**权威**：`documentStore.overwriteDisk()`。

**决定**：`saveIssue` 里没有磁盘修订号时（`stale_write` 那条 409 比的是
updatedAt，body 里不带 hash），「覆盖」**去摘要那里补一个真基线**；补不到才
退回删除。

**理由**：不补的话，「覆盖」只是把 `diskRevision` 删掉，而下一次写之前的确认
又会探到一份「我从没读过的文档」→ 原样再弹一次同一个框。用户看到的是一个
点了没反应的按钮（第二次点才成功，因为那时 `saveIssue.disk` 已经换成带
revision 的摘要了）。

**不能改成清空基线**：清空等于此后每一次写都不再校验，按一次「覆盖」把这份
文档的外部修改检测永久关掉。
