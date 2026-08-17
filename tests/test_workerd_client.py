"""`engine/workerd_client.py`——多路复用、崩溃处置、重启上限、二进制发现。

对面用一个**假 supervisor**（临时目录里的小 Python 脚本），不需要 cargo 产物：
这里验的全是客户端自己那一半。真 workerd 的行为由 `workerd/` 的 cargo test 看护，
两条链路合起来才是完整的验收。
"""
import json
import os
import threading
import time

import pytest

from magplot.engine import workerd_client

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
        emit({"supervisor_protocol_version": VERSION,
              "request_id": req["request_id"], "ok": False,
              "error": {"code": payload.get("code", "internal"),
                        "retryable": bool(payload.get("retryable")),
                        "message": payload.get("message", "boom"),
                        "traceback": payload.get("traceback", ""),
                        "known": ["Fig1"]}})
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
    """`MAGPLOT_WORKERD=0` 一律走 Python 池——排障时要能一键切回参考实现。"""
    for value in ("0", "off", "FALSE", "No"):
        monkeypatch.setenv("MAGPLOT_WORKERD", value)
        assert workerd_client.find_workerd() is None


def test_an_explicit_path_is_honoured(monkeypatch, tmp_path):
    exe = tmp_path / "magplot-workerd"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("MAGPLOT_WORKERD", str(exe))
    assert workerd_client.find_workerd() == str(exe)


def test_a_bad_explicit_path_falls_back_instead_of_crashing(monkeypatch, caplog):
    """用户明确指了路径却指错——回退，但**必须留一条警告**。

    静默回退是最难排查的一种失灵：他以为在测 workerd，其实一直跑的 Python 池。
    """
    monkeypatch.setenv("MAGPLOT_WORKERD", "/nope/magplot-workerd")
    with caplog.at_level("WARNING", logger="mm.engine"):
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
