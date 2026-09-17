# `tavotto run` 的控制面（ADR 0021，Beta）

> 原文出自 `src/tavotto/AGENTS.md`「`tavotto run` 的控制面（ADR 0021，Beta）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
| `enginesession.py` | **"谁来渲染"的唯一判据**（按 `execution_profile` 路由） |

改动纪律（每一条都有用例，改之前先看它们）：

- **CLI 必须继续拥有用户的 Python。** 让 sidecar 去 spawn 会同时失掉
  stdin / stdout / cwd / env / Ctrl+C 五样（ADR 0021 §1）。
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
- **不许再写第二处 `pool.get()` 分支**：`app.py` 里所有"谁来渲染"都经
  `enginesession.resolve()`（结构性守卫
  `test_native_api::test_the_resolver_is_the_only_place_that_branches`）。
- **native 会话绝不进池**：LRU 淘汰会杀掉用户正在跑的脚本。
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
- **native 面板"出自哪一档"只有一个出处**：`enginesession.profile_of()`。
  `/api/runtime/status` 的 `execution_profile` 与渲染路由读的是同一份，
  另立一份迟早在某个边角上分叉，而分叉的那一侧会在界面上显示成"能编辑"。
