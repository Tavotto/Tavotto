"""桌面拥有的 native 脚本进程（P06，ADR 0125）。

`tavotto run` 的进程关系是倒过来的（ADR 0021 §1）：用户的 Python 是 **CLI** 的子进程，
sidecar 只通过 relay 连上去，所以 sidecar 对那个进程没有任何所有权。桌面里直接打开 `.py`
并运行（P07）没有 CLI——进程只能由 sidecar 自己 spawn。本模块把这一类**明确**建出来，
而不是让某个调用点偷偷 `Popen`：

```text
sidecar（本模块）
   ├── 用户 Python（Bridge Runner）  ← 直接子进程；自成进程组（POSIX）/ Job（Windows）
   │      ├── stdin   ← 桌面持有（pipe：write_stdin / close_stdin；null：一开始就是 EOF）
   │      ├── stdout / stderr → 两条常驻排空线程 → 有界尾部 + 可选 sink（与控制协议无关）
   │      └── 控制通道 ← 127.0.0.1 loopback + 一次性 token（同 ADR 0020 / `nativerelay` 的认证 accept）
   └── NativeTransport 单 reader → NativeSession（同一个注册表、同一套状态机）
```

## 复用什么、不新建什么

* 启动身份是 `execspec.desktop_native_spec`（同一个 `bridge_argv`，同一个 runner）；
* 认证 accept 是 `nativerelay.NativeRelay.adopt_child`（token 比对 / 认证失败不收摊 / 盯子进程）；
* 控制通道只有 `nativesession.NativeTransport` 一个 reader；会话是 `NativeSession`，进
  `nativesession.REGISTRY`，对上层仍是 Worker-like，由 `enginesession.resolve` 路由；
* 环境租约是 `envlease`——**启动前取**，由所有者在进程（组）确认退出后放。

## 所有权的三条硬规则

1. **断开不是结束。** 控制连接断了（WebView 关了 / socket 被 poison / `session.shutdown()`）
   进程照跑，租约照持；只有 `cancel()` 或脚本自己退出才结束它。
2. **租约在所有者确认后才放。** `finished` 要求：直接子进程已 reap **且**进程组 / Job 已确认空。
   确认不了（进程不死）就一直持有租约、后台继续试——绝不为了「释放」而谎报，让安装器改一个
   还有脚本在用的环境。
3. **只动自己的组。** 取消只发给**本进程自己的**进程组 / Job；不按 PPID 猜、不遍历进程表、不碰
   自己起的组之外的任何进程（厂商服务 / 数据库 / 别的会话）。组之外的子进程（自己 `setsid` 走的）
   不是我们纳管的，如实不管。已 reap 之后的组 id 只在「组里确实还有成员」时才再发信号。

## 平台

* POSIX：`start_new_session=True`（组长 = 直接子进程，pgid == pid），取消 = `killpg(SIGTERM)` →
  `term_grace` → `killpg(SIGKILL)`，`killpg(pgid, 0)` 判组空。
* Windows：`CREATE_NO_WINDOW` + Job Object（`KILL_ON_JOB_CLOSE`），取消 = `TerminateJobObject`，
  `ActiveProcesses` 判空。spawn 与入 Job 之间有一个很短的窗口（进程已起、尚未入 Job），窗口里
  它自己起的孙进程不在 Job 里；Python 解释器启动（毫秒到百毫秒级）远长于这个窗口，但这是已知
  残余，不是消除。**Windows 代码在 CI 上以替身 kernel32 单测；真机验收 not_run（P07/P10）。**
  入 Job 失败（老系统已在不可嵌套的 Job 里）时退化为只管直接子进程（`scope_kind="process"`）。

纯标准库 + 兄弟模块。**不 import matplotlib / Flask。**
"""

from __future__ import annotations

import ctypes
import dataclasses
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import (
    bridge,
    config,
    envlease,
    execspec,
    launcher,
    nativerelay,
    nativesession,
    runcodes,
    runtime,
)
from .runcodes import RunError

_LAUNCH_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

SCOPE_GROUP = "process_group"
SCOPE_JOB = "job_object"
SCOPE_PROCESS = "process"


@dataclasses.dataclass(frozen=True)
class OwnedPolicy:
    """取消 / 清理的有界时间。**每一项都可配置**——测试用毫秒级，产品用默认。"""

    #: 等用户的 Python 连回控制通道的上限（与 `runcli.CHILD_TIMEOUT` 同口径）。
    connect_timeout: float = 90.0
    #: 取消时先礼后兵：POSIX 发 SIGTERM 后等直接子进程退出这么久，没退再 SIGKILL。
    term_grace: float = 3.0
    #: 强杀之后，等进程组 / Job 清空的上限。到点还没空 = 取消「未确认」，租约继续持有。
    kill_timeout: float = 10.0
    #: 脚本自己退出后，组里残留成员再给这么久，仍在就按取消的强度收掉。
    linger_grace: float = 2.0
    #: stdout / stderr 各留多少字节的尾部（诊断与界面首屏用；全量走 sink）。
    tail_bytes: int = 64 * 1024


@dataclasses.dataclass(frozen=True)
class OwnedLaunch:
    """一次桌面拥有的运行的**不可变启动身份**（ADR 0125 §二）。

    * `spec`：解释器 / 入口（脚本或 `-m`）/ argv（经运行配置引用）/ 显式 cwd / env 增量及来源 /
      stdin 通道——全在 `execspec.desktop_native_spec` 的产物里；
    * `launch_id`：回执与会话的关联键（公开，不含任何参数值）；
    * 会话产物目录不在身份里：恒为 `data_dir()/cache/native/<会话 id>`，由所有者独占（会话 id 含每次启动唯一的后缀）。
    """

    spec: execspec.ExecutionSpec
    launch_id: str = ""
    preview_dpi: int = 200
    policy: OwnedPolicy = OwnedPolicy()

    def __post_init__(self) -> None:
        spec = self.spec
        if not (spec.profile == execspec.PROFILE_NATIVE and spec.owner == execspec.OWNER_DESKTOP):
            raise RunError(
                runcodes.DESKTOP_RUN_LAUNCH_INVALID, reason="不是桌面拥有的 native 启动身份"
            )
        if not self.launch_id:
            object.__setattr__(self, "launch_id", uuid.uuid4().hex[:16])
        elif not _LAUNCH_ID_RE.fullmatch(self.launch_id):
            # launch_id 会拼进缓存目录名与会话 / 租约键：白名单字符 + 长度上限，不合格当场拒绝
            raise RunError(
                runcodes.DESKTOP_RUN_LAUNCH_INVALID,
                reason="launch_id 只能是 1–64 位字母、数字、下划线、连字符",
            )


@dataclasses.dataclass(frozen=True)
class CancelResult:
    """`cancel()` 的结果。`confirmed` = 直接子进程已 reap **且**进程组 / Job 已确认空。"""

    confirmed: bool
    exit_code: int | None
    scope: str


# ---------------------------------------------------------------------------
# 进程组 / Job：「我们纳管的那一组进程」
# ---------------------------------------------------------------------------
class _PosixGroup:
    kind = SCOPE_GROUP

    def __init__(self, pgid: int):
        self.pgid = pgid

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(getattr(signal, "SIGKILL", signal.SIGTERM))

    def _signal(self, sig) -> None:
        try:
            os.killpg(self.pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass  # 组已空 / 成员正在退出：没有谁需要再发

    def empty(self) -> bool:
        try:
            os.killpg(self.pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False  # 组里还有我们发不到信号的成员：不能说它空
        return False

    def close(self) -> None:
        pass


class _DirectScope:
    """只管直接子进程（入 Job 失败时的退化；`scope_kind` 如实报 `process`）。"""

    kind = SCOPE_PROCESS

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    def terminate(self) -> None:
        try:
            self._proc.terminate()
        except OSError:
            pass

    def kill(self) -> None:
        try:
            self._proc.kill()
        except OSError:
            pass

    def empty(self) -> bool:
        return self._proc.poll() is not None

    def close(self) -> None:
        pass


# Windows Job Object（kernel32 以参数注入：CI 上用替身单测，真机见 P07/P10）
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def _win_structs():
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_uint64)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class BasicLimit(ctypes.Structure):
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

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class BasicAccounting(ctypes.Structure):
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

    return ExtendedLimit, BasicAccounting


def _win_kernel32():  # pragma: no cover — 只在 Windows 上走到
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.TerminateJobObject.restype = wintypes.BOOL
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.QueryInformationJobObject.restype = wintypes.BOOL
    k.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


class _WindowsJob:
    """一个 Job Object：`KILL_ON_JOB_CLOSE`（sidecar 崩了 / 句柄关了，成员一并结束）。"""

    kind = SCOPE_JOB

    def __init__(self, k32, handle):
        self._k = k32
        self._h = handle
        self._closed = False

    @classmethod
    def create(cls, k32, process_handle) -> _WindowsJob | None:
        """建 Job、设 KILL_ON_JOB_CLOSE、把进程放进去；任何一步失败返回 None（调用方退化）。"""
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        ExtendedLimit, _ = _win_structs()
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok or not k32.AssignProcessToJobObject(job, process_handle):
            k32.CloseHandle(job)
            return None
        return cls(k32, job)

    def terminate(self) -> None:
        # Windows 没有对一整组的「先礼」：TerminateJobObject 就是强度最高的那一下。
        self.kill()

    def kill(self) -> None:
        if not self._closed:
            self._k.TerminateJobObject(self._h, runcodes.EXIT_CANCELLED)

    def empty(self) -> bool:
        if self._closed:
            return True
        _, Accounting = _win_structs()
        acc = Accounting()
        ok = self._k.QueryInformationJobObject(
            self._h,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(acc),
            ctypes.sizeof(acc),
            None,
        )
        return bool(ok) and acc.ActiveProcesses == 0

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._k.CloseHandle(self._h)


def _scope_for(proc: subprocess.Popen, *, nt: bool | None = None, kernel32=None):
    """spawn 之后立刻给这个进程配它的纳管范围。`nt` / `kernel32` 只给用例注入替身。"""
    if os.name == "nt" if nt is None else nt:
        job = _WindowsJob.create(kernel32 or _win_kernel32(), int(proc._handle))  # noqa: SLF001
        return job if job is not None else _DirectScope(proc)
    return _PosixGroup(proc.pid)  # start_new_session：组长 = 直接子进程，pgid == pid


# ---------------------------------------------------------------------------
# 输出排空
# ---------------------------------------------------------------------------
class OutputTail:
    """一条流的有界尾部。**只增不阻塞**：写满就丢最老的，排空线程永远不会因为它停下。"""

    def __init__(self, limit: int):
        self._limit = max(0, int(limit))
        self._buf = bytearray()
        self._lock = threading.Lock()
        self.total = 0  # 累计字节数（含被丢掉的）

    def feed(self, data: bytes) -> None:
        with self._lock:
            self.total += len(data)
            self._buf += data
            over = len(self._buf) - self._limit
            if over > 0:
                del self._buf[:over]

    def text(self) -> str:
        with self._lock:
            return bytes(self._buf).decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 所有者
# ---------------------------------------------------------------------------
class DesktopProcess:
    """一个桌面拥有的用户 Python 进程（含它的进程组 / Job）及它的全部 I/O。

    线程：一个**等待线程**（`wait` → 清组 → 确认 → 回调）、两个**排空线程**。没有别的。
    所有状态迁移都在等待线程里单向发生：`exited`（直接子进程 reap）→ `finished`（组确认空）。
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        scope,
        policy: OwnedPolicy,
        *,
        on_output=None,
        on_exit=None,
        gated: bool = False,
    ):
        self.proc = proc
        self.pid = proc.pid
        self.scope = scope
        self.policy = policy
        self.stdout = OutputTail(policy.tail_bytes)
        self.stderr = OutputTail(policy.tail_bytes)
        self.exit_code: int | None = None
        self.cancelled = False
        self._on_output = on_output
        self._on_exit = on_exit
        self._lock = threading.Lock()
        self._cancel_lock = threading.Lock()
        self._exited = threading.Event()
        self._confirmed = threading.Event()
        #: 启动闸门（`start()` 用）：握手 / 传输 reader / 登记都就位之前，完成回调不许跑——否则快退出的
        #: 子进程会在 reader 还没起来时被判「没通知就走了」，或在登记之前就放掉 launch_id 占位。
        #: `gated=False`（默认）= 一开始就放行，直接用 `DesktopProcess` 的用例不受影响。
        self._armed = threading.Event()
        if not gated:
            self._armed.set()
        self._finished = threading.Event()
        self._pumps: list[threading.Thread] = []
        self._waiter: threading.Thread | None = None
        self._stdin_closed = proc.stdin is None

    # ---------------- 状态 ----------------
    @property
    def exited(self) -> bool:
        """直接子进程已退出并被 reap。"""
        return self._exited.is_set()

    @property
    def confirmed(self) -> bool:
        """进程组 / Job 已确认空（内部状态，会话放租约只认它）；回调还没做完时 `finished` 仍为 False。"""
        return self._confirmed.is_set()

    @property
    def finished(self) -> bool:
        """**所有者确认结束**：进程组 / Job 已确认空，且会话的退出回调（落终态、放租约）已做完——对外的完成栅栏。"""
        return self._finished.is_set()

    @property
    def scope_kind(self) -> str:
        return self.scope.kind

    def arm(self) -> None:
        """启动流程走完（成功或失败收尾）：放行完成回调。幂等。"""
        self._armed.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._finished.wait(timeout)

    # ---------------- 启动 ----------------
    def start(self) -> None:
        for stream, tail, name in (
            (self.proc.stdout, self.stdout, "stdout"),
            (self.proc.stderr, self.stderr, "stderr"),
        ):
            t = threading.Thread(
                target=self._pump,
                args=(stream, tail, name),
                daemon=True,
                name=f"tavotto-owned-{name}",
            )
            t.start()
            self._pumps.append(t)
        self._waiter = threading.Thread(
            target=self._wait_loop, daemon=True, name="tavotto-owned-wait"
        )
        self._waiter.start()

    def _pump(self, stream, tail: OutputTail, name: str) -> None:
        """持续排空一条流（管道写满会让用户脚本卡在 `print` 上，所以必须有人一直读）。

        不解析、不转发到控制通道；sink 抛错只丢那一块，排空不停。"""
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    return
                tail.feed(chunk)
                if self._on_output is not None:
                    try:
                        self._on_output(name, chunk)
                    except Exception:  # noqa: BLE001 — 观察者的故障不许堵住用户脚本的管道
                        pass
        except (OSError, ValueError):
            return

    def _wait_loop(self) -> None:
        code = runcodes.exit_code_of(self.proc.wait())
        with self._lock:
            self.exit_code = code
        self._exited.set()
        self._close_stdin()
        self._settle_group()
        for t in self._pumps:
            t.join(2.0)  # 孙进程还攥着管道写端时排空线程不会自己结束：不为它卡住确认
        self.scope.close()
        self._confirmed.set()  # 组确认空：租约此刻才有资格放（会话回调里核它）
        self._armed.wait()  # 启动还没走完就先不回调（见 `_armed`）
        try:
            if self._on_exit is not None:
                self._on_exit(code, cancelled=self.cancelled)
        finally:
            self._finished.set()  # 对外的完成栅栏：回调（会话落终态、放租约）做完才算

    def _settle_group(self) -> None:
        """直接子进程走了之后，把组里的残余收干净（**确认**，不是假设）。

        自然退出：给残余 `linger_grace` 自己收尾的时间；取消中：立刻按强度收。
        一直收不空就持续重试（租约因此不放）——不谎报「已结束」。
        """
        scope, pol = self.scope, self.policy
        deadline = time.monotonic() + (0.0 if self.cancelled else pol.linger_grace)
        while not scope.empty() and time.monotonic() < deadline:
            time.sleep(0.02)
        if scope.empty():
            return
        scope.terminate()
        deadline = time.monotonic() + pol.kill_timeout
        while not scope.empty():
            if time.monotonic() >= deadline:
                scope.kill()
                time.sleep(0.2)
                continue
            time.sleep(0.02)

    # ---------------- stdin ----------------
    def write_stdin(self, data: str | bytes) -> bool:
        """向脚本的 stdin 送入数据（`pipe` 模式）。通道已关 / 进程已走返回 False。"""
        if self._stdin_closed or self.proc.stdin is None:
            return False
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        try:
            self.proc.stdin.write(raw)
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def close_stdin(self) -> None:
        """送 EOF：脚本的 `input()` / `sys.stdin.read()` 得到 EOF。幂等。"""
        self._close_stdin()

    def _close_stdin(self) -> None:
        with self._lock:
            if self._stdin_closed:
                return
            self._stdin_closed = True
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    # ---------------- 取消 ----------------
    def cancel(self) -> CancelResult:
        """结束本进程及其纳管的组 / Job，并**等到确认**（上限 `term_grace + kill_timeout`）。

        幂等、可并发调用（后来者等同一次结果）。已经自然退出的进程不会被标成 `cancelled`。
        """
        pol = self.policy
        with self._cancel_lock:
            if not self.exited:
                with self._lock:
                    if not self.exited:
                        self.cancelled = True
                self.scope.terminate()
                if not self._exited.wait(pol.term_grace):
                    self.scope.kill()
            # 直接子进程走了之后，组里的残余由等待线程按取消的强度收（`cancelled` 已置位）
            confirmed = self._finished.wait(pol.kill_timeout + 1.0)
        return CancelResult(confirmed=confirmed, exit_code=self.exit_code, scope=self.scope.kind)

    def terminate_sync(self) -> bool:
        """关停用：置 cancelled，先礼（`term_grace`）后兵，**同步**等直接子进程与进程组确认消失（有界）。

        不等 `finished`（启动线程可能还握着闸门）；返回是否确认消失。进程忽略 SIGTERM 时也会在返回前被强杀。"""
        with self._lock:
            if not self.exited:
                self.cancelled = True
        pol = self.policy
        if not self.exited:
            self.scope.terminate()
            if not self._exited.wait(pol.term_grace):
                self.scope.kill()
                self._exited.wait(pol.kill_timeout)
        deadline = time.monotonic() + pol.kill_timeout
        while not self.scope.empty() and time.monotonic() < deadline:
            self.scope.kill()
            time.sleep(0.02)
        return self.exited and self.scope.empty()

    def kill_now(self) -> None:
        """不等确认的取消（sidecar 关停时对「还在启动」的所有者用）：置 cancelled、先礼后兵地发信号就返回。

        启动线程还握着闸门，`cancel()` 会等到超时；这里只负责让进程尽快消失——先礼（`term_grace`）
        后兵由一条 daemon 线程兜底，清组与确认仍由等待线程完成。"""
        with self._lock:
            if self.exited:
                return
            self.cancelled = True
        self.scope.terminate()

        def _escalate() -> None:
            if not self._exited.wait(self.policy.term_grace):
                self.scope.kill()

        threading.Thread(target=_escalate, daemon=True, name="tavotto-owned-kill").start()

    def snapshot(self) -> dict:
        """诊断用：**没有输出内容、没有路径、没有 argv**。"""
        return {
            "scope": self.scope.kind,
            "exited": self.exited,
            "finished": self.finished,
            "cancelled": self.cancelled,
            "exit_code": self.exit_code,
            "stdout_bytes": self.stdout.total,
            "stderr_bytes": self.stderr.total,
        }


# ---------------------------------------------------------------------------
# 入口：起进程 → 认证 → 会话
# ---------------------------------------------------------------------------
def start(
    launch: OwnedLaunch,
    *,
    registry: nativesession.NativeSessionRegistry | None = None,
    on_output=None,
) -> nativesession.NativeSession:
    """按不可变启动身份起一个桌面拥有的用户 Python，握手成功后返回已登记的 `NativeSession`。

    顺序是契约的一部分：**取租约 → spawn → 认证握手 → 单 reader 接管 → 登记**。
    租约先于第一行用户代码（环境正在被安装改动时 `envlease.EnvironmentBusy` 原样抛给调用方）；
    任何一步失败都先让所有者收掉进程，租约由所有者确认结束后放（成功路径也是同一条）。
    """
    registry = registry or nativesession.REGISTRY
    spec, pol = launch.spec, launch.policy
    if not os.path.isdir(spec.cwd):
        raise RunError(runcodes.DESKTOP_RUN_LAUNCH_INVALID, reason="cwd 不是一个目录")
    if not os.path.isfile(spec.interpreter):
        raise RunError(runcodes.INTERPRETER_NOT_EXECUTABLE, interpreter=spec.interpreter)
    # 租约键 = 会话 id：每次启动唯一（launch_id + 随机后缀），不同所有者之间互相删不掉对方的租约
    # 完整 128 位随机（不截断）：同一 launch_id 反复复用时也不会生日碰撞回到旧目录 / 旧回执身份
    session_id = f"native-owned-{launch.launch_id}-{uuid.uuid4().hex}"
    # 输出目录由所有者独占、每次启动唯一，不接受调用方指定（共用目录会互相争用、读到别的启动的旧
    # `.preview-ledger` 与产物；相对路径在 sidecar 和子进程里还会解析到不同位置）
    cache_root = Path(config.data_path("cache", "native")).resolve()
    out_dir = str((cache_root / session_id).resolve())
    if Path(out_dir).parent != cache_root:  # 白名单之外的最后一道：必须就在缓存根下一层
        raise RunError(runcodes.DESKTOP_RUN_LAUNCH_INVALID, reason="会话目录不在缓存根内")
    cache_root.mkdir(parents=True, exist_ok=True)
    # 独占创建：目录已存在说明会话 id 撞了（或残留），宁可失败也不继承旧 `.preview-ledger`
    Path(out_dir).mkdir(exist_ok=False)

    session = nativesession.NativeSession(
        session_id=session_id,
        descriptor={
            "native_id": launch.launch_id,
            "out_dir": out_dir,
            "metadata": {
                "project_root": spec.project_root,
                "interpreter": spec.interpreter,
                "target_kind": spec.target_kind,
                "target_display": spec.target,
                "cwd": spec.cwd,
                "arg_count": len(spec.argv),
                "command_fingerprint": "",
                "permission_key": "",
                "run_config": spec.run_config,
                "launch_id": launch.launch_id,
            },
        },
    )
    # launch_id 复用：会话 id 也是租约键，后者会被前者的回调放掉。原子占位，先于租约与 spawn 拒绝
    registry.reserve(launch.launch_id)
    try:
        envlease.acquire_native(spec.interpreter, session_id)  # 先于一切：拿不到就一行都不跑
    except BaseException:
        registry.unreserve(launch.launch_id)
        raise
    relay = None
    owner: DesktopProcess | None = None
    ticket = None
    try:
        ticket = registry.begin_start(
            launch.launch_id
        )  # spawn 之前先登记在途：关停的快照一定看得见
        # Conda 环境：与 safe 档同一个启动决策（`launcher_for_interpreter`）。桌面拥有的会话由 GUI 启动，没有用户 shell 替它激活。
        # **在租约之内、spawn 之前**取：激活要跑 conda 与 activate.d，不能和正在进行的装包并发（租约挡住）；
        # 激活失败 = 不 spawn、稳定码，下面的收尾放租约
        try:
            base_env = launcher.activate_env(spec.interpreter, dict(os.environ))
        except launcher.LauncherUnavailable as exc:
            raise RunError(runcodes.CONDA_LAUNCHER_UNAVAILABLE, reason=str(exc)[:200]) from exc
        relay = nativerelay.NativeRelay()
        argv = execspec.bridge_argv(
            spec,
            runner_py=bridge.RUNNER_PY,
            out_dir=out_dir,
            preview_dpi=launch.preview_dpi,
            control_host=relay.host,
            control_port=relay.child_port,
        )
        env = merged_env(base_env, spec.env or {}, {bridge.TOKEN_ENV: relay.child_token})
        registry.check_start(ticket)  # Popen 之前的最后检查点：关停已开始就不再 spawn
        try:
            proc = subprocess.Popen(  # noqa: S603 — argv 是列表，shell=False
                argv,
                cwd=spec.cwd,
                env=env,
                stdin=subprocess.PIPE if spec.stdin == execspec.STDIN_PIPE else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # 桌面拥有的隐藏子进程（与 pool 同类）；POSIX 另自成新会话 = 自己的进程组
                creationflags=runtime.CREATE_NO_WINDOW,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise RunError(
                runcodes.INTERPRETER_NOT_EXECUTABLE, interpreter=spec.interpreter
            ) from exc
        try:
            scope = _scope_for(proc)
        except BaseException:
            proc.kill()  # 没有纳管范围就不留它：之后谁也没法保证结束它
            proc.wait()
            raise
        owner = DesktopProcess(
            proc,
            scope,
            pol,
            on_output=on_output,
            on_exit=_exit_callback(session, registry, launch.launch_id, ticket),
            gated=True,
        )
        session.owned = owner
        session.spec = spec
        owner.start()
        registry.attach_owner(ticket, owner)  # 关停已开始则拒绝：下面的失败收尾会收掉这个子进程
        try:
            sock, hello = relay.adopt_child(
                pol.connect_timeout,
                watch=_child_watch(proc),
                timeout_code=runcodes.DESKTOP_RUN_CONNECT_TIMEOUT,
            )
        except BaseException:
            owner.arm()  # 握手没成：没有 reader 也没有登记，收尾已完整，放行回调再取消
            owner.cancel()  # 收掉进程；租约由所有者确认结束后放
            raise
        transport = nativesession.NativeTransport(
            sock,
            on_event=session._on_frame,  # noqa: SLF001
            on_close=session._on_transport_closed,  # noqa: SLF001
        )
        session.transport = transport
        session._set_state(nativesession.STARTING_PYTHON)  # noqa: SLF001
        session.note_hello(hello)
        # 时序：reader 先起（缓冲里的帧才有人处理，所有者判「没通知就走了」前 `join_reader` 才有对象），
        # 再登记，最后才放行所有者回调（占位 / 租约 / 终局判断都在登记尝试结束之后）
        transport.start()
        registry.adopt(session)
        owner.arm()
        return session
    except BaseException:
        if owner is None:
            # 进程根本没起来：没有谁会回调，租约在这里放
            envlease.release_native(spec.interpreter, session_id)
        else:
            # spawn 之后的任何失败（含登记被拒）：关传输、收进程；租约由所有者确认结束后放
            if session.transport is not None:
                session.transport.close()
            owner.arm()  # 登记尝试已结束（失败）：放行回调，再取消
            owner.cancel()  # 清组超时则返回未确认：占位仍由所有者的完成回调释放，不在这里放
        if owner is None:
            registry.unreserve(launch.launch_id)  # 没有所有者就没有回调
        raise
    finally:
        if ticket is not None and owner is None:
            registry.end_start(ticket)  # 有所有者的由它的完成回调撤票据
        if owner is not None:
            owner.arm()  # 任何路径都不许把闸门留着（否则等待线程永远不回调）
        if relay is not None:
            relay.close()


def _exit_callback(session, registry, launch_id: str, ticket):
    """所有者的完成回调：先让会话落终态 / 放租约，再放 launch_id 的占位，**最后**撤在途票据。

    票据一直留到所有者 finished：握手 / 登记失败后清组没能在期限内确认时，`shutdown_all` 仍要看得见、
    等得到这个还在收尾的所有者。"""

    def _on_exit(code, *, cancelled):
        try:
            session._on_owner_exit(code, cancelled=cancelled)  # noqa: SLF001
        finally:
            registry.unreserve(launch_id)
            registry.end_start(ticket)

    return _on_exit


def merged_env(base, *overlays, nt: bool | None = None) -> dict[str, str]:
    """子进程环境 = sidecar 环境 + 若干层增量（后者覆盖前者）。

    Windows 的环境变量名不区分大小写：增量里的 `Path` 必须**覆盖**基础环境里的 `PATH`，而不是并存成
    两个键（`CreateProcess` 的环境块里同名两项，哪个生效不确定）。覆盖时保留增量的写法。
    `nt` 只给用例模拟 Windows 分支。"""
    fold = (os.name == "nt") if nt is None else nt
    out = dict(base)
    for layer in overlays:
        for key, value in layer.items():
            if fold:
                folded = key.upper()
                for existing in [k for k in out if k.upper() == folded and k != key]:
                    del out[existing]
            out[key] = value
    return out


def _child_watch(proc: subprocess.Popen):
    """等子进程连回来期间盯着它——它要是直接死了，不该白等满整个 timeout。"""

    def _watch() -> None:
        rc = proc.poll()
        if rc is not None:
            raise RunError(runcodes.BRIDGE_CHILD_EXITED, code=runcodes.exit_code_of(rc))

    return _watch
