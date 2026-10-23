# `tavotto run` 的控制面（ADR 0021，Beta）

> 原文出自 `src/tavotto/AGENTS.md`「`tavotto run` 的控制面（ADR 0021，Beta）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/native/`（含 `test_run_cli_integration.py`、`test_native_inconsistent.py`）、`tests/test_windows_regressions.py`

进程关系是**倒过来的**——用户的 Python 是 **CLI 的子进程**，sidecar 只是
通过一条认证 relay 连上去：

```text
用户终端 → tavotto run CLI ─┬─ 用户的 Python（Bridge Runner）
                            └─ Tavotto 桌面 sidecar
```

| 模块 | 职责 |
|---|---|
| `runcodes.py` | **稳定错误码 + 中英文案的唯一出处**；`RunError`；退出码闭集 |
| `runspec.py` | 严格 invocation 解析（`--` 强制）、解释器体检、cwd vs project_root、status file |
| `runcli.py` | `tavotto run` 本身：拥有 stdio / env / cwd / 子进程，按顺序编排 |
| `nativehandoff.py` | 一次性交接凭据（0700 目录 / 0600 文件 / 墓碑 / 过期 / realpath 判据） |
| `nativerelay.py` | 两侧认证 + **纯字节**转发。**不许 import 任何引擎语义** |
| `nativesession.py` | sidecar 侧注册表 + **单 reader** 传输 + 状态闭集 + live route |
| `nativeperm.py` | "记住这个项目和这个 Python"（绑定 项目 × 解释器 × schema） |
| `envlease.py` | **环境占用的唯一一张表**：safe worker / native 会话 / pip 安装三方共用 |
| `desktopproc.py` | **桌面拥有的**用户 Python（P06，ADR 0125）：spawn / 进程组·Job / stdio 排空 / 取消 / 确认结束；复用上面的 relay 认证 accept、`NativeTransport`、`NativeSession`、`envlease` |
| `enginesession.py` | **"谁来渲染"的唯一判据**（按 `execution_profile` 路由） |

改动纪律（每一条都有用例，改之前先看它们）：

- **`tavotto run` 的 CLI 必须继续拥有用户的 Python。** 让 sidecar 去 spawn 会同时失掉
  stdin / stdout / cwd / env / Ctrl+C 五样（ADR 0021 §1）。桌面里直接运行 `.py` 没有 CLI，那是**第二类**
  进程所有权（见下一节），不是对这条的绕过：只有 `desktopproc.start` 一个 spawn 点。
- **确认之前一行用户代码都不许跑。** 顺序是产品语义的一部分
  （`test_not_a_single_line_runs_before_the_user_confirms`）。
- **Tavotto 的话只写 stderr。** stdout 是用户程序的——所以也没有 `--json`。
  **`--help` 是唯一的例外**：它在解析阶段就返回，一个子进程都没起，stdout 此刻
  不归任何用户程序，而 `--help` 是用户要的输出（POSIX），所以走 stdout 退 0；
  用法错误照旧 stderr 退 2。两个流向一起钉在
  `tests/native/test_run_cli_integration.py`（ADR 0021 §10.1，issue #198）。
- **`creationflags` 必须显式声明是哪一类**：GUI 拥有的隐藏子进程用
  `CREATE_NO_WINDOW`，CLI 拥有的控制台子进程用 `INHERIT_CONSOLE`
  （`test_windows_regressions` 按闭集判）。
- **屏障释放必经 `bridge_runner.release_barrier()`**：保存 patch → 恢复成
  脚本原样。下一个屏障 `rebase()` 重新采基准 + 重放。任何绕过它的释放路径
  都会让**故障路径上的语义比正常路径更宽松**（ADR 0021 §8.1）。
- **Figure 当前快照资格与已保存历史分开**：native release 已可能发出却无确定响应，或活屏障控制连接断开
  （runner 会释放屏障继续脚本），只撤当前屏障 receipt 的资格，不伪造成功执行事实。明确未发送 / 还原失败且连接仍活
  不撤；冷开时明示的已保存历史引用仍按文档合同读取。HTTP 返回的真实执行事实与 SSE 同样消费，不能按动作名猜是否执行；
  跨项目 approve 的归属来自已连接 session 的项目短 id，不能使用当前标签页。看护 `tests/native/test_native_release_outcome.py`、
  `test_native_api.py`、`web/src/store/nativeSessionSnapshot.test.ts`。
- **不许再写第二处 `pool.get()` 分支**：`app.py` 里所有"谁来渲染"都经
  `enginesession.resolve()`（结构性守卫
  `test_native_api::test_the_resolver_is_the_only_place_that_branches`）。
- **native 会话绝不进池**：LRU 淘汰会杀掉用户正在跑的脚本。
- **图与文档不一致只挡、不杀**（ADR 0080 / 0021 §9.3，Codex #549 第八轮）：`NativeSession`
  按 v1 render 结果的 `unrestored`（结构化字段，不解析 warning）记下「与文档不一致」的 stem
  与那份列表的 canonical hash；期间只放行同一份列表的重渲染（报 0 即解除），换列表的编辑、
  `export`、`preview_png` 一律 `native_figure_inconsistent`（409，与 offline 同一条路；导出
  作业里它和 offline 一样在拿 live 图那一步抛出）。「查标记 → 发请求 → 按响应更新」整段在
  会话的 `_figure_lock` 里（传输允许并发等待者，不锁就会在检查通过后排到不一致的 Figure
  上执行）；export / preview_png 临时套用再还原的那次失败同样在锁里记账（锚在会话列表上）；
  continue 成功后切 CONTINUING / DETACHED 也在同一段锁里（锁一放、状态还是 BARRIER 的
  那一瞬，等锁的渲染会把帧发给已经离开控制循环的 runner）。任何一张图不一致时 continue / detach 拒绝、terminate 放行；runner 侧
  `release_barrier()` 恢复不回去同样不放行（回同一个码、重放回编辑态；控制通道断了就按
  终止退出，ADR 0021 §8.1），两层各自成立。runner 的 `INCONSISTENT_CODE` ↔
  `runcodes.NATIVE_FIGURE_INCONSISTENT` 是严格同源对。看护 `tests/native/test_native_inconsistent.py`。
- **环境占用只有 `envlease` 一张表**：加第二张就保证了它们迟早不一致。
- **连接过的 socket 一律 `shutdown(SHUT_RDWR)` 再 `close()`。** Linux 上
  `close(fd)` **不唤醒**另一个线程里阻塞着的 `recv(fd)`——那个系统调用还持着
  底层的 file description，于是**套接字不拆、FIN 不发**，对端永远等不到 EOF；
  macOS 会让阻塞中的 `recv` 带 `EBADF` 返回，**所以这类缺陷本机恒绿、CI 恒红**。
  产品上的形状：用户按了 Ctrl+C，脚本收到了也退出了，但 runner 停在"脚本
  结束"那个屏障上等控制通道说话——通道没关、屏障不放、终端再也回不来。
  判据要两条：一条量**不变式本身**（替身 socket 记 `shutdown` / `close` 的
  调用顺序，任何平台都红），一条量行为（对端看不看得到 EOF，只有 Linux 红）。
  只留后者等于把判据的有效性押在 CI 的平台组合上。
- **runner 这一侧，握手之后对端 reset 与对端 EOF 是同一个结论：父进程走了**（#240）。
  CLI 撤掉 relay 之后 runner 只要再写一个字节（进屏障先发 `barrier` 事件），对端内核
  就回 RST，紧接着的 `recv` 读到 EOF 还是 `ECONNRESET` 只看 RST 与 `recv` 谁先到——
  纯时序。归错的形状是 Ctrl+C 之后一串 Tavotto 的 traceback + 退出码 1（而不是脚本的
  130），或者 `plt.show()` 屏障上的断线变成用户脚本的崩溃。归类只在
  `bridge_runner.Control` 一处（`ConnectionError` 一族，不放宽到 `OSError`；握手期间
  照旧大声失败）。判据用 `SO_LINGER(1, 0)` 的假父进程把次序钉死（只发 RST 不发 FIN，
  runner 不可能先读到 EOF）：`tests/bridge/test_bridge_parent_gone.py`。
- **native 面板"出自哪一档"只有一个出处**：`enginesession.profile_of()`。
  `/api/runtime/status` 的 `execution_profile` 与渲染路由读的是同一份，
  另立一份迟早在某个边角上分叉，而分叉的那一侧会在界面上显示成"能编辑"。

## 桌面拥有的脚本进程（P06，ADR 0125）

`ExecutionSpec.owner` 区分 native 档的两类进程所有权；`desktop` 的启动身份只由 `execspec.desktop_native_spec()` 构造，
`owner/env_source/stdin` 给 safe 即 `ValueError`（不是 safe 的开关）。纪律（看护 `tests/native/test_desktop_owned_process.py`、
`test_desktop_owner_scope.py`、`tests/test_execspec_desktop_owner.py`）：

- **`force_kill` / `shutdown` 永远是放手，不是 kill**——终端拥有的会话靠它不被误杀（EX09）。结束桌面拥有的进程只有
  `NativeSession.cancel_owned()`；终端会话调它抛 `native_process_not_owned`。
- **断开不是结束**：控制连接断了进程与租约照旧；租约只在所有者**确认**进程组 / Job 空之后放
  （`NativeSession._release_lease` 对 owned 会话看 `owned.finished`）。确认不了就持有，不谎报。
- **启动前取租约**（`envlease.acquire_native` 先于 spawn）；spawn 失败 / 连不上 / 提前退出都由所有者收进程、确认后放租约。
- **只动自己的组**：`start_new_session` / Job；不按 PPID 猜、不遍历进程表；取消发给本进程自己的 pgid / Job。
- **stdout / stderr 持续排空且不进控制协议**（用户输出里的伪控制帧不是协议）；stdin 通道（`pipe` / `null`）由桌面持有。
- 公开投影（`stable_payload` / `launch_context` / 回执 / `public_state` / 诊断）只带 `argv_count` / `run_config` / env 键名；
  值只在进程内（`session.spec` 私有，`repr` 不带值）。
- `creationflags` 声明 `CREATE_NO_WINDOW`（GUI 拥有的隐藏子进程）；Windows Job 代码在 CI 以替身 kernel32 单测，**真机 not_run**。
- `preparation.plan_for(execution="native")` 只改计划里的 `LaunchContext`，默认 `safe`；入口接线与输入见下一节（P07，ADR 0126）。

## 桌面拥有的脚本：输入与入口（P07，ADR 0126）

看护 `tests/native/test_desktop_input_bridge.py`（真 spawn 用户 Python）、`tests/test_desktop_open_to_edit_e2e.py`（真服务子进程 + HTTP）、
`web/src/store/nativeInput.test.ts`。纪律：

- **输入桥只属于 `stdin=bridge`**：`bridge_argv` 仅此档多 `--input-bridge`；terminal / `pipe` / `null` 命令行逐字节不变。runner 里提问的阻塞读
  在提问的主线程上，不起后台线程；`input_request` / `input_reply` 两个键名与 `nativesession.INPUT_REQUEST_EVENT` / `INPUT_REPLY_KEY` 严格同源。
- **EOF 粘性只属于 stdin 家族**（input / readline / read）；`getpass` 不受它影响也不制造它。
- **Pending 在后端、先到先得**：`answer_input` 在锁里认领再发；迟到 / 重复 / 错 id / 终端拥有的会话一律 `native_input_not_pending`（409）。
  事件与 `diagnostics()` 里没有提示词和答案；口令值不落任何盘（用例做字节扫描）。
- **取消是真终止**：准备会话取消、`POST /api/native/sessions/<id>/cancel`、关闭项目都走 `cancel_owned()`；`force_kill` 对 native 会话永远是放手。
- **入口只是换 runner**：`execution: "native"` 的 `script` 目标走同一个准备会话 / 同一个 `PreparationService` 状态机，只有 `_native_preparation_runner`
  替换 `pool.build_owned`；不另建状态机。省略 `execution` = safe，逐字节不变。
- 脚本自己的异常以 `script_error` + 真实 traceback 收场（进程放行跑完、退出码保留），不吞；native 路径不自动修复缺依赖。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- CLI 拥有用户的 Python
- 确认前一行用户代码不跑
- Tavotto 只写 stderr（`--help` 唯一例外）
- 屏障释放必经 `release_barrier()`
- socket 先 `shutdown(SHUT_RDWR)` 再 `close()`
