"""用例级挂死看门狗：一条用例（含 setup / teardown）超过上限就点名、打栈、硬退出。

起因是 issue #1226：合并组里 macOS `backend-platforms` 一片跑满 90 分钟被取消，日志只有点状进度，
看不出卡在哪条。`pytest.ini` 的 `faulthandler_timeout = 300` 只在**单条用例超过 300 秒**时打栈，
而且（见 pytest.ini 的长注释）刻意不退出；这里补它够不到的两件事：

1. **点名 + 退出**：超过 `TAVOTTO_TEST_HANG_SECONDS` 秒（CI 上设 900，本机不设 = 整个机制关闭）
   就打出当前 nodeid、阶段、所有线程的栈、本进程的子进程树，然后 `os._exit(1)`——CI 上变成一个
   「某某用例挂了」的明确红灯，而不是等 `timeout-minutes` 把整片取消、顺带踢走队列里无关的 PR。
   不用 `faulthandler.dump_traceback_later(exit=True)`：全进程只有一个 faulthandler 定时器，
   pytest 内置的 300 秒那个每条用例都会重设它，两者会互相覆盖；而且 faulthandler 打不出 nodeid。
2. **慢用例的实时留痕**：`TAVOTTO_TEST_SLOW_NOTE_SECONDS`（默认 60）以上的用例一结束就往 stderr
   写一行 nodeid + 秒数。`--durations=N` 只在会话正常结束时才打，被取消的 job 一行都看不到。
   每片这样的用例只有几十条，日志量可忽略。

计时线程是 daemon：用例卡在 C 层持有 GIL 的死循环里它也醒不来，但本仓库已知的挂法（等子进程、等
socket、等锁）都会释放 GIL；这类极端情况仍由 job 的 `timeout-minutes` 兜底。
"""

from __future__ import annotations

import faulthandler
import os
import subprocess
import sys
import threading
import time

HANG_ENV = "TAVOTTO_TEST_HANG_SECONDS"
SLOW_NOTE_ENV = "TAVOTTO_TEST_SLOW_NOTE_SECONDS"
DEFAULT_SLOW_NOTE_SECONDS = 60.0
HANG_EXIT_CODE = 1


def _positive_float(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        return None
    return value if value > 0 else None


def hang_seconds(environ=os.environ) -> float | None:
    """未设置 / 非正数 / 写坏了都当关闭——本机默认行为不变。"""
    return _positive_float(environ.get(HANG_ENV))


def slow_note_seconds(environ=os.environ) -> float | None:
    """只有看门狗开着时才有实时留痕；阈值可用环境变量改，写坏了回默认。"""
    if hang_seconds(environ) is None:
        return None
    return _positive_float(environ.get(SLOW_NOTE_ENV)) or DEFAULT_SLOW_NOTE_SECONDS


def describe_children() -> str:
    """本进程的后代进程（POSIX 用 ps）；拿不到就说拿不到，不影响退出。

    `-ww`：Linux procps 在 stdout 不是终端时把 command 截在 80 列（CI 报告里只剩 `python -m`，#1285），
    `-ww` 不限宽；macOS 的 ps 同样认。"""
    if os.name == "nt":
        return "  （Windows：未枚举子进程）"
    try:
        proc = subprocess.Popen(
            ["ps", "-ww", "-axo", "pid=,ppid=,etime=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        probe_pid = proc.pid
        out, _ = proc.communicate(timeout=10)
    except Exception as exc:  # noqa: BLE001 - 退出路径上不许再抛
        return f"  （枚举子进程失败：{exc!r}）"
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if (
            len(parts) == 4
            and parts[0].isdigit()
            and parts[1].isdigit()
            and int(parts[0]) != probe_pid
        ):
            rows.append((int(parts[0]), int(parts[1]), parts[2], parts[3]))
    mine = {os.getpid()}
    grew = True
    while grew:
        grew = False
        for pid, ppid, _etime, _cmd in rows:
            if ppid in mine and pid not in mine:
                mine.add(pid)
                grew = True
    kids = [r for r in rows if r[0] in mine and r[0] != os.getpid()]
    if not kids:
        return "  （没有子进程）"
    return "\n".join(
        f"  pid={pid} ppid={ppid} 已运行={etime} {cmd[:600]}" for pid, ppid, etime, cmd in kids
    )


class Watchdog:
    """每条用例一个：`start()` 起计时，`cancel()` 取消。到点调 `on_hang(nodeid, limit)`。"""

    def __init__(self, nodeid: str, limit: float, on_hang):
        self.nodeid = nodeid
        self.limit = limit
        self._timer = threading.Timer(limit, on_hang, args=(nodeid, limit))
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()


def _stderr_write(text: str) -> None:
    """utf-8 + replace 写 bytes：Windows runner 的 stderr 可能是 cp1252，写中文会抛 UnicodeEncodeError。"""
    data = text.encode("utf-8", "replace")
    stream = sys.__stderr__
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    else:
        os.write(2, data)


def report_and_exit(nodeid: str, limit: float, *, write=_stderr_write, exit_fn=os._exit) -> None:
    """打报告然后硬退出。`write` / `exit_fn` 可注入；无论报告写出是否异常，都必须走到 exit_fn。"""
    try:
        write(
            f"\n!!! 用例挂死：{nodeid} 运行超过 {limit:g} 秒（{HANG_ENV}），"
            f"pid={os.getpid()}，下面是所有线程的栈与子进程；进程即将以退出码 {HANG_EXIT_CODE} 退出 !!!\n"
        )
        try:
            sys.__stderr__.flush()
        except Exception:  # noqa: BLE001
            pass
        faulthandler.dump_traceback(file=sys.__stderr__, all_threads=True)
        write("子进程：\n" + describe_children() + "\n")
    except BaseException as exc:  # noqa: BLE001
        try:
            os.write(2, f"\n（挂死报告写出失败：{exc!r}）\n".encode("ascii", "replace"))
        except Exception:  # noqa: BLE001
            pass
    finally:
        exit_fn(HANG_EXIT_CODE)


def format_slow_note(nodeid: str, seconds: float, now: float | None = None) -> str:
    stamp = time.strftime("%H:%M:%S", time.gmtime(now if now is not None else time.time()))
    return f"[slow-test {stamp}Z] {seconds:.0f}s {nodeid}\n"
