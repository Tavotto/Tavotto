# CI03b · `package` job 冒烟的实例隔离（2026-09-16）

- 改动对象：`.github/workflows/ci.yml`（`package` 的两步 + 一步失败 artifact）、`scripts/ci/package_smoke.py`（新）、
  `tests/support/stub_http_server.py`（新，单测用的桩服务器）、`tests/test_package_smoke.py`（新，41 条）、
  `tests/test_merge_queue_workflows.py`（新增 `TestPackageSmokeIsolation` 七条）、`.github/AGENTS.md`（一段）、
  `acceptance.json`（CIP-018 / CIP-019 的 evidence + note）、`coverage_ledger.json`（四条 `heavy.package.*`）。
  叠在 CI03c `35b912a0`（PR #375）之上；栈：#372 CI00 → #373 CI01 → #374 CI03a → #375 CI03c → 本 PR。
- **改了什么**（ci.yml 非注释行，逐条）：
  1. `装进干净环境并冒烟`：`python -m venv /tmp/smoke` + `BIN=/tmp/smoke/bin; [ -d "$BIN" ] || BIN=/tmp/smoke/Scripts` →
     `VENV="${{ runner.temp }}/smoke-venv"` / `python -m venv "$VENV"` / `BIN="$VENV/bin"; [ -d "$BIN" ] || BIN="$VENV/Scripts"`；
     后面四条 `"$BIN/python" -c …` 断言一个字不动；
  2. `起服务并请求首页`：删掉 `env:` 的 `TAVOTTO_DATA_DIR` / `TAVOTTO_CONFIG_DIR` 两行；加 `timeout-minutes: 5`（step 级）；
     run 从 `BIN=/tmp/smoke/…` + `"$BIN/python" -m tavotto --port 5199 --no-browser &` + `sleep 8` + 两条 `curl -sf` + `echo`
     改成 `VENV=…` / `BIN=…`（同上）+ `python scripts/ci/package_smoke.py --python "$BIN/python" --workdir "${{ runner.temp }}/smoke-run"`；
  3. 新增一步 `if: failure()` 的 `upload-artifact`：`package-smoke-logs-${{ matrix.os }}-${{ matrix.python }}`，
     path `${{ runner.temp }}/smoke-run/**`，7 天，`if-no-files-found: ignore`（前面的步骤红时 workdir 可能还没建）。
- **没改什么**：job id（`package`）、`needs: [frontend]`、`if:`（merge_group / full-ci）、job 级 `timeout-minutes: 60`、
  四条腿的 matrix include、`runs-on`、`ci-integration-gate` 的 `needs` / `--required` 闭集、`scripts/ci/aggregate_gate.py`、
  `构建前端进包` / `打 wheel` 两步、装 wheel 之后的四条 import / 资源断言；**`src/` 一行没动**（产品 `--port` 仍是 int、
  仍不接受 0，`resolve_port` 的顺延行为原样）；`scripts/smoke_app.py` 一行没动（只被 import）。
- 本机实测是 macOS 上从本 worktree 真打的 wheel（§4）。PR #376 首跑：Linux 六片、Windows 两片全绿（Windows 上 `package_smoke.py`
  真跑通过），macOS 一片 10 条红——根因、诊断与修法见 §9。
- 回退：恢复两步原文（`/tmp/smoke` + `--port 5199 --no-browser &` + `sleep 8` + 两条 curl + 两行 env），删掉上传步骤；
  脚本、桩与两组测试可以留着（合同测试会红，一起删）。

## 1. 三处隔离各自的主语

| 撞点 | 原来 | 主语（谁的、哪个进程、哪个时刻） | 现在 | 钉在哪 |
|---|---|---|---|---|
| venv 路径 | `/tmp/smoke`，整台机器一份 | **这个 job** 的 venv；第二个实例的 `python -m venv` 会把第一个正在 `pip install` / 正在跑的那份覆盖掉 | `${{ runner.temp }}/smoke-venv`（托管 runner 每个 job 一个 `RUNNER_TEMP`） | `test_the_venv_lives_under_runner_temp_and_both_steps_share_it`（两步同一个 `$VENV`，`bin` / `Scripts` 探测照旧） |
| 端口 + 就绪 | `--port 5199` + `sleep 8` + curl 5199 | **服务实际 bind 的**那个端口；**我们起的**那个进程；**它就绪的**那一刻。原来第二个实例的 `resolve_port` 顺延到 5200 而 curl 仍打 5199——验的是第一个实例，第二个从没被验过；慢机器 8 秒没起来是假红，快机器白等 | 脚本向系统租端口（§2）；就绪 = `/api/version` 200 + JSON **且**应答者持有本实例凭据（§3.1） | `tests/test_package_smoke.py`（桩的十一种坏法）；ci.yml 侧 `test_the_smoke_step_runs_the_isolated_script_on_the_venv_python` |
| 终止 | `&` 起、从不终止 | **进程不存在**（不是「发了信号」）：托管 VM 销毁时一起没了，多 runner 主机上就是泄漏进程 | `finally` 里整组 SIGTERM → 等 10 秒 → SIGKILL → `wait()` → `killpg(pgid, 0)` 必须 ESRCH，组里剩人再升级；Windows `taskkill /T /F`（§3.2） | `test_termination_leaves_neither_the_server_nor_its_worker` / `…ignores_sigterm…` / `test_wait_group_gone_*` |
| data / config | yml 的 `env:` 指 `${{ runner.temp }}/tavotto-data`（这一点本来是对的） | **本次尝试**的实例——租约丢了换端口重来时，上一次的 `port-<P>.json` / app.log 不该和这一次混在一个目录里 | 脚本放在 `--workdir/attempt-<n>/{data,config,server.log}`；yml **不再**另设那两行（设了也会被脚本覆盖，留着只会让人以为隔离是它做的） | `test_isolation_dirs_are_owned_by_the_script_not_by_the_yml`（yml 无 + `child_env` 直接问）；`test_a_healthy_server_passes_and_everything_lands_under_the_workdir`（桩用产品自己的 `publish_secret` 写的凭据文件落在 `attempt-1/data/session/`，证明子进程拿到的就是那个目录） |

脚本 stdout **恒一行 JSON**（`ok / port / attempts / ready_seconds / version / workdir / history[…]`，同一份也写到
`workdir/result.json`），进度与失败原因走 stderr；退出码 0 通过 / 1 冒烟失败 / 2 用法错误。子进程的 stdout+stderr
落 `attempt-<n>/server.log`（不开 PIPE：`test_no_launcher_leaves_a_child_pipe_undrained`），失败时尾 40 行打到 stderr。

## 2. 端口租约与竞争：产品**不会**因端口被占而报错退出

租约 = `smoke_app._free_port()`（`bind(("127.0.0.1", 0))` 取号再释放）。产品的 `--port` 是 `int`、默认 5089、不接受 0
——**不改它**：`tests/test_windows_regressions.py::test_busy_port_falls_back_instead_of_crashing` 要的正是「被占用就顺延」
（双击启动的应用不能因为端口冲突一声不响地退出），04 §4 也写明「端口冲突专用用例保留有意冲突，不把产品行为改掉以便并行」。
所以租约与真 bind 之间有窗口，而窗口里被抢时产品的表现**不是抛错**（`src/tavotto/app.py::resolve_port` / `main`）：

| 抢在哪一刻 | 产品的表现 | 日志里的文案 | 脚本的判定 |
|---|---|---|---|
| 释放租约 → `resolve_port` 之间，抢的是别的程序 | `port_is_free(P)` 为假 → 顺延到 P+1，**在 P+1 上正常服务** | `* 端口 P 被占用，改用 P+1` | 对着 P 等永远等不到；日志一出现这句就判 `lease_lost`，终止这个实例（它在 P+1 上活着），换号重来 |
| 同上，抢的是另一个 Tavotto | `tavotto_is_serving(P)` 为真 → 打一句就**退 0** | `* Tavotto 已在 http://127.0.0.1:P/ 运行，打开现有窗口` | 进程退出 + 日志匹配 → `lease_lost` → 重来。**只看 `/api/version` 的话这一档是假绿**：P 上那个 Tavotto 也答 200 |
| `resolve_port` → `app.run` 之间那几毫秒 | werkzeug `bind()` 抛 `OSError` → 退非零 | `[Errno 48/98] Address already in use` / `[WinError 10048] Only one usage of each socket address …`（中文区域是「通常每个套接字地址(协议/网络地址/端口)只允许使用一次」，所以还认 `WinError 10048`） | 同上 |

三种形状用同一条判据：**子进程日志里出现占用类文案**（`package_smoke._BUSY`，还多认一条 Node 风格的 `EADDRINUSE`），每一轮
轮询都看（`poll()` 之后再读日志，进程已退出时日志才完整），认出来就 `lease_lost` → 终止 → 换号，最多 `--attempts`（默认 3）次；
连丢到用完是失败（rc 1，理由带「已连丢 N 次租约」）。别的退出原因（`crash` 那类）一律直接失败，不重试。
`被占用` 只认 `端口 \d+ 被占用` 这个形状——写回时的「文件被占用」不该触发换端口。

真产品的顺延形状在本机复现过（§4.2）：用 `evidence/ci03b/occupy_then_run.py` 在租来的端口上先占住再起真 wheel，
产品打了那句顺延、在 P+1 上服务并把 `port-<P+1>.json` 写进了本实例 data dir，脚本 3.8 秒内判 `lease_lost`、终止整组、换号后 0.26 秒就绪。

## 3. 就绪与终止

### 3.1 就绪 = 「**我们的**进程在租来的端口上应答」，三道少一道都不算

1. `GET /api/version` 200 且 body 是 JSON 对象（公共端点，ADR 0008；503 / 非 JSON 都继续等）；
2. 本实例 data dir 里有 `port-<P>.json`——产品在 bind **之前**由 `security.new_browser_state` → `session_client.publish_secret` 写，
   别的实例写不到这个目录；读它的是 `smoke_app.adopt_session_credentials`（**唯一实现**，`test_session_credential_logic_has_a_single_implementation`
   不许第二份），装上之后所有请求带 `X-Tavotto-Auth`；
3. `GET /api/session/ping` 200——应答者持有只有我们的子进程才写得出的 secret。

为什么不止第 1 道：§2 的第二行——租约被另一个 Tavotto 抢走时，P 上那个进程也是 Tavotto、也答 200、也是 JSON；
第 2 道单独也不够：文件是我们写的，但 P 上答话的可能是抢在 `app.run` 之前那几毫秒的别人（我们的马上就要 bind 失败退出）。
两道各有桩（`no-credentials` / `reject-credentials`）、各有变异（M05 / M06）。就绪之后再打与原来两条 curl 同一件事的两条：
`GET /` 200、`GET /api/version` 200 + 非空 `version` 字段（`index-500` / `no-version-field` 两个桩）。
`ready_seconds` 从 `Popen` 返回起算，轮询间隔 0.25 秒。

这一层顺带把 wheel 形态的会话认证也验了一遍（凭据文件写得出、`/api/session/ping` 认得出）——smoke_app 对 .exe 形态做的
同一件事，原来 `package` 那两条 curl 不覆盖。

`smoke_app` 的复用与 `scripts/ci/soak.py` 同一种（`sys.path.insert(0, scripts/)` + `import smoke_app as SA`）；仓库门禁
`test_every_app_launcher_adopts_credentials` 从含 `Popen` 的函数出发只看**一层**可达，所以凭据装载写在 `wait_ready` 里
（`run_attempt → wait_ready → adopt_session_credentials`），第一版把它再包了一层就被这条门禁当场打红。

### 3.2 终止 = 进程不存在

POSIX：`start_new_session=True` 起（子进程自己一个进程组，pgid == pid）；`finally` 里先问一句它是不是组长
（`os.getpgid(pid) == pid`——不是的话 `killpg(pid, 0)` 的 ESRCH 会把「没有这个组」读成「组里没人了」，恒真；M17）→
`killpg(SIGTERM)` → `wait(10)`，不退就 `killpg(SIGKILL)` → `wait()` → `_wait_group_gone`：`killpg(pgid, 0)` 抛 ESRCH 才算空，
最多等 3 秒；还有人就整组 SIGKILL 再看一次（`--worker-ignores-term` 的桩：worker 对 SIGTERM 置 SIG_IGN，`how` 必须是
`killpg-term+kill-group` 且最后 `group_gone: true`；M14）。`group_gone` 是最后那一眼，不是「发过信号」；它是假就让这次
尝试失败（M18，`test_a_group_that_is_still_populated_after_termination_fails_the_attempt`）。最后再用
`smoke_app._leftover_workers(data_dir)`（按本实例 data dir 认）核一遍 worker 残留（M26）。

**本机抓到的一条**：macOS 上直接子进程刚被 reap、孙进程正在退出的那一瞬，`killpg(pgid, 0)` 会短暂报 **EPERM**（不是 ESRCH
也不是成功），半秒后才 ESRCH。第一版把 EPERM 当「组里有不是我们的进程」立刻返回 False，对着真产品 + 一层包装进程
（`occupy_then_run.py`）跑第一次就 `group_gone: false` 假红（`evidence/ci03b/local_runs/real_wheel_occupied_stderr.txt` 之前那一版）。
探针（同一形状 4 次里 3 次出现 EPERM 一拍）：

| 形状 | `killpg(pgid, 0)` 序列 |
|---|---|
| 直接子进程 `sleep`，SIGTERM 后 | `ESRCH` |
| 子进程 + 一个从不 wait 的已退出孙进程（僵尸） | `ESRCH` |
| 子进程 `subprocess.run(孙)`，整组 SIGTERM 后 | `EPERM, ESRCH` ×3、`ESRCH` ×1 |

修法：EPERM 算「还有东西」，继续轮询到 ESRCH 或超时（`test_wait_group_gone_polls_through_the_transient_eperm` 用假 `killpg`
钉死这条序列；`test_a_wrapper_process_shape_still_ends_with_an_empty_group` 用真的包装形状跑；M27 打红）。
这是「恢复 / 失效那一刻才是缺陷藏身处」的又一例：判据在方便的那个时刻（组早就空了）恒绿，只在 reap 之后那一瞬才露出来。

Windows：`taskkill /T /F /PID`（`Popen.terminate()` 是 TerminateProcess，只杀直接子进程），再 `wait()`；组的判据不存在
（`group_gone: null`）。**本机验不了**（§8）。

**取消那一刻**（CIP-019）：runner 取消 step / step 超时 / 人按 Ctrl+C 时脚本收到的是 SIGINT / SIGTERM，而 Python 对 SIGTERM 的默认
处置是直接退出、`finally` 不跑——服务就活到 VM 销毁。`main()` 一开始把 SIGTERM / SIGINT（Windows 上还有 SIGBREAK）装成
`raise SystemExit(128 + signum)`，异常沿栈走到 `run_attempt` 的 finally；用例 `test_sigterm_while_waiting_still_terminates_the_server_and_its_worker`
对着 never-ready 的桩在等待期间发 SIGTERM：脚本退 143、桩与 worker 都不在、result.json 照写（M28）。

### 3.1 失败日志上传前脱敏（lead 审核补，commit df721c98 / fe5fffbf）

失败时上传的 artifact 原来是 `smoke-run/**`——它会把 `attempt-*/data/session/port-<P>.json`（本实例
的会话凭据，ADR 0008）与带 `#dnonce=<nonce>` 的 `server.log` 一起传出这台 VM。虽然那时进程已死、
凭据无用，`03_RUNNERS_AND_TRUST.md` §3「上传失败日志前脱敏」的纪律是明确的：

* artifact 路径改为**正面列全**的两条：`smoke-run/result.json` 与 `smoke-run/attempt-*/server.log`；
  `data` / `config` 永远不在里面（合同用例 `test_failure_logs_are_uploaded_under_a_name_unique_per_leg`
  逐行比路径清单，写回 `smoke-run/**` 就红）。
* 脚本在每次尝试的 `finally` 里、子进程终止**之后**把 `server.log` 原地脱敏：`#dnonce=<值>` →
  `#dnonce=<redacted>`（键名保留，读日志的人仍认得出那一行是握手 URL）；失败时打到 stderr 的日志尾
  同样经过 `redact()`。桩服务器打一行同形的握手 URL 当靶子，
  `test_the_handshake_nonce_is_redacted_from_the_log_before_it_can_be_uploaded` 的主语是**落盘的
  server.log** 与 stderr 日志尾；拿掉 `redact_log_file()` 调用 → 红（rc 1）。

## 4. 本机实测（macOS，worktree，前台跑；命令与产物在 `evidence/ci03b/local_runs/`）

### 4.1 真 wheel 走一遍 `package` 的流程

`python -m build --wheel`（`build_wheel.log`，`tavotto-0.14.0-py3-none-any.whl`，1.68 MB）→ `python -m venv <scratch>/smoke-venv` →
`pip install --quiet dist/*.whl`（3.5 s）→ `python scripts/ci/package_smoke.py --python <venv>/bin/python --workdir <scratch>/smoke-run`：

| 项 | 值 |
|---|---|
| rc | **0** |
| `ready_seconds` | **2.33**（原来盲等 8 秒：慢机器上 8 秒不够就假红，这台机器上白等 5.7 秒） |
| 就绪三道 | `/api/version` 200 → `attempt-1/data/session/port-57875.json` 在 → `/api/session/ping` 200（`real_wheel_server.log`，nonce 已抹） |
| 就绪后 | `GET /` 200、`GET /api/version` 200 `{"version": "0.14.0", "build": "index-CfkcuH7t"}` |
| 终止 | `killpg-term`，returncode −15，`group_gone: true`，`leftover_workers: []`；整次 2.37 s |

### 4.2 租约被抢（真产品的顺延形状）

`--launch "python evidence/ci03b/occupy_then_run.py --state-file S --port {port} -- <venv>/bin/python -m tavotto --port {port} --no-browser"`
（驱动第一次起时先在 `{port}` 上 `bind + listen`，再把真产品起在同一个端口上；第二次不占）：

| 尝试 | 端口 | 状态 | 产品日志 | 用时 |
|---|---:|---|---|---:|
| 1 | 58253 | `lease_lost` | `* 端口 58253 被占用，改用 58254`，随后在 58254 上服务、写了 `port-58254.json` | 3.83 s（含终止；`group_gone: true`） |
| 2 | 58267 | `ready` | 正常 | 0.58 s（就绪 0.26 s，缓存热） |

rc 0，`attempts: 2`；之后 `ps` 里没有任何 `-m tavotto` 残留。这一跑第一次做时 `group_gone: false`（§3.2 的 EPERM），修后重跑得到上表。

### 4.3 单测与合同（`tests/test_package_smoke.py` 41 条 / `TestPackageSmokeIsolation` 7 条）

41 条 ≈ 27 秒（三条 3 秒超时的负例 + 一条 2 秒 slow-ready + 一条 3.7 秒的 SIG_IGN 升级）；四条 POSIX 专属用例在 Windows 上 skip
并写明理由（进程组 / SIG_IGN / 可捕获的 SIGTERM 是 POSIX 语义）。跑完 `ps` 里没有桩与 `sleep 600` 残留。

## 5. 负例（桩 `tests/support/stub_http_server.py` 的 `--fail-mode`，全部有用例）

| 桩 | 行为 | 脚本 | 用例 |
|---|---|---|---|
| `bind-busy`（第一次） | 同一进程把端口 bind 两遍，把平台真实的 EADDRINUSE 原话打到 stderr 后退 1 | `lease_lost` → 换号 → rc 0，`attempts == 2`，第一次 `elapsed < 15` | `…lost_lease…[bind-busy]` |
| `fallback`（第一次） | 打「* 端口 P 被占用，改用 P+1」并真的在 P+1 上服务 | 同上，且顺延那个活着的实例在重试前被终止 | `…[fallback]`、`test_the_fallback_instance_is_terminated_before_the_retry` |
| `bind-busy`（每次） | 没有 state-file，次次占 | `--attempts 2` → 两次 `lease_lost` → rc 1，「已连丢 2 次租约」 | `…failure_not_an_endless_loop` |
| `never-ready` | `/api/version` 永远 503 | `--timeout 3` → `timeout` rc 1，stderr 带日志尾与「/api/version → 503」 | `test_never_ready_…` |
| `no-credentials` | 答 200 但不写凭据文件 | 不算就绪 → rc 1，理由带「凭据文件」 | `test_a_public_200_from_a_stranger_…` |
| `reject-credentials` | 写了文件，`/api/session/ping` 对谁都 401 | 不算就绪 → rc 1，理由带 ping 401 | `test_a_server_that_rejects_our_credentials_…` |
| `bad-json` | `/api/version` 200 但 body 是 HTML | 不算就绪 → rc 1 | `test_a_200_that_is_not_json_…` |
| `index-500` | 就绪全过，`/` 500 | `failed`，「GET / → 500」，`index_status: 500` | `…index_is_not_200_fails` |
| `no-version-field` | `/api/version` 200 的 JSON 没有 `version` | `failed`，「version 字段」 | `…without_the_version_field_fails` |
| `crash` | 启动即退 3 | `exited` rc 1，stderr 含桩那句，不重试，`elapsed < 15` | `test_a_crash_at_startup_…` |
| `slow-ready --delay 2` | 2 秒后才 200 | rc 0，`ready_seconds ≥ 2` | `…waited_for_by_the_predicate_not_by_a_sleep` |
| `--worker-ignores-term` | worker 对 SIGTERM 置 SIG_IGN | `how == killpg-term+kill-group`，`group_gone: true`，两个 pid 都不在 | `…ignores_sigterm_…`（POSIX） |
| 等待中收到 SIGTERM | never-ready 的桩，脚本轮询期间被 `SIGTERM` | 退 143，桩与 worker 都不在，result.json 在 | `test_sigterm_while_waiting_…`（POSIX） |
| 包装形状 | `python -c "subprocess.run([stub])"` | `how == killpg-term`，`group_gone: true`（不被 EPERM 那一拍判假） | `…wrapper_process_shape…`（POSIX） |
| 用法 | 没给 `--workdir` / `--python` 与 `--launch` 同给或都不给 / 模板没有 `{port}` | rc 2，不起任何进程 | 三条 `…usage_error` |

桩本身像 Tavotto 的那几处：`/` 200、`/api/version` 200 JSON、bind 之前用**产品自己的** `session_client.publish_secret`
写凭据（路径公式与文件形状不另写一份）、`/api/session/ping` 凭 `X-Tavotto-Auth` 判 200 / 401、起一个 `sleep 600` 当 worker。
**刻意不像**的一处：桩的 `server_bind` 不做父类那次反查主机名（§9）。

## 6. 变异反证（退出码判；每条先断言目标串恰好一次 → 变异 → 清 `__pycache__` → pytest → 还原核 md5）

### 6.1 `scripts/ci/package_smoke.py` vs `tests/test_package_smoke.py`：**28/28 KILLED**（`evidence/ci03b/mutations.json`）

M01–M04 四句占用文案各拿掉一条 / M05 就绪不再要求凭据文件 / M06 不再要求 ping 200 / M07 body 不再要求 JSON 对象 /
M08 子进程退出不算失败 / M09 租约丢了不重试 / M10 就绪后不打那两条 / M11 `/` 不要求 200 / M12 不要求 `version` 字段 /
M13 只 `terminate()` 直接子进程 / M14 组里剩人不升级 SIGKILL / M15 组的存在性恒真 / M16 不用新会话起 / M17 组长判据恒真 /
M18 组里仍有进程不算失败 / M19 模板不要求 `{port}` / M20 `--python` 与 `--launch` 不再二选一 / M21 失败不打日志尾 /
M22 不设 `TAVOTTO_DATA_DIR` / M23 子进程编码只钉父侧 / M24 stdout 多一行 / M25 租约连丢到用完退 0 / M26 worker 残留不算失败 /
M27 EPERM 立刻判 False / M28 不装 SIGTERM 处理器（取消时 finally 不跑）。
第一轮（改结构之前，`mutations_round1.json`）24/27：M18 / M26 存活是因为「判据在、但没有任何用例把它逼进控制流」——组永远被 SIGKILL 清空、
`_leftover_workers` 在桩上永远空，于是加了两条 monkeypatch 判据结果的用例（判据要进控制流）；M27 存活是因为 EPERM
那一拍靠时序，加了假 `killpg` 序列的用例之后确定性打红。改完结构（§3.1 末）后重跑一轮 27/27；加上 SIGTERM 处理器与 M28 之后按最终树再跑一轮，结果见 `mutations.json`。

### 6.2 ci.yml vs `TestPackageSmokeIsolation`：**18/18 KILLED**（`evidence/ci03b/mutations_ci.json`，每条记着哪几条用例红）

C01/C02 两步的 venv 回到 `/tmp/smoke` / C03 workdir 不在 runner.temp / C04 换回旧写法（5199 + sleep + curl）/ C05 被测解释器不是
venv 的 / C06 不探 `Scripts` / C07 删 step timeout / C08 job timeout 60 → 90 / C09 artifact 名去掉 `matrix.python` / C10 `failure()` →
`always()` / C11 artifact 路径不是 workdir / C12 删整个上传步骤 / C13 又设回 `TAVOTTO_DATA_DIR` / C14 needs 回到 `[backend-fast, frontend]`
（连带 `TestHeavyLaneDependencies` 两条红）/ C15 少一条 windows 腿 / C16 末尾加 `sleep 2` / C17 装 wheel 那步写死另一个路径 /
C18 从 Gate 的 `--required` 里拿掉 package（连带 `TestGates` 红）。

**没做的变异**（写在明处）：`looks_busy` 在 `poll()` 之前还是之后读日志（§2 的顺序）——只在「退出的同一拍」有差别，静态用例造不出
那一拍；`_leftover_workers` 的真实匹配（需要一个 `tavotto/engine/worker.py --figures-dir …` 进程，产品不开项目不会起 worker）。

## 7. 验证命令与退出码（2026-09-16，worktree，本 PR 的树）

| 命令 | 退出码 |
|---|---:|
| `/opt/homebrew/bin/actionlint .github/workflows/ci.yml` | 0 |
| `.venv/bin/ruff check . && .venv/bin/ruff format --check .` | 0（374 files already formatted） |
| `.venv/bin/python -m pytest tests/test_package_smoke.py tests/test_merge_queue_workflows.py tests/test_windows_regressions.py tests/test_ci_tooling.py tests/test_docs_references.py tests/test_source_hygiene.py` | 0（244 passed, 1 skipped：test_ci_tooling 的「非 Linux 无 /proc」；38 s） |
| `… tests/test_ci_qualification.py tests/test_ci_baseline.py tests/test_aggregate_gate.py tests/test_e2e_leg_topology.py tests/test_support_matrix.py tests/test_merge_queue_ruleset.py tests/test_release_workflow_contract.py tests/test_pytest_shard.py tests/test_playwright_shard_check.py tests/test_tracked_paths_are_windows_safe.py tests/test_generated_untracked.py` | 0（350 passed；17 s） |
| `python scripts/ci/package_smoke.py --python <venv>/bin/python --workdir …`（§4.1） | 0 |
| 同上 `--launch` 指向 `occupy_then_run.py`（§4.2） | 0（`attempts: 2`） |
| 实机：PR 上 full-ci 的 `package` 四条腿各自的 `起服务并请求首页` 结论与（失败时的）`package-smoke-logs-*` | **not_run**（不能 push） |

仓库既有门禁对新文件的裁决：`test_script_entry_points_pin_their_output_encoding`（脚本模块作用域钉 stdout+stderr）、
`test_support_probes_reconfigure_stdout_to_utf8`（桩有 `__main__`，同样钉）、`test_no_ci_script_hard_codes_a_posix_only_signal`
（`SIGKILL` 走 `getattr`）、`test_no_launcher_leaves_a_child_pipe_undrained`（Popen 落文件）、
`test_every_app_launcher_adopts_credentials` / `test_no_app_request_anywhere_skips_auth` / `test_session_credential_logic_has_a_single_implementation`
（脚本设了 `TAVOTTO_DATA_DIR` 所以算启动器：走 `smoke_app` 的唯一实现，每个 `Request` 带 `_AUTH`）、
`test_windows_bound_subprocesses_pin_their_decoding`（`tests/` 里每个 `subprocess.run` 钉 `encoding="utf-8"`）——全绿，
其中启动器那条在第一版上红过一次（§3.1 末）。

## 9. macOS runner 上的 getfqdn 停顿：首跑 10 条红的根因（PR #376，2026-09-16）

**现象**：PR #376 首跑（run 35024490379）Linux 六片、Windows 两片全绿，只有 `backend-platforms (macos-latest, 2)` 红 10 条，
全在 `tests/test_package_smoke.py`——共同签名是某一次尝试里桩**一行都没打**（「日志是空的」），脚本最后一次轮询是
`URLError: <urlopen error timed out>`（**timed out，不是 refused**）。红的是 `--timeout 3` 的五条负例 + `--timeout 30` 的
五条（bind-busy / fallback 的第 2 次尝试、两条 `run_attempt(…, 30)`）；`--timeout 60` 的正例、不构造 HTTP server 的
（crash / bind-busy 每次 / sigterm——pid 文件在 server 之前写）全绿。

**诊断补丁**（commit `2e176774`，不是修复）：桩每个阶段打带相对秒数的进度行（`starting pid= python=` → 参数 → state-file →
`import session_client` → `publish_secret` 前后 → worker spawn 前后 → **socket 已 bind（还没 listen）→ getfqdn 计时 → 已 listen** →
serving；连续相同的访问行折叠成一句计数，别把启动期挤出 40 行日志尾）；脚本把「连不上」分成 refused / connect timed out /
recv timed out / 其它 errno，并记每次尝试的轮询序列（同结果连续 N 次折成一段）；单测阈值 3 → 15、30 → 60。

**证据**（诊断 run 35028309531，job 104580559570；两次 run 的失败摘录原文在 `evidence/ci03b/macos_getfqdn_trace.txt`）：
5 条 15 s 阈值的红，60 s 的全绿。失败用例的日志尾停在

```
stub: +  0.01s HTTPServer 构造前（socket → bind → getfqdn → listen）
stub: +  0.01s socket 已 bind 49597（还没 listen）；getfqdn 前
```

之后 15 秒内没有下一行；脚本的轮询序列是 `[0.03s ×1] refused（端口上没人 bind） → [3.38s–16.65s ×5] connect timed out（SYN 无回音）`。

**机制**：`http.server.HTTPServer.server_bind` 在 `socket.bind` **之后**、`listen` **之前**调 `socket.getfqdn(host)`（按 host 反查
主机名填 `server_name`），GitHub 的 macOS runner 上这次反查 30 秒以上没有回音（< 60 s：60 s 的正例都绿）；而 macOS 对
「已 bind 未 listen」端口的 SYN 是**丢掉**不是 RST——本机实测 `bound-not-listening → TimeoutError('timed out') 3.01 s`、
`nothing bound → ConnectionRefusedError 0.00 s`。于是脚本每次 `connect` 都吃满 3 秒超时，日志尾没有「serving」，负例在
桩起来之前就判了超时。三条根因假设里这一条被证实，另两条（进程没起来 / stderr 没落到脚本读的文件；macOS 26 loopback 丢 SYN）
被 `starting` 行与「已 bind」之前的 refused 一拍证伪。

**桩的修法**（本节的 commit）：`Server.server_bind` 只调 `socketserver.TCPServer.server_bind`（bind），`server_name` 直接填 host，
**不做反查**——桩不用 `server_name`；追踪行改成「已 bind（还没 listen）→ 已 listen」。本机 DNS 快，行为上反证不出差别（由 macOS run 判），
所以钉的是静态合同 `test_the_stub_never_resolves_a_hostname_between_bind_and_listen`：正面 = `class Server` 覆写了 `server_bind`
且调的是 `socketserver.TCPServer.server_bind`（覆写整个删掉就回到父类那条路，光看名字不出现是恒真）；反面 = 桩源码里
（AST 属性 / 名字 + 子串，注释也算）不出现 `getfqdn`。变异：加回 `self.server_name = socket.getfqdn(host)` → rc 1；删掉
`server_bind` 覆写 → rc 1（本机，退出码判）。单测阈值维持 15 / 60：它们现在量的只是桩的 Python 启动，不再量 DNS。

**产品侧影响（本计划不改产品）**：werkzeug 3.1.8 的 `BaseWSGIServer` 继承同一个 `server_bind`、没有覆写——真产品在 macOS runner 上
每次起服务都多等 ~36 s：#376 首跑 `package (macos-latest, 3.13)` 的 `ready_seconds` = **35.78 s**（Linux 0 / Windows 2）；CI00 基线里
旧写法（`sleep 8` + curl）这一步 macOS 48 s vs 其它腿 13 s——那时它没红多半是因为 curl 没带 --max-time，对着同一个卡住的端口重发 SYN 直到反查回来才
反查回来。用户机器上反向 DNS 无回音（离线、公司 DNS 不答 PTR）也会有同样的首开延迟。**建议立 issue**（产品 / werkzeug 侧：
`app.run` 之前 `socket.getfqdn` 的替代、或自带一个只 bind 的 server 子类），本计划只在 CI 留余量。
**拍板（2026-09-16，用户）：现在就修产品，单开分支，不进 CI 栈**——修复 PR 另开；修好之后 `--timeout 120` 可以回到默认值（合同 `test_the_smoke_step_runs_the_isolated_script_on_the_venv_python` 跟着改）。

**为什么 `--timeout 120`**：就绪上限默认 60 s，macOS 腿实测 35.78 s——余量贴边（DNS 再慢 20 秒就假红）。改成 120（step 级 5 分钟不动：
一次真正的就绪超时 120 s + 换号重试每次几秒——三种租约丢失的文案都在产品 bind 之前出现——+ 每次 ≤ 16 s 终止 ≈ 2.5 分钟）；
合同 `test_the_smoke_step_runs_the_isolated_script_on_the_venv_python` 钉 `--timeout 120`（变异：拿掉 → rc 1）。
这个数字是「留余量」不是「量出来的判据」：根因修掉之后可以回到默认值。

## 8. 已知边界（如实）

- **macOS runner 上真产品起服务多等 ~36 s**（§9）：根因在 werkzeug 继承的 `server_bind` 反查主机名，本计划不改产品，CI 只把就绪上限抬到 120 s。
- **Windows 腿一次都没跑过**：`taskkill /T /F` 那一支、`tasklist` 判存在、bash 里 `VENV="D:\a\_temp/smoke-venv"` 这种混合分隔符
  路径的 `[ -d ]` / exec（原来 `/tmp/smoke` 走的是 MSYS 的路径转换），都只在 CI 验。四条 POSIX 专属用例在 Windows 上 skip。
  `smoke_app._leftover_workers` 在 Windows 上用 `wmic`，新 runner 镜像没有它时静默回空表——这是 smoke_app 既有的边界，不是本轮新增。
- **租约有窗口**：产品 `--port` 不接受 0，脚本只能取号再释放；窗口里被抢按 §2 的三种文案认。文案是产品 / 平台的原话，产品改
  措辞要回来改 `_BUSY`（`test_looks_busy_recognises_every_platform_and_product_phrasing` 钉着七句）。
- **产品的 `#dnonce=…` 会进 `server.log`**：`* 打开 http://…/#dnonce=<一次性 nonce>` 是产品 stdout 的原话，原来 `&` 起时也在 job 日志里；
  现在只在失败时随 artifact 上传（7 天），且 nonce 随进程一起作废。evidence 里那两份已抹掉。
- **同机双实例的真实并发没跑**：本机只跑了单实例 + 人为抢占；两个 `package` 同时跑在一台机器上要等 CI04 的多 runner 主机。
  CIP-018 / CIP-019 因此不写 pass。
- 就绪超时默认 60 秒 × 3 次 + 每次 ≤ 16 秒终止（10 秒等退出 + 两次 3 秒核组空）≈ 3.8 分钟上限，step 级 5 分钟在它之外；Windows runner 冷启动 wheel 的真实数字要等 CI。
- `--launch` 模板按 shlex（POSIX 规则）切：Windows 上路径要用正斜杠或引号包好（单测用 `Path.as_posix()`）。
- `windows-exe-smoke` 的 `dist/` 与 `posix-e2e` 的 `python -m tavotto` 形态**没动**——它们的实例隔离不在本 PR（04 §4 的其余撞点）。
