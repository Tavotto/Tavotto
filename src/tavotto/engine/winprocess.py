"""Win32 extended startup structures shared by process launchers."""

from __future__ import annotations

EXTENDED_STARTUPINFO_PRESENT = 0x00080000


class ProcessStructures:
    """ctypes 结构体，首次用到才建（模块 import 不碰 ctypes）。"""

    _cached: "ProcessStructures | None" = None

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
    def get(cls) -> "ProcessStructures":
        if cls._cached is None:
            cls._cached = cls()
        return cls._cached
