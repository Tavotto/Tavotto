"""结束「一个进程和它起的全部后代」——scripts/ci 里唯一的实现。

为什么不用 `taskkill /T /F`：它是一个外部进程，要自己枚举进程树、再逐个 OpenProcess。
在非管理员账户（Windows Lab、用户机器）上它既慢又不可靠——枚举得到的树不全、单次调用
可以拖到好几十秒，而调用方给它的是固定超时。hosted `windows-latest` 的账户是管理员，
这些症状在那里不出现。

这里改成两条只用 Win32 API 的路径，不起任何外部进程：

* **Job Object**（`spawn()` 起的进程）：进程在**挂起**状态下就被放进 Job，再恢复运行，
  所以它后来起的任何后代都逃不出去。结束 = `TerminateJobObject`，判「已不存在」= Job 的
  `ActiveProcesses == 0`。Job 的成员关系不随父进程退出而消失，直接子进程先走了也收得到后代。
* **进程快照回退**（不是我们起的进程，或放不进 Job）：`CreateToolhelp32Snapshot` 列出
  父子关系，从叶子往根 `TerminateProcess`。只认「现在还在的」父子链。

判活一律 `OpenProcess` + `GetExitCodeProcess`（STILL_ACTIVE = 259），不解析 tasklist 的
本地化输出（与 `scripts/winlab/agent.py` 同一做法）。POSIX 上这个模块几乎是空的：进程组
由调用方自己管（`start_new_session=True` + `killpg`），这里只给同形状的 `kill_tree` 兜底。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

IS_WINDOWS = os.name == "nt"

_STILL_ACTIVE = 259
_CREATE_SUSPENDED = 0x00000004
_JOB_KILL_ON_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED = 0x1000
_SYNCHRONIZE = 0x00100000
_TH32CS_SNAPPROCESS = 0x00000002
_JOB_OBJECT_BASIC_ACCOUNTING = 1  # JobObjectBasicAccountingInformation
_JOB_OBJECT_EXTENDED_LIMIT = 9  # JobObjectExtendedLimitInformation
_POLL_S = 0.05

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    for _name in ("Process32FirstW", "Process32NextW"):
        getattr(_k32, _name).argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        getattr(_k32, _name).restype = wintypes.BOOL

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]


# --------------------------------------------------------------------------- 判活 / 单个进程
def is_alive(pid: int) -> bool:
    """这个 pid 现在还在不在。进程已退出但还有人握着句柄时，退出码不是 STILL_ACTIVE → 不算活。"""
    if os.name == "nt":
        return _win_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _win_is_alive(pid: int) -> bool:
    handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        # 拒绝访问 = 进程在、只是不属于我们；其余（参数无效 = 没有这个 pid）= 不在
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        _k32.CloseHandle(handle)


def terminate_pid(pid: int) -> bool:
    """只结束这一个进程（不碰后代）。POSIX 发 SIGTERM，Windows 是 TerminateProcess。"""
    if not IS_WINDOWS:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True
    handle = _k32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        return False
    try:
        return bool(_k32.TerminateProcess(handle, 1))
    finally:
        _k32.CloseHandle(handle)


# --------------------------------------------------------------------------- 进程快照
def _snapshot_children() -> dict[int, list[int]]:
    """{父 pid: [子 pid…]}。快照失败返回空表——调用方退化成只结束根进程。"""
    kids: dict[int, list[int]] = {}
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return kids
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            kids.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return kids


def descendants(pid: int) -> list[int]:
    """`pid` 的全部后代，**叶子在前**（先杀叶子，父进程就没有机会再起新的）。"""
    if not IS_WINDOWS:
        return []
    kids = _snapshot_children()
    order: list[int] = []
    seen = {pid}
    stack = [pid]
    while stack:
        cur = stack.pop()
        for child in kids.get(cur, ()):
            if child not in seen:  # pid 复用会让父子表出环
                seen.add(child)
                order.append(child)
                stack.append(child)
    order.reverse()
    return order


# --------------------------------------------------------------------------- Job Object
class Job:
    """一个 KILL_ON_JOB_CLOSE 的 Job Object。只在 Windows 上存在实例。"""

    def __init__(self, handle) -> None:
        self._handle = handle

    def kill(self) -> bool:
        """结束 Job 里的全部进程。已经关掉的 Job 当作已空。"""
        if self._handle is None:
            return True
        return bool(_k32.TerminateJobObject(self._handle, 1))

    def active_count(self) -> int | None:
        """Job 里还活着的进程数；查不到返回 None（调用方不能把 None 当成 0）。"""
        if self._handle is None:
            return 0
        info = _BasicAccounting()
        if not _k32.QueryInformationJobObject(
            self._handle,
            _JOB_OBJECT_BASIC_ACCOUNTING,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            return None
        return int(info.ActiveProcesses)

    def close(self) -> None:
        """释放句柄（KILL_ON_JOB_CLOSE：Job 里剩下的进程随之结束）。"""
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None


def adopt(proc: subprocess.Popen) -> Job | None:
    """把一个**已经在跑**的进程放进新的 Job。拿不到（POSIX / 无权限）返回 None。

    晚于它起的后代不在 Job 里，所以能用 `spawn()` 就别用这个。
    """
    if not IS_WINDOWS:
        return None
    job = _k32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE
    ok = _k32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT, ctypes.byref(limits), ctypes.sizeof(limits)
    ) and _k32.AssignProcessToJobObject(job, int(proc._handle))  # noqa: SLF001
    if not ok:
        _k32.CloseHandle(job)
        return None
    return Job(job)


def spawn(argv: list[str], **kwargs) -> subprocess.Popen:
    """`subprocess.Popen`，但 Windows 上先挂起、放进 Job、再恢复，后代逃不出 Job。

    放不进 Job（嵌套 Job 被外层禁止等）时照常恢复运行，`kill_tree` 回退到进程快照。
    Job 挂在返回的 Popen 上（`proc.tavotto_job`），没有就是 None。
    """
    if not IS_WINDOWS:
        proc = subprocess.Popen(argv, **kwargs)
        proc.tavotto_job = None  # type: ignore[attr-defined]
        return proc
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | _CREATE_SUSPENDED
    proc = subprocess.Popen(argv, **kwargs)
    job = None
    try:
        job = adopt(proc)
    finally:
        # 无论放没放进去都必须恢复，否则进程永远挂起
        ctypes.WinDLL("ntdll").NtResumeProcess(int(proc._handle))  # noqa: SLF001
    proc.tavotto_job = job  # type: ignore[attr-defined]
    return proc


# --------------------------------------------------------------------------- 结束整棵树
def kill_tree(proc: subprocess.Popen) -> str | None:
    """让 `proc` 和它的后代都被结束。返回用了哪条路径（`job` / `snapshot` / `process-group` /
    `process`），一个都没送到返回 None。**只是发出，不保证已经没了**——要确认用 `tree_gone`。"""
    if not IS_WINDOWS:
        try:
            if os.getpgid(proc.pid) == proc.pid:
                os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                return "process-group"
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            return None
        return "process"
    job = getattr(proc, "tavotto_job", None)
    if job is not None and job.kill():
        return "job"
    # 先拍快照再杀：根进程一死，子孙的父子链就断了
    victims = descendants(proc.pid)
    proc.tavotto_victims = victims  # type: ignore[attr-defined]  # tree_gone 要盯着它们
    hit = False
    for pid in victims:
        hit = terminate_pid(pid) or hit
    hit = terminate_pid(proc.pid) or hit
    return "snapshot" if hit else None


def tree_gone(proc: subprocess.Popen, timeout: float) -> bool:
    """等到 `proc` 的整棵树都**不存在**为止；`timeout` 内没等到返回 False。

    判据是存在性：Job 里的 `ActiveProcesses == 0`；没有 Job 时是 `proc` 与它当初的后代都不在。
    POSIX 上只看 `proc` 自己（进程组的存在性判据在 package_smoke 里）。
    """
    job = getattr(proc, "tavotto_job", None) if IS_WINDOWS else None
    watch = [proc.pid, *getattr(proc, "tavotto_victims", ())] if job is None else []
    deadline = time.monotonic() + timeout
    while True:
        if job is not None:
            n = job.active_count()
            if n == 0:
                return True
        elif not any(is_alive(p) for p in watch):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_S)
