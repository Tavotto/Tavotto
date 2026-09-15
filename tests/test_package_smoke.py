"""`scripts/ci/package_smoke.py`（CI03b：`package` job 冒烟的实例隔离）的看护。

被起的进程换成 `tests/support/stub_http_server.py`（`--launch` 模板），每种 `--fail-mode` 对应
脚本的一条判据：

* 正例：rc 0，JSON 里 `version.version == "stub"`、`attempts == 1`、data / config 在 `--workdir` 下；
* 租约丢了（桩把端口 bind 两遍打出平台真实的 EADDRINUSE / 桩打出产品顺延那句「端口 P 被占用，
  改用 Q」）：换端口重试，`attempts == 2`，第一次的状态是 `lease_lost`，**不必等到超时**；
* 不算就绪的三种：`/api/version` 一直 503（超时）、答 200 但本实例 data dir 里没有凭据文件
  （像隔壁实例）、答 200 且有凭据文件但 `/api/session/ping` 401（像端口上答话的是别人）——
  三种都 rc 1，stderr 带日志尾；
* 启动即崩：rc 1，stderr 含桩的那句；不重试；
* 慢就绪：rc 0 且 `ready_seconds ≥ delay`（等的是判据，不是 sleep）；
* 终止：脚本退出后桩**与它起的 worker** 都不存在（主语是进程存在性）；
* 用法错误 rc 2：没给 `--workdir`、`--python` 与 `--launch` 同给 / 都不给、模板里没有 `{port}`。

纯函数（`parse_launch` / `looks_busy` / `build_command` / `child_env`）在进程内直接测。每条负例写完
都做过一次变异反证（拿掉脚本里对应的检查 → 该条红，退出码判），记录在
docs/implementation/ci-foundation/CI03B_PACKAGE_SMOKE_ISOLATION.md。
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import package_smoke as PS  # noqa: E402

SCRIPT = CI_DIR / "package_smoke.py"
STUB = ROOT / "tests" / "support" / "stub_http_server.py"


def _stub_launch(*extra: str) -> str:
    """`--launch` 模板：`<python> <stub> --port {port} …`。路径用正斜杠——模板按 shlex（POSIX）切，
    Windows 的反斜杠会被当成转义。"""
    parts = [Path(sys.executable).as_posix(), STUB.as_posix(), "--port", "{port}", *extra]
    return " ".join(shlex.quote(p) for p in parts)


def _run(
    workdir: Path, *args: str, timeout_s: float = 120
) -> tuple[subprocess.CompletedProcess, dict]:
    """跑一次脚本；返回 (进程结果, stdout 那一行 JSON)。stdout 必须**恰好一行**且是 JSON。

    传给脚本的 `--timeout` 不是判据的主语——判据是「503 / 非 JSON / 凭据不认 → 不算就绪 → 超时 rc 1」，
    `--timeout` 只是留给**桩起来**的时间。PR #376 macOS 首跑把它定在 3 秒，而那台 runner 上桩在
    `HTTPServer.server_bind` 的 `getfqdn` 里就耗掉了更久（日志空 + connect timed out），十条负例全红——
    量错了对象的贴边阈值。所以负例给 15 秒、要真起服务的正例给 60 秒，断言不变。
    """
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--workdir", str(workdir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout 应恰好一行 JSON，实际：{out.stdout!r}\nstderr：{out.stderr}"
    return out, json.loads(lines[0])


def _alive(pid: int) -> bool:
    """这个 pid 现在还存在吗。Windows 上 `os.kill(pid, 0)` 是 TerminateProcess，**不能**拿来探测。"""
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        ).stdout
        return f" {pid} " in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while _alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


# ---------------------------------------------------------------- 纯函数


def test_parse_launch_fills_the_port_into_every_token_and_splits_like_a_shell():
    argv = PS.parse_launch("'/a b/python' stub.py --port {port} --url http://x:{port}/", 5099)
    assert argv == ["/a b/python", "stub.py", "--port", "5099", "--url", "http://x:5099/"]


@pytest.mark.parametrize("template", ["", "   ", "python stub.py --port 5099"])
def test_parse_launch_refuses_templates_that_cannot_take_the_leased_port(template):
    with pytest.raises(ValueError):
        PS.parse_launch(template, 5099)


def test_build_command_defaults_to_the_product_module_on_the_leased_port():
    assert PS.build_command("/v/bin/python", None, 5123) == [
        "/v/bin/python",
        "-m",
        "tavotto",
        "--port",
        "5123",
        "--no-browser",
    ]
    with pytest.raises(ValueError):
        PS.build_command(None, None, 5123)


@pytest.mark.parametrize(
    "line",
    [
        "OSError: [Errno 48] Address already in use",  # macOS
        "OSError: [Errno 98] Address already in use",  # Linux
        "OSError: [WinError 10048] Only one usage of each socket address (protocol/network address/port) is normally permitted",
        "OSError: [WinError 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次。",  # 中文 Windows
        "Error: listen EADDRINUSE: address already in use 127.0.0.1:5199",
        "* 端口 5199 被占用，改用 5200",  # 产品 resolve_port 顺延
        "* Tavotto 已在 http://127.0.0.1:5199/ 运行，打开现有窗口",  # 对面已是 Tavotto
    ],
)
def test_looks_busy_recognises_every_platform_and_product_phrasing(line):
    assert PS.looks_busy("noise\n" + line + "\nmore") == line


@pytest.mark.parametrize(
    "line",
    [
        "* 打开 http://127.0.0.1:5199/",
        " * Running on http://127.0.0.1:5199",
        "文件被占用，无法写回",  # 「被占用」但不是端口
        "Traceback (most recent call last):",
    ],
)
def test_looks_busy_ignores_ordinary_startup_lines(line):
    assert PS.looks_busy(line) is None


def test_child_env_isolates_the_instance_and_pins_both_sides_of_the_encoding(monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", "/elsewhere")
    monkeypatch.setenv("TAVOTTO_INSECURE_NO_AUTH", "1")
    env = PS.child_env(Path("/w/data"), Path("/w/config"))
    assert env["TAVOTTO_DATA_DIR"] == str(Path("/w/data"))
    assert env["TAVOTTO_CONFIG_DIR"] == str(Path("/w/config"))
    assert env["TAVOTTO_NO_TELEMETRY"] == "1" and env["TAVOTTO_NO_UPDATE_CHECK"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8" and env["PYTHONUTF8"] == "1"
    assert "TAVOTTO_INSECURE_NO_AUTH" not in env, "冒烟验的是认证默认开着，开发旁路不许泄进去"


# ---------------------------------------------------------------- 正例


def test_a_healthy_server_passes_and_everything_lands_under_the_workdir(tmp_path):
    out, res = _run(tmp_path / "run", "--launch", _stub_launch())
    assert out.returncode == 0, out.stderr
    assert res["ok"] is True and res["attempts"] == 1
    assert res["version"] == {"version": "stub", "build": "stub"}
    assert isinstance(res["port"], int) and res["ready_seconds"] >= 0
    attempt = tmp_path / "run" / "attempt-1"
    assert (attempt / "server.log").is_file()
    # 桩用产品自己的 publish_secret 写凭据——文件落在**这次尝试的** data dir 里，证明子进程拿到的
    # TAVOTTO_DATA_DIR 就是 workdir 下那一个（不是调用方环境里的、也不是 /tmp）
    assert (attempt / "data" / "session" / f"port-{res['port']}.json").is_file()
    assert (attempt / "config").is_dir()
    assert json.loads((tmp_path / "run" / "result.json").read_text(encoding="utf-8"))["ok"] is True
    rec = res["history"][0]
    assert rec["index_status"] == 200 and rec["version_status"] == 200
    assert rec["leftover_workers"] == []


@pytest.mark.parametrize("mode", ["bind-busy", "fallback"])
def test_a_lost_lease_is_retried_on_a_fresh_port_without_waiting_for_the_timeout(tmp_path, mode):
    """两种「租约丢了」的形状：bind 真的抛 EADDRINUSE（平台原话）/ 产品顺延（「端口 P 被占用，改用 Q」）。"""
    launch = _stub_launch("--fail-mode", mode, "--state-file", (tmp_path / "state").as_posix())
    out, res = _run(tmp_path / "run", "--launch", launch, "--timeout", "60")
    assert out.returncode == 0, out.stderr
    assert res["ok"] is True and res["attempts"] == 2
    first, second = res["history"]
    assert first["state"] == "lease_lost", first
    assert second["state"] == "ready" and second["port"] != first["port"]
    # 认出来是靠日志里的文案，不是等 30 秒超时
    assert first["elapsed_seconds"] < 15, first
    assert "换端口重试" in out.stderr


def test_the_fallback_instance_is_terminated_before_the_retry(tmp_path):
    """顺延那次桩其实在 Q 上活着——重试之前必须把它连组终止，否则每次重试都多留一个进程。"""
    launch = _stub_launch(
        "--fail-mode",
        "fallback",
        "--state-file",
        (tmp_path / "state").as_posix(),
        "--pid-file",
        (tmp_path / "pids.json").as_posix(),
    )
    out, res = _run(tmp_path / "run", "--launch", launch, "--timeout", "60")
    assert out.returncode == 0, out.stderr
    assert res["history"][0]["state"] == "lease_lost"
    assert res["history"][0]["terminate"]["how"] != "already-exited", "顺延那次进程是活着被终止的"
    # pid 文件被第二次起覆盖了，这里只核第一次的终止记录 + 最终没有任何桩残留
    pids = json.loads((tmp_path / "pids.json").read_text(encoding="utf-8"))
    assert _wait_gone(pids["pid"]) and _wait_gone(pids["worker"])


def test_a_slow_server_is_waited_for_by_the_predicate_not_by_a_sleep(tmp_path):
    out, res = _run(
        tmp_path / "run", "--launch", _stub_launch("--fail-mode", "slow-ready", "--delay", "2")
    )
    assert out.returncode == 0, out.stderr
    assert res["ready_seconds"] >= 2.0, res["ready_seconds"]


# ---------------------------------------------------------------- 负例


def test_the_handshake_nonce_is_redacted_from_the_log_before_it_can_be_uploaded(tmp_path):
    """产品把桌面握手的一次性 nonce 打在启动 URL 里（`#dnonce=…`），server.log 失败时会作为
    artifact 上传（03 §3「上传前脱敏」）。判据的主语是**落盘的 server.log**（上传的就是它）与
    失败时打到 stderr 的日志尾——两处都不能再出现 nonce 的值，键名保留让人认得出那一行。"""
    out, res = _run(tmp_path / "run", "--launch", _stub_launch())
    assert out.returncode == 0, out.stderr
    log = (tmp_path / "run" / "attempt-1" / "server.log").read_text(encoding="utf-8")
    assert "#dnonce=<redacted>" in log, log
    assert "STUBNONCE" not in log, log
    # 失败路径的日志尾同样经过脱敏：never-ready 桩也打那一行
    out, _ = _run(
        tmp_path / "run2", "--launch", _stub_launch("--fail-mode", "never-ready"), "--timeout", "15"
    )
    assert out.returncode == 1
    assert "#dnonce=<redacted>" in out.stderr and "STUBNONCE" not in out.stderr, out.stderr


def test_never_ready_times_out_with_the_server_log_tail(tmp_path):
    out, res = _run(
        tmp_path / "run", "--launch", _stub_launch("--fail-mode", "never-ready"), "--timeout", "15"
    )
    assert out.returncode == 1
    assert res["ok"] is False and res["history"][0]["state"] == "timeout"
    assert "服务日志尾" in out.stderr and "stub: serving on" in out.stderr, out.stderr
    assert "/api/version → 503" in out.stderr


def test_a_public_200_from_a_stranger_is_not_our_readiness(tmp_path):
    """答 200 的那个进程没往**本实例的** data dir 写凭据——它是隔壁实例。不算就绪。"""
    out, res = _run(
        tmp_path / "run",
        "--launch",
        _stub_launch("--fail-mode", "no-credentials"),
        "--timeout",
        "15",
    )
    assert out.returncode == 1
    assert res["history"][0]["state"] == "timeout"
    assert "凭据文件" in res["history"][0]["reason"], res["history"][0]["reason"]


def test_a_server_that_rejects_our_credentials_is_not_our_readiness(tmp_path):
    """凭据文件是我们的进程写的，但端口上答话的不认它——那不是我们的进程。不算就绪。"""
    out, res = _run(
        tmp_path / "run",
        "--launch",
        _stub_launch("--fail-mode", "reject-credentials"),
        "--timeout",
        "15",
    )
    assert out.returncode == 1
    assert res["history"][0]["state"] == "timeout"
    assert (
        "/api/session/ping" in res["history"][0]["reason"] and "401" in res["history"][0]["reason"]
    )


def test_a_200_that_is_not_json_is_not_readiness(tmp_path):
    out, res = _run(
        tmp_path / "run", "--launch", _stub_launch("--fail-mode", "bad-json"), "--timeout", "15"
    )
    assert out.returncode == 1
    assert res["history"][0]["state"] == "timeout"
    assert "不是 JSON 对象" in res["history"][0]["reason"], res["history"][0]["reason"]


def test_a_ready_server_whose_index_is_not_200_fails(tmp_path):
    """就绪判据全过之后还有两条请求要真的打——与原来那两条 curl 同一件事。"""
    out, res = _run(tmp_path / "run", "--launch", _stub_launch("--fail-mode", "index-500"))
    assert out.returncode == 1
    rec = res["history"][0]
    assert rec["state"] == "failed" and "GET / → 500" in rec["reason"], rec
    assert rec["index_status"] == 500 and rec["version_status"] == 200


def test_a_version_payload_without_the_version_field_fails(tmp_path):
    out, res = _run(tmp_path / "run", "--launch", _stub_launch("--fail-mode", "no-version-field"))
    assert out.returncode == 1
    rec = res["history"][0]
    assert rec["state"] == "failed" and "version 字段" in rec["reason"], rec


def test_a_crash_at_startup_fails_at_once_with_the_child_stderr_and_no_retry(tmp_path):
    out, res = _run(
        tmp_path / "run", "--launch", _stub_launch("--fail-mode", "crash"), "--timeout", "30"
    )
    assert out.returncode == 1
    assert res["attempts"] == 1 and res["history"][0]["state"] == "exited"
    assert "returncode=3" in res["history"][0]["reason"]
    assert "启动即崩" in out.stderr, out.stderr
    assert res["history"][0]["elapsed_seconds"] < 15, "崩了就该立刻失败，不是等超时"


def test_a_lease_lost_on_every_attempt_is_a_failure_not_an_endless_loop(tmp_path):
    """没有 --state-file 的 bind-busy 每次都占：attempts 用完就失败，rc 1。"""
    out, res = _run(
        tmp_path / "run", "--launch", _stub_launch("--fail-mode", "bind-busy"), "--attempts", "2"
    )
    assert out.returncode == 1
    assert res["attempts"] == 2 and [h["state"] for h in res["history"]] == ["lease_lost"] * 2
    assert "已连丢 2 次租约" in out.stderr


# ---------------------------------------------------------------- 终止


def test_termination_leaves_neither_the_server_nor_its_worker(tmp_path):
    """主语是**进程存在性**：脚本退出后桩与它起的 `sleep 600` 都不在了。"""
    pid_file = tmp_path / "pids.json"
    out, res = _run(tmp_path / "run", "--launch", _stub_launch("--pid-file", pid_file.as_posix()))
    assert out.returncode == 0, out.stderr
    pids = json.loads(pid_file.read_text(encoding="utf-8"))
    assert _wait_gone(pids["pid"]), "桩自己还活着"
    assert _wait_gone(pids["worker"]), "桩起的 worker 成了孤儿"
    term = res["history"][0]["terminate"]
    if os.name == "nt":
        assert term["how"] == "taskkill", term
    else:
        # 恰好一次 SIGTERM 就退，组里也没剩人——`+kill` / `+kill-group` 出现说明有人不理 SIGTERM
        assert term == {
            "how": "killpg-term",
            "returncode": -15,
            "group_gone": True,
            "group_leader": True,
        }, term


@pytest.mark.skipif(
    os.name == "nt", reason="SIG_IGN 是 POSIX 语义；Windows 上 taskkill /F 没有「不理」这回事"
)
def test_a_worker_that_ignores_sigterm_is_still_killed_and_the_group_is_verified_empty(tmp_path):
    """终止判据是**进程不存在**：SIGTERM 之后组里还剩人就升级 SIGKILL，最后那一眼才算数。"""
    pid_file = tmp_path / "pids.json"
    out, res = _run(
        tmp_path / "run",
        "--launch",
        _stub_launch("--pid-file", pid_file.as_posix(), "--worker-ignores-term"),
    )
    pids = json.loads(pid_file.read_text(encoding="utf-8"))
    try:
        assert out.returncode == 0, out.stderr
        term = res["history"][0]["terminate"]
        assert term["how"] == "killpg-term+kill-group" and term["group_gone"] is True, term
        assert _wait_gone(pids["pid"]) and _wait_gone(pids["worker"])
    finally:
        # 无论判据怎么说，别给下一个用例留一个不理 SIGTERM 的孤儿
        for pid in pids.values():
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except OSError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="进程组是 POSIX 的东西")
def test_wait_group_gone_answers_by_existence_not_by_having_signalled():
    """直接量 `_wait_group_gone`：活着的组 → False（等满超时）；SIGKILL 之后 → True。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert PS._is_group_leader(proc.pid)
        assert PS._wait_group_gone(proc.pid, 0.5) is False
    finally:
        os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        proc.wait()
    assert PS._wait_group_gone(proc.pid, 3.0) is True
    # 不用 start_new_session 起的子进程不是组长：终止路径必须先问这一句，否则 `killpg(pid, 0)`
    # 的 ESRCH（没有这个组）会被读成「组里没人了」——恒真
    plain = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert PS._is_group_leader(plain.pid) is False
    finally:
        plain.kill()
        plain.wait()


@pytest.mark.skipif(os.name == "nt", reason="进程组是 POSIX 的东西")
def test_wait_group_gone_polls_through_the_transient_eperm(monkeypatch):
    """macOS 上直接子进程刚被 reap、孙进程正在退出的那一瞬 `killpg(pgid, 0)` 报 EPERM，半秒后才
    ESRCH（本机对真产品 + 一层包装进程复现，CI03B §3.2）。EPERM 是「还有东西」，不是「判不了」。"""
    answers = iter([PermissionError(), PermissionError(), ProcessLookupError()])

    def fake_killpg(pgid, sig):
        assert sig == 0
        exc = next(answers)
        raise exc

    monkeypatch.setattr(os, "killpg", fake_killpg)
    assert PS._wait_group_gone(4242, 5.0) is True


@pytest.mark.skipif(os.name == "nt", reason="进程组是 POSIX 的东西")
def test_a_wrapper_process_shape_still_ends_with_an_empty_group(tmp_path):
    """被起的进程自己再 `subprocess.run` 一层（真产品的复现驱动就是这个形状）：组里两个进程，
    终止后组必须空，`group_gone` 必须是 True 而不是被那一瞬的 EPERM 判成 False。"""
    code = (
        "import subprocess, sys; sys.exit(subprocess.run([sys.executable, "
        f"{STUB.as_posix()!r}, '--port', '{{port}}']).returncode)"
    )
    launch = " ".join(shlex.quote(t) for t in (Path(sys.executable).as_posix(), "-c", code))
    out, res = _run(tmp_path / "run", "--launch", launch)
    assert out.returncode == 0, out.stderr
    term = res["history"][0]["terminate"]
    assert term["how"] == "killpg-term" and term["group_gone"] is True, term


def test_a_group_that_is_still_populated_after_termination_fails_the_attempt(tmp_path, monkeypatch):
    """判据要进控制流：`group_gone is False` 必须让这次尝试失败，不只是记进 JSON。"""
    real = PS.terminate

    def still_there(proc):
        info = real(proc)  # 真的终止掉（别给下一个用例留桩），只把判据改成「还有人」
        info["group_gone"] = False
        return info

    monkeypatch.setattr(PS, "terminate", still_there)
    port = PS.SA._free_port()
    rec = PS.run_attempt(1, PS.parse_launch(_stub_launch(), port), tmp_path / "a1", port, 60)
    assert rec["state"] == "failed" and rec["ok"] is False
    assert "进程组里仍有进程" in rec["reason"], rec


def test_leftover_workers_after_termination_fail_the_attempt(tmp_path, monkeypatch):
    """同上：`smoke_app._leftover_workers` 报了残留就是失败。"""
    monkeypatch.setattr(
        PS.SA, "_leftover_workers", lambda data_dir: ["python worker.py --figures-dir x"]
    )
    port = PS.SA._free_port()
    rec = PS.run_attempt(1, PS.parse_launch(_stub_launch(), port), tmp_path / "a1", port, 60)
    assert rec["state"] == "failed" and rec["ok"] is False
    assert "worker 残留" in rec["reason"], rec


@pytest.mark.skipif(
    os.name == "nt", reason="SIGTERM 可捕获是 POSIX 语义；Windows 上 TerminateProcess 没有 finally"
)
def test_sigterm_while_waiting_still_terminates_the_server_and_its_worker(tmp_path):
    """runner 取消 / step 超时那一刻：脚本收到 SIGTERM 要走完 finally 里的终止，退 143，不留孤儿（CIP-019）。"""
    pid_file = tmp_path / "pids.json"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--workdir",
            str(tmp_path / "run"),
            "--timeout",
            "60",
            "--launch",
            _stub_launch("--fail-mode", "never-ready", "--pid-file", pid_file.as_posix()),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.monotonic() + 30
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert pid_file.exists(), "桩没起来"
        pids = json.loads(pid_file.read_text(encoding="utf-8"))
        time.sleep(0.5)  # 让脚本进到轮询循环里（桩永远 503，它会一直等）
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 128 + signal.SIGTERM, err
    assert _wait_gone(pids["pid"]), "收到 SIGTERM 之后桩还活着——finally 没跑"
    assert _wait_gone(pids["worker"]), "桩的 worker 成了孤儿"
    res = json.loads(out.strip().splitlines()[-1])
    assert res["ok"] is False
    assert (tmp_path / "run" / "result.json").is_file()


# ---------------------------------------------------------------- 用法错误


def _usage(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_missing_workdir_is_a_usage_error(tmp_path):
    out = _usage("--python", sys.executable)
    assert out.returncode == 2 and "--workdir" in out.stderr


def test_python_and_launch_are_exactly_one(tmp_path):
    both = _usage("--workdir", str(tmp_path), "--python", sys.executable, "--launch", "x {port}")
    neither = _usage("--workdir", str(tmp_path))
    assert both.returncode == 2 and "二选一" in both.stderr
    assert neither.returncode == 2 and "二选一" in neither.stderr


def test_a_launch_template_without_the_port_placeholder_is_a_usage_error(tmp_path):
    out = _usage("--workdir", str(tmp_path), "--launch", "python stub.py --port 5099")
    assert out.returncode == 2 and "{port}" in out.stderr
    assert not (tmp_path / "attempt-1").exists(), "用法错误不该起任何进程"
