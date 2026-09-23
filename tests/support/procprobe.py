"""「我们起的那个子进程还在不在」——主语是**那个进程**，不是「这个 pid 此刻有没有进程」。

* POSIX：`os.kill(pid, 0)`，已 reap 的 pid 抛 ProcessLookupError（ESRCH）。
* Windows：`os.kill(pid, 0)` **不是探测**——信号 0 走 TerminateProcess，只要这个 pid 眼下属于任何进程就「成功」
  （顺手把它杀了）；而 Windows 回收 pid 很快（#513 的 Windows 腿两次各红一条，pid 都是 1160 / 4136 这种小数）。
  这里用 `OpenProcess` + `GetExitCodeProcess`（还在运行吗）+ `GetProcessTimes`（创建时间）：起 child 那一刻记下
  创建时间，事后同一个 pid 上若是另一个创建时间，那是复用了 pid 的别人，我们的 child 已经不在了。

纯标准库（Windows 分支用 ctypes）。
"""

from __future__ import annotations

import os
import time


def started(pid: int) -> int | None:
    """Windows：pid 此刻那个**还在运行**的进程的创建时间（FILETIME）；没有 / 已退出回 None。POSIX 恒回 None。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k.CloseHandle(h)


def alive(pid: int, born: int | None = None) -> bool:
    """pid 上是不是还跑着**我们的**那个进程。`born` 是它起来时 `started(pid)` 的值（Windows；不知道就 None，
    那时只能判「pid 上有没有运行中的进程」）。"""
    if os.name == "nt":
        now = started(pid)
        return now is not None and (born is None or now == born)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_gone(pid: int, born: int | None = None, timeout: float = 2.5) -> bool:
    deadline = time.monotonic() + timeout
    while alive(pid, born):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True
