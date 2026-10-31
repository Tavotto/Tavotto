# ADR 0125：桌面拥有的 native 脚本进程——第二类进程所有权、不可变启动身份、取消与租约

日期：2026-10-13 · 状态：**Proposed**（兼容性改造 P06，随该 PR 评审）
相关：[0014 Safe/Native 两档](0014-safe-native-execution-profiles.md)、[0020 Native Matplotlib Bridge](0020-native-matplotlib-bridge.md)、
[0021 `tavotto run` 产品契约](0021-tavotto-run-product-contract.md)（本 ADR **修订其 §1** 的适用范围，不推翻）、
[0107 采用用户 Python 时默认脚本目录](0107-silent-system-interpreter-adoption-and-native-workdir.md)、[0116 准备会话](0116-preparation-session-as-gui-entry.md)；
规则全文 `docs/rules/backend/tavotto-run-control-plane.md`「桌面拥有的脚本进程（P06）」。

## 问题

兼容性审计（EX01–05 / 07–10 / 13）的结论：**环境选对仍不保证结果对**。safe worker 即使换成用户的 Python 也继续吞 `savefig`、
守卫改写文件操作；真实的 `savefig → 读回`、`os.replace` 语义只有 native 路径保留。而 native 目前只有一种进程关系——
`tavotto run` 的 CLI 拥有用户的 Python，sidecar 只通过 relay 附着（ADR 0021 §1：**sidecar 绝不 spawn 用户进程**）。
新手在桌面里直接打开 `.py` 并运行没有终端、没有 CLI，要走 native 就只能由 sidecar 自己起进程。

三处合同挡在前面：

1. ADR 0021 §1 的「sidecar 不 spawn」是为终端拥有的进程写的（它保住 stdin / env / cwd / Ctrl+C 四样）；
   桌面没有这四样可继承，规则没法照搬，也不该被某个调用点悄悄 `Popen` 绕过。
2. `execspec` 里 native 禁 `run_config`、`env` 恒 None、argv 公开形状是「用户自己的命令行」；`preparation.plan_for` 固定构造 safe spec。
3. `NativeSession.force_kill` 是 `shutdown` 的别名（放手）。若为了「能取消」把它改成 kill，**终端里用户自己的进程会被误杀**（EX09）。

## 决策

### 一、进程所有权是 native 档的一维：`terminal`（默认）与 `desktop`

`ExecutionSpec.owner` ∈ {`terminal`, `desktop`}，仅 native 使用（safe 恒空串，其进程归 pool）。

| | terminal（`tavotto run`） | desktop（本 ADR） |
|---|---|---|
| 谁 spawn | CLI | sidecar（`desktopproc.start`） |
| stdin | 继承终端 | 桌面持有：`pipe`（write/close=EOF）或 `null`（立即 EOF）；P07 增 `bridge`（提问走控制通道由界面作答，[ADR 0126](0126-desktop-owned-script-input-and-run-entry.md)） |
| stdout / stderr | 继承终端，Tavotto 不碰 | 桌面持有的管道，**持续排空**；有界尾部 + 可选 sink；与控制协议分开 |
| cwd / env | 调用者 shell 的，原样 | 启动身份里显式给的 cwd；env = **sidecar 环境 + 显式增量** |
| `force_kill` / `shutdown` | 放手（detach），脚本继续 | 只断开控制连接，**不结束进程、不放租约** |
| 结束进程 | 用户在终端按 Ctrl+C；Tavotto 不杀 | `cancel_owned()`：结束进程组 / Job 并等确认 |
| 租约何时放 | 控制连接结束 / CLI 走了 | **所有者确认进程组 / Job 空**之后 |

ADR 0021 §1 在 terminal 这一类上**原样有效**；desktop 是被显式建模的第二类，不是对它的绕过。
`cancel_owned()` 对 terminal 会话抛 `native_process_not_owned`；`force_kill` 的语义（放手）不变，有真进程级用例钉着（EX09）。

### 二、不可变启动身份

`execspec.desktop_native_spec()` 是桌面拥有的启动身份的**唯一构造函数**（`native_spec` 仍是 terminal 的）。字段：

* 解释器（绝对路径；不接受 `py -3.12` 之类 launcher）、入口（脚本或 `-m` 模块，`raw_target` = `sys.argv[0]`）；
* `argv` + 运行配置引用 `run_config`：argv 非空时引用必填。**原文只在进程内**（`to_payload()` 与 OS 命令行——脚本必须收到它，
  与 terminal 拥有的同样可被 `ps` 看到；这是 native 的定义，不是新泄漏）；公开投影（`stable_payload` / `launch_context` / 回执 /
  `public_state` / 诊断）只带 `argv_count` 与 `run_config`；`repr` 也不带值；
* `cwd`：显式绝对路径；
* `env`：叠在 sidecar 环境上的**增量** + `env_source`（今天只有 `sidecar_inherited`）；公开投影只带**键名**；
* `stdin`：`pipe` | `null`；
* 回执关联：`launch_id`（会话 id 的一部分）与 `run_config`；回执的 `spec_stable` / `launch_context` 取自会话上保存的这份身份
  （`receipt.from_native_session`），不从会话元数据重建。

构造即校验：`owner=desktop` 只能与 `profile=native` 同现；terminal 拥有的 native 仍然拒绝 `run_config` / `env` / 非继承 stdin。
**safe 拿不到任何开关**：`owner` / `env_source` / `stdin` 在 safe 上构造即 `ValueError`，safe 的守卫与吞 savefig 一字不动（有用例）。
terminal 的 `stable_payload()` / `launch_context()` 逐字节不变（golden）；只有 desktop 多出 `owner` 等键。

### 三、cwd

桌面拥有的进程**没有沙盒这一档**。默认 cwd = 脚本所在目录（用户自己的 Python「像终端里那样跑」，ADR 0107）；用户在工作目录问题里选了
`project_root`（ADR 0057）则是项目根（`execspec.desktop_cwd`）。`launch_context.cwd_origin = "desktop.cwd"`，`write_mode = "unrestricted"`——
如实告诉用户：这一档的脚本拥有与终端里自己敲命令相同的权限。
`preparation.plan_for(execution="native")` 据此构造计划里的 `LaunchContext`；**默认仍是 `safe`**，换解释器与换执行语义是两个维度，
选了用户的 Python 不会让 safe 自动变成 native。把它接到桌面入口、runner 与首跑状态机是 P07，本阶段不接。

### 四、环境上下文：不声称没有证据的恢复

`env_source` 只承认 `sidecar_inherited`。激活过的 conda / venv / IDE / 内核环境没有被恢复的证据，就不写进身份，也不声称恢复：
调用方要让脚本看到某个变量，就显式放进 `env` 增量（公开投影只暴露键名）。整份继承环境（含密钥）既不序列化也不落盘。
原环境缺包 / 激活问题是上层准备的输入；适配器不猜包、不热安装。

### 五、所有权规则：断开不是结束；租约在确认后才放

1. **启动前取租约**：`envlease.acquire_native` 在 spawn 之前；环境正在安装时抛 `EnvironmentBusy`，一行用户代码都不跑。
2. **断开不是结束**：WebView / socket 断开、transport poison、`session.shutdown()` 都只关控制连接（状态 `detached`），进程照跑，租约照持。
   屏障上断线时 runner 先恢复 baseline 再放开（ADR 0021 §8.1），脚本继续跑完。
3. **租约在所有者确认后才放**：`DesktopProcess.finished` = 直接子进程已 reap **且** 进程组 / Job 已确认空。确认不了（进程不死）就一直持有租约并在
   后台重试——不谎报，让安装器改一个仍有脚本在用的环境。
4. **只动自己的组**：POSIX 子进程 `start_new_session`（自成进程组，pgid == pid），取消 = `killpg(SIGTERM)` →（`term_grace`）→ `killpg(SIGKILL)`；
   Windows 为 Job Object（`KILL_ON_JOB_CLOSE`），取消 = `TerminateJobObject`。**不按 PPID 猜、不遍历进程表**；组外的子进程（自己 `setsid` 走的）
   不是我们纳管的，如实不管。脚本自然退出后组里的残余再给 `linger_grace` 收尾，仍在就按取消强度收掉——否则它们会带着租约的环境活下去。
5. **sidecar 退出**：`REGISTRY.shutdown_all()` 对 terminal 会话仍只放手；对桌面拥有的会话先断开再 `cancel_owned()`（有界），不留孤儿。
   结构见下「关停与启动的交界」。
6. **启动时序**（`desktopproc.start`）：占位(`launch_id`) → 租约（会话 id 每次启动唯一后缀）→ spawn → 握手 → **reader 先起** →
   登记 → **最后才放行所有者的完成回调**（`DesktopProcess` 的启动闸门 `arm()`）。快退出的子进程因此既不会在 reader 起来前被判
   「没通知就走了」，也不会在登记前放掉占位；任何失败路径先 `arm()` 再 `cancel()`。启动期间的 `cancel()` 会等闸门（微秒级，登记走完即放）。
   残余：`owner.start()` 自己抛错（线程创建失败）时闸门会等 `finally` 的 `arm()`，不处理更深的资源耗尽。
7. **退出码原样**：脚本自己的退出码（`sys.exit(3)`、未捕获异常的 1）保留；POSIX 被信号杀折成 `128+signo`（`runcodes.exit_code_of`，与 `tavotto run` 同口径）。
   取消只额外记 `cancelled=True`，不改写码；Windows 取消的 Job 退出码是 `EXIT_CANCELLED`(3)，靠 `cancelled` 区分。

时间全部在 `OwnedPolicy`（连接 / 先礼 / 强杀 / linger / 尾部大小），测试用毫秒级，产品用默认。

### 六、编辑通道与重跑

只复用既有机制：单 reader 的 `NativeTransport`、`NativeSession` 状态机、`enginesession.resolve`、restore-before-continue、临时导出隔离。
进程活着才有对象编辑；结束后编辑报 `native_session_ended`，不 pickle Figure、不为进入编辑重跑用户计算。
桌面拥有的会话的**展示面由准备会话独占**（V2 PR4）：前端 `nativeSessionStore` 对 `owner === 'desktop'` 的会话不 build、不放置图、不开选择器（runner 自己已 `ensure_built`），
图只由准备卡的「进入编辑」（或「打开并运行」完成后的自动进入）放进画布；终端拥有的会话照旧。握手是 `NativeRelay.adopt_child`
（同一份认证 accept，只要 child 一侧），没有第二个协议 reader。

## 后果与残余（如实）

* Windows：Job 路径写好并以替身 kernel32 在 CI 单测；**真机行为 not_run**。spawn 与入 Job 之间有极短窗口，窗口内进程自己起的孙进程不在 Job 里（残余，不是消除）；
  入 Job 失败时退化为只管直接子进程，`scope_kind="process"` 如实报告。
* POSIX：sidecar 被 SIGKILL / 崩溃时没有 `KILL_ON_JOB_CLOSE` 的等价物，owned 子进程成孤儿（Linux 的 `PR_SET_PDEATHSIG` 对多线程 + `preexec_fn` 不安全，未用）。
  PID 1 不回收孤儿僵尸的容器里，「组确认空」可能因僵尸成员而不成立（租约会一直持有，偏保守）。
* stdout 管道对 Python 默认块缓冲：要实时输出，调用方在 `env` 增量里放 `PYTHONUNBUFFERED=1`（P07 决定默认）。
* 不支持：任意 GUI 事件循环、Jupyter/IPython、子进程创建的 Figure、已运行的外部宿主 attach——与 ADR 0021 §0 同。
* 本阶段**不接**前端 / 桌面入口 / preparation runner / 输入弹窗（P07）；桌面 GUI、Windows/macOS 真机归 P07 / P10。

## 关停与启动的交界（`REGISTRY.shutdown_all()` × `desktopproc.start()`）

结构：注册表有 `closing` 状态。`shutdown_all()` 第一步在锁内置位，此后 `reserve` / `begin_start` / `attach_owner` / `adopt`
一律原子拒绝（`native_registry_closing`）；在途启动在下一个检查点看到拒绝，**自己**收掉子进程（走既有失败收尾：关传输、`arm()`、`cancel()`、
放租约与占位）。`begin_start` 在 **spawn 之前**领 `StartTicket`，Popen 之后才 `attach_owner`——快照里要么看得到票据（标 `cancelled`，
挂所有者的一步被拒），要么 closing 已置位根本领不到。关停对已登记会话走 `cancel_owned()`（等到 `finished`），对在途所有者走
`terminate_sync()`（先礼 `term_grace`，后兵，**同步**等进程与进程组确认消失再返回；不等启动闸门）。`forget` / `prune` 不得移除
`owned` 未 finished 的会话。`reopen()` 仅供测试 / 同进程重启（`tests/conftest.py` 每个用例前后调用）。

窗口清单（逐个标注）：

| 窗口 | 处理 |
|---|---|
| 关停先于 `reserve` / `begin_start` | closing 拒绝，一行用户代码没跑 |
| `begin_start` 之后、Popen 之前关停 | Popen 前有检查点（`check_start`）看到 cancelled 就不再 spawn；`shutdown_all` 还会等每张票据的启动线程收尾（`StartTicket.done`，上限 30 s），所以返回时不会再有子进程冒出来 |
| Popen 之后、`attach_owner` 之前关停 | `attach_owner` 被拒，启动线程自己收子进程（`shutdown_all` 同样等它收尾） |
| `attach_owner` 之后、握手 / reader / 登记之前 | 快照里有所有者，`terminate_sync` 同步收；启动线程看到子进程退出走失败收尾 |
| `adopt` 之前 closing | `adopt` 拒绝，失败收尾收进程 |
| 已登记、`arm()` 之前 | `cancel_owned()` 等闸门（微秒级） |
| 进程忽略 SIGTERM | `term_grace` 后强杀，返回前确认（POSIX 有用例；Windows 的 Job 本就是强杀） |
| `shutdown_all` 返回之后才进来的启动 | closing 拒绝 |
| **残余**：sidecar 被 SIGKILL / 崩溃 | POSIX 无 `KILL_ON_JOB_CLOSE` 等价物，子进程成孤儿（Windows 靠 Job） |
| **残余**：`terminate_sync` / `cancel_owned` 的并发上限 60 s | 超时仍在则不阻止退出（租约随进程消失） |
| **残余**：`owner.start()` 自己抛错（线程创建失败） | 不处理更深的资源耗尽 |

会话产物目录默认用每次启动唯一的会话 id（`native-owned-<launch_id>-<后缀>`），同一 `launch_id` 重试不继承上一次的 `.preview-ledger` 与产物。

输出目录由所有者独占、每次启动唯一：`OwnedLaunch` 不接受调用方指定的目录（共用目录会互相争用并读到别的启动的旧产物，相对路径在 sidecar
与子进程里还会解析到不同位置）。在途票据一直留到所有者 `finished`，由它的完成回调撤销：握手 / 登记失败后清组超时，`shutdown_all` 仍看得见并等它。
