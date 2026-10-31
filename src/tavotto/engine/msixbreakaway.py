"""MSIX 包身份：检测，以及「脱离包环境」地拉起进程（唯一实现）。

背景（issue #1574，Windows Lab 实测）：Codex 桌面版是 MSIX 包。从它拉起的进程继承包身份，
`%LOCALAPPDATA%` / `%APPDATA%` 下**新建**的文件被重定向到
`Packages\\<pfn>\\LocalCache\\...`，于是「从 Codex 拉起」和「直接启动」各有一套数据（最近项目、
设置、受管环境、缓存）。父树带 `DISABLE_PROCESS_TREE` 继承时，普通 `subprocess` / `cmd /c start` /
`Start-Process` 都脱离不了；**只有 `CreateProcessW` + `STARTUPINFOEX` +
`PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY` = `PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_ENABLE_PROCESS_TREE`
（0x01）能脱离，且整棵子树都在包外**。

本模块是 Python 侧「用 0x01 策略创建进程」的**唯一实现**（Rust 壳的兜底在
`src-tauri/src/msix_breakaway.rs`，是同一份契约的镜像）：

* `spawn_detached(argv, creationflags=…)` —— Windows 上用上述策略创建，返回 Popen 形状的对象；
  非 Windows、API 不可用、任何一步失败都返回 `None`，调用方退回原来的 `subprocess.Popen`
  （行为不变；文件不存在之类的真错误由 Popen 那条路按原样抛）。
* `has_package_identity()` —— `GetCurrentPackageFullName` 是否成功（只回 bool，不带包名）。

纯标准库（Flask 父进程与 CLI 交接都会 import 它）；kernel32 可注入，Windows 专属逻辑在别的平台也能测。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

#: `PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY`（Win10 1703+）
PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY = 0x00020012
#: `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`：只让列出的句柄被子进程继承
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
#: `PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_ENABLE_PROCESS_TREE`：子进程（及其整棵子树）建在包环境之外
PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_ENABLE_PROCESS_TREE = 0x01

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001
STILL_ACTIVE = 259
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF
PROCESS_TERMINATE_EXIT = 1

#: Rust 壳兜底的防循环 / 结果标记（`src-tauri/src/msix_breakaway.rs::GUARD_ENV`，严格同源对，
#: `tests/test_msix_breakaway.py` 对拍）。sidecar 继承它，诊断包如实报出。
GUARD_ENV = "TAVOTTO_MSIX_BREAKAWAY"
#: 标记的闭集取值（壳写入）；其它值一律当「没有」。
GUARD_VALUES = ("relaunched", "still_packaged", "failed")

ERROR_INSUFFICIENT_BUFFER = 122
APPMODEL_ERROR_NO_PACKAGE = 15700


def _is_windows() -> bool:
    return os.name == "nt"


def _kernel32():
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def has_package_identity(*, kernel32: Any = None) -> bool:
    """当前进程是否带 MSIX 包身份：`GetCurrentPackageFullName` 成功 / 报缓冲不足 = 有，
    `APPMODEL_ERROR_NO_PACKAGE`（15700）= 没有。非 Windows、查不出来一律 False。只回 bool，不带包名。"""
    if kernel32 is None and not _is_windows():
        return False
    try:
        import ctypes

        k = _kernel32() if kernel32 is None else kernel32
        fn = k.GetCurrentPackageFullName
        fn.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        fn.restype = ctypes.c_int32
        length = ctypes.c_uint32(0)
        rc = fn(ctypes.byref(length), None)
        return rc in (0, ERROR_INSUFFICIENT_BUFFER)
    except Exception:  # noqa: BLE001 —— ctypes 的 OSError / AttributeError 都算查不出
        return False


def shell_breakaway_outcome(environ: "dict | None" = None) -> str:
    """壳兜底的结果（闭集）：`none`（没触发过）/ `relaunched` / `still_packaged` / `failed`。"""
    env = os.environ if environ is None else environ
    value = env.get(GUARD_ENV)
    return value if value in GUARD_VALUES else "none"


def path_under_packages(path: str | os.PathLike | None) -> bool:
    """realpath 是否含 `\\Packages\\` 段（大小写不敏感，两种分隔符都认）。只回 bool。"""
    if not path:
        return False
    try:
        real = os.path.realpath(os.fspath(path))
    except (OSError, ValueError):
        return False
    segs = real.replace("\\", "/").lower().split("/")
    return "packages" in segs[:-1]


class _Structs:
    """ctypes 结构体，首次用到才建（模块 import 不碰 ctypes）。"""

    _cached: "_Structs | None" = None

    def __init__(self) -> None:
        import ctypes

        c = ctypes
        vp = c.c_void_p

        class STARTUPINFOW(c.Structure):
            _fields_ = [
                ("cb", c.c_uint32),
                ("lpReserved", c.c_wchar_p),
                ("lpDesktop", c.c_wchar_p),
                ("lpTitle", c.c_wchar_p),
                ("dwX", c.c_uint32),
                ("dwY", c.c_uint32),
                ("dwXSize", c.c_uint32),
                ("dwYSize", c.c_uint32),
                ("dwXCountChars", c.c_uint32),
                ("dwYCountChars", c.c_uint32),
                ("dwFillAttribute", c.c_uint32),
                ("dwFlags", c.c_uint32),
                ("wShowWindow", c.c_uint16),
                ("cbReserved2", c.c_uint16),
                ("lpReserved2", vp),
                ("hStdInput", vp),
                ("hStdOutput", vp),
                ("hStdError", vp),
            ]

        class STARTUPINFOEXW(c.Structure):
            _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", vp)]

        class PROCESS_INFORMATION(c.Structure):
            _fields_ = [
                ("hProcess", vp),
                ("hThread", vp),
                ("dwProcessId", c.c_uint32),
                ("dwThreadId", c.c_uint32),
            ]

        self.STARTUPINFOW = STARTUPINFOW
        self.STARTUPINFOEXW = STARTUPINFOEXW
        self.PROCESS_INFORMATION = PROCESS_INFORMATION

    @classmethod
    def get(cls) -> "_Structs":
        if cls._cached is None:
            cls._cached = cls()
        return cls._cached


class BreakawayProcess:
    """`CreateProcessW` 拿到的进程句柄，包成 `subprocess.Popen` 够用的形状
    （`pid` / `returncode` / `poll` / `wait` / `terminate` / `kill`）——调用方的就绪轮询与收尸只认这几个。"""

    def __init__(self, handle: Any, pid: int, kernel32: Any) -> None:
        self._handle = handle
        self._k = kernel32
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None or self._handle is None:
            return self.returncode
        import ctypes

        code = ctypes.c_uint32(0)
        if not self._k.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            return None
        if code.value == STILL_ACTIVE:
            return None
        self.returncode = int(code.value)  # 与 Popen 一样是无符号退出码
        self._close()
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        ms = INFINITE if timeout is None else max(0, int(timeout * 1000))
        rc = self._k.WaitForSingleObject(self._handle, ms)
        if rc == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(["<breakaway>"], timeout)
        got = self.poll()
        return 0 if got is None else got

    def terminate(self) -> None:
        if self.poll() is None:
            self._k.TerminateProcess(self._handle, 1)

    kill = terminate

    def _close(self) -> None:
        h, self._handle = self._handle, None
        if h is not None:
            try:
                self._k.CloseHandle(h)
            except Exception:  # noqa: BLE001
                pass

    def __del__(self) -> None:  # pragma: no cover —— 句柄兜底回收
        self._close()


def spawn_detached(
    argv: list[str],
    *,
    creationflags: int = 0,
    cwd: str | None = None,
    kernel32: Any = None,
    open_nul=None,
) -> "BreakawayProcess | None":
    """Windows：`CreateProcessW` + `STARTUPINFOEX` + `DESKTOP_APP_POLICY=0x01` 拉起 `argv`。

    语义对齐 `subprocess.Popen(argv, stdin/stdout/stderr=DEVNULL, creationflags=…)`：
    环境与 cwd 照旧继承；三个标准句柄指向 NUL，且**只有它**经 `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`
    被继承（不把父进程别的可继承句柄漏给子进程）；`creationflags` 原样并上
    `EXTENDED_STARTUPINFO_PRESENT`。非 Windows、API 不可用或任何一步失败回 `None`——调用方
    退回 `subprocess.Popen`（文件不存在这类真错误由那条路按原样抛）。
    """
    if kernel32 is None and not _is_windows():
        return None
    nul_handle = None
    attr_list = None
    try:
        import ctypes

        k = _kernel32() if kernel32 is None else kernel32
        st = _Structs.get()
        vp = ctypes.c_void_p
        u32 = ctypes.c_uint32

        k.CreateProcessW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            vp,
            vp,
            ctypes.c_int,
            u32,
            vp,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        k.CreateProcessW.restype = ctypes.c_int
        k.InitializeProcThreadAttributeList.argtypes = [
            vp,
            u32,
            u32,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k.InitializeProcThreadAttributeList.restype = ctypes.c_int
        k.UpdateProcThreadAttribute.argtypes = [
            vp,
            u32,
            ctypes.c_size_t,
            vp,
            ctypes.c_size_t,
            vp,
            vp,
        ]
        k.UpdateProcThreadAttribute.restype = ctypes.c_int
        k.DeleteProcThreadAttributeList.argtypes = [vp]
        k.DeleteProcThreadAttributeList.restype = None
        k.CloseHandle.argtypes = [vp]
        k.CloseHandle.restype = ctypes.c_int

        # NUL 句柄：三个标准流共用一个（HANDLE_LIST 里重复的句柄会被拒）
        nul_handle = (_open_nul_inheritable(k) if open_nul is None else open_nul)()
        if not nul_handle:
            return None

        # 两个属性：句柄白名单 + 脱离策略
        size = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        if not size.value:
            return None
        attr_buf = ctypes.create_string_buffer(size.value)
        attr_list = ctypes.cast(attr_buf, vp)
        if not k.InitializeProcThreadAttributeList(attr_list, 2, 0, ctypes.byref(size)):
            attr_list = None
            return None

        handles = (vp * 1)(nul_handle)
        if not k.UpdateProcThreadAttribute(
            attr_list,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handles, vp),
            ctypes.sizeof(handles),
            None,
            None,
        ):
            return None
        policy = u32(PROCESS_CREATION_DESKTOP_APP_BREAKAWAY_ENABLE_PROCESS_TREE)
        if not k.UpdateProcThreadAttribute(
            attr_list,
            0,
            PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY,
            ctypes.byref(policy),
            ctypes.sizeof(policy),
            None,
            None,
        ):
            return None

        si = st.STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(st.STARTUPINFOEXW)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = nul_handle
        si.StartupInfo.hStdOutput = nul_handle
        si.StartupInfo.hStdError = nul_handle
        si.lpAttributeList = attr_list
        pi = st.PROCESS_INFORMATION()

        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in argv]))
        ok = k.CreateProcessW(
            None,
            cmdline,
            None,
            None,
            1,  # bInheritHandles：实际继承的只有 HANDLE_LIST 里的 NUL
            creationflags | EXTENDED_STARTUPINFO_PRESENT,
            None,  # 环境照旧继承
            cwd,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            return None
        if pi.hThread:
            k.CloseHandle(pi.hThread)
        return BreakawayProcess(pi.hProcess, int(pi.dwProcessId), k)
    except Exception:  # noqa: BLE001 —— 任何一步失败都退回 Popen，绝不拦死交接
        return None
    finally:
        if attr_list is not None:
            try:
                k.DeleteProcThreadAttributeList(attr_list)  # type: ignore[possibly-undefined]
            except Exception:  # noqa: BLE001
                pass
        if nul_handle:
            try:
                k.CloseHandle(nul_handle)  # type: ignore[possibly-undefined]
            except Exception:  # noqa: BLE001
                pass


def _open_nul_inheritable(k: Any):
    """返回一个「开 NUL 的可继承句柄」的工厂。"""

    def _open():
        import ctypes

        k.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        k.CreateFileW.restype = ctypes.c_void_p
        k.SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        k.SetHandleInformation.restype = ctypes.c_int
        generic_rw, share_rw, open_existing = 0xC0000000, 0x3, 3
        h = k.CreateFileW("NUL", generic_rw, share_rw, None, open_existing, 0, None)
        if not h or h == ctypes.c_void_p(-1).value:
            return None
        if not k.SetHandleInformation(h, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT):
            k.CloseHandle(h)
            return None
        return h

    return _open
