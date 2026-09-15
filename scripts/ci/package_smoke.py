#!/usr/bin/env python3
"""`package` job 的冒烟（CI03b，2026-09-16）：把干净 venv 里装好的 wheel **真起一次**，等它就绪，
打两条请求，再把它干净地终止——每一步都有判据，不靠 sleep。

    python scripts/ci/package_smoke.py --python "$RUNNER_TEMP/smoke-venv/bin/python" \\
        --workdir "$RUNNER_TEMP/smoke-run"

    # 换掉被起的进程（单测用桩服务器；模板里的 {port} 由脚本填，按 shlex 规则切分）：
    python scripts/ci/package_smoke.py --workdir /tmp/x \\
        --launch "python tests/support/stub_http_server.py --port {port} --fail-mode crash"

替换的是 ci.yml `package` job 原来那一步：`python -m tavotto --port 5199 --no-browser &`、
`sleep 8`、两条 curl。那一步有三件事在同一台机器上跑两个 `package` 实例时会互相撞，而托管
VM 用完即毁所以从没暴露（docs/implementation/ci-foundation/04_BUILD_TEST_CACHE.md §4）：

* **固定端口 5199**：第二个实例的 `resolve_port` 顺延到 5200，curl 打的却仍是 5199——验的
  是**第一个**实例，第二个从没被验过；
* **`sleep 8`**：慢机器上 8 秒没起来就假红，快机器白等；就绪与否要问 `/api/version`；
* **从不终止**：`&` 起的服务一直活到 VM 销毁；进了多 runner 主机就是泄漏进程。
  （固定路径 `/tmp/smoke` 那件在 ci.yml 里改，不在这个脚本里。）

判据的主语（根 AGENTS.md「判据的主语」）：

* **端口**：脚本向系统租（`bind(("127.0.0.1", 0))` 取号再释放）。产品的 `--port` 不接受 0
  ——不改它：端口冲突用例要的正是「被占用就顺延」这个行为——所以租约与真 bind 之间有窗口。
  窗口里被人抢走时产品**不会报错退出**：`resolve_port` 顺延到下一个空闲端口（stdout 打
  「端口 P 被占用，改用 Q」），对面已是一个 Tavotto 时直接退 0（「已在 … 运行」），只有
  `resolve_port` 与 `app.run` 之间那几毫秒被抢才是 bind 抛 EADDRINUSE。三种形状都按「日志里
  出现占用类文案」认成**租约丢了**，换号重来（≤ `--attempts`）；别的退出原因一律失败并把
  日志尾打到 stderr。
* **就绪**：`/api/version` 200 且 body 是 JSON 对象——**而且应答的是我们起的那个进程**。
  后一半靠 ADR 0008 的本机凭据：产品在 bind 之前把 `port-<P>.json` 写进**本实例的** data
  dir，脚本用 `smoke_app.adopt_session_credentials`（唯一实现）读它，再打一次带凭据的
  `/api/session/ping`；200 = 应答者持有只有我们的子进程才写得出的 secret。只看
  `/api/version` 200 的话，租约丢失时那个 200 可能来自隔壁实例（公共端点谁都答）。
* **终止**：判据是**进程不存在**，不是「发了信号」。POSIX 上 `start_new_session=True` 起、
  对整个进程组 SIGTERM → 等 10 秒 → SIGKILL → `wait()`，最后 `killpg(pgid, 0)` 必须 ESRCH；
  Windows 上 `taskkill /T /F` 杀整棵树再 `wait()`（`terminate()` 只杀直接子进程）。之后再用
  `smoke_app._leftover_workers`（按本实例 data dir 认）确认没有 worker 残留。

stdout 永远只有一行 JSON（`ok / port / attempts / ready_seconds / version / workdir / history`），
进度与失败原因走 stderr。退出码：0 通过；1 冒烟失败（就绪超时 / 非 200 / 子进程异常退出 /
终止后仍有残留）；2 用法错误。子进程的 stdout+stderr 落 `--workdir` 下的日志文件
（**不开 PIPE**：tests/test_source_hygiene.py::test_no_launcher_leaves_a_child_pipe_undrained），
失败时尾 40 行打到 stderr。纯标准库 + `scripts/smoke_app.py`（与 `scripts/ci/soak.py` 同一种复用）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows 上 stdout 一旦不是真控制台（被 CI 捕获 / 管道 / 重定向）就退回系统区域
# 编码（cp1252/cp936），第一句中文或 ✓ 的输出就 UnicodeEncodeError——脚本明明
# 做完了却以非零退出，而父进程只看得见「它挂了」。写法与 aggregate_gate.py 同源。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # scripts/ ——复用既有冒烟工具（与 soak.py 同一种复用）

import smoke_app as SA  # noqa: E402

POLL_S = 0.25
TERMINATE_GRACE_S = 10.0
GROUP_GONE_WAIT_S = 3.0
#: Windows 没有 SIGKILL（tests/test_windows_regressions.py::test_no_ci_script_hard_codes_a_posix_only_signal）；
#: 用到它的分支只在 POSIX 走到，getattr 是为了模块在所有平台都 import 得动。
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
LOG_TAIL_LINES = 40
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_ATTEMPTS = 3

#: 「租约丢了」的文案，三个平台 + 产品自己的两句。**只在子进程日志里找**，主语是被起的
#: 那个进程写出来的字节：
#:   * POSIX `bind()`：`[Errno 48/98] Address already in use`；
#:   * Windows：`[WinError 10048] Only one usage of each socket address …`——中文区域
#:     那句是「通常每个套接字地址(协议/网络地址/端口)只允许使用一次」，所以还认 `WinError 10048`；
#:   * Node / libuv 风格的 `EADDRINUSE`（将来换控制面也不用回来改）；
#:   * 产品 `resolve_port` 顺延：`* 端口 P 被占用，改用 Q`；对面已是 Tavotto：`* Tavotto 已在 … 运行`。
_BUSY = re.compile(
    r"[Aa]ddress already in use"
    r"|Only one usage of each socket address"
    r"|WinError 10048"
    r"|EADDRINUSE"
    r"|端口 \d+ 被占用"
    r"|已在 \S+ 运行"
)


def looks_busy(text: str) -> str | None:
    """日志里第一句占用类文案（所在的那一行）；没有就 None。"""
    m = _BUSY.search(text)
    if not m:
        return None
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return text[start : end if end != -1 else None].strip()


def parse_launch(template: str, port: int) -> list[str]:
    """`--launch` 模板 → argv。按 shlex（POSIX 规则）切，`{port}` 逐 token 替换。

    模板里必须有 `{port}`：脚本租到的端口要有地方填，否则被起的进程听在别的端口上、
    脚本却对着租来的那个等到超时——那是用法错误，不是冒烟失败。
    """
    tokens = shlex.split(template)
    if not tokens:
        raise ValueError("--launch 模板是空的")
    if not any("{port}" in tok for tok in tokens):
        raise ValueError("--launch 模板里没有 {port}——脚本租到的端口要有地方填")
    return [tok.replace("{port}", str(port)) for tok in tokens]


def build_command(python: str | None, launch: str | None, port: int) -> list[str]:
    if launch:
        return parse_launch(launch, port)
    if not python:
        raise ValueError("--python 与 --launch 二选一")
    return [python, "-m", "tavotto", "--port", str(port), "--no-browser"]


def child_env(data_dir: Path, config_dir: Path) -> dict[str, str]:
    """子进程环境：本实例自己的 data / config 目录，编码钉两侧，联网检查与遥测硬关。"""
    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        # 冒烟不该依赖 GitHub 与 telemetry.tavotto.com 可达；CI 也绝不能产生真实的产品事件
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_NO_TELEMETRY": "1",
        # 编码钉两侧：父进程按 UTF-8 读日志文件，子进程也得按 UTF-8 写（只钉一侧的话，
        # Windows 上子进程按代码页写出的中文在这边是乱码，占用类文案就匹配不上）
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    # 冒烟验证的是「认证默认开着」，外面误设的开发旁路不许泄进来
    env.pop("TAVOTTO_INSECURE_NO_AUTH", None)
    return env


def read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def log_tail(path: Path, n: int = LOG_TAIL_LINES) -> str:
    lines = redact(read_log(path)).splitlines()
    return "\n".join(lines[-n:])


#: 产品启动时把桌面握手用的一次性 nonce 打在 stdout 的 URL 里（`app.py` 的
#: `url += "#dnonce=" + nonce`）。它是本实例的会话凭据之一（ADR 0008），而 server.log
#: 在失败时会作为 artifact 上传——03_RUNNERS_AND_TRUST §3「上传失败日志前脱敏」。
#: 只抹值，保留键名：读日志的人仍看得出「那一行是握手 URL」。
_DNONCE = re.compile(r"(#dnonce=)[^\s\"'&<>]+")


def redact(text: str) -> str:
    return _DNONCE.sub(r"\1<redacted>", text)


def redact_log_file(path: Path) -> None:
    """子进程退出**之后**把 server.log 原地脱敏（它退出前日志还在被写，先抹会被覆盖）。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    cleaned = redact(raw)
    if cleaned != raw:
        path.write_text(cleaned, encoding="utf-8")


def _status(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    """裸 GET，返回 (状态码, body)。HTTPError 也是一种**应答**（有状态码），与「连不上」不同。

    带上 `smoke_app._AUTH`：就绪之前它是空的（公共端点本来就不要），凭据装上之后所有请求
    都带着走（tests/test_ci_qualification.py::test_no_app_request_anywhere_skips_auth）。
    """
    req = urllib.request.Request(url, headers=dict(SA._AUTH))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def describe_connect_error(exc: BaseException) -> str:
    """「连不上」分三档说清楚——它们指向三种不同的状态，混成一句就没法诊断：

    * `refused`：端口上没人 bind（对端立刻 RST）；
    * `connect timed out`：SYN 发出去没有回音。**macOS 实测**：端口已 bind 但还没 `listen` 时就是
      这个（SYN 被丢，不是 RST）；`http.server.HTTPServer.server_bind` 恰好在 bind 与 listen 之间
      调 `socket.getfqdn(host)`（反向 DNS）——PR #376 macOS 首跑的签名；
    * `recv timed out`：三次握手成了、请求发了、对方不答——listen 了但没在 accept / 处理挂住。
    urllib 把 connect 阶段的错包在 `URLError.reason` 里，recv 阶段的 `TimeoutError` 裸抛，两处都认。
    """
    inner = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    phase = "connect" if isinstance(exc, urllib.error.URLError) else "recv"
    if isinstance(inner, ConnectionRefusedError):
        return "refused（端口上没人 bind）"
    if isinstance(inner, TimeoutError):
        if phase == "connect":
            return "connect timed out（SYN 无回音——macOS 上 = 已 bind 未 listen）"
        return "recv timed out（握手成了但没应答）"
    if isinstance(inner, OSError):
        return f"{type(inner).__name__} errno={inner.errno}: {inner}"
    return f"{type(inner).__name__}: {inner}"


class ProbeLog:
    """每一轮轮询的结果序列（去重成「同一结果连续 N 次」的段），判「一直 refused 然后 timed out」
    还是「一开始就 timed out」全靠它——只留最后一次的话两种形状看起来一样。"""

    KEEP = 12  # 记前几段；序列再长就只在最后补一段

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.runs: list[dict] = []  # {"outcome", "count", "first_at", "last_at"}
        self.total = 0

    def note(self, outcome: str) -> None:
        now = round(time.monotonic() - self.t0, 2)
        self.total += 1
        if self.runs and self.runs[-1]["outcome"] == outcome:
            self.runs[-1]["count"] += 1
            self.runs[-1]["last_at"] = now
            return
        if len(self.runs) >= self.KEEP:
            self.runs[-1]["truncated_after"] = True
        self.runs.append({"outcome": outcome, "count": 1, "first_at": now, "last_at": now})

    def summary(self) -> str:
        return " → ".join(
            f"[{r['first_at']}s–{r['last_at']}s ×{r['count']}] {r['outcome']}" for r in self.runs
        )


def wait_ready(
    base: str,
    proc: subprocess.Popen,
    data_dir: Path,
    port: int,
    timeout: float,
    log_path: Path,
    probes: ProbeLog | None = None,
) -> tuple[str, object]:
    """轮询到「**我们的**进程在 `port` 上应答」为止。

    返回 (状态, 详情)：`ready` → `{"version": …, "seconds": …}`；`lease_lost` / `exited` /
    `timeout` → 一句原因。每一轮先 `poll()` 再读日志：读在 poll 之后，进程已退出时日志才是
    完整的（先读后 poll 会把「退出且日志里有占用文案」读成「退出而日志里还没有」）。

    「就绪」三道，少一道都不算：`/api/version` 200 且 JSON 对象 → 本实例 data dir 里有
    `port-<P>.json`（产品在 bind 之前写的，别的实例写不到这个目录；`smoke_app.adopt_session_credentials`
    是唯一实现）→ 用它打 `/api/session/ping` 200。租约丢失时 `/api/version` 的 200 可能来自隔壁
    实例——它也是 Tavotto、也答 200、也是 JSON。凭据的装载**就写在这一层**：仓库门禁
    `test_every_app_launcher_adopts_credentials` 从含 Popen 的函数出发只看一层可达。
    """
    probes = probes if probes is not None else ProbeLog()
    t0 = time.monotonic()
    deadline = t0 + timeout
    last = "还没收到任何应答"
    while True:
        rc = proc.poll()
        busy = looks_busy(read_log(log_path))
        if busy:
            probes.note(last)
            return "lease_lost", f"日志里出现端口占用类文案：{busy}"
        if rc is not None:
            probes.note(last)
            return "exited", f"子进程在就绪前退出，returncode={rc}"
        try:
            code, body = _status(f"{base}/api/version", timeout=3)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = f"{base}/api/version 连不上：{describe_connect_error(exc)}"
            code, body = None, b""
        if code is None:
            pass
        elif code != 200:
            last = f"/api/version → {code}"
        else:
            try:
                version = json.loads(body.decode("utf-8"))
            except ValueError:
                version = None
            if not isinstance(version, dict):
                last = f"/api/version 200 但 body 不是 JSON 对象：{body[:80]!r}"
            elif not SA.adopt_session_credentials(data_dir, port):
                last = (
                    f"/api/version 200，但本实例 data dir 里还没有 port-{port} 的凭据文件"
                    "——应答的可能是别人起的实例，或我们的还没走到 bind"
                )
            else:
                try:
                    ping, _ = _status(f"{base}/api/session/ping", timeout=3)
                except (urllib.error.URLError, OSError, TimeoutError) as exc:
                    ping = None
                    last = f"/api/session/ping 连不上：{type(exc).__name__}: {exc}"
                if ping == 200:
                    probes.note("ready")
                    return "ready", {"version": version, "seconds": time.monotonic() - t0}
                if ping is not None:
                    last = f"/api/session/ping 用本实例凭据打回 {ping}——{port} 上应答的不是我们起的进程"
        probes.note(last)
        if time.monotonic() >= deadline:
            return (
                "timeout",
                f"{timeout:.0f}s 内没就绪；最后一次：{last}；轮询序列：{probes.summary()}",
            )
        time.sleep(POLL_S)


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _wait_group_gone(pgid: int, timeout: float) -> bool:
    """进程组里一个都不剩了吗——`killpg(pgid, 0)` 抛 ESRCH 才算。主语是**存在性**，不是「发过信号」。

    EPERM 也算「还有东西」而不是「判不了」：macOS 上直接子进程刚被 reap、孙进程正在退出的那
    一瞬 `killpg(pgid, 0)` 会短暂报 EPERM，半秒后才 ESRCH（本机对着真产品 + 一层包装进程复现，
    CI03B 文档 §3.2）。当成失败返回的话，恰恰在「组里还剩一个正在死的进程」这一刻误判。
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)


def _taskkill_tree(pid: int) -> bool:
    """Windows：杀整棵树。`Popen.terminate()` 是 TerminateProcess，只杀直接子进程。"""
    try:
        out = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def _is_group_leader(pid: int) -> bool:
    """这个 pid 是不是自己进程组的组长（`start_new_session=True` 起的必然是）。

    不是组长时 `killpg(pid, …)` 打的是**别人的**组（或者 ESRCH 被静默吞掉），而
    `killpg(pid, 0)` 的 ESRCH 会把「没有这个组」读成「组里没人了」——恒真。所以先问一句。
    进程已经不在了就答 True：它是用 setsid 起的，组 id 只可能是它自己。
    """
    try:
        return os.getpgid(pid) == pid
    except ProcessLookupError:
        return True


def terminate(proc: subprocess.Popen) -> dict:
    """让子进程（连同它起的子进程）**不存在**。返回怎么终止的与最后的存在性判据。

    POSIX：对整组 SIGTERM → 等直接子进程 ≤ 10 秒（不退就整组 SIGKILL）→ 再看**组里**还有没有
    人（worker 之类的孙进程可能不理 SIGTERM）→ 有就整组 SIGKILL 再看一次。`group_gone` 是最后
    那一眼的结果，不是「发过信号」。Windows：`taskkill /T /F` 一次杀整棵树，组的判据不存在。
    """
    info: dict = {"how": "already-exited", "returncode": None, "group_gone": None}
    if os.name != "nt":
        info["group_leader"] = _is_group_leader(proc.pid)
    if proc.poll() is None:
        if os.name == "nt":
            info["how"] = "taskkill" if _taskkill_tree(proc.pid) else "terminate"
            if info["how"] == "terminate":
                proc.terminate()
        elif info["group_leader"]:
            info["how"] = "killpg-term"
            _killpg(proc.pid, signal.SIGTERM)  # start_new_session=True → pgid == pid
        else:
            info["how"] = "terminate"  # 不该走到：Popen 是 start_new_session=True 起的
            proc.terminate()
        try:
            proc.wait(timeout=TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            info["how"] += "+kill"
            if os.name == "nt" or not info["group_leader"]:
                proc.kill()
            else:
                _killpg(proc.pid, _SIGKILL)
            proc.wait()
    info["returncode"] = proc.returncode
    if os.name != "nt" and info["group_leader"]:
        gone = _wait_group_gone(proc.pid, GROUP_GONE_WAIT_S)
        if not gone:
            info["how"] += "+kill-group"
            _killpg(proc.pid, _SIGKILL)
            gone = _wait_group_gone(proc.pid, GROUP_GONE_WAIT_S)
        info["group_gone"] = gone
    return info


def run_attempt(n: int, cmd: list[str], attempt_dir: Path, port: int, timeout: float) -> dict:
    """起一次、等就绪、打两条请求、终止。每次尝试各自一套 data / config / 日志。"""
    data_dir = attempt_dir / "data"
    config_dir = attempt_dir / "config"
    for d in (data_dir, config_dir):
        d.mkdir(parents=True, exist_ok=True)
    log_path = attempt_dir / "server.log"
    base = f"http://127.0.0.1:{port}"
    record: dict = {
        "attempt": n,
        "port": port,
        "command": cmd,
        "log": str(log_path),
        "state": None,
        "ok": False,
    }
    print(f"· 第 {n} 次：租到端口 {port}，起 {shlex.join(cmd)}", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    # 落文件、不开 PIPE：应用写满 64 KiB 管道缓冲就会永久阻塞在写日志上，症状指向「关不干净」
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            env=child_env(data_dir, config_dir),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),  # POSIX：自己一个进程组，终止时整组一起
        )
    probes = ProbeLog()
    try:
        state, detail = wait_ready(base, proc, data_dir, port, timeout, log_path, probes)
        record["state"] = state
        record["probes"] = {"total": probes.total, "runs": probes.runs}
        if state != "ready":
            record["reason"] = detail
        else:
            assert isinstance(detail, dict)
            record["ready_seconds"] = round(detail["seconds"], 2)
            record["version"] = detail["version"]
            print(f"✓ 就绪 {record['ready_seconds']}s（端口 {port}）", file=sys.stderr, flush=True)
            problems = _request_pair(base, record)
            if problems:
                record["state"] = "failed"
                record["reason"] = "；".join(problems)
    finally:
        record["terminate"] = terminate(proc)
        record["leftover_workers"] = SA._leftover_workers(data_dir)
        record["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        redact_log_file(log_path)  # 子进程已终止，日志不会再被写；上传前抹掉 #dnonce
    if record["state"] == "ready":
        after = []
        if record["terminate"]["group_gone"] is False:
            after.append("终止后进程组里仍有进程")
        if record["leftover_workers"]:
            after.append(f"终止后仍有 worker 残留：{record['leftover_workers']}")
        if after:
            record["state"] = "failed"
            record["reason"] = "；".join(after)
        else:
            record["ok"] = True
    return record


def _request_pair(base: str, record: dict) -> list[str]:
    """就绪之后的两条判据——与原来那两条 curl 同一件事：`/` 200、`/api/version` 200 + `version` 字段。"""
    problems: list[str] = []
    try:
        code, _ = _status(f"{base}/", timeout=30)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        problems.append(f"GET / 连不上：{exc}")
    else:
        record["index_status"] = code
        if code != 200:
            problems.append(f"GET / → {code}（期望 200）")
    try:
        code, body = _status(f"{base}/api/version", timeout=30)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        problems.append(f"GET /api/version 连不上：{exc}")
    else:
        record["version_status"] = code
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            payload = None
        if code != 200 or not isinstance(payload, dict):
            problems.append(f"GET /api/version → {code}，body {body[:80]!r}")
        elif not isinstance(payload.get("version"), str) or not payload["version"]:
            problems.append(f"/api/version 的 JSON 里没有非空的 version 字段：{payload}")
        else:
            record["version"] = payload
    return problems


def _report_failure(record: dict) -> None:
    print(
        f"ERROR: 第 {record['attempt']} 次（端口 {record['port']}）{record['reason']}",
        file=sys.stderr,
    )
    probes = record.get("probes") or {}
    if probes.get("runs"):
        print(
            f"--- 轮询序列（共 {probes['total']} 次）---\n"
            + "\n".join(
                f"  [{r['first_at']}s–{r['last_at']}s ×{r['count']}] {r['outcome']}"
                for r in probes["runs"]
            ),
            file=sys.stderr,
        )
    log = Path(record["log"])
    print(f"--- 服务日志尾（最后 {LOG_TAIL_LINES} 行）{log} ---", file=sys.stderr)
    print(log_tail(log) or "（日志是空的）", file=sys.stderr, flush=True)


def _install_signal_handlers() -> None:
    """runner 取消 / step 超时 / 人按 Ctrl+C：让 `finally` 里的终止跑完再退，别把服务留成孤儿。

    Python 对 SIGTERM 的默认处置是直接退出——`finally` 不跑，起的服务就活到 VM 销毁；多 runner
    主机上那就是泄漏进程（CIP-019）。这里把 SIGTERM / SIGINT（Windows 上还有 SIGBREAK）都转成
    `SystemExit(128 + 信号号)`，异常沿调用栈走到 `run_attempt` 的 finally，`main` 的 finally 照样
    写 result.json、打那一行 JSON。信号名按 getattr 取：SIGBREAK 只有 Windows 有。
    """

    def _exit(signum, _frame):
        raise SystemExit(128 + signum)

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _exit)


def main(argv: list[str] | None = None) -> int:
    _install_signal_handlers()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--python", help="冒烟 venv 的解释器：起 `<python> -m tavotto --port {port} --no-browser`"
    )
    ap.add_argument(
        "--launch",
        help="换掉被起的命令（模板，{port} 由脚本填，按 shlex 规则切）；与 --python 二选一",
    )
    ap.add_argument(
        "--workdir",
        required=True,
        type=Path,
        help="本实例根：每次尝试的 data / config / server.log 与 result.json 全在它下面；调用方必须给",
    )
    ap.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="每次尝试的就绪上限（秒，默认 60）"
    )
    ap.add_argument(
        "--attempts", type=int, default=DEFAULT_ATTEMPTS, help="端口租约被抢时最多试几次（默认 3）"
    )
    args = ap.parse_args(argv)
    if bool(args.python) == bool(args.launch):
        ap.error("--python 与 --launch 二选一")
    if args.attempts < 1:
        ap.error("--attempts 至少 1")
    if args.timeout <= 0:
        ap.error("--timeout 必须是正数")
    try:
        build_command(args.python, args.launch, 1)  # 先验模板形状，别等租了端口才发现
    except ValueError as exc:
        ap.error(str(exc))

    workdir = args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    history: list[dict] = []
    result: dict = {
        "ok": False,
        "workdir": str(workdir),
        "python": args.python,
        "launch": args.launch,
        "timeout": args.timeout,
        "max_attempts": args.attempts,
        "attempts": 0,
        "port": None,
        "ready_seconds": None,
        "version": None,
        "history": history,
    }
    rc = 1
    try:
        for n in range(1, args.attempts + 1):
            port = SA._free_port()
            cmd = build_command(args.python, args.launch, port)
            record = run_attempt(n, cmd, workdir / f"attempt-{n}", port, args.timeout)
            history.append(record)
            result["attempts"] = n
            if record["ok"]:
                result.update(
                    ok=True,
                    port=port,
                    ready_seconds=record["ready_seconds"],
                    version=record["version"],
                )
                rc = 0
                break
            if record["state"] == "lease_lost" and n < args.attempts:
                print(f"! 第 {n} 次：{record['reason']}——换端口重试", file=sys.stderr, flush=True)
                continue
            if record["state"] == "lease_lost":
                record["reason"] += f"（已连丢 {n} 次租约，不再重试）"
            _report_failure(record)
            break
    finally:
        (workdir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
