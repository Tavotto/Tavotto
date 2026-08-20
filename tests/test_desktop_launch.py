"""桌面启动就绪判据（2026-08-20 假成功修复）的看护。

以前 `tavotto open` 的桌面路径是 `_spawn_detached` 起完就报成功：SIGABRT 的
桌面进程照样拿到 `ok: true`，用户对着一个没出现的窗口等（实测撞到——从受限
执行上下文直接 exec 包内二进制，AppKit 在 `RegisterApplication` 处 abort）。

这里用假 spawn / run / pids / clock 把两条真实现的状态机钉住：

* `_launch_desktop_via_open`（macOS 主路径，`open -na <bundle> --args …`）
* `_launch_desktop_via_spawn`（Windows / 裸二进制覆盖）

成功必须**等出来**（进程存在且活过稳定窗，或单实例转发完成），失败必须带
`exit_code` / `signal` / `log_path` / `retryable`。全部轮询跑在假时间里，
测试零真实等待——**没有一个 sleep 是真的**。
"""
import contextlib
import io
import json

import pytest

from tavotto.engine import handoff


@pytest.fixture()
def figures(tmp_path):
    d = tmp_path / "figures"
    d.mkdir()
    (d / "fig1_demo.py").write_text(
        "def main():\n    pass\n", encoding="utf-8")
    (d / "Fig1_demo.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return d


class _FakeClock:
    """假时间：sleep 即前进。轮询循环在假时间里跑完，测试零真实等待。"""

    def __init__(self):
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class _FakeProc:
    """poll() 按脚本给：None = 还活着，整数 = 退出码；脚本走完停在最后一格。"""

    def __init__(self, polls):
        self._polls = list(polls)
        self.pid = 4242

    def poll(self):
        if len(self._polls) > 1:
            return self._polls.pop(0)
        return self._polls[0] if self._polls else None


def test_bundle_root_shapes():
    assert handoff._bundle_root(
        "/Applications/Tavotto.app/Contents/MacOS/Tavotto") == \
        "/Applications/Tavotto.app"
    assert handoff._bundle_root("/A/Tavotto") is None           # 裸二进制
    assert handoff._bundle_root(
        "/x/Foo/Contents/MacOS/Tavotto") is None                # 不是 .app


# ------------------------------ spawn 那条 --------------------------------
def test_spawn_launch_stays_alive_is_ready():
    fake = _FakeClock()
    proc = _FakeProc([None])
    out = handoff._launch_desktop_via_spawn(
        "/A/Tavotto", handoff.Target("/p", "Fig1"),
        spawn=lambda argv, **kw: proc, clock=fake.clock, sleep=fake.sleep)
    assert out["mode"] == "desktop" and out["ready"] == "process_alive"
    assert out["pid"] == 4242 and out["ready_ms"] >= 0


def test_spawn_launch_immediate_sigabrt_is_launch_failed():
    """崩溃（returncode=-6）必须变成结构化失败，不是 ok:true。"""
    fake = _FakeClock()
    proc = _FakeProc([None, -6])
    with pytest.raises(handoff.HandoffError) as exc:
        handoff._launch_desktop_via_spawn(
            "/A/Tavotto", handoff.Target("/p", None),
            spawn=lambda argv, **kw: proc, clock=fake.clock, sleep=fake.sleep)
    err = exc.value
    assert err.code == "launch_failed"
    assert err.extra["signal"] == "SIGABRT" and err.extra["exit_code"] == 134
    assert err.extra["retryable"] is False
    assert "log_path" in err.extra and "app" in err.extra


def test_spawn_launch_nonzero_exit_is_launch_failed():
    fake = _FakeClock()
    proc = _FakeProc([3, 3])
    with pytest.raises(handoff.HandoffError) as exc:
        handoff._launch_desktop_via_spawn(
            "/A/Tavotto", handoff.Target("/p", None),
            spawn=lambda argv, **kw: proc, clock=fake.clock, sleep=fake.sleep)
    assert exc.value.code == "launch_failed"
    assert exc.value.extra["exit_code"] == 3 and exc.value.extra["signal"] is None


def test_spawn_launch_clean_exit_means_forwarded():
    """单实例转发：第二个进程转发完 argv 以 0 退出——这是成功，不是崩溃。"""
    fake = _FakeClock()
    proc = _FakeProc([None, 0])
    out = handoff._launch_desktop_via_spawn(
        "/A/Tavotto", handoff.Target("/p", None),
        spawn=lambda argv, **kw: proc, clock=fake.clock, sleep=fake.sleep)
    assert out["handoff"] == "forwarded" and out["ready"] == "forwarder_exited"


# --------------------------- LaunchServices 那条 ---------------------------
def _open_ok(argv, **kw):
    class R:
        returncode = 0
        stdout = stderr = ""
    return R()


def test_open_launch_waits_for_the_process():
    """macOS 主路径：`open -na <bundle> --args …`，等进程出现且活过稳定窗。"""
    fake = _FakeClock()
    ran = []
    appearances = {"n": 0}

    def pids_of(exe):
        appearances["n"] += 1
        return [777] if appearances["n"] > 2 else []   # 第三次轮询才出现

    def run(argv, **kw):
        ran.append(argv)
        return _open_ok(argv)

    app = "/Applications/Tavotto.app/Contents/MacOS/Tavotto"
    out = handoff._launch_desktop_via_open(
        app, "/Applications/Tavotto.app", handoff.Target("/p", "图 1"),
        run=run, pids_of=pids_of, clock=fake.clock, sleep=fake.sleep)
    assert ran[0][:4] == ["open", "-na", "/Applications/Tavotto.app", "--args"]
    assert ran[0][4:] == ["--open", "/p", "--stem", "图 1"]
    assert out["via"] == "launchservices" and out["handoff"] == "launched"
    assert out["pid"] == 777 and out["ready"] == "process_alive"


def test_open_launch_forwarding_to_running_instance():
    """App 已在跑：`-n` 起的第二个实例转发 argv；老实例活着 = 就绪。"""
    fake = _FakeClock()
    out = handoff._launch_desktop_via_open(
        "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
        "/Applications/Tavotto.app", handoff.Target("/p", None),
        run=_open_ok, pids_of=lambda exe: [55],
        clock=fake.clock, sleep=fake.sleep)
    assert out["handoff"] == "forwarded" and out["pid"] == 55


def test_open_launch_crash_after_appearing_is_launch_failed():
    """进程出现又消失（RegisterApplication SIGABRT 的形状）→ launch_failed。"""
    fake = _FakeClock()
    seq = {"n": 0}

    def pids_of(exe):
        seq["n"] += 1
        # 第 1 次是唤起前的「已在跑吗」探测（没在跑）；第 2 次进程出现；
        # 之后消失——正是「启动即崩」的时间线
        return [888] if seq["n"] == 2 else []

    with pytest.raises(handoff.HandoffError) as exc:
        handoff._launch_desktop_via_open(
            "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
            "/Applications/Tavotto.app", handoff.Target("/p", None),
            run=_open_ok, pids_of=pids_of, clock=fake.clock, sleep=fake.sleep)
    assert exc.value.code == "launch_failed"
    assert "log_path" in exc.value.extra


def test_open_launch_never_appearing_is_a_timeout():
    fake = _FakeClock()
    with pytest.raises(handoff.HandoffError) as exc:
        handoff._launch_desktop_via_open(
            "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
            "/Applications/Tavotto.app", handoff.Target("/p", None),
            run=_open_ok, pids_of=lambda exe: [],
            clock=fake.clock, sleep=fake.sleep)
    assert exc.value.code == "launch_timeout"
    assert exc.value.extra["retryable"] is True


def test_open_launch_rejected_by_launchservices():
    """`open` 非零退出（Gatekeeper 拦、bundle 坏）→ launch_failed + stderr。"""
    fake = _FakeClock()

    def run(argv, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "kLSNoExecutableErr"
        return R()

    with pytest.raises(handoff.HandoffError) as exc:
        handoff._launch_desktop_via_open(
            "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
            "/Applications/Tavotto.app", handoff.Target("/p", None),
            run=run, pids_of=lambda exe: [], clock=fake.clock, sleep=fake.sleep)
    assert exc.value.code == "launch_failed"
    assert "kLSNoExecutableErr" in str(exc.value)


def test_open_launch_without_process_table_is_honest():
    """ps 查不了（受限环境）：只能信 open 的退出码，如实标 unverified。"""
    fake = _FakeClock()
    out = handoff._launch_desktop_via_open(
        "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
        "/Applications/Tavotto.app", handoff.Target("/p", None),
        run=_open_ok, pids_of=lambda exe: None,
        clock=fake.clock, sleep=fake.sleep)
    assert out["ready"] == "unverified"


# ------------------------------- 分派与 CLI --------------------------------
def test_launch_routes_macos_bundle_to_launchservices(monkeypatch):
    """launch() 的分派：darwin + bundle 形状 → LaunchServices 那条。"""
    called = {}
    monkeypatch.setattr(handoff, "_launch_desktop_via_open",
                        lambda app, bundle, target, **kw:
                        called.setdefault("open", (app, bundle)) or
                        {"mode": "desktop"})
    monkeypatch.setattr(handoff, "_launch_desktop_via_spawn",
                        lambda app, target, **kw: pytest.fail("不该走 spawn"))
    app = "/Applications/Tavotto.app/Contents/MacOS/Tavotto"
    handoff.launch(handoff.Target("/p", None), system="darwin",
                   environ={handoff.APP_ENV: app}, isfile=lambda p: p == app)
    assert called["open"] == (app, "/Applications/Tavotto.app")


def test_launch_routes_windows_to_spawn(monkeypatch):
    monkeypatch.setattr(handoff, "_launch_desktop_via_open",
                        lambda *a, **kw: pytest.fail("win32 不该走 open"))
    seen = {}
    monkeypatch.setattr(handoff, "_launch_desktop_via_spawn",
                        lambda app, target, **kw:
                        seen.setdefault("app", app) or {"mode": "desktop"})
    app = "C:\\Tools\\Tavotto\\Tavotto.exe"
    handoff.launch(handoff.Target("/p", None), system="win32",
                   environ={handoff.APP_ENV: app}, isfile=lambda p: p == app)
    assert seen["app"] == app


def test_launch_failure_json_carries_the_details(figures, monkeypatch):
    """CLI 层：launch_failed 的 exit_code/signal/log_path 逐键进那行 JSON。"""
    monkeypatch.setattr(handoff, "find_desktop_app", lambda **kw: "/A/Tavotto")

    def boom(app, target, **kw):
        raise handoff.HandoffError(
            "桌面进程在就绪前退出", "launch_failed", app=app,
            exit_code=134, signal="SIGABRT", log_path="/logs/sidecar.log",
            retryable=False)

    monkeypatch.setattr(handoff, "_launch_desktop_via_spawn", boom)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = handoff.cli([str(figures / "Fig1_demo.pdf"), "--json"])
    out = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert rc == 2 and out["code"] == "launch_failed"
    assert out["exit_code"] == 134 and out["signal"] == "SIGABRT"
    assert out["log_path"] == "/logs/sidecar.log" and out["retryable"] is False


def test_sidecar_log_path_per_platform():
    darwin = handoff.sidecar_log_path(system="darwin",
                                      environ={"HOME": "/Users/x"})
    assert darwin == "/Users/x/Library/Logs/com.tavotto.tavotto/sidecar.log"
    win = handoff.sidecar_log_path(
        system="win32", environ={"LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local"})
    assert win == ("C:\\Users\\x\\AppData\\Local\\com.tavotto.tavotto"
                   "\\logs\\sidecar.log")
    assert handoff.sidecar_log_path(system="linux", environ={}) is None
