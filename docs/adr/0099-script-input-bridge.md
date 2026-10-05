# ADR 0099：脚本里的 input() 在界面上作答，答案按项目记住

日期：2026-09-26 · 状态：**Accepted**（通道形态、超时语义、答案存放位置（§四 A）均由用户 2026-09-26 拍板；
位置只收在 `scriptanswers.answers_path()` 一个函数里）· **2026-10-06 修订（§九，T08）**：复用键加上下文、执行转录供冷重放、
getpass 改为掩码且不进记账
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
| `getpass.getpass(prompt)` | 桥接函数 | **只转发提示、不做掩码**：界面上这个框显示的是明文（ADR 在此写明，不假装保密）；**答案不记住**，每次都问；**口令不落盘**：worker.log 只写一行固定的「已作答（口令不转录）」，回复文件读完即删 |

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
- **超时与作答只能一方算数**：谁先用 `O_EXCL` 建成会合目录里的 `claim-<n>.json` 谁赢。超时赢了，父进程当场收起那一问
  （`script.input_closed`，reason `timed_out`），迟到的答案被拒——不回给脚本、**不记住**；否则本次输出没用它、下次运行却用它
  （Codex #680 P2）。作答先定了案，worker 到点也不跑，等回复写出来再用（落盘失败则放掉定案，照旧能超时）。
- **等人的时间不算脚本的静默预算**：worker 发问时把提示写进 worker.log（input 的提示本来就进 stdout），收到答案时
  再写一行「提示 → 答案」的转录（`getpass` 只写固定标记、不写答案——worker.log 会进诊断包与错误里的日志尾巴，
  Codex #680 P1）。静默看门狗在这两个点各清零一次，所以等人的那段最多占一个 10 分钟的窗口，
  答完之后脚本仍有完整的 20 分钟。**4 小时兜底上限照算**（它兜的是一直打印的死循环；一个人答上几百问
  才可能撞到它）。
- 不选「按脚本超时处理」：那样等人和脚本卡死是同一句话、同一个 20 分钟，用户分不清是谁的问题。
- 测试可用环境变量 `TAVOTTO_SCRIPT_INPUT_TIMEOUT` 把等待缩到秒级（worker 从自己的环境读，只在测试里设）。

### 四、记住答案：键、位置、写法

- **键** = (脚本相对项目的 POSIX 路径, 本次运行里第 N 次读取, 提示原文)。提示文字变了就当成新问题重新问；
  同一序号的旧条目被新条目替换。`getpass` / `readline` / `read` 与 `input` 共用一个序号计数。
- **位置**（用户拍板中，先按 A）：
  - **A（用户 2026-09-26 决定）**：项目里的 `tavottofile/_script_inputs.json`。重跑、换电脑都能复现：随项目**文件夹**一起复制、
    同步、进 git。**项目包（`/api/package`，只打画布 + 素材 + 脚本）不含它**——这是现状，不是为本 ADR 新加的剔除。
    登记进 `documents.RESERVED_DOCUMENT_FILENAMES`，免得被当成画布列出来。
    **收件人打开项目包之后运行脚本，会重新弹框问**（包里没有答案）。项目包要不要带上答案是项目包的能力扩展，
    另开跟进，不在本 ADR 里做。
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
2. **问界面**（池会话，没记住）：此刻至少有一个**声明能答题、且正在看这个项目**的界面连着，就发
   `script.input_requested`（提示、序号、stdout 最近片段），等回填。声明 = `/api/events?answers=1`（主界面那条事件流带
   这个标记，MCP 画布等其它消费者不带）；这条流连上时收到 `stream.hello`（流 id），界面据此经
   `POST /api/script_input/listen` 报「我此刻在看哪个项目」，每次换项目再报一次——在**认领新项目的同一时刻**报
   （`setCurrentProjectId` 的监听），不等换代做完：事件过滤从认领那一刻就换了，晚报会留一段两边对不上的窗口。**按项目认**是因为事件流是全进程一条、
   跨项目存活的，而界面按 `pj` 丢掉别的项目的事件：开着 A 的界面算作 B 的答题方的话，B 的后台 / MCP 渲染会白等
   10 分钟（Codex #680 P1）。
   能力标记只是同一条 `/api/events` 的查询参数，**认证与普通事件流完全相同**（ADR 0008 的全局 guard），不是新通道。
   `listen` 与回填端点 `POST /api/script_input/answer` 同样在 guard 之内。回填**先校验、先落盘、成功之后才出队**：
   答案不合法时这一问仍在等，改好再交一次照样答得上（Codex #680 P2）。
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

### 九、修订（T08，2026-10-06）：上下文匹配、执行转录、口令掩码

本节**修订** §一 的 getpass 行、§四 的「键」、§五 的策略表与非交互场景表；其余条款不变（通道、超时、claim 竞争、
无答题方快速失败、native 不桥接、子进程 input 不覆盖）。

1. **答案复用键加上下文**（修订 §四「键」）。worker 每一问附一个上下文摘要 `context`
   （`scriptinput.context_digest`：读取方式 + 提示 + **上一问之后**脚本打印的那段输出（有界）+ 本次运行里前面每一问的
   回答；口令只按占位进摘要，不哈希它）。记住的答案按 (脚本, 运行配置引用, 序号, 读取方式, 提示, 上下文) 存
   （`_script_inputs.json` 版本 2，条目多可选 `context` / `run_config`；旧读者忽略它们）。**只有全部对上才原样复用**；
   提示相同而上下文 / 运行配置不同、或版本 1 留下的没有上下文的条目，**最多是建议**：重新问，事件里带
   `suggestion`（旧答案，不预填、不自动交）与 `recheck`（`context_changed` / `config_changed` / `legacy_answer`）。
   没有能答题的界面时就是 `script_needs_input`——菜单换了序（T00 F2 实测：提示一字不差、清单重排，旧「2」静默选错列）
   不再画一张错图。任意 stdout 不保证语义识别：输出里有时间戳之类的易变内容会让每次都重问——这是安全方向的代价。
2. **执行转录与冷重放**（修订 §五「非交互场景」）。build **成功**后，这一次实际用到的问答（build 响应的
   `script_inputs`）成为不可变的执行转录，绑到 (项目, 脚本, 运行配置引用)——也就是这批 runtime 产物的身份
   （`engine/inputtranscript.py`，存 Tavotto 数据目录，不写用户项目、不进项目包）。池会话在 `serving()` 进门时冻结策略：
   这批产物有转录就**按转录重放**（`ReplayAnswers.transcript`），不读之后被同步 / 手改的项目答案文件；上下文对不上
   就重新问（有界面）或 `script_needs_input`（`reason=transcript_mismatch`）。没有转录（T08 之前的产物、超出上限）
   回到上下文匹配的项目答案。新的成功执行整条替换转录；失败的执行不动它。答案管理里改 / 删答案 = 明确要用新答案
   重算：作废该脚本的转录（`_after_script_answers_changed`）。写回 verify 的一次性重放不写转录。
3. **口令**（修订 §一 getpass 行与 §四 隐私）。界面用密码框（`type=password`、不自动补全），交出去就清空输入框；
   答案不进 store、不进事件、不进 worker.log（原有）、**不进 worker 记账**：build 响应 / `last_build_script_inputs` /
   执行转录里那一问只有 `secret: true`、没有值。重放到它（冷重放或写回 verify）时**重新问**；没有能答题的界面就
   `script_needs_input`（`reason=secret_required`），绝不拿空串或 EOF 继续。这只证明 Tavotto 自己不记录口令：
   用户脚本自己 `print` 出来、写进文件，不在这个保证里。
4. **展示面**。同一问只有一个 id；准备会话报告在 `phase=awaiting_runtime_input` 时带 `runtime_input`
   （`Pending.public()`：id / 序号 / 读取方式 / 是否口令，**没有**提示与答案），回答仍走 `/api/script_input/answer`。
   前端 `scriptInputStore.claimPresentation()` / `releasePresentation()`：准备面板认领时原对话框让开，面板关掉就放手、
   对话框接着显示同一问——关面板只换展示，不取消脚本。多标签页抢答沿用 claim 竞争。
5. **任务诊断**。每次 build 的问答去向记成计数（`inputbroker.InputFacts`：asked / shown / answered / eof / autofilled /
   replayed / timed_out / stopped / secret + 闭集 `no_answer`），经 `inputbroker.facts_projection` 进试运行与准备尝试的
   T04 白名单快照（`input` 段）；提示、答案、输出片段、上下文摘要都不进。

看护：`tests/test_script_input_context.py`（真 worker：F2 菜单换序重问、冷重放用转录、口令不进记账 / 落盘、重放重问口令）、
`tests/test_script_input_transcript_api.py`（第二问依赖第一问、准备会话 `runtime_input`、HTTP / SSE / 诊断 / 项目包 /
日志的口令哨兵、超时与停止进诊断）、`tests/test_input_transcript.py`（单元）、`web/src/components/ScriptInputDialog.test.tsx`。

## 用例的等待上限

用例把 `TAVOTTO_SCRIPT_INPUT_TIMEOUT` 缩到 20 秒（超时用例 1.5 秒）：桥接的某一环回归时（比如父进程不再当答题方），
用例在几十秒内红，而不是挂满 10 分钟。**唯一例外**是「桥接根本没装」：那时脚本读的是协议管道，按
`BUILD_IDLE_TIMEOUT` 的静默看门狗才会被收——这正是本 ADR 要修的症状本身。

## 看护

`tests/test_script_input.py`（worker 层：input / readline / read / getpass 被桥接；超时 EOFError 与 `script_input_timeout`；
`except Exception` 吞不掉 `ScriptNeedsInput`；两条控制面同一行为）、`tests/test_script_input_api.py`（端点、SSE、
记住的答案、缓存失效先热缓存再改答案、数据绑定修订、verify 重放用同一组答案、没有能答题的界面立即报错）、
`tests/test_write_back.py::test_verify_replay_answers_script_input_with_the_hot_answers`、
`web/src/store/scriptInputStore.test.tsx`、`web/src/components/ScriptInputDialog.test.tsx`、
`web/src/components/ScriptAnswersDialog.test.tsx`、`web/e2e/script-input.spec.ts`。
