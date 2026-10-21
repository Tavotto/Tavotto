# worker 协议、计时、超时与关停

> 原文出自 `src/tavotto/AGENTS.md`「渲染引擎核心机制」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **worker 协议 v1（2026-08-18）**：请求带 `protocol_version/request_id/
  worker_generation/render_revision/canonical_patch_hash` 信封，命令
  ping/build/render/render_png/preview_png/export/cancel/shutdown，
  错误带 code + retryable；generation/revision/hash 由 worker **原样回显**
  （校验归调用方，`request_id` 对不上当场 kill 会话）。无 `protocol_version`
  的老信封仍按旧形状回应（双栈，手工调试用）。patch 规范化与哈希的**唯一
  权威实现**是 `engine/patchspec.py`（纯标准库，父子进程共用同一份），
  golden vectors 在 `tests/golden/patch_vectors.json`——Rust supervisor 要
  逐字节复现，改任一侧必须同步另一侧。cancel 只是尽力而为的 no-op，硬取消 =
  kill + 重启。完整契约见 `docs/adr/0003-worker-protocol-v1.md`，改协议前先读。
- **计时管道与性能基线（2026-08-18）**：worker 的 build/render/export 响应带
  `timings`（`script_build_ms` / `patch_apply_ms` / `canvas_draw_ms` /
  `manifest_ms`；**没有 `svg_ms`**——SVG 序列化与 draw 在 matplotlib 里分不开），
  `pool` 补 `queue_wait_ms` / `total_ms` 并把冷启动那次的 build 计时折叠进来，
  `app.py` 再补 `worker_get_ms`（取/spawn 会话，既不属于 worker 也不属于 build，
  **漏了它冷启动的十几秒在数据里就凭空消失**）。全部是加字段，协议不升版；
  legacy 信封一个字节不加。基线报告与「值得做的优化」清单在
  `docs/perf-baseline.md`，重测走 `python scripts/bench_render.py`。
  **先测量后优化**：那份文档里被数据否掉的两条（挪 SVG 顺序省 draw = 图例 bbox
  错 0.18–0.32 分数，写回自检必报 divergence；`draw_without_rendering` 只省 8%）
  别再重试。**「只为布局」的 draw 不重采样图片**（2026-09-24）：manifest 开头的 `fig.canvas.draw()` 与几何组
  之后的 `draw_without_rendering()` 都套 `overrides.image_pixels_skipped(fig)`——含大图的图热态 448 → 266ms，
  预览 / 导出不走这里。新加一处「只为量布局」的 draw 也要套上；要像素的 draw 绝不能套（`tests/test_manifest_image_pixels.py`）。
- 前端渲染态分键与假实时预览（渲染平面 / 历史平面）的规则在
  `web/AGENTS.md`——引擎侧只需知道：render 请求带 `inline_svg` 时 SVG 与
  manifest 必须同一次响应返回；SSE 的 render.started/done 只带 fileId。
- **export / preview_png 都是状态中立的一次性动作**：应用自己那组 patches 出图后
  必须把 `state.applied` 还原回去（还原那次的 warnings 丢弃）。不还原的话历史版本
  恢复与画布导出（每个面板各带一套 overrides）会把别人的状态留在常驻 figure 上，
  前端的 lastPatches 与 worker 真实状态错位，「全量列表」的还原就还错了东西
  （test_export_is_state_neutral 看护）。**中立的是 override 状态，不是 matplotlib 的
  绘制缓存**：那次别的 dpi 的 savefig 会把图例文字的像素偏移留在 figure 上，manifest
  对此的防线在自己那边（隐藏图例按文档 dpi 现排，`legend-model.md`），不在这里补 draw。
- **worker 请求一律有超时**（`pool.BUILD_TIMEOUT/REQUEST_TIMEOUT/EXPORT_TIMEOUT`；
  build 用**静默看门狗**而不是平坦上限（ADR 0050）：`worker.log` 还在长就一直等，
  连着 `BUILD_IDLE_TIMEOUT`（20 分钟）没长才判死，`BUILD_HARD_TIMEOUT`（4 小时）
  兜底拦一直打印的死循环。**判据两条控制面同一条**——Python 池 `stat` 日志、
  workerd 收 `idle_timeout_ms` 后 `stat` 同一个文件。注册表的 `cost` **不再参与
  引擎侧超时**（它只剩「冷启动可能要几分钟」那句预测文案）；ADR 0048 的分档已删除。
  画布前端按同一个信号续期（#1207）：日志长大时引擎发 SSE `render.progress {pj, id}`
  （`ProgressListeners`，节流 `PROGRESS_EMIT_INTERVAL`；订阅者含排在同一把 `w.lock` 后面
  的重试；workerd 路径由 `_relay_log_growth` 转述），`renderStore.ts` 的看门狗是租约
  （窗口 `WATCHDOG_MS` 2 / 5 / 15 分钟，总上限 `RENDER_HARD_CAP_MS` 对齐
  `BUILD_HARD_TIMEOUT`）。没有进度通道的消费者（MCP 内嵌画布、SSE 断线）退回「请求开始起
  一个窗口」。改节流或总上限要同步另一侧（`tests/test_build_watchdog.py` 钉着；ADR 0050 补注）。
  测试可 monkeypatch）：超时即 kill 并报 `code=worker_timeout`，会话由下一次
  `get()` 原地重建——**状态未知的 worker 绝不复用**。超时实现是「读线程 +
  join」而不是 select（Windows 的 select 不接管道）。无超时的 readline 会让一个
  死循环脚本持着 `w.lock` 把整个会话占死，连 shutdown 都抢不到锁
  （test_request_timeout_kills_and_rebuilds_worker 看护）。
- **管道 EOF 先问死因，再说话（#435）**。EOF 之后两条控制面都先给子进程
  `EXIT_GRACE`（1.5 秒，Python 与 Rust 同一个数——两侧各自钉在 `tests/golden/exit_grace_ms.txt`，
  不拿正则扫对方的源码，没有解析器就分不清注释 / 字符串 / 原始字符串）自己退出，拿到退出状态才 kill
  兜底（`pool.exit_report` / `WorkerProc::reap_after_eof`）——EOF 那一刻直接 kill，
  退出码永远是 TerminateProcess / SIGKILL 的那一个，真正的死因就被盖掉了。
  退出状态 `{"code", "signal", "lingered"}` 随 `session_dead` 的信封带出（Rust 放在
  `error.exit`，Python 池放在 `WorkerError.extra["exit"]`；加字段不升协议版本），
  **怎么解释归 Python 侧一处**：`pool.describe_exit` 是退出码 → 人话的唯一表
  （Windows NTSTATUS 的两种写法——Python 的无符号 DWORD 与 Rust 的 i32——查到
  同一条；**只在 Windows 成立的一列单独放 `_NT_EXIT_EXPLANATIONS`**——退出码 3 在
  Windows 是 C 运行时收下的 abort()，在 POSIX 只是某个 `os._exit(3)`，abort 走信号 6），`pool.session_dead_message` 是两条控制面共用的那句话：它怎么死的 /
  worker.log 这一代**空不空要说出来** / 日志在哪。文案说「渲染进程退出了」，
  不说「崩溃（无响应）」——进程既没崩也不是无响应，它是退出了。
  worker 自己开着 `faulthandler`（stderr 重配之后装，记的是那一刻的 fd）：硬崩溃
  的 Python 栈落在 worker.log。worker.log 跨代追加：spawn 前 `pool.start_log_generation`
  量它现在多大 = 这一代的起点（`_log_offset`），并落盘到旁边的 `worker.log.start` 给诊断包
  读——两条控制面都在 Python 里量，只写这一处。脚本的 `sys.exit(0)` 是正常结束（`python fig.py`
  的语义）；非零按异常从哪个模块抛出来分两条 code（`worker._script_exit_error`）：
  **抛出点那一帧**（最内层）的模块来历（`f_globals["__name__"]`，不看路径分量——脚本放在
  叫 `click/` 的目录里路径会撒谎；不看整条栈——控制流经过 click 不等于 click 退出的）
  是 argparse / click 等命令行解析库 → `script_needs_arguments`（脚本要参数而
  safe 档不带参数，出路是默认值或 `tavotto run`；argparse 的 usage 打在 stderr，
  `pool._attach_script_output` 把 worker.log 尾巴接到 traceback 前面，两条控制面
  同一处拼），否则 `script_exited`——都不再把 worker 带走。`ensure_built` 放行
  `ProtocolError`，只把裸 `Exception` 归 `script_error`。probe 原样透传这两个码。
  **守卫拦下的关键文件操作不洗成完整成功（P01a，`safe_file_operation_blocked`，AC-02）**：safe 的删除 /
  改名 / `write_text` 守卫与 savefig 拦截靠「悄悄跳过」工作，脚本看不到异常——脚本 `os.replace` 缓存后读它，
  拿到旧值 1（直接 Python 是 2）却 `ok`。守卫一字不动，另有一本带外账本 `figcapture.BlockedFileOps`
  （`worker.BLOCKED_OPS`）：守卫每拦一次记「操作类别 + 影响的路径」，**最外层**（晚于输入观察器与相对读回退，看到的是脚本交出的原始路径，不是回退改写后的；沙盒档项目里有旧 `stage.png` 时
  装在回退之下会永远对不上，#952 第三轮 P1）的 open / `os.stat` 包装在
  **拦下之后**看到脚本打开（读，含 `r+`/`a+`）或 stat 了受影响路径，才算 material；脚本自己以 `w`/`x` 重写
  该路径则清掉嫌疑（成功重写的后代同时记入「已重写」集合，目录级污点盖不到它——`rmtree` 后重建目录再写再读；目录重建**只在直跑也会得到同样成功结果之后**才记：`os.mkdir` 成功、或 `makedirs(exist_ok=True)` / `Path.mkdir(exist_ok=True)` 撞上守卫留下的目录（外层吞掉），豁免该目录自身的状态查询；非 `exist_ok` 的 `os.mkdir` 撞上还在的目录（直跑里它成功）抛 `FileExistsError` 是守卫造成的分叉，本身记为观察；被删目录之下没重建就 `open(...,'w')` 写子文件（直跑里 `FileNotFoundError`）也是分叉，不给「已重写」豁免、记为观察；`makedirs` / `mkdir(parents=True)` 建被删目录之内的子目录会连父目录一起重建，不带 `parents` 的算分叉；里面没被重写的文件仍受污点；后来的阻断撤销豁免）；相对只读读还要按回退的同一判据（`install_relative_read_fallback(...).resolve`）解析出实际会读的项目路径一并比对（绝对路径的阻断 + 沙盒里相对路径的回读）；守卫按签名取参，`dst=` / `src=` / `path=` 关键字与位置参数等价（**只在底层 open 成功返回之后清**；`x` 撞上被我们拦删而留下的旧文件抛 `FileExistsError` 时不清，且这次失败本身就是一次观察——直接运行里删除生效、`x` 会成功，不等回读；**只对直跑结果会让该路径不存在的事件**生效：删除、删除目录、改名/移动的源；被拦的写入、改名/移动的目的、savefig 目标直跑后同样存在，`x` 两边都失败，不走这条捷径），`shutil.move` 进已存在目录只记 `dst/basename(src)` 与源，不把整个目录记成受影响，被拦截的 `savefig` 目标记**有效目标名**（`figcapture.savefig_effective_names`：无后缀且无 `format=` 时**只**记补了 `rcParams["savefig.format"]`/`png` 后缀的名字、不记字面名（matplotlib 不会写它，否则同名无关旧文件被读会误判），`format=` 显式则字面名）、只认读内容、不认 `exists`（校验打印不判失败），先于拦截的读、读别的路径、从没回读（`test_workdir_mode` 那种顺手删过期输出）都不算。
  material 非空时 build 以该 code 结束——脚本跑到末尾、`except Exception` 吞掉了异常、或因读不到而崩
  （原因是守卫不是 `missing_input`）、或随后 `sys.exit` 非零，一律如此。载荷只有类别（`operations`）、
  `required_execution="preserve_file_semantics"` 与 `adapter_available=false`，没有路径（message 会进
  app.log）。**拦下超过 200 次也不丢**：事件账上限 `MAX_EVENTS=200`，超出的操作并入按 (op, kind) 的溢出路径集合
  （去重；超 `MAX_SPILL_PATHS=2048` 粗化为父目录；目录也超则记「丢失」，此后任何观察都算命中），
  所以清理几百个缓存再回读第 201+ 个路径照样判失败，拦下几百次但从不回读照旧成功；只有拦下上千个互不相干
  的目录之后才会退化成「之后任何观察都算」，这是宁可误报不假成功的取舍。**状态查询入口**：除 `os.stat` 外，
  `os.path.exists`/`lexists`/`isfile`/`isdir`/`islink` 逐个包装——Windows 3.12+ 它们是 C 实现、不经 `os.stat`，
  不包就观察不到（CI Windows 腿抓到的 `os.remove` 后 `os.path.exists` 假成功）；被拦截的 savefig 目标仍只认读内容。
  盲区写在明处：C 扩展直接 `fopen`、子进程、`os.access` 等未包装的状态查询观察不到，
  账本不宣称它们干净。
  **三档保真度与 savefig 衍生的失败（V2 PR3a，用户 2026-10-14 裁决 D1–D5 的执行侧证据）**：账本在 `material()`（确定偏差）之外
  多记一档弱信号（`suspects()`，只有 (kind, via) 计数）：savefig 目标被 stat（`exists` 校验打印；`via=stat`）、stat / `os.open` /
  守卫放行的改名删除在受影响路径上 `FileNotFoundError`（`probe_failed` / `move_failed`，可能被脚本 `except OSError` 吞掉）、
  `listdir` / `scandir`（`glob` / `os.walk` 经它）列了含受影响路径的目录（`listing`）、被守卫静默跳过的改名其源就是 savefig 目标（`rename_src`）。
  `exists()` / `isfile` / `Path.exists` 这类探路本来靠 stat 落空作答，不算 `probe_failed`。弱信号**不阻断**：build 成功，
  `runtime.fidelity.level=suspect`；没有任何信号是 `none_observed`（只表示这些通道没看到，`unobserved` 列着盲区）；`deviated` 才对应失败。
  **savefig 目标衍生的 `FileNotFoundError` 是守卫造成的**：直接运行时 `savefig(p)` 之后 `p` 必然存在，所以终局异常链里指向被拦截
  savefig 目标（或被拦改名路径）的 `FileNotFoundError`（`getsize` / `Path.stat` / `os.replace(p, q)` / `os.open(p)`）由 `explains(exc)` 升为确定偏差，
  非零 `sys.exit` 之前查过 savefig 目标状态的由 `explain_exit()` 升级——一律走 `safe_file_operation_blocked`，**排在 `missing_input_of` / `enoent_fact` 之前**，
  不再弹 `missing_input` 让用户去指认脚本自己的输出。`fidelity` 段（`level` / `blocked` / `intercepted` / `signals` / `unobserved`，闭集枚举 + 计数，无路径）
  随成功的 `runtime` 进回执（`ExecutionReceipt.fidelity`，不进公开语义身份），随失败的 `error.extra.fidelity` 进诊断；worker.log 留一行 `[fidelity]`。
  `adapter_available` 仍为 false：它的意思是"这个失败之后桌面是否已会自动换到按真实文件运行的路径"，没接通之前不承诺；终端 `tavotto run` 一直可用。pool 的镜像常量 `FILE_OPERATION_BLOCKED_CODE` 与
  `file_operation_blocked_params`、HTTP `_worker_error_payload`、probe 透传同一份载荷。看护
  `tests/test_worker_blocked_file_ops.py`。`SystemExit`
  的载荷若不是整数（`sys.exit("…")`）不进 message——那是用户的文字，message 会进 app.log
  再随诊断包出门；退出状态按 CPython 的规则记 1，文字只在 traceback 区的 `SystemExit: …`。**协议管道上的
  一行先判 UTF-8、再解析 JSON，两条控制面同一口径**：非 UTF-8 字节是「管道上有垃圾」
  （protocol_mismatch，杀掉重建，那一行以 U+FFFD 代替坏字节带出去），不是 EOF——Rust 读线程
  按字节读（`read_until` + `std::str::from_utf8`，`BufRead::lines()` 会把一行坏字节当 Err 交回来，
  活着的 worker 被判成「崩溃」并被杀掉），Python 池 Popen 用 `errors="surrogateescape"`、
  `_parse_line` 在 `json.loads` 之前查 U+DC80–U+DCFF 的残留。**不许先 lossy 再解析**：坏字节夹在
  合法 JSON 字串里时 U+FFFD 之后的信封照样合法、request_id 也对得上，一条被篡改的响应会被当成
  正常结果收下（评审 #443 第九轮）。非 JSON 的一行同样是 protocol_mismatch，不许让 `json.loads`
  的异常炸出去。看护：`tests/test_worker_exit_report.py`、`tests/test_worker_protocol.py` 的
  `test_non_utf8_bytes_inside_a_json_response…` / `test_a_non_json_line…`、
  `workerd/tests/supervisor_behaviour.rs` 的 `a_dead_worker_reports_how_it_died…` /
  `non_utf8_bytes_on_the_protocol_pipe…` / `non_utf8_bytes_inside_a_json_envelope…`。
  **请求之间死掉的 worker 只有一个答案**（QA LONG-03-B1）：`EngineWorker.request` 拿锁时已看得见它死了、
  写管道撞上 `BrokenPipeError` / 管道已关、写进去后读到 EOF，三处都走同一条带退出状态的 `session_dead`——
  不许有 code 为空的「worker 进程已退出」，也不许让 `OSError` 冒成 internal_error
  （`test_worker_exit_report.py` 的 `test_a_worker_killed_between_requests_is_always_session_dead`）。
  `session_dead`（shutdown 之后的 EOF 除外）顺带丢掉那条解释器的进程内体检结论（`pool.forget_python_verdict`）。
- **关停必须闭环：`kill()` ≠「进程已经退出并释放了文件」**。`Popen.kill()`
  两个平台上都只是发出请求（POSIX 是 SIGKILL，Windows 是 TerminateProcess），
  调用返回时进程可能还在，它打开的句柄一定还在。`EngineWorker.shutdown()` /
  `force_kill()` 统一走 `_terminate_and_reap()`：**发 shutdown → 等自然退出 →
  超时 kill → 再 `wait()` 一次 → 关 stdin/stdout/log**，每一步有界、幂等、
  不碰模块级 `_lock`。worker 收到 shutdown 就 `raise SystemExit(0)`，**协议上
  不回普通成功信封**——父进程读到 EOF 是预期现象，不是故障，但 EOF 之后仍然
  必须 reap。少了这一步，Windows 上后脚的 `rmtree` 撞 sharing violation
  （merge_group run 32937999297：`_replay-…` 目录残留污染了后面的测试文件）。
  一次性目录的删除走 `_remove_oneshot_tree()`：**不许 `ignore_errors=True`**
  （它把失败变成静默的空操作），撞锁做 0.35 秒封顶的有限退让，最终失败要记
  exact path + 异常 + 尝试次数 + 脚本名。看护：`test_windows_regressions.py`
  的假 Popen 锁窗口三条 + `test_worker_roundtrip.py` 的真 worker exact-base 用例。
  **supervisor 那一侧同一条纪律**：`workerd_client` 的两处 kill（半启动回收、
  shutdown 超时）走 `_kill_and_reap()`，收尸排在「重新 open 同一个日志文件」
  与 `self._log.close()` 之前——workerd 的 stderr 就绑在那个文件上。看护：
  `test_workerd_client.py` 的假 supervisor 两条（每处 call site 各一条，
  合并成一条就抓不到只漏改一处的回归）。

- **脚本里的 `input()` 不走协议管道（ADR 0099，2026-09-26）**。safe worker 的 `sys.stdin` 就是协议管道：
  build 期间 `scriptinput.install()` 换掉 `builtins.input` / `sys.stdin` / `getpass.getpass`，每一问写成会话缓存目录
  `out/script-input/` 里的 `req-<n>.json`，父进程在 **`ensure_built` 这一次请求期间**由 `inputbroker.serving()` 轮询作答
  （两条控制面同一个 context manager；Rust 与信封一个字节不动，build 响应只加 `script_inputs` 字段）。协议循环读的是
  `main()` 开头定死的 `protocol_in`，不是 `sys.stdin`。等人上限 `scriptinput.INPUT_WAIT_TIMEOUT` **必须小于**
  `BUILD_IDLE_TIMEOUT`（到点 EOF，看门狗轮不到杀等人的 worker；超时与作答用 `claim-<n>.json` 的 `O_EXCL` 定案，超时赢了父进程当场收起那一问、迟到的答案不记住）；发问与作答各往 worker.log 写一行（getpass 的作答只写固定标记，口令绝不进日志），静默计时各清零一次。
  没人能答 → `ScriptNeedsInput`（**BaseException**，脚本的 `except Exception` 吞不掉）→ `script_needs_input`；超时后脚本没接住
  EOF → `script_input_timeout`。写回的 `one_shot(script_inputs=热态 last_build_script_inputs)` 严格重放、从不问人。
  看护：`tests/test_script_input.py`（两条控制面）、`tests/test_script_input_api.py`。
- **答案复用要上下文全对上；冷重放用执行转录；口令不进记账（ADR 0099 §九，T08）**。每一问带 `context` 摘要
  （上一问之后的输出 + 前面的回答，口令只记占位）；`scriptanswers.recall()` 只有 (运行配置, 序号, 读取方式, 提示, 上下文)
  全对上才回填，否则只给建议、重新问（没有界面 = `script_needs_input`）。上下文摘要是无盐 sha256、可枚举，**只记在
  本机侧表**（`scriptanswers.contexts_path()`，数据目录，绑定所属答案），项目文件 `_script_inputs.json` 里没有它
  （ADR 0099 §十，T12）；换机器 / 答案被别处改过 = 只当建议。build 成功后 `inputbroker.finished()` 把问答绑成
  执行转录（`inputtranscript`，数据目录，按 (脚本, 运行配置) 绑这批产物）；`serving()` 进门冻结策略：有转录就按转录重放、
  不读之后改过的项目答案文件；答案管理按 (脚本, 运行配置, 序号) 改 / 删，只作废对应配置的转录（明确重算）；显式整脚本删除才清全部配置。
  **不变量：读者校验（Codex #816 r2/r3/r8/r9 收口）**。转录绑定时带「答案状态基线」`basis`（整脚本级 + 配置级两个作废令牌），基线在 build 开始时由 `serving()` 取、`finished()` 绑定时带上；答案管理改 / 删（`inputtranscript.forget`）先换令牌；`lookup()` 比对转录基线与**当前**令牌，对不上 = 当转录不存在（回到项目答案 + 上下文匹配），绝不重放旧值。因此作废 / 绑定 / 写盘的先后、build 在飞时被改答案、写盘失败都不是正确性前提（`bind` 里先作废旧绑定、基线不符不绑只是清理）。**绑定中止必须收回热结果**：`finished()` 抛错（`StaleTranscriptError`）时，两种 worker（`EngineWorker` / `WorkerdWorker`）的 `ensure_built` 在重抛前置 `built=False`、`build_failed=True`，下一次渲染 / 导出重新走 `ensure_built`，不复用未绑定的热结果。**读必须 fail-closed（r4224223190 / r4224295365）**：凡据以做决定的读（比基线、绑不绑、`lookup` 成不成立）用严格读 `_load`——只有文件不存在才是初始（空），读失败 / 损坏 / 形状不对 / 更新版本一律 `StoreUnreadable`：`bind` 不绑也不写、`lookup` 当没有；写一律是对严格读到的当前值做读-改-写，读失败就中止、绝不写 `{}` 抹掉令牌；写不动时只有存储里**没有任何令牌**才允许删存储作废旧绑定，否则 `StaleTranscriptError`。`scriptanswers` 同理：写路径（remember / update / forget）严格读答案文件与本机上下文侧表（`AnswersUnreadable`），纯读（recall / load / digest）仍当「没有」= 重新问。**没有基线的 build 不存在（r4224357981）**：`serving()` 进门取基线 / `_frozen_policy()` 读转录失败（`OSError` / `ValueError`）= 这次 build 直接失败，稳定 code `script_input_transcript_failed`（`WorkerError`），脚本没跑、worker 不算已 build、不被复用。防御层：`bind(basis=None)` 不绑，并**作废该 (脚本, 配置) 现有的绑定**，作废（或读存储）失败抛 `StaleTranscriptError`，`finished()` 置 build_failed。看护 `tests/test_input_transcript.py`（基线）、`tests/test_build_binding_abort.py`。
  管理投影保留不透明 `run_config`，无参数旧条目缺省；重跑按引用取回参数，不回落到空 argv 或当前草稿。getpass 那一问在 worker 记账里只有
  `secret: true`：build 响应、热会话、转录都没有值，重放时重新问，没人答 → `reason=secret_required`，绝不填空。
  问答去向计数（`InputFacts`）经 `facts_projection` 进 T04 任务诊断。不向协议 stdin 写答案；native / 子进程 input 不变。
  看护：`tests/test_script_input_context.py`、`tests/test_script_input_transcript_api.py`、`tests/test_input_transcript.py`。

## TeX 工具链失败的分类（`texdiag`）

`text.usetex` 的失败在两条错误收口（`pool.EngineWorker._error_of` / `pool._worker_error`）按 matplotlib 的固定模板分四码，
**先于缺依赖判**（LaTeX 日志不会落进 `missing_dependency` 的 pip 出口）：`tex_command_missing`（`command` 闭集）/
`tex_package_missing`（`.sty` / `.tfm`，文件名白名单清洗）/ `tex_compile_failed`（LaTeX 的 `! …` 行）/ `tex_failed`
（落在 matplotlib 的 TeX 模块里但认不出原因；`dviread.find_tex_file` 那句分不清 kpsewhich 缺失与文件缺失，也归这里）。
build 与之后的 render / export 同一收口；超时 / 取消保持各自的码。参数经错误转换合同 `pool.recovery_params`，渲染端点 /
试运行探测 / 准备会话同一份。分类只读已发生的那次执行，不另起探测、不执行用户代码。worker 继承父进程 PATH（从 Finder 启动的
桌面版里没有 `/Library/TeX/texbin`），文案据此提示「装完重启 Tavotto」；宏包装上后同一会话重跑即重新验证（kpathsea 每次编译现查，
不依赖环境代）。看护 `tests/test_tex_diagnostics.py`（stub）与 `tests/test_tex_toolchain_real.py`（真 TeX，没有时 environment-blocked）。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 协议 v1 信封原样回显、`request_id` 对不上 kill
- patch 规范化唯一权威 `patchspec.py`（↔ Rust 逐字节）
- build 用静默看门狗（ADR 0050）
- 管道 EOF 先 `EXIT_GRACE` 内问退出状态再 kill，退出码进信封、解释只在 `pool.describe_exit` / `session_dead_message`，worker 开 faulthandler、脚本 `sys.exit(0)` 是正常结束（#435）
- `_terminate_and_reap()` / `_kill_and_reap()` 闭环
- export / preview_png 状态中立
- 请求之间死掉的 worker（拿锁时已死 / 写管道 BrokenPipe / 读到 EOF）一律同一个带退出状态的 `session_dead`，非 shutdown 的 `session_dead` 丢掉那条解释器的体检结论（`pool.forget_python_verdict`）

## 预览发布：同代指针 + 逐图隔离（P01b #958 → P08a）

写预览的是 worker / native 子进程，读的是 app 进程（`/api/engine/svg`、manifest 读取、
runtimeasset 物化，全经 `figcapture.usable_preview_path` / 各 worker 的 `svg_path`）——
**进程内内存标记对读方无效**，每一条保证都必须是读方能观测到的东西。

**同代发布**（`figcapture.publish_preview`）：每次发布写一个**不可变**的 `.gen-<序号>/<stem>.svg|json`，
账本 `.preview-ledger` 里该 stem 的记录（`seq` / `svg_bytes` / `capabilities`）就是**指针**，也是完成描述符；
切换它（一次 `os.replace`）是唯一提交点。要 SVG + manifest 成对的读方用 `usable_preview_generation`
一次解出，两份出自同一次发布；只要一份的用 `usable_preview_path`（指向账本里那一代）。没有指针 = 无预览，
目录里碰巧同名的文件、`.part` 临时件、被取代的旧代目录（落后 `GENERATION_GRACE` 个序号**且**过了 `GENERATION_GRACE_SECONDS` 秒才清，
刚解出旧路径的读方不会撞上刚删的文件）读方都看不见。

失败语义：提交前任何一步失败，新代目录撤掉、账本不动——**上一版整套原封不动，没有「还原」这一步**；
账本**存在但读不出来 / 格式坏**（`PreviewLedgerUnreadable`）发布失败并如实报错，**不许**当空账本重建
（会丢已发布的其它图）；账本不存在才是首次发布。完成计数（`published_preview_stems`）= 账本有记录且指针指向的
SVG 字节数对得上，不数文件。

**逐图隔离**（`LiveFigureSession.instrument_all`）：一张图在「建状态 / 出预览」里的普通异常只属于这张图——
记进 `failures`（`preview_capture_failed`）、从活会话撤掉，其它图照常发布；build 响应带 `failed_figures`，
native 另带 `figure_facts`（逐图 `state` / `generation` / `capabilities` / `saved_originals` / 时刻）与
`capture_summary`。不是 `except: pass`：失败图动过的 Figure 要核对恢复（`failed_restore_owed`，还原不了照样挡 continue）；
选定产物上下文（`artifact_source`）、线程归属这类不属于单张图的异常原样抛；产物目录 / 账本的问题（发布段）
仍整批失败。safe worker 传 `raise_if_none_live=True`（一张活图都不剩时把第一个失败原样抛出，build 仍失败）。
能力只按实际完成的工作声明：`preview` + `live_edit`（同代发布过的活图）与 `original_readonly`（**这个 Figure
对象**本次运行成功 save 的原产物，`SavefigObservations.saved_originals` 按 Figure 身份认领并对账字节数 / mtime，
绝不按 basename 或目录扫描找替身）是独立的事实——Saved Stage 的磁盘原件与之后 Unsaved Final 的活对象不必相同。

### 保留的兜底层级（P01b）

原子指针之后，发布路径不再产生「新旧错配」，下面几级只在遗留路径上可达，代码保留（`mark_preview_invalid` /
`poison_preview_dir` / `PreviewPoisoned` 仍被父进程三条控制面识别）：

1. **磁盘失效标记**（`.<stem>.preview-invalid`，`mark_preview_invalid` 返回是否写成，**不许吞**）：读方回「无预览」。
2. **会话作废**：`PreviewPoisoned`（`protocol_code = preview_poisoned`，非 retryable，build 里也不归 `script_error`）。
   父进程在**三条控制面**上同样处理：Python 池 `_error_of` 与 workerd `_to_worker_error`
   （`preview_poisoned ∈ _FATAL_CODES`）判死会话、下一次 `get()` 重建；native `_request` 把传输标 `poison`
   （既有的「会话不再可信」机制）；bridge 同。同时 `figcapture.poison_preview_dir(out_dir)` 把
   `out_dir` 记进**读方进程**的作废表，`usable_preview_path` 对它回「无预览」，直到新一代会话 build 成功
   （`clear_preview_poison`：作废之后没重新发布的指针摘掉再解除）。

新增会改预览发布的失败路径时，别在某一级里加 `except OSError: pass`——先问「这一级失败后读方还有没有拦截」。

## 实际执行事实（X03 快照资格）

`executionfact` 的 48 位不透明 ID 只在 worker 即将进入用户代码的边界产生，v1 成功/错误响应与请求级短文件为独立通道；
marker 不含脚本、路径、argv 或输出，读写失败不阻止科学运行。owner 由父进程可信 spec 的项目/script/run_config 提供，
不是从描述符子集、HTTP 成功或 Figure 数量推断。正常轮询早通知；导出按 owner 同步刷新关闭轮询间隔。
两条证据都丢失时以 dispatch 时序记 unknown，不能说没执行，也不伪造 started。执行中尚无事实时旧快照导出临时拒绝，
confirmed pre-send/排队取消/依赖门不撤旧代；unknown 的迟到结果不能污染更新执行。单次验证 `one_shot` 不替代当前图。
safe 回执的私有 instance 含实际 execution ID，透明重开不靠未变化的 worker generation 冒充同一次执行；公开语义身份不变。
看护：`tests/test_execution_observation.py`、`tests/test_execution_receipt.py`、`tests/test_svg_execution_lifecycle.py`。
