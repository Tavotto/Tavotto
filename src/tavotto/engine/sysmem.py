"""本机可用物理内存（字节）——给 worker 池按内存定会话上限用（`pool.max_alive()`）。

Flask 父进程的 engine 模块**纯标准库**（`docs/rules/backend/process-boundaries.md`），所以这里不用 psutil：
Windows 走 ctypes `GlobalMemoryStatusEx`，macOS 读 `vm_stat`，Linux 读 `/proc/meminfo` 的 `MemAvailable`。
**任何一步读不到都回 None**（调用方回落到保守下限），绝不抛、绝不猜一个数。
"""

from __future__ import annotations

import re
import subprocess
import sys

from .runtime import CREATE_NO_WINDOW


def parse_meminfo(text: str) -> int | None:
    """`/proc/meminfo` 文本 → `MemAvailable` 字节；没有这一行（很老的内核）回 None。"""
    match = re.search(r"^MemAvailable:\s+(\d+)\s*kB", text, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def parse_vm_stat(text: str) -> int | None:
    """macOS `vm_stat` 输出 → 可回收内存字节：free + inactive + speculative 页。认不出页大小或页数回 None。"""
    size = re.search(r"page size of (\d+) bytes", text)
    if size is None:
        return None
    pages = 0
    seen = False
    for label in ("free", "inactive", "speculative"):
        got = re.search(rf"^Pages {label}:\s+(\d+)\.", text, re.MULTILINE)
        if got is not None:
            pages += int(got.group(1))
            seen = True
    return pages * int(size.group(1)) if seen else None


def _windows() -> int | None:
    import ctypes

    class _Status(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _Status()
    status.dwLength = ctypes.sizeof(_Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return int(status.ullAvailPhys)


def _macos() -> int | None:
    # 绝对路径：桌面版 sidecar 的 PATH 可能很精简
    done = subprocess.run(
        ["/usr/bin/vm_stat"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=2,
        check=False,
        creationflags=CREATE_NO_WINDOW,  # GUI（后端）拥有的子进程；macOS 上为 0
    )
    return parse_vm_stat(done.stdout) if done.returncode == 0 else None


def _linux() -> int | None:
    with open("/proc/meminfo", encoding="ascii", errors="replace") as handle:
        return parse_meminfo(handle.read())


def available_memory_bytes() -> int | None:
    """可用物理内存；读取失败 / 平台不认识 / 结果不是正数 → None。"""
    try:
        if sys.platform == "win32":
            got = _windows()
        elif sys.platform == "darwin":
            got = _macos()
        elif sys.platform.startswith("linux"):
            got = _linux()
        else:
            return None
    except Exception:  # noqa: BLE001 - 读不到内存绝不能影响出图，调用方回落到下限
        return None
    return got if isinstance(got, int) and got > 0 else None
