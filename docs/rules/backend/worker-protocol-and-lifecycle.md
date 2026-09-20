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
  别再重试。
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
  超时**（它只剩「冷启动可能要几分钟」那句预测文案）；ADR 0048 的分档已删除。
  测试可 monkeypatch）：超时即 kill 并报 `code=worker_timeout`，会话由下一次
  `get()` 原地重建——**状态未知的 worker 绝不复用**。超时实现是「读线程 +
  join」而不是 select（Windows 的 select 不接管道）。无超时的 readline 会让一个
  死循环脚本持着 `w.lock` 把整个会话占死，连 shutdown 都抢不到锁
  （test_request_timeout_kills_and_rebuilds_worker 看护）。
- **管道 EOF 先问死因，再说话（#435）**。EOF 之后两条控制面都先给子进程
  `EXIT_GRACE`（1.5 秒，Python 与 Rust 同一个数）自己退出，拿到退出状态才 kill
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
  的 Python 栈落在 worker.log。脚本的 `sys.exit(0)` 是正常结束（`python fig.py`
  的语义）；非零按异常从哪个模块抛出来分两条 code（`worker._script_exit_error`）：
  **抛出点那一帧**（最内层）的模块来历（`f_globals["__name__"]`，不看路径分量——脚本放在
  叫 `click/` 的目录里路径会撒谎；不看整条栈——控制流经过 click 不等于 click 退出的）
  是 argparse / click 等命令行解析库 → `script_needs_arguments`（脚本要参数而
  safe 档不带参数，出路是默认值或 `tavotto run`；argparse 的 usage 打在 stderr，
  `pool._attach_script_output` 把 worker.log 尾巴接到 traceback 前面，两条控制面
  同一处拼），否则 `script_exited`——都不再把 worker 带走。`ensure_built` 放行
  `ProtocolError`，只把裸 `Exception` 归 `script_error`。probe 原样透传这两个码。**Rust 读线程按字节读**
  （`read_until` + lossy UTF-8）：非 UTF-8 字节是「管道上有垃圾」（protocol_mismatch），
  不是 EOF——`BufRead::lines()` 会把一行坏字节当 Err 交回来，活着的 worker 被判成
  「崩溃」并被杀掉。看护：`tests/test_worker_exit_report.py`、
  `workerd/tests/supervisor_behaviour.rs` 的 `a_dead_worker_reports_how_it_died…` /
  `non_utf8_bytes_on_the_protocol_pipe…`。
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
