"""桌面拥有的进程的「纳管范围」与租约确认（P06，ADR 0125 §五）。

* Windows Job Object：CI 上用**替身 kernel32**把调用序列与参数钉死（建 Job → KILL_ON_JOB_CLOSE →
  入 Job → TerminateJobObject → ActiveProcesses 判空 → CloseHandle）。**真机行为 not_run**
  （P07 / P10 的 Windows 验收），这里的绿不代表 Windows 上已经跑通过。
* 租约确认：进程组「确认不了空」时，租约必须继续持有，不能因为直接子进程走了就放。
"""

from __future__ import annotations

import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from support import desktopkit as dk
from tavotto.engine import desktopproc, envlease, nativesession, runcodes, runtime


class FakeKernel32:
    """记调用序列的 kernel32 替身；`active` 是 Job 里的存活进程数。"""

    def __init__(self, *, assign_ok=True, set_ok=True, create_ok=True):
        self.calls: list[tuple] = []
        self.active = 1
        self.assign_ok, self.set_ok, self.create_ok = assign_ok, set_ok, create_ok
        self.limit_flags = None

    def CreateJobObjectW(self, attrs, name):  # noqa: N802
        self.calls.append(("create",))
        return 0xBEEF if self.create_ok else 0

    def SetInformationJobObject(self, job, klass, ref, size):  # noqa: N802
        self.calls.append(("set", job, klass, size))
        self.limit_flags = ref._obj.BasicLimitInformation.LimitFlags
        return 1 if self.set_ok else 0

    def AssignProcessToJobObject(self, job, handle):  # noqa: N802
        self.calls.append(("assign", job, handle))
        return 1 if self.assign_ok else 0

    def TerminateJobObject(self, job, code):  # noqa: N802
        self.calls.append(("terminate", job, code))
        self.active = 0
        return 1

    def QueryInformationJobObject(self, job, klass, ref, size, ret):  # noqa: N802
        self.calls.append(("query", job, klass))
        ref._obj.ActiveProcesses = self.active
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.calls.append(("close", handle))
        return 1


def test_job_is_created_with_kill_on_close_then_the_process_is_assigned():
    k = FakeKernel32()
    job = desktopproc._WindowsJob.create(k, 1234)
    assert job is not None and job.kind == desktopproc.SCOPE_JOB
    assert [c[0] for c in k.calls] == ["create", "set", "assign"]
    assert k.calls[1][2] == 9  # JobObjectExtendedLimitInformation
    assert k.limit_flags & 0x2000, "没设 KILL_ON_JOB_CLOSE：sidecar 崩了成员会变孤儿"
    assert k.calls[2] == ("assign", 0xBEEF, 1234)


@pytest.mark.parametrize("fail", ["create_ok", "set_ok", "assign_ok"])
def test_a_job_that_cannot_be_set_up_returns_none_and_leaks_no_handle(fail):
    k = FakeKernel32(**{fail: False})
    assert desktopproc._WindowsJob.create(k, 1) is None
    created = any(c[0] == "create" for c in k.calls) and k.create_ok
    if created:
        assert ("close", 0xBEEF) in k.calls, "半成品 Job 的句柄泄漏了"


def test_scope_falls_back_to_the_direct_process_only_when_the_job_fails():
    proc = SimpleNamespace(_handle=77, pid=1)
    assert desktopproc._scope_for(proc, nt=True, kernel32=FakeKernel32()).kind == "job_object"
    scope = desktopproc._scope_for(proc, nt=True, kernel32=FakeKernel32(assign_ok=False))
    assert scope.kind == desktopproc.SCOPE_PROCESS  # 如实报「只管直接子进程」
    assert desktopproc._scope_for(SimpleNamespace(pid=9), nt=False).kind == "process_group"


def test_job_terminate_empty_and_close():
    k = FakeKernel32()
    job = desktopproc._WindowsJob.create(k, 1)
    assert job.empty() is False  # ActiveProcesses == 1
    job.terminate()
    assert ("terminate", 0xBEEF, runcodes.EXIT_CANCELLED) in k.calls
    assert job.empty() is True
    job.close()
    job.close()  # 幂等
    assert [c for c in k.calls if c[0] == "close"] == [("close", 0xBEEF)]
    n = len(k.calls)
    job.kill()
    assert len(k.calls) == n, "关闭之后不许再碰已经关了的句柄"


def test_a_failed_query_never_claims_the_job_is_empty():
    k = FakeKernel32()
    job = desktopproc._WindowsJob.create(k, 1)
    k.QueryInformationJobObject = lambda *a: 0
    assert job.empty() is False


def test_the_spawn_declares_a_gui_owned_hidden_process():
    """桌面拥有的子进程是 GUI 拥有的隐藏子进程：声明的是 CREATE_NO_WINDOW（Windows 回归闭集的那一类）。"""
    import ast
    import inspect

    src = inspect.getsource(desktopproc.start)
    calls = [
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("Popen")
    ]
    assert len(calls) == 1
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords if k.arg}
    assert kw["creationflags"].endswith("CREATE_NO_WINDOW")
    assert "start_new_session" in kw, "POSIX 上不自成新会话就没有自己的进程组可管"
    assert "shell" not in kw


# ---------------------------------------------------------------------------
# 租约确认：组确认不了空，租约就继续持有
# ---------------------------------------------------------------------------
class StuckScope:
    """包着真实纳管范围，但在 `released` 之前永远报「组里还有成员」。"""

    kind = "process_group"

    def __init__(self, inner):
        self.inner = inner
        self.released = threading.Event()

    def terminate(self):
        self.inner.terminate()

    def kill(self):
        self.inner.kill()

    def empty(self):
        return self.released.is_set() and self.inner.empty()

    def close(self):
        self.inner.close()


def test_lease_is_held_until_the_group_is_confirmed_empty(tmp_path):
    popen = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=runtime.CREATE_NO_WINDOW,
        start_new_session=sys.platform != "win32",
    )
    scope = StuckScope(desktopproc._scope_for(popen))
    session = nativesession.NativeSession(
        session_id="native-owned-stuck",
        descriptor={
            "native_id": "stuck",
            "out_dir": str(tmp_path),
            "metadata": {"interpreter": sys.executable},
        },
    )
    envlease.acquire_native(sys.executable, session.session_id)
    policy = desktopproc.OwnedPolicy(term_grace=0.3, kill_timeout=0.5, linger_grace=0.0)
    owner = desktopproc.DesktopProcess(popen, scope, policy, on_exit=session._on_owner_exit)
    session.owned = owner
    owner.start()
    try:
        result = owner.cancel()
        assert popen.poll() is not None, "直接子进程应已被 reap"
        assert owner.exited and not owner.finished
        assert result.confirmed is False, "组没确认空却报了已确认"
        assert session.session_id in envlease.native_sessions_on(sys.executable), (
            "直接子进程走了就放租约：组里还有成员时这等于放安装器进来"
        )
        assert (
            dk.install_is_refused(sys.executable) == runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION
        )

        scope.released.set()  # 组终于空了
        assert owner.wait(10)
        dk.wait_until(lambda: not dk.holds_lease(session), timeout=10, what="确认后放租约")
        assert dk.install_is_refused(sys.executable) is None
    finally:
        scope.released.set()
        owner.cancel()
        envlease.reset_for_tests()


def test_a_natural_exit_is_not_marked_cancelled_and_keeps_the_script_exit_code(tmp_path):
    popen = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import sys; sys.exit(4)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=runtime.CREATE_NO_WINDOW,
        start_new_session=sys.platform != "win32",
    )
    seen = {}
    owner = desktopproc.DesktopProcess(
        popen,
        desktopproc._scope_for(popen),
        desktopproc.OwnedPolicy(linger_grace=0.2),
        on_exit=lambda code, cancelled: seen.update(code=code, cancelled=cancelled),
    )
    owner.start()
    assert owner.wait(30)
    assert seen == {"code": 4, "cancelled": False}
    late = owner.cancel()  # 已结束的再取消：幂等，不改写成 cancelled
    assert late.confirmed and late.exit_code == 4 and owner.cancelled is False


def test_cancel_is_idempotent_and_concurrent_callers_get_the_same_answer(tmp_path):
    popen = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=runtime.CREATE_NO_WINDOW,
        start_new_session=sys.platform != "win32",
    )
    owner = desktopproc.DesktopProcess(
        popen,
        desktopproc._scope_for(popen),
        desktopproc.OwnedPolicy(term_grace=2.0, kill_timeout=5.0, linger_grace=0.1),
    )
    owner.start()
    results = []
    threads = [threading.Thread(target=lambda: results.append(owner.cancel())) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert len(results) == 4 and all(r.confirmed for r in results)
    assert len({r.exit_code for r in results}) == 1 and owner.cancelled


def test_the_public_completion_barrier_includes_the_owner_callback(tmp_path):
    """Codex P2：`wait()` / `cancel()` 报完成时，会话必须已落终态、租约已放（回调做完才算）。"""
    gate = threading.Event()
    popen = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(600)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=runtime.CREATE_NO_WINDOW,
        start_new_session=sys.platform != "win32",
    )
    session = nativesession.NativeSession(
        session_id="native-owned-barrier",
        descriptor={
            "native_id": "barrier",
            "out_dir": str(tmp_path),
            "metadata": {"interpreter": sys.executable},
        },
    )
    envlease.acquire_native(sys.executable, session.session_id)

    def slow_exit(code, *, cancelled):
        gate.wait(0.5)  # 回调慢半拍：旧实现此时 finished 已经为真
        session._on_owner_exit(code, cancelled=cancelled)  # noqa: SLF001

    owner = desktopproc.DesktopProcess(
        popen,
        desktopproc._scope_for(popen),
        desktopproc.OwnedPolicy(term_grace=1.0, kill_timeout=5.0, linger_grace=0.1),
        on_exit=slow_exit,
    )
    session.owned = owner
    owner.start()
    try:
        result = owner.cancel()
        assert result.confirmed
        assert session.state == nativesession.ENDED and session.cancelled
        assert not dk.holds_lease(session), "确认取消时租约还没放"
        assert dk.install_is_refused(sys.executable) is None
    finally:
        gate.set()
        envlease.reset_for_tests()


def test_env_overlay_is_merged_case_insensitively_on_windows_and_exactly_elsewhere():
    base = {"PATH": "/sys", "Home": "/h", "KEEP": "1"}
    delta = {"Path": "/user", "HOME": "/x"}
    token = {"tavotto_bridge_token": "t"}
    win = desktopproc.merged_env(base, delta, token, nt=True)
    assert win == {"Path": "/user", "HOME": "/x", "KEEP": "1", "tavotto_bridge_token": "t"}
    assert sorted(k.upper() for k in win) == sorted(set(k.upper() for k in win)), "同名键并存"
    posix = desktopproc.merged_env(base, delta, token, nt=False)
    assert posix["PATH"] == "/sys" and posix["Path"] == "/user"  # POSIX 大小写敏感：各是各的
    assert desktopproc.merged_env({"A": "1"}, nt=True) == {"A": "1"}


@pytest.mark.parametrize("cancelled", [True, False])
def test_a_reset_from_a_killed_peer_is_ended_when_cancelled_and_failed_otherwise(
    tmp_path, cancelled
):
    """Windows 上 TerminateJobObject 杀掉对端后，reader 读到的是 reset（不是干净 EOF，也没有 exit 帧）。
    所有者主动取消的，终态是 ENDED；不是取消的（进程自己崩了）才是 FAILED。"""
    session = nativesession.NativeSession(
        session_id="native-owned-reset",
        descriptor={"native_id": "reset", "out_dir": str(tmp_path), "metadata": {}},
    )
    session.owned = SimpleNamespace(cancelled=cancelled, confirmed=True, finished=False)
    session._set_state(nativesession.BARRIER)  # noqa: SLF001
    session._on_transport_closed(  # noqa: SLF001
        {"code": runcodes.NATIVE_SESSION_DISCONNECTED, "detail": "ConnectionResetError"}
    )
    if cancelled:
        assert session.state == nativesession.ENDED and session.terminal_error is None
    else:
        assert session.state == nativesession.FAILED
        assert session.terminal_error["code"] == runcodes.NATIVE_SESSION_DISCONNECTED
