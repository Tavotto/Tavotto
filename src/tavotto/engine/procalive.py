"""进程是否还活着（跨平台）。

`pid_alive(pid)` 回 `True` / `False`；量不了（权限之外的系统错误）回 `None`，调用方自己决定保守方向。
POSIX 用 `os.kill(pid, 0)`；Windows 上 `os.kill(pid, 0)` **不是探测**（会真的结束进程），改用
`OpenProcess` + `GetExitCodeProcess == STILL_ACTIVE`。pid 会被复用：判「属主还活着」的调用方应再配一个年龄上限。
"""

from __future__ import annotations

import os

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87


def pid_alive(pid: int) -> bool | None:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在，只是不是我们的
    except OSError:
        return None
    return True


def _pid_alive_windows(pid: int) -> bool | None:  # pragma: no cover — 只在 Windows 上执行
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        if err == _ERROR_ACCESS_DENIED:
            return True  # 存在，只是没权限查询
        if err == _ERROR_INVALID_PARAMETER:
            return False  # 没有这个 pid
        return None
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value == _STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)
