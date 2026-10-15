# ADR 0126：桌面拥有的脚本——输入由界面作答（同一进程续跑）与「打开并运行」入口接线

日期：2026-10-14 · 状态：**Proposed**（兼容性改造 P07 第一轮，随该 PR 评审）
相关：[0125 桌面拥有的 native 进程](0125-desktop-owned-native-process.md)（本 ADR 接它「不接前端 / 桌面入口 / preparation runner」那条残余）、
[0116 准备会话](0116-preparation-session-as-gui-entry.md)、[0124 一次运行意图](0124-run-intent-continues-preparation-to-one-execution.md)、
[0099 脚本 input 桥](0099-script-input-bridge.md)（safe worker 的那套，**不动**）；规则全文 `docs/rules/backend/tavotto-run-control-plane.md`「桌面拥有的脚本：输入与入口（P07）」。
验收：AC-01 / 14 / 15 / 16 / 21 / 23（本轮范围与未完成项见 PR 正文）。

## 问题

P06 让桌面能 spawn 并拥有用户的 Python，但没有人把它接到真实入口，也没回答桌面进程里脚本的 `input()` 怎么办：

1. 桌面拥有的进程没有终端。P06 只给了 OS stdin 的 `pipe` / `null`——`null` 让 `input()` 立刻 `EOFError`，`pipe` 要调用方自己写字节，
   而 Tavotto **不知道脚本此刻停在提问上**（写早了进缓冲、写晚了脚本已经 EOF），更没有「哪个窗口回答」「口令不落盘」的合同。
2. safe worker 的 `input` 桥（ADR 0099）靠重放：无应答端时抛 `script_needs_input` 展开栈，之后只能**重跑**。对桌面拥有的进程这等于
   丢掉进程内状态（AC-15 要求同 PID、同对象、同调用栈续跑）。
3. 准备会话（ADR 0116 / 0124）的启动提供者只会 `pool.build_owned`；P06 已让计划与会话键认 `execution=native`，但没有 native 的 runner。

## 决策

### 一、输入桥：提问走控制通道，阻塞在提问的那个（主）线程

`ExecutionSpec.stdin` 新增第四档 **`bridge`**（桌面拥有专用）：OS stdin 是 null，Bridge Runner 带 `--input-bridge` 启动，
把脚本的 `builtins.input`、`sys.stdin`（`readline` / `read` / `readlines` / 迭代）、`getpass.getpass` 换成**控制通道上的一问一答**：
runner 发 `input_request` 事件（`request_id` / `kind` / `prompt` / `ordinal`），**就在这一行用户代码上阻塞读**控制通道，直到收到匹配的
`input_reply`。没有后台线程（runner 的线程模型不变）；答复回来这一行返回，**同一个进程、同一批对象、同一个调用栈**往下走。
`pipe` / `null` / terminal 的命令行与行为逐字节不变（golden；P06 的 stdin 用例原样有效）。

* `kind` ∈ `input` / `readline` / `read` / `getpass`。`input` 与 `getpass` 的提示词随提问交给界面；`readline` / `read` 没有提示词（输出尾部本来就在 stdout）。
* **EOF 是 stdin 家族的粘性状态**：用户选「没有输入」（或控制通道断了）→ `input` 抛 `EOFError`、`readline` / `read` 回空串，之后同一进程里
  input / readline / read **不再提问**——与关闭的 stdin 一致。`getpass` 在真终端读的是 `/dev/tty` 而不是 stdin，所以不受粘性 EOF 影响，也不制造它。
* `read()`（读到 EOF）：一次回答就是全部内容，随后 EOF。`readline`：一次回答一行（补换行）。
* 终端拥有的 native **不装**这套钩子（继续真 stdin）。

### 二、Pending 是会话的后端事实，不属于任何窗口

`NativeSession.pending_input`（`request_id` / `kind` / `prompt` / `masked` / `ordinal` / `at`）。`masked` 由 `kind == getpass` 推出，不信对端自报。
出现 / 被答 / 被清都推一次会话事件（序号 +1；事件日志条目里只有 `input` 与 `request_id` / `input_kind`，**没有提示词也没有答案**）；`public_state()["pending_input"]`（含提示词、不含答案）让任何窗口重连后接回同一问。

`answer_input(request_id, text | eof)`：**先到先得**——认领（清 pending）在锁里一步完成，之后才发；第二个窗口、旧提问、没有提问、终端拥有的会话
一律 `native_input_not_pending`（HTTP 409），不排队、不喂给之后的提问。答复超 4 MiB / 非文本是 `native_input_invalid`（脚本继续等）。
进入屏障 / 放行 / 终态时 pending 随之清掉（脚本不可能还停在提问上）。控制通道发送串行化（`NativeTransport._send_lock`）。

**口令**：值只经 `answer_input` → 控制通道一次，局部变量里；不进 pending / 事件 / `public_state` / 诊断 / 日志 / 任何落盘文件
（本轮 native 路径没有答案记忆也没有转录，所以口令根本没有落盘的路径；用例对 data_dir / 项目 / 产物目录做字节扫描）。

### 三、入口：准备会话 `execution: native`，同一个执行线程与状态机

`POST /api/engine/preparation-sessions` 的 `script` 目标新增可选 `execution`（`safe` | `native`；省略 = safe，会话与计划逐字节不变）。
会话键、指纹、计划已由 P06 认 `execution`。**只有「怎么把 runtime 起起来」按计划选定的执行语义换**：`_launch_preparation` 对 native 计划用
`_native_preparation_runner`——`desktopproc.start`（取租约、spawn、握手）→ 把所有权报给 `PreparationService.note_owner` →
等脚本到屏障或结束 → build 一次 → 绑活图路由；返回 `(NativeSession, build 响应, True)`，`NativeSession` 就是 Worker-like 的 worker，
`_execute` 的回执 / 捕获 / 终局判断一字不改。不另建状态机、不另建任务框架、不改 P05b 的意图 / 授权 / 认领逻辑。

`PreparationPlan.native_spec`（私有）是桌面拥有的启动身份（`stdin=bridge`、`env={"PYTHONUNBUFFERED": "1"}`，ADR 0125 残余的 P07 决定）；
公开的仍是 `launch_context`。

### 四、取消与关闭是真终止

* 准备会话的取消 / 迟到取消：对「本计划新建、已不在池里」的会话，`PreparationService` 对桌面拥有的会话走 `cancel_owned()`（结束进程组 / Job），
  不是 `force_kill()`（那是终端会话的「放手」，ADR 0125 §一）。
* `POST /api/native/sessions/<id>/cancel`：运行中 / 停在提问上 / 停在屏障上都可（终端拥有的 409 `native_process_not_owned`）。
* 关闭项目：`NativeSessionRegistry.cancel_project(root)` 结束该项目里桌面拥有的活会话；终端拥有的不碰（ADR 0021 §8.1）。
* 同一脚本重新运行：上一次桌面运行的活图先让位（`cancel_project(root, script=)`），否则它占着 live route 与环境租约。

### 五、脚本自己的异常不被吞

脚本抛错时 runner 仍进 `script_end` 屏障并带上 `script_error`；runner 据此把进程放行跑完（退出码 1 原样）并以 `script_error` + 真实 traceback 收场，
不把它洗成「没有图」。缺依赖在 native 路径**不**自动修复（原环境缺包是上层准备的输入，ADR 0125 §四）。

## 不做 / 残余（如实）

* **没有真桌面窗口验证**：Tauri 壳、WebView、Windows / macOS 真机（含 Job 路径）归 P10；本轮的「真」是真服务子进程 + 真用户 Python + HTTP。
* 前端只做到把 `pending_input` 镜像进**同一个**答题队列（`scriptInputStore`）、作答 / 停止走 native 接口；**没有新增「native 运行」按钮或设置**——
  何时发 `execution: "native"`（默认？仅用户 Python？）是产品决定，本轮留给调用方。
* **成功答案的上下文记忆**（`scriptanswers`）与菜单 / 读取方式 / argv 变化失效：未接到 native 路径（本轮每个提问都问）。
* **`reuse_last_success`（#982）**：依赖 P09 / #975，本轮不做。接口点：打开脚本动作发 `execution` 的那一处。
* 缺依赖、凭据、外部软件的「一句话原因 + 一个动作」（AC-21）沿用 safe 路径的映射，native 的 `script_error` 暂以原文呈现。
* 提问只支持主线程里的调用：runner 的控制通道只在主线程上使用（线程模型不变）；脚本在子线程里 `input()` 与屏障同时读同一条通道的边角未处理（残余）。
* `sys.stdin.readlines()` / `for line in sys.stdin` 读到 EOF 才结束——界面上要点「没有输入」才会收尾，与终端里按 Ctrl-D 同义。
* 无应答端 / 重连期间脚本已停在提问上：Pending 保留在后端（进程活着、租约持有），窗口回来即可答；窗口永远不来 = 脚本一直等，用户可取消（没有超时自动 EOF）。
