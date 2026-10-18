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
    (d / "fig1_demo.py").write_text("def main():\n    pass\n", encoding="utf-8")
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
    assert (
        handoff._bundle_root("/Applications/Tavotto.app/Contents/MacOS/Tavotto")
        == "/Applications/Tavotto.app"
    )
    assert handoff._bundle_root("/A/Tavotto") is None  # 裸二进制
    assert handoff._bundle_root("/x/Foo/Contents/MacOS/Tavotto") is None  # 不是 .app


# ------------------------------ spawn 那条 --------------------------------
def test_spawn_launch_stays_alive_is_ready():
    fake = _FakeClock()
    proc = _FakeProc([None])
    out = handoff._launch_desktop_via_spawn(
        "/A/Tavotto",
        handoff.Target("/p", "Fig1"),
        spawn=lambda argv, **kw: proc,
        clock=fake.clock,
        sleep=fake.sleep,
    )
    assert out["mode"] == "desktop" and out["ready"] == "process_alive"
    assert out["pid"] == 4242 and out["ready_ms"] >= 0


def test_spawn_launch_immediate_sigabrt_is_launch_failed():
    """崩溃（returncode=-6）必须变成结构化失败，不是 ok:true。"""
    fake = _FakeClock()
    proc = _FakeProc([None, -6])
    with pytest.raises(handoff.HandoffError) as exc:
        handoff._launch_desktop_via_spawn(
            "/A/Tavotto",
            handoff.Target("/p", None),
            spawn=lambda argv, **kw: proc,
            clock=fake.clock,
            sleep=fake.sleep,
        )
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
            "/A/Tavotto",
            handoff.Target("/p", None),
            spawn=lambda argv, **kw: proc,
            clock=fake.clock,
            sleep=fake.sleep,
        )
    assert exc.value.code == "launch_failed"
    assert exc.value.extra["exit_code"] == 3 and exc.value.extra["signal"] is None


def test_spawn_launch_clean_exit_means_forwarded():
    """单实例转发：第二个进程转发完 argv 以 0 退出——这是成功，不是崩溃。"""
    fake = _FakeClock()
    proc = _FakeProc([None, 0])
    out = handoff._launch_desktop_via_spawn(
        "/A/Tavotto",
        handoff.Target("/p", None),
        spawn=lambda argv, **kw: proc,
        clock=fake.clock,
        sleep=fake.sleep,
    )
    assert out["handoff"] == "forwarded" and out["ready"] == "forwarder_exited"


# ----------------- 转发卡住：已有实例时，活着的转发进程不是「起来了」 -----------------
class _StuckProc(_FakeProc):
    """转发进程挂住：poll 永远 None；terminate 可配成无效（逼出 kill）。"""

    def __init__(self, *, terminate_works=True):
        super().__init__([None])
        self.terminated = False
        self.killed = False
        self._terminate_works = terminate_works

    def terminate(self):
        self.terminated = True
        if self._terminate_works:
            self._polls = [1]

    def kill(self):
        self.killed = True
        self._polls = [1]


def _spawn_with(proc, pids, fake):
    return handoff._launch_desktop_via_spawn(
        "C:\\T\\Tavotto.exe",
        handoff.Target("/p", "Fig1"),
        spawn=lambda argv, **kw: proc,
        pids_of=pids if callable(pids) else (lambda app: pids),
        clock=fake.clock,
        sleep=fake.sleep,
    )


def test_spawn_forward_stuck_with_existing_instance_is_launch_failed_and_reaped():
    """#1149：已有实例、转发进程活过窗口 = 转发卡住，不是 process_alive；进程要收掉。"""
    fake = _FakeClock()
    proc = _StuckProc()
    with pytest.raises(handoff.HandoffError) as exc:
        _spawn_with(proc, [777], fake)
    err = exc.value
    assert err.code == "launch_failed" and err.extra["reason"] == "forward_stuck"
    assert err.extra["retryable"] is False and "app" in err.extra and "log_path" in err.extra
    assert proc.terminated and not proc.killed


def test_spawn_forward_stuck_kills_when_terminate_does_not_take():
    fake = _FakeClock()
    proc = _StuckProc(terminate_works=False)
    with pytest.raises(handoff.HandoffError):
        _spawn_with(proc, [777], fake)
    assert proc.terminated and proc.killed


class _SlowKillProc(_StuckProc):
    """kill 之后还要再 poll 几次才真正退出（POSIX 上要 wait 才收尸）。"""

    def __init__(self, *, exits_after=3):
        super().__init__(terminate_works=False)
        self._left = exits_after
        self.polls_after_kill = 0

    def poll(self):
        if self.killed:
            self.polls_after_kill += 1
            return 1 if self.polls_after_kill >= self._left else None
        return None


def test_reap_waits_for_the_killed_process_to_exit():
    fake = _FakeClock()
    proc = _SlowKillProc()
    handoff._reap(proc, clock=fake.clock, sleep=fake.sleep)
    assert proc.killed and proc.poll() is not None


def test_reap_is_bounded_and_silent_when_the_process_never_dies():
    fake = _FakeClock()
    proc = _SlowKillProc(exits_after=10**9)
    handoff._reap(proc, clock=fake.clock, sleep=fake.sleep)  # 不抛、不死等
    assert proc.killed and fake.t < 3 * handoff.LAUNCH_REAP_WINDOW + 1


def _pids_seq(*answers):
    """pids_of 的脚本：每次调用按序给一个答案（Exception 实例则抛），最后一格重复。"""
    seq = list(answers)

    def pids_of(app):
        a = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(a, Exception):
            raise a
        return a

    return pids_of


def test_spawn_old_instance_gone_and_new_process_alive_is_process_alive():
    """旧实例在观察窗口内退出、新进程接手成了主实例：不是卡住，不能杀它。"""
    fake = _FakeClock()
    proc = _StuckProc()
    out = _spawn_with(proc, _pids_seq([777], [4242]), fake)
    assert out["ready"] == "process_alive" and not proc.terminated and not proc.killed


def test_spawn_old_instance_still_there_alongside_new_pid_is_stuck():
    fake = _FakeClock()
    proc = _StuckProc()
    with pytest.raises(handoff.HandoffError) as exc:
        _spawn_with(proc, _pids_seq([777], [777, 4242]), fake)
    assert exc.value.extra["reason"] == "forward_stuck" and proc.terminated


@pytest.mark.parametrize("recheck", [None, OSError("tasklist gone"), TypeError("bad")])
def test_spawn_recheck_failure_is_conservative_never_kills(recheck):
    """复查查不出（None / 抛错）：误杀健康的新实例比报一次假成功更糟，保守放行。"""
    fake = _FakeClock()
    proc = _StuckProc()
    out = _spawn_with(proc, _pids_seq([777], recheck), fake)
    assert out["ready"] == "process_alive" and not proc.terminated and not proc.killed


def test_spawn_without_existing_instance_alive_is_still_process_alive():
    fake = _FakeClock()
    proc = _StuckProc()
    out = _spawn_with(proc, [], fake)
    assert out["ready"] == "process_alive" and out["handoff"] == "launched"
    assert not proc.terminated


def test_spawn_unknown_instance_state_keeps_old_behavior():
    fake = _FakeClock()
    proc = _StuckProc()
    out = _spawn_with(proc, None, fake)
    assert out["ready"] == "process_alive" and not proc.terminated


def test_spawn_existing_instance_fast_zero_exit_is_still_forwarded():
    fake = _FakeClock()
    out = _spawn_with(_FakeProc([None, 0]), [777], fake)
    assert out["handoff"] == "forwarded" and out["ready"] == "forwarder_exited"


SHELL = "C:\\Users\\u\\AppData\\Local\\Tavotto\\Tavotto.exe"
SIDECAR = "C:\\Users\\u\\AppData\\Local\\Tavotto\\sidecar\\Tavotto\\Tavotto.exe"


def test_pids_of_windows_same_image_name_in_sidecar_dir_is_not_an_instance():
    """sidecar 与桌面壳同名 Tavotto.exe：只能按完整路径比，孤儿 sidecar 不算已有实例。"""
    images = [(10, SIDECAR), (11, SIDECAR), (12, "C:\\Windows\\explorer.exe")]
    assert handoff._pids_of_windows(SHELL, enum=lambda: images) == []
    assert handoff._pids_of_windows(SHELL, enum=lambda: [*images, (20, SHELL)]) == [20]


def test_pids_of_windows_ignores_prefix_case_and_separators():
    images = [(5, "\\\\?\\" + SHELL.upper()), (6, SHELL.replace("\\", "/"))]
    assert handoff._pids_of_windows(SHELL, enum=lambda: images) == [5, 6]


def test_pids_of_windows_total_failure_is_none():
    assert handoff._pids_of_windows(SHELL, enum=lambda: None) is None
    assert handoff._windows_process_images() is None or handoff.os.name == "nt"


def test_pids_of_dispatches_to_windows_by_path(monkeypatch):
    """真调 `_pids_of`（不注入）：nt 分支的调用签名要和 `_pids_of_windows` 对得上（真机 TypeError）。"""
    monkeypatch.setattr(handoff.os, "name", "nt")
    monkeypatch.setattr(
        handoff, "_windows_process_images", lambda: [(10, SIDECAR), (20, SHELL.lower())]
    )
    assert handoff._pids_of(SHELL) == [20]


def test_pids_of_probe_failure_never_blocks_the_handoff():
    """求「已有实例」只是辅助判据：pids_of 抛什么都按旧行为走，不拦死交接。"""

    def boom(app):
        raise TypeError("bad signature")

    fake = _FakeClock()
    out = _spawn_with(_FakeProc([None, 0]), boom, fake)
    assert out["ready"] == "forwarder_exited"
    out = _spawn_with(_StuckProc(), boom, _FakeClock())
    assert out["ready"] == "process_alive"


class _FakeDll:
    """任何属性都是可记录调用的桩函数；`ret` 按函数名给返回值，`args` 记每次调用的实参。"""

    def __init__(self, ret):
        self.calls = []
        self.args = []
        self._ret = ret

    def __getattr__(self, name):
        owner = self

        class F:
            argtypes = None
            restype = None

            def __call__(self, *args):
                owner.calls.append(name)
                owner.args.append((name, args))
                return owner._ret.get(name, True)

        return F()


def test_windows_ctypes_paths_run_to_the_end_with_fake_dlls(monkeypatch):
    """ctypes 两段只在 Windows 上真跑；这里用桩 DLL 走完 Python 层，防止名字 / 签名笔误
    被 `except Exception` 悄悄吞成「查不出」（真机就踩过一次 TypeError）。"""
    import ctypes

    kernel, advapi = (
        _FakeDll({"OpenProcess": 0}),
        _FakeDll({"IsTokenRestricted": False}),
    )
    dlls = {"kernel32": kernel, "advapi32": advapi}
    monkeypatch.setattr(handoff.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: dlls[name], raising=False)
    # 枚举：EnumProcesses 成功但 got=0 → 空列表（None 才是「整体失败」）
    assert handoff._windows_process_images() == []
    assert "K32EnumProcesses" in kernel.calls
    # 令牌：AppContainer 走有文档的 GetTokenInformation(29)；受限令牌走 IsTokenRestricted，
    # 不再用保留给系统的信息类 40 调 GetTokenInformation
    assert handoff._windows_token_sandbox() is None
    classes = [a[1] for n, a in advapi.args if n == "GetTokenInformation"]
    assert classes == [29]
    assert advapi.calls.count("IsTokenRestricted") == 1
    assert "CloseHandle" in kernel.calls
    advapi._ret["IsTokenRestricted"] = True
    assert handoff._windows_token_sandbox() == "restricted_token"


# ----------------- 沙箱判据：受限令牌 / AppContainer 里不拉进程 -----------------
_WIN_APP = "C:\\Tools\\Tavotto\\Tavotto.exe"


def _win_launch(probe, spawn):
    return handoff.launch(
        handoff.Target("/p", "Fig1"),
        system="win32",
        environ={handoff.APP_ENV: _WIN_APP},
        isfile=lambda p: p == _WIN_APP,
        spawn=spawn,
        token_probe=probe,
        pids_of=lambda app: [],
    )


@pytest.mark.parametrize("kind", ["restricted_token", "app_container"])
def test_sandboxed_token_refuses_before_any_spawn(kind):
    def no_spawn(argv, **kw):
        pytest.fail("受限沙箱里不许拉进程")

    with pytest.raises(handoff.HandoffError) as exc:
        _win_launch(lambda: kind, no_spawn)
    err = exc.value
    assert err.code == "sandboxed_launch"
    assert err.extra["sandbox"] == kind and err.extra["retryable"] is True
    assert err.extra["app"] == _WIN_APP and "log_path" in err.extra
    assert "沙箱外" in str(err)


def test_sandbox_probe_error_or_unknown_keeps_old_behavior():
    def boom():
        raise OSError("ctypes broke")

    for probe in (boom, lambda: None):
        spawned = []
        proc = _FakeProc([None])
        out = _win_launch(probe, lambda argv, **kw: spawned.append(argv) or proc)
        assert spawned and out["mode"] == "desktop"


def test_sandbox_probe_is_windows_only(monkeypatch):
    app = "/A/Tavotto"
    for system in ("linux", "darwin"):
        called = []
        spawned = []
        out = handoff.launch(
            handoff.Target("/p", None),
            system=system,
            environ={handoff.APP_ENV: app},
            isfile=lambda p: p == app,
            spawn=lambda argv, **kw: spawned.append(argv) or _FakeProc([None]),
            token_probe=lambda: called.append(1) or "restricted_token",
            pids_of=lambda a: [],
        )
        assert not called and spawned and out["mode"] == "desktop"
    assert handoff._sandbox_kind("linux", lambda: "restricted_token") is None
    assert handoff._sandbox_kind("win32", lambda: "bogus") is None
    assert handoff._windows_token_sandbox() is None if handoff.os.name != "nt" else True


def test_sandboxed_launch_json_carries_the_fields(figures, monkeypatch):
    monkeypatch.setattr(handoff, "find_desktop_app", lambda **kw: _WIN_APP)
    monkeypatch.setattr(handoff.sys, "platform", "win32")
    monkeypatch.setattr(handoff, "_windows_token_sandbox", lambda: "restricted_token")
    monkeypatch.setattr(handoff, "_spawn_detached", lambda *a, **kw: pytest.fail("不该拉进程"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = handoff.cli([str(figures / "Fig1_demo.pdf"), "--json"])
    out = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert rc == 2 and out["protocol"] == 1 and out["ok"] is False
    assert out["code"] == "sandboxed_launch" and out["sandbox"] == "restricted_token"
    assert out["retryable"] is True


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
        return [777] if appearances["n"] > 2 else []  # 第三次轮询才出现

    def run(argv, **kw):
        ran.append(argv)
        return _open_ok(argv)

    app = "/Applications/Tavotto.app/Contents/MacOS/Tavotto"
    out = handoff._launch_desktop_via_open(
        app,
        "/Applications/Tavotto.app",
        handoff.Target("/p", "图 1"),
        run=run,
        pids_of=pids_of,
        clock=fake.clock,
        sleep=fake.sleep,
    )
    assert ran[0][:4] == ["open", "-na", "/Applications/Tavotto.app", "--args"]
    assert ran[0][4:] == ["--open", "/p", "--stem", "图 1"]
    assert out["via"] == "launchservices" and out["handoff"] == "launched"
    assert out["pid"] == 777 and out["ready"] == "process_alive"


def test_open_launch_forwarding_to_running_instance():
    """App 已在跑：`-n` 起的第二个实例转发 argv；老实例活着 = 就绪。"""
    fake = _FakeClock()
    out = handoff._launch_desktop_via_open(
        "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
        "/Applications/Tavotto.app",
        handoff.Target("/p", None),
        run=_open_ok,
        pids_of=lambda exe: [55],
        clock=fake.clock,
        sleep=fake.sleep,
    )
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
            "/Applications/Tavotto.app",
            handoff.Target("/p", None),
            run=_open_ok,
            pids_of=pids_of,
            clock=fake.clock,
            sleep=fake.sleep,
        )
    assert exc.value.code == "launch_failed"
    assert "log_path" in exc.value.extra


def test_open_launch_never_appearing_is_a_timeout():
    fake = _FakeClock()
    with pytest.raises(handoff.HandoffError) as exc:
        handoff._launch_desktop_via_open(
            "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
            "/Applications/Tavotto.app",
            handoff.Target("/p", None),
            run=_open_ok,
            pids_of=lambda exe: [],
            clock=fake.clock,
            sleep=fake.sleep,
        )
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
            "/Applications/Tavotto.app",
            handoff.Target("/p", None),
            run=run,
            pids_of=lambda exe: [],
            clock=fake.clock,
            sleep=fake.sleep,
        )
    assert exc.value.code == "launch_failed"
    assert "kLSNoExecutableErr" in str(exc.value)


def test_open_launch_without_process_table_is_honest():
    """ps 查不了（受限环境）：只能信 open 的退出码，如实标 unverified。"""
    fake = _FakeClock()
    out = handoff._launch_desktop_via_open(
        "/Applications/Tavotto.app/Contents/MacOS/Tavotto",
        "/Applications/Tavotto.app",
        handoff.Target("/p", None),
        run=_open_ok,
        pids_of=lambda exe: None,
        clock=fake.clock,
        sleep=fake.sleep,
    )
    assert out["ready"] == "unverified"


# ------------------------------- 分派与 CLI --------------------------------
def test_launch_routes_macos_bundle_to_launchservices(monkeypatch):
    """launch() 的分派：darwin + bundle 形状 → LaunchServices 那条。"""
    called = {}
    monkeypatch.setattr(
        handoff,
        "_launch_desktop_via_open",
        lambda app, bundle, target, **kw: (
            called.setdefault("open", (app, bundle)) or {"mode": "desktop"}
        ),
    )
    monkeypatch.setattr(
        handoff, "_launch_desktop_via_spawn", lambda app, target, **kw: pytest.fail("不该走 spawn")
    )
    app = "/Applications/Tavotto.app/Contents/MacOS/Tavotto"
    handoff.launch(
        handoff.Target("/p", None),
        system="darwin",
        environ={handoff.APP_ENV: app},
        isfile=lambda p: p == app,
    )
    assert called["open"] == (app, "/Applications/Tavotto.app")


def test_launch_routes_windows_to_spawn(monkeypatch):
    monkeypatch.setattr(
        handoff, "_launch_desktop_via_open", lambda *a, **kw: pytest.fail("win32 不该走 open")
    )
    seen = {}
    monkeypatch.setattr(
        handoff,
        "_launch_desktop_via_spawn",
        lambda app, target, **kw: seen.setdefault("app", app) or {"mode": "desktop"},
    )
    app = "C:\\Tools\\Tavotto\\Tavotto.exe"
    handoff.launch(
        handoff.Target("/p", None),
        system="win32",
        environ={handoff.APP_ENV: app},
        isfile=lambda p: p == app,
    )
    assert seen["app"] == app


def test_launch_failure_json_carries_the_details(figures, monkeypatch):
    """CLI 层：launch_failed 的 exit_code/signal/log_path 逐键进那行 JSON。"""
    monkeypatch.setattr(handoff, "find_desktop_app", lambda **kw: "/A/Tavotto")

    def boom(app, target, **kw):
        raise handoff.HandoffError(
            "桌面进程在就绪前退出",
            "launch_failed",
            app=app,
            exit_code=134,
            signal="SIGABRT",
            log_path="/logs/sidecar.log",
            retryable=False,
        )

    monkeypatch.setattr(handoff, "_launch_desktop_via_spawn", boom)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = handoff.cli([str(figures / "Fig1_demo.pdf"), "--json"])
    out = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert rc == 2 and out["code"] == "launch_failed"
    assert out["exit_code"] == 134 and out["signal"] == "SIGABRT"
    assert out["log_path"] == "/logs/sidecar.log" and out["retryable"] is False


def test_sidecar_log_path_per_platform():
    darwin = handoff.sidecar_log_path(system="darwin", environ={"HOME": "/Users/x"})
    assert darwin == "/Users/x/Library/Logs/com.tavotto.tavotto/sidecar.log"
    win = handoff.sidecar_log_path(
        system="win32", environ={"LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local"}
    )
    assert win == ("C:\\Users\\x\\AppData\\Local\\com.tavotto.tavotto\\logs\\sidecar.log")
    assert handoff.sidecar_log_path(system="linux", environ={}) is None
