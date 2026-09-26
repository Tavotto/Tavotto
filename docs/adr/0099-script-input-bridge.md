# ADR 0099：脚本里的 input() 在界面上作答，答案按项目记住

日期：2026-09-26 · 状态：**Accepted**（通道形态、超时语义由用户 2026-09-26 拍板；答案存放位置暂按 §四 A，
等用户最终确认——位置只收在 `scriptanswers.answers_path()` 一个函数里）
相关：[0003 worker 协议 v1](0003-worker-protocol-v1.md)（本 ADR **不**改协议信封，只给 build 响应加一个字段）、
[0004 workerd supervisor](0004-workerd-supervisor.md)（Rust 一行不动）、[0008 会话认证](0008-unified-local-session-auth.md)、
[0014 执行语义](0014-safe-native-execution-profiles.md) / [0020 native bridge](0020-native-matplotlib-bridge.md)（native 不桥接）、
[0047 在脚本目录里运行](0047-safe-profile-project-workdir.md)、[0050 build 静默看门狗](0050-build-silence-watchdog.md)、
[0070 执行回执与数据绑定](0070-execution-receipt-and-manifest-identities.md)、写回事务（`docs/rules/backend/writeback-transaction.md`）

## 问题

用户的真实科研脚本在 glob 找到数据文件之后，列出一张编号清单（`1. xxx  2. yyy`），再调用
`input("请输入编号：")` 让人挑。用户的脚本一行都不许改（兼容性一律从产品侧解决）。

safe worker 的 `sys.stdin` **就是协议管道**（`worker.main()` 从它逐行读请求）。于是：

- `input()` 阻塞在协议 stdin 上，没有人会往这里写答案，worker 一直静默下去，直到 `BUILD_IDLE_TIMEOUT`（20 分钟）的
  看门狗把它杀掉，界面上只看到「渲染进程没有响应」；
- 更坏的情况：这段时间里父进程往管道写了下一条命令（如 shutdown），脚本会把它**当成答案读走**。

## 裁决

### 一、截获点：三处都换，只在 safe worker 里换

在跑用户代码之前（`Worker.build`，和 savefig 拦截同一个时刻），`scriptinput.install()` 替换三样东西：

| 入口 | 替换成 | 语义 |
| --- | --- | --- |
| `builtins.input(prompt)` | 桥接函数 | 提示写进脚本的 stdout（和终端一样落进 worker.log），问一次，回答不带换行；EOF → `EOFError` |
| `sys.stdin` | `BridgedStdin`（`io.TextIOBase`） | `readline()` 问一次，回答补 `\n`；EOF → `""`。`read()` 问一次，把答案当成全部内容，之后一律 EOF。`readlines()` / 迭代建在 `readline()` 上 |
| `getpass.getpass(prompt)` | 桥接函数 | **只转发提示、不做掩码**：界面上这个框显示的是明文（ADR 在此写明，不假装保密）；**答案不记住**，每次都问（口令不落盘） |

- 这三样装上就不卸：build 之后脚本已经跑完，再有谁读 stdin（极少见，比如导出时回调里的 input），拿到的是 EOF，
  **绝不会再读到协议管道**。协议循环持有的是 `main()` 开头留下的原始 stdin 引用。
- 子进程里的 `input()` 不管：子进程继承的 fd 0 仍是协议管道——这是现状，和本 ADR 无关，文案里不承诺。
- **native bridge（`tavotto run`）不桥接。** native 的 stdin/stdout 是用户在终端里的原样（ADR 0020 的执行语义表），
  `input()` 读的就是用户的终端，与 `python fig.py` 完全一致。在那里改答案来源等于偷偷改掉用户的调用语义。

### 二、通道：会话缓存目录里的文件会合（不改协议、不改 Rust）

「请求输入 / 回填」这一对消息**不走协议管道**，走文件：

- **会合目录**：`<会话 base>/out/script-input/`——`out_dir` 下面，只能在 Tavotto 自己的会话缓存（`ENGINE_CACHE`）里，
  **绝不放在用户目录**（项目目录、脚本目录、cwd）。池会话用池会话的 base，one_shot 用它自己的 `_replay-…` base，
  互不相通。worker 在 build 开始时清空它，父进程在这一次 build 结束时删掉它。
- worker 发问：写 `req-<n>.json`（先写 `.tmp` 再 `os.replace`；worker 侧的装载闭包刻意不含 `atomicio`——这是一次性的
  进程间信号，不是文档），然后每 `WORKER_POLL`（0.1 秒）看一次 `reply-<n>.json`。
- 父进程作答：`inputbroker.serving(worker)` 在 **`ensure_built` 这一次请求期间**起一个轮询线程（`BROKER_POLL`，0.2 秒），
  看到新的 `req-*.json` 就按策略（§五）决定怎么答，回复用 **`atomicio.write_json`** 写。两条控制面
  （Python 池 `EngineWorker` 与 `WorkerdWorker`）的 `ensure_built` 都包在这同一个 context manager 里——只有一份实现。
- 为什么不走协议管道：协议是「一条请求，一条响应」。在飞请求中途插一条事件，要同时改 Python 池的 `_readline`、
  Rust 的 `await_response`（它把任何 request_id 对不上的行都当 `protocol_mismatch` 杀掉），再给
  `workerd_client ↔ workerd` 加一对命令去转发事件、路由回填。改完两条控制面还得靠对拍证明行为一致。
  文件会合从构造上就只有一份实现，Rust 与信封一个字节不动。
- build 响应加一个字段 `script_inputs`（本次 build 实际用到的每一问：序号 / 类型 / 提示 / 答案，EOF 记 `null`）。
  加字段不升协议版本（ADR 0003 §1）；legacy 信封不带。

### 三、超时：10 分钟没人答 → EOFError（等同终端 Ctrl-D）

- `INPUT_WAIT_TIMEOUT = 600` 秒，**必须小于** `BUILD_IDLE_TIMEOUT`（1200 秒）——`tests/test_script_input.py` 把这条不等式钉住。
  到点后 `input()` 抛 `EOFError`、`readline()` 回 `""`：和在终端里按 Ctrl-D 一样，脚本自己 `try` 了就照常往下跑。
- 脚本没接住、build 因此失败 → 错误码 **`script_input_timeout`**（不是笼统的 `script_error`），文案说「等了 10 分钟没有回答」。
  判据：本次 build 里确实有一问超时了，且 build 以异常结束。
- **等人的时间不算脚本的静默预算**：worker 发问时把提示写进 worker.log（input 的提示本来就进 stdout），收到答案时
  再写一行「提示 → 答案」的转录。静默看门狗在这两个点各清零一次，所以等人的那段最多占一个 10 分钟的窗口，
  答完之后脚本仍有完整的 20 分钟。**4 小时兜底上限照算**（它兜的是一直打印的死循环；一个人答上几百问
  才可能撞到它）。
- 不选「按脚本超时处理」：那样等人和脚本卡死是同一句话、同一个 20 分钟，用户分不清是谁的问题。
- 测试可用环境变量 `TAVOTTO_SCRIPT_INPUT_TIMEOUT` 把等待缩到秒级（worker 从自己的环境读，只在测试里设）。

### 四、记住答案：键、位置、写法

- **键** = (脚本相对项目的 POSIX 路径, 本次运行里第 N 次读取, 提示原文)。提示文字变了就当成新问题重新问；
  同一序号的旧条目被新条目替换。`getpass` / `readline` / `read` 与 `input` 共用一个序号计数。
- **位置**（用户拍板中，先按 A）：
  - **A（当前实现）**：项目里的 `tavottofile/_script_inputs.json`。重跑、换电脑都能复现：随项目**文件夹**一起复制、
    同步、进 git。**项目包（`/api/package`，只打画布 + 素材 + 脚本）不含它**——这是现状，不是为本 ADR 新加的剔除。
    登记进 `documents.RESERVED_DOCUMENT_FILENAMES`，免得被当成画布列出来。
  - B：本机 `data_dir` 按项目存。不外泄，但换电脑、分享项目后会重新问。
  - C：放在项目里，打项目包时剔除。
  位置**只**由 `scriptanswers.answers_path(project_root)` 决定，换成 B / C 只改这一个函数（外加 C 的剔除）。
- **隐私**：答案可能含路径。答案管理里明写「答案保存在项目文件夹的 tavottofile/_script_inputs.json 里，会随项目文件夹
  一起复制、同步或分享；项目包不包含它」；答案不进遥测、不进诊断包、不进 app.log（日志只记「第 N 问已作答」）。
  `getpass` 读的那一问不记住。
- 写入一律 `atomicio.write_json`；读用 `documents.loads_document` 的同一条非有限数纪律（答案只是字符串，读坏了当作空）。

### 五、谁来答：三种策略

`inputbroker` 按 worker 身上的策略决定每一问怎么答：

1. **记住的答案**（池会话）：命中键就立即回填，发 SSE `script.input_autofilled`，界面给一条轻提示
   「已用上次的答案：1,2,4（修改）」。不弹框。
2. **问界面**（池会话，没记住）：此刻至少有一个**声明能答题**的界面连着（`/api/events?answers=1`——主界面那条事件流带
   这个标记，MCP 画布等其它消费者不带），就发 `script.input_requested`（提示、序号、stdout 最近片段），等回填。
   能力标记只是同一条 `/api/events` 的查询参数，**认证与普通事件流完全相同**（ADR 0008 的全局 guard），不是新通道。
   回填端点 `POST /api/script_input/answer` 同样在 guard 之内。
3. **没人能答** → 立即回「无答案」，worker 抛 `ScriptNeedsInput`，build 以 **`script_needs_input`** 失败，
   文案带提示原文：「脚本需要输入：<提示>，请在 Tavotto 界面里运行一次」。**绝不卡死。**

`ScriptNeedsInput` 继承 **`BaseException`**，不是 `Exception`：它和 `SystemExit` / `KeyboardInterrupt` 同类——
是「这次运行由外部终止」，不是脚本能处理的错误。继承 `Exception` 的话，脚本里常见的 `try: ... except Exception:`
（比如包住整段数据读取）会把它吞掉，脚本拿着空选择继续跑，画出一张**错的图**而不报任何错。
（裸 `except:` 仍然吞得掉——那也吞得掉 Ctrl-C，属于脚本自己的选择。）用例钉着 `except Exception` 吞不掉它。

**非交互场景**：

| 场景 | 行为 |
| --- | --- |
| 首开扫描（静态分析） | 不执行，无关 |
| 导出时的后台重渲染、MCP / Codex 插件经 Flask 的渲染 | 池会话策略：记住的就用；没记住且没有能答题的界面 → `script_needs_input` |
| 写回事务的 verify 重放（one_shot） | **严格重放**：只用热态会话 build 时实际用到的那组答案（`last_build_script_inputs`），按 (序号, 提示) 对；对不上 / 多问一次 → `script_needs_input`，从不问人。热态 == 重放因此成立 |
| `tavotto run`（native） | 用户终端的原样 stdin，见 §一 |

### 六、多次输入与循环

- 一个对话框，**按顺序逐个问**：脚本是顺序阻塞的，同一时刻最多一问在等。对话框标出「第 N 个问题」，答完关闭，
  下一问来了再打开（界面上表现为同一个框换了内容）。
- 对话框上有「停止脚本」：走现成的 `pool.force_cancel`（硬杀会话，与试运行的取消同一条路），在飞的请求以
  `execution_cancelled` 落地。循环里无上限的 `input()` 靠它中止。
- 答案管理的入口：素材库「脚本」区那一行上的「记住的输入」图标钮——**只在这个脚本真有记住的答案时出现**，其余
  脚本行一个像素不变（没有给每一行加 ⋯ 菜单）；自动回填的轻提示上的「修改」也打开它。
- 「结束输入」按钮回 EOF（给 `for line in sys.stdin` 这类读到 EOF 才停的循环）。
- 对话框不能用 Esc / 点外面关掉：关掉而不答，worker 会白等 10 分钟。

### 七、缓存与重跑

- 答案变了必须重跑：`/api/script_input/answers` 的改 / 删 → `pool.invalidate(脚本, 项目)`（热会话作废）+
  `panel.file_changed`（reason=`script_input`，界面重渲染用到这个脚本的面板）；答案管理保存后再对这个脚本发起一次运行
  （产出的图名可能随选择变化，重新登记）。
- **数据绑定（ADR 0070）纳入答案**：`databinding.binding_for` 在这个脚本有记住的答案时多一个 `script_inputs` 摘要，
  它进 `revision`——答案一变，预检计划就以 `data_binding_changed` 过期。没有答案的脚本不加这个键，已有修订一个字节不变。

### 八、安全

- 答案只是字符串，原样交给脚本，不 `eval`、不拼进命令行。
- 界面上的提示与 stdout 片段一律纯文本渲染（React 文本节点，不用 `dangerouslySetInnerHTML`）。
- stdout 片段有上限：最近 `TAIL_LINES = 40` 行、`TAIL_CHARS = 4000` 字符；提示本身截到 `PROMPT_CHARS = 2000`。
- 会话认证不开旁路：新端点全在 guard 之内；会合目录只在 Tavotto 自己的缓存里；worker 沙盒与删除守卫原样。

## 用例的等待上限

用例把 `TAVOTTO_SCRIPT_INPUT_TIMEOUT` 缩到 20 秒（超时用例 1.5 秒）：桥接的某一环回归时（比如父进程不再当答题方），
用例在几十秒内红，而不是挂满 10 分钟。**唯一例外**是「桥接根本没装」：那时脚本读的是协议管道，按
`BUILD_IDLE_TIMEOUT` 的静默看门狗才会被收——这正是本 ADR 要修的症状本身。

## 看护

`tests/test_script_input.py`（worker 层：input / readline / read / getpass 被桥接；超时 EOFError 与 `script_input_timeout`；
`except Exception` 吞不掉 `ScriptNeedsInput`；两条控制面同一行为）、`tests/test_script_input_api.py`（端点、SSE、
记住的答案、缓存失效先热缓存再改答案、数据绑定修订、verify 重放用同一组答案、没有能答题的界面立即报错）、
`tests/test_write_back.py::test_verify_replay_answers_script_input_with_the_hot_answers`、
`web/src/store/scriptInputStore.test.ts`、`web/src/components/ScriptInputDialog.test.tsx`、
`web/src/components/ScriptAnswersDialog.test.tsx`、`web/e2e/script-input.spec.ts`。
