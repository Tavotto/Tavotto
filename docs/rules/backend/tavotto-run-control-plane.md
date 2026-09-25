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
- **native 面板"出自哪一档"只有一个出处**：`enginesession.profile_of()`。
  `/api/runtime/status` 的 `execution_profile` 与渲染路由读的是同一份，
  另立一份迟早在某个边角上分叉，而分叉的那一侧会在界面上显示成"能编辑"。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- CLI 拥有用户的 Python
- 确认前一行用户代码不跑
- Tavotto 只写 stderr（`--help` 唯一例外）
- 屏障释放必经 `release_barrier()`
- socket 先 `shutdown(SHUT_RDWR)` 再 `close()`
