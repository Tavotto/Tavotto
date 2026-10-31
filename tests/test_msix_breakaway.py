"""MSIX 包身份与「脱离包拉起」（issue #1574）。

Windows 专属逻辑用可注入的 kernel32 替身测**参数**（属性值 0x00020012、策略 0x01、flags、句柄白名单）；
真机行为（脱离后子树写真实 AppData）只能在 Windows 上验，见 PR 的「未验证范围」。
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import types
from pathlib import Path

import pytest

from tavotto.engine import diagnostics, handoff, msixbreakaway as mb

REPO = Path(__file__).resolve().parents[1]
DETACHED_NEWPG = 0x00000008 | 0x00000200


class _Fn:
    def __init__(self, fn):
        self._fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *a):
        return self._fn(*a)


class FakeKernel32:
    """记录每次调用的参数；`fail_at` 让指定的一步失败。"""

    NUL = 0xABCD
    PROC = 0x1111

    def __init__(self, fail_at: str | None = None, exit_code: int = 259):
        self.fail_at = fail_at
        self.exit_code = exit_code
        self.attrs: dict[int, bytes] = {}
        self.create_args: dict | None = None
        self.closed: list[int] = []
        self.deleted_list = False
        self.terminated = False
        self.package_rc = 15700
        # ctypes 的函数对象能挂 argtypes/restype；绑定方法不行，所以逐个包成可设属性的 callable
        for name in (
            "CreateFileW SetHandleInformation InitializeProcThreadAttributeList "
            "UpdateProcThreadAttribute DeleteProcThreadAttributeList CreateProcessW "
            "CloseHandle GetExitCodeProcess WaitForSingleObject TerminateProcess"
        ).split():
            setattr(self, name, _Fn(getattr(self, name)))

    # --- NUL 句柄 ---
    def CreateFileW(self, name, *rest):
        assert name == "NUL"
        return None if self.fail_at == "nul" else self.NUL

    def SetHandleInformation(self, h, mask, flags):
        assert (h, mask, flags) == (self.NUL, 1, 1)
        return 1

    # --- 属性列表 ---
    def InitializeProcThreadAttributeList(self, lst, count, flags, size_ref):
        assert count == 2 and flags == 0
        if lst is None:
            size_ref._obj.value = 64
            return 0  # 询问长度按文档返回 FALSE
        return 0 if self.fail_at == "init" else 1

    def UpdateProcThreadAttribute(self, lst, flags, attr, value, size, prev, ret):
        if self.fail_at == f"attr{attr:#x}":
            return 0
        raw = ctypes.string_at(value, size)
        self.attrs[attr] = raw
        return 1

    def DeleteProcThreadAttributeList(self, lst):
        self.deleted_list = True

    # --- CreateProcessW ---
    def CreateProcessW(self, app, cmdline, pa, ta, inherit, flags, env, cwd, si_ref, pi_ref):
        if self.fail_at == "create":
            return 0
        si = si_ref._obj
        self.create_args = {
            "app": app,
            "cmdline": cmdline.value,
            "inherit": inherit,
            "flags": flags,
            "env": env,
            "cwd": cwd,
            "si_flags": si.StartupInfo.dwFlags,
            "std": (si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError),
            "cb": si.StartupInfo.cb,
            "has_attr_list": bool(si.lpAttributeList),
        }
        pi = pi_ref._obj
        pi.hProcess = self.PROC
        pi.hThread = 0x2222
        pi.dwProcessId = 4321
        return 1

    def CloseHandle(self, h):
        self.closed.append(h)
        return 1

    # --- 进程对象 ---
    def GetExitCodeProcess(self, h, code_ref):
        code_ref._obj.value = self.exit_code
        return 1

    def WaitForSingleObject(self, h, ms):
        return 0 if self.exit_code != 259 else 0x102

    def TerminateProcess(self, h, code):
        self.terminated = True
        self.exit_code = code
        return 1


def _ptr_u32(raw: bytes) -> int:
    return int.from_bytes(raw, "little")


def test_spawn_passes_breakaway_policy_attribute_and_flags():
    k = FakeKernel32()
    proc = mb.spawn_detached(
        ["C:\\Program Files\\Tavotto\\Tavotto.exe", "--open", "D:\\my fig"],
        creationflags=DETACHED_NEWPG,
        kernel32=k,
    )
    assert proc is not None and proc.pid == 4321
    # 属性值 0x00020012 = 0x01（BREAKAWAY_ENABLE_PROCESS_TREE）
    assert mb.PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY == 0x00020012
    assert mb.PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_ENABLE_PROCESS_TREE == 0x01
    assert _ptr_u32(k.attrs[0x00020012]) == 0x01
    a = k.create_args
    # flags：调用方的 DETACHED|NEW_PROCESS_GROUP 原样保留，并上 EXTENDED_STARTUPINFO_PRESENT
    assert a["flags"] == DETACHED_NEWPG | 0x00080000
    assert a["has_attr_list"] and a["app"] is None and a["env"] is None  # 环境照旧继承
    assert a["cmdline"] == subprocess.list2cmdline(
        ["C:\\Program Files\\Tavotto\\Tavotto.exe", "--open", "D:\\my fig"]
    )
    # 句柄继承语义：三个标准流都是 NUL，且继承只限 HANDLE_LIST 里的那一个
    assert a["inherit"] == 1 and a["si_flags"] & 0x100
    assert a["std"] == (k.NUL, k.NUL, k.NUL)
    assert len(k.attrs[0x00020002]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.c_void_p.from_buffer_copy(k.attrs[0x00020002]).value == k.NUL
    # 资源收尾：线程句柄、NUL、属性列表都释放；进程句柄留给返回的对象
    assert 0x2222 in k.closed and k.NUL in k.closed and k.deleted_list
    assert k.PROC not in k.closed


@pytest.mark.parametrize("fail_at", ["nul", "init", "attr0x20002", "attr0x20012", "create"])
def test_any_failed_step_returns_none_and_releases_resources(fail_at):
    k = FakeKernel32(fail_at=fail_at)
    assert mb.spawn_detached(["x.exe"], creationflags=0, kernel32=k) is None
    if fail_at != "nul":
        assert k.NUL in k.closed
    if fail_at in ("attr0x20002", "attr0x20012", "create"):
        assert k.deleted_list


def test_non_windows_without_injected_kernel32_is_not_attempted(monkeypatch):
    monkeypatch.setattr(mb, "_is_windows", lambda: False)
    assert mb.spawn_detached(["x"], creationflags=0) is None
    assert mb.has_package_identity() is False


def test_process_object_has_the_popen_surface_the_launcher_needs():
    k = FakeKernel32()
    proc = mb.spawn_detached(["x.exe"], creationflags=0, kernel32=k)
    assert proc.poll() is None and proc.returncode is None
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(timeout=0.01)
    proc.terminate()
    assert k.terminated
    assert proc.poll() == 1 and proc.returncode == 1 and proc.wait() == 1


def test_exit_code_is_unsigned_like_popen():
    k = FakeKernel32(exit_code=0xC0000005)
    proc = mb.spawn_detached(["x.exe"], creationflags=0, kernel32=k)
    assert proc.poll() == 0xC0000005


@pytest.mark.parametrize("rc,expected", [(122, True), (0, True), (15700, False), (5, False)])
def test_has_package_identity_reads_get_current_package_full_name(rc, expected):
    k = FakeKernel32()
    k.GetCurrentPackageFullName = _Fn(lambda length, buf: rc)
    assert mb.has_package_identity(kernel32=k) is expected


def test_has_package_identity_swallows_api_errors():
    class Boom:
        def __getattr__(self, name):
            raise OSError("no such export")

    assert mb.has_package_identity(kernel32=Boom()) is False


def test_path_under_packages_is_a_bool_about_the_segment(tmp_path):
    real = tmp_path / "AppData" / "Local" / "Packages" / "Vendor.App_abc" / "LocalCache" / "Local"
    real.mkdir(parents=True)
    assert mb.path_under_packages(real) is True
    assert mb.path_under_packages(tmp_path / "AppData" / "Local" / "Tavotto") is False
    assert mb.path_under_packages("C:\\Users\\u\\AppData\\Local\\PACKAGES\\x\\y") in (True, False)
    # 叶子名叫 Packages 不算「在 Packages 下」
    assert mb.path_under_packages(tmp_path / "Packages") is False
    assert mb.path_under_packages(None) is False


def test_shell_outcome_is_a_closed_set():
    assert mb.shell_breakaway_outcome({}) == "none"
    for v in ("relaunched", "still_packaged", "failed"):
        assert mb.shell_breakaway_outcome({mb.GUARD_ENV: v}) == v
    assert mb.shell_breakaway_outcome({mb.GUARD_ENV: "C:\\Users\\bob"}) == "none"


# ---------------------------- handoff 接线 ----------------------------
def _fake_subprocess(popen):
    return types.SimpleNamespace(Popen=popen, DEVNULL=subprocess.DEVNULL)


def test_windows_handoff_spawn_goes_through_breakaway_first(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_breakaway(argv, *, creationflags, **kw):
        seen.update(argv=argv, flags=creationflags)
        return sentinel

    def popen(*a, **k):  # pragma: no cover —— 不该被调用
        raise AssertionError("Popen 不该被调用")

    monkeypatch.setattr(handoff, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(handoff, "subprocess", _fake_subprocess(popen))
    monkeypatch.setattr(handoff.msixbreakaway, "spawn_detached", fake_breakaway)
    assert handoff._spawn_detached(["Tavotto.exe", "--open", "d"], spawn=popen) is sentinel
    assert seen == {"argv": ["Tavotto.exe", "--open", "d"], "flags": DETACHED_NEWPG}


def test_windows_handoff_falls_back_to_popen_when_breakaway_unavailable(monkeypatch):
    calls = []

    def popen(argv, **kw):
        calls.append((argv, kw))
        return "popen-proc"

    monkeypatch.setattr(handoff, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(handoff, "subprocess", _fake_subprocess(popen))
    monkeypatch.setattr(handoff.msixbreakaway, "spawn_detached", lambda *a, **k: None)
    assert handoff._spawn_detached(["Tavotto.exe"], spawn=popen) == "popen-proc"
    assert calls[0][1]["creationflags"] == DETACHED_NEWPG  # 原有语义不变
    assert calls[0][1]["stdin"] == subprocess.DEVNULL


def test_injected_spawn_never_touches_breakaway(monkeypatch):
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("注入的 spawn 不走 breakaway")

    monkeypatch.setattr(handoff, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(handoff.msixbreakaway, "spawn_detached", boom)
    assert handoff._spawn_detached(["x"], spawn=lambda argv, **kw: "stub") == "stub"


def test_posix_handoff_spawn_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(handoff, "os", types.SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        handoff.msixbreakaway,
        "spawn_detached",
        lambda *a, **k: calls.append(1),  # pragma: no cover
    )

    def popen(argv, **kw):
        assert kw["start_new_session"] is True and "creationflags" not in kw
        return "p"

    monkeypatch.setattr(handoff, "subprocess", _fake_subprocess(popen))
    assert handoff._spawn_detached(["x"], spawn=popen) == "p"
    assert calls == []


def test_only_handoff_creates_desktop_processes_with_the_policy():
    """单一权威：0x00020012 / CreateProcessW 只出现在 msixbreakaway.py（Rust 壳的镜像除外）。"""
    hits = []
    for p in (REPO / "src" / "tavotto").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "DESKTOP_APP_POLICY" in text or "CreateProcessW" in text:
            hits.append(p.name)
    assert hits == ["msixbreakaway.py"], hits


# ---------------------------- 与 Rust 壳的同源对 ----------------------------
def test_rust_shell_mirrors_the_python_constants():
    rs = (REPO / "src-tauri" / "src" / "msix_breakaway.rs").read_text(encoding="utf-8")
    assert re.search(rf'GUARD_ENV: &str = "{mb.GUARD_ENV}"', rs)
    for v in mb.GUARD_VALUES:
        assert f'= "{v}"' in rs, v
    assert mb.PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY == 0x0002_0012
    assert "ATTR_DESKTOP_APP_POLICY: usize = 0x0002_0012" in rs
    assert "BREAKAWAY_ENABLE_PROCESS_TREE: u32 = 0x01" in rs


# ---------------------------- 诊断事实 ----------------------------
def test_report_carries_the_closed_set_package_facts(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics.msixbreakaway, "has_package_identity", lambda **k: True)
    monkeypatch.setenv(mb.GUARD_ENV, "still_packaged")
    report = diagnostics.build_report()
    assert report["system"]["has_package_identity"] is True
    assert report["system"]["msix_breakaway"] == "still_packaged"
    assert isinstance(report["paths"]["data_dir_under_packages"], bool)
    assert isinstance(report["paths"]["config_dir_under_packages"], bool)


def test_report_package_facts_never_carry_names(monkeypatch):
    monkeypatch.setenv(mb.GUARD_ENV, "OpenAI.Codex_2p2nqsd0c76g0")
    report = diagnostics.build_report()
    assert report["system"]["msix_breakaway"] == "none"
    assert "OpenAI" not in str(report["system"])
