"""`engine/workerd_client.py`——多路复用、崩溃处置、重启上限、二进制发现。

对面用一个**假 supervisor**（临时目录里的小 Python 脚本），不需要 cargo 产物：
这里验的全是客户端自己那一半。真 workerd 的行为由 `workerd/` 的 cargo test 看护，
两条链路合起来才是完整的验收。
"""
import json
import os
import subprocess
import threading
import time

import pytest

from tavotto.engine import workerd_client

FAKE_SUPERVISOR = '''\
import json, sys, threading, time

# argv: [die_after, version]  die_after>0 表示处理这么多条之后直接退出（模拟崩溃）
DIE_AFTER = int(sys.argv[1]) if len(sys.argv) > 1 else 0
VERSION = int(sys.argv[2]) if len(sys.argv) > 2 else 1

_lock = threading.Lock()


def emit(obj):
    with _lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
        sys.stdout.flush()


seen = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    seen += 1
    if DIE_AFTER and seen > DIE_AFTER:
        break
    op = req.get("op")
    base = {"supervisor_protocol_version": VERSION,
            "request_id": req["request_id"], "ok": True}
    payload = req.get("payload") or {}
    if op == "slow":
        delay = float(payload.get("delay_ms", 0)) / 1000.0

        def later(resp=dict(base), d=delay):
            time.sleep(d)
            emit({**resp, "delay_ms": d * 1000})

        threading.Thread(target=later, daemon=True).start()
    elif op == "fail":
        env = {"supervisor_protocol_version": VERSION,
               "request_id": req["request_id"], "ok": False,
               "error": {"code": payload.get("code", "internal"),
                         "retryable": bool(payload.get("retryable")),
                         "message": payload.get("message", "boom"),
                         "traceback": payload.get("traceback", ""),
                         "known": ["Fig1"]}}
        # workerd 的 `.with_session()` 在成功与失败两条路上都会附**顶层**
        # session_id；open 失败时它是调用方唯一的线索
        if payload.get("session_id"):
            env["session_id"] = payload["session_id"]
        emit(env)
    elif op == "never":
        pass                      # 收下但永不回应
    else:
        emit({**base, "echo": op, "session_id": req.get("session_id"),
              "stem": req.get("stem"), "payload": payload})
'''


def _fake_exe(tmp_path, die_after=0, version=1):
    """把假 supervisor 包成一个可执行文件（客户端只会 `Popen([exe])`）。"""
    import sys
    script = tmp_path / "fake_supervisor.py"
    script.write_text(FAKE_SUPERVISOR, encoding="utf-8")
    if os.name == "nt":
        exe = tmp_path / "fake.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" {die_after} {version}\r\n',
                       encoding="utf-8")
    else:
        exe = tmp_path / "fake.sh"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" '
                       f'{die_after} {version}\n', encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


@pytest.fixture
def client(tmp_path):
    c = workerd_client.WorkerdClient(_fake_exe(tmp_path))
    yield c
    c.close()


# ------------------------------ 二进制发现 ------------------------------
def test_the_env_switch_can_disable_workerd_entirely(monkeypatch):
    """`TAVOTTO_WORKERD=0` 一律走 Python 池——排障时要能一键切回参考实现。"""
    for value in ("0", "off", "FALSE", "No"):
        monkeypatch.setenv("TAVOTTO_WORKERD", value)
        assert workerd_client.find_workerd() is None


def test_an_explicit_path_is_honoured(monkeypatch, tmp_path):
    exe = tmp_path / "tavotto-workerd"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("TAVOTTO_WORKERD", str(exe))
    assert workerd_client.find_workerd() == str(exe)


def test_a_bad_explicit_path_falls_back_instead_of_crashing(monkeypatch, caplog):
    """用户明确指了路径却指错——回退，但**必须留一条警告**。

    静默回退是最难排查的一种失灵：他以为在测 workerd，其实一直跑的 Python 池。
    """
    monkeypatch.setenv("TAVOTTO_WORKERD", "/nope/tavotto-workerd")
    with caplog.at_level("WARNING", logger="tavotto.engine"):
        assert workerd_client.find_workerd() is None
    assert any("不存在" in r.getMessage() for r in caplog.records)


# ------------------------------ 多路复用 ------------------------------
def test_hello_negotiates_the_protocol_version(client):
    client.ensure_started(max_sessions=2, max_queue=8)
    assert client.hello["supervisor_protocol_version"] == 1


def test_a_foreign_supervisor_version_is_refused(tmp_path):
    c = workerd_client.WorkerdClient(_fake_exe(tmp_path, version=9))
    try:
        with pytest.raises(workerd_client.WorkerdError) as e:
            c.ensure_started()
        assert e.value.code == "protocol_mismatch"
    finally:
        c.close()


def test_responses_are_routed_by_request_id_not_by_arrival_order(client):
    """**多路复用的全部要害**：先发的慢、后发的快，各回各的。

    按到达顺序配对的话，一次并发渲染就会让 A 图的 manifest 落到 B 图上——
    和 worker 协议 v1 之前那个「少回一条就全体错位」是同一种病。
    """
    client.ensure_started()
    results: dict[str, dict] = {}

    def call(name, delay):
        results[name] = client.call("slow", payload={"delay_ms": delay},
                                    timeout=10)

    threads = [threading.Thread(target=call, args=(name, delay))
               for name, delay in (("slow", 600), ("fast", 10))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert results["slow"]["delay_ms"] == pytest.approx(600, abs=1)
    assert results["fast"]["delay_ms"] == pytest.approx(10, abs=1)


def test_many_threads_share_one_pipe_without_crossing_wires(client):
    client.ensure_started()
    seen: list[tuple[int, str]] = []
    lock = threading.Lock()

    def call(i):
        resp = client.call("echo", stem=f"Fig{i}", payload={"n": i}, timeout=10)
        with lock:
            seen.append((i, resp["stem"]))

    threads = [threading.Thread(target=call, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert len(seen) == 24
    assert all(f"Fig{i}" == stem for i, stem in seen)


def test_error_envelopes_become_structured_exceptions(client):
    client.ensure_started()
    with pytest.raises(workerd_client.WorkerdError) as e:
        client.call("fail", payload={"code": "unknown_stem", "message": "stem 不存在: nope",
                                     "traceback": "tb"}, timeout=10)
    assert e.value.code == "unknown_stem"
    assert e.value.traceback_text == "tb"
    assert e.value.extra["known"] == ["Fig1"]     # 多带的字段原样转交


def test_the_session_id_on_a_failure_envelope_is_not_dropped(client):
    """失败响应的**顶层** session_id 必须转交给调用方。

    这里以前只拆 `resp["error"]`，把顶层那个丢了。后果落在 open 上：
    握手/spawn 失败时 workerd 已经把会话记进了 sessions / by_hash，而调用方
    永远学不到它的 id，也就永远关不掉——refs 停在 1，只能等超出 max_sessions
    时被淘汰，而被挤掉的往往是**真正在用**的那条会话。
    """
    client.ensure_started()
    with pytest.raises(workerd_client.WorkerdError) as e:
        client.call("fail", payload={"code": "handshake_timeout",
                                     "session_id": "s-42"}, timeout=10)
    assert e.value.session_id == "s-42"


# ------------------------------ 崩溃与重启 ------------------------------
def test_a_crash_fails_every_pending_call_immediately(tmp_path):
    """**绝不许把调用线程挂死**。

    workerd 没了还让线程等在 Event 上，就是把 Python 池那个「会话死锁」原样
    搬到新控制面上：整个渲染从此无响应，而且一条日志都没有。
    """
    c = workerd_client.WorkerdClient(_fake_exe(tmp_path, die_after=3))
    try:
        c.ensure_started()                      # 第 1 条 = hello
        c.call("echo", timeout=5)               # 第 2 条
        box: list = []

        def call():
            try:
                # 第 3 条被收下但永不回应；第 4 条会让假 supervisor 退出
                c.call("never", timeout=30)
            except workerd_client.WorkerdError as exc:
                box.append(exc)

        t = threading.Thread(target=call, daemon=True)
        t.start()
        time.sleep(0.3)
        try:
            c.call("echo", timeout=5)           # 第 4 条 → 对面 break 退出
        except workerd_client.WorkerdError:
            pass
        t.join(timeout=10)
        assert box, "对面死了，等着的调用必须当场失败而不是干等 30 秒"
        assert box[0].code == "workerd_dead"
        assert box[0].retryable
    finally:
        c.close()


def test_it_restarts_on_the_next_call_after_a_crash(tmp_path):
    c = workerd_client.WorkerdClient(_fake_exe(tmp_path, die_after=1))
    try:
        c.ensure_started()                      # 第 1 条 = hello
        first_pid = c._proc.pid
        with pytest.raises(workerd_client.WorkerdError):
            c.call("echo", timeout=5)           # 第 2 条 → 对面直接退出
        # 下一次调用把它重新拉起来（会话丢了，但那是上层重开会话的事）
        time.sleep(0.2)
        c.ensure_started()
        assert c._proc.pid != first_pid
        assert not c.disabled
    finally:
        c.close()


def test_a_binary_that_dies_on_startup_is_disabled_after_a_few_tries(tmp_path):
    """起来就崩的产物不许变成无限重启循环——每次渲染白等一轮比直接回退更糟。"""
    c = workerd_client.WorkerdClient(_fake_exe(tmp_path, die_after=0, version=1))
    # 换成一个立刻退出的可执行文件
    dead = tmp_path / "dead.sh"
    if os.name == "nt":
        dead = tmp_path / "dead.cmd"
        dead.write_text("@exit /b 0\r\n", encoding="utf-8")
    else:
        dead.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        dead.chmod(0o755)
    c.exe = str(dead)
    for _ in range(6):
        try:
            c.ensure_started()
        except workerd_client.WorkerdError:
            pass
        if c.disabled:
            break
    assert c.disabled, "反复崩溃必须停用 workerd（回退 Python 池），不能一直重启"
    c.close()


def test_a_wedged_supervisor_times_out_with_its_own_code(client, monkeypatch):
    """workerd 整个卡住 ≠ 渲染超时，code 必须分开——处置完全不同。"""
    monkeypatch.setattr(workerd_client, "_SLACK_SECONDS", 0.5)
    client.ensure_started()
    with pytest.raises(workerd_client.WorkerdError) as e:
        client.call("never", timeout=0.2)
    assert e.value.code == "workerd_unavailable"


def test_the_request_envelope_matches_the_supervisor_protocol(client):
    client.ensure_started()
    resp = client.call("echo", session_id="s-7", stem="Fig1",
                       payload={"patches": []}, timeout=3)
    assert resp["session_id"] == "s-7"
    assert resp["stem"] == "Fig1"
    assert resp["payload"] == {"patches": []}
    assert json.loads(json.dumps(resp))         # 纯 JSON，可原样进日志


# ----------------- kill 之后不收尸：句柄还占着就去动它占的东西 -----------------
# #46 的形状在 supervisor 生命周期路径上的两处复发（#132）。`kill()` 只是把终止
# 请求交给内核，`poll()` 也只是问一次——**只有 `wait()` 回来了，子进程手里的日志
# 文件句柄才真的还给了系统**。旧写法两处都是 kill 完立刻动它占着的东西：
#   * 半启动回收：kill 之后马上拿同一个日志文件重新 open 一个写句柄；
#   * shutdown 的 finally：kill 之后紧接着 `self._log.close()`。
# 下面两条把那个窗口在任何平台上确定性地复现出来（真 Windows 上它才会炸，
# 但判据不该只在某台机器上成立）。

class _LingeringSupervisor:
    """Windows 关进程的真实时序：kill 只发信号，wait 才是「它没了」。

    * `poll()` 在被 reap 之前**永远**回 None（进程对象看着还活着）；
    * 优雅关停的那次 `wait()` **超时**——这正是 shutdown 路径走到 kill 的前提；
    * `kill()` 只置一个「信号发出去了」的标记；
    * kill 之后的 `wait()` 才把它标成真的退出，句柄也是这一刻才还回来。
    """

    class _Pipe:
        def __init__(self):
            self.closed = False

        def write(self, _line):
            if self.closed:
                raise ValueError("I/O operation on closed file")

        def flush(self):
            pass

        def close(self):
            self.closed = True

    def __init__(self):
        self.pid = 9200
        self.stdin = self._Pipe()
        self.stdout = self._Pipe()
        self.kill_called = False
        self.reaped = False

    def poll(self):
        return 0 if self.reaped else None

    def kill(self):
        self.kill_called = True          # ← 只发信号，进程还在，句柄还占着

    def wait(self, timeout=None):
        if not self.kill_called:
            raise subprocess.TimeoutExpired("tavotto-workerd", timeout or 0)
        self.reaped = True               # ← 到这一刻它才真的没了
        return 0


class _LogHandle:
    """记下「关它的时候，子进程收尸了没有」——这条用例真正的判据。"""

    def __init__(self, proc: _LingeringSupervisor):
        self._proc = proc
        self.closed_while_alive: bool | None = None

    def close(self):
        self.closed_while_alive = not self._proc.reaped


def test_close_reaps_the_supervisor_before_closing_the_log(tmp_path):
    """`close()` 关日志句柄时，子进程必须已经被 wait 回收。

    workerd 的 stderr 直接绑在这个日志文件上。优雅关停超时 → kill → 立刻
    `self._log.close()`，就是与一个「已 kill 但尚未退出」的子进程争同一个
    文件——#46 在 Windows 上炸出来的那条链。
    """
    c = workerd_client.WorkerdClient(_fake_exe(tmp_path))
    fake = _LingeringSupervisor()
    log = _LogHandle(fake)
    c._proc, c._log, c._ready = fake, log, True

    c.close()

    assert fake.kill_called, "优雅关停超时后本该 kill"
    assert fake.reaped, "kill 之后没有再 wait 一次：进程还没消失就返回了"
    assert log.closed_while_alive is False, \
        "日志句柄在子进程收尸之前就关了——正是 #46 的形状"


def test_a_half_started_supervisor_is_reaped_before_the_restart(tmp_path):
    """重启前回收「半启动」的那条：kill 完要等到它真的退出。

    紧接着的几行会拿**同一个日志文件**重新 open 一个写句柄；kill 之后子进程
    还占着它，不收尸就是每重启一次泄漏一个句柄。这里把重启计数顶到上限，
    让函数在真的 spawn 之前退出——要看的判据在 kill 那几行，不在 spawn。
    """
    c = workerd_client.WorkerdClient(_fake_exe(tmp_path))
    fake = _LingeringSupervisor()
    c._proc, c._ready = fake, False
    c._started_at = time.time()                       # 起来就崩：计数只加不清
    c._restarts = workerd_client._MAX_RESTARTS

    with pytest.raises(workerd_client.WorkerdError):
        c.ensure_started()

    assert fake.kill_called, "半启动的那条根本没被收掉"
    assert fake.reaped, "kill 之后没有再 wait 一次：进程还没消失就往下走了"
