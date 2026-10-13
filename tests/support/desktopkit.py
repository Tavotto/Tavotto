"""桌面拥有的 native 进程用例的公共助手（P06，ADR 0125）。

与 `nativekit` 的分工：那边的「假桌面」只替掉窗口、进程仍归 `tavotto run` CLI；这边没有 CLI——
进程由 `desktopproc.start` 在本测试进程里真 spawn，用户 Python、Bridge Runner、Matplotlib
Figure 都是真的。唯一没有的是窗口与 Flask。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

from support import nativekit, procprobe
from tavotto.engine import desktopproc, envlease, execspec, nativesession

#: 测试用的有界时间：毫秒到秒级，产品默认见 `desktopproc.OwnedPolicy`。
FAST = desktopproc.OwnedPolicy(
    connect_timeout=60.0, term_grace=1.5, kill_timeout=8.0, linger_grace=0.5, tail_bytes=4096
)

FIGURE_HEADER = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
"""


def launch_for(
    tmp_path: Path,
    script_text: str,
    *,
    name: str = "figure.py",
    argv: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    stdin: str = execspec.STDIN_NULL,
    policy: desktopproc.OwnedPolicy = FAST,
    interpreter: str | None = None,
    launch_id: str = "",
) -> desktopproc.OwnedLaunch:
    """项目在 `tmp_path/proj`，cwd 故意放在另一个目录 `tmp_path/work`：证明 cwd 是启动身份里
    显式给的那个，不是脚本目录也不是项目根。"""
    proj = tmp_path / "proj"
    work = tmp_path / "work"
    proj.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    script = nativekit.write(proj / name, script_text)
    spec = execspec.desktop_native_spec(
        str(script),
        interpreter=interpreter or nativekit.USER_PYTHON,
        cwd=str(work),
        project_root=str(proj),
        argv=argv,
        run_config="rc_p06test" if argv else "",
        env=env,
        stdin=stdin,
    )
    return desktopproc.OwnedLaunch(
        spec,
        launch_id=launch_id,
        policy=policy,
    )


def start(tmp_path: Path, script_text: str, **kw) -> nativesession.NativeSession:
    return desktopproc.start(launch_for(tmp_path, script_text, **kw))


def finish(session, timeout: float = 120.0) -> None:
    """把剩下的每个屏障应答掉，等所有者确认结束（`finished`）。"""
    deadline = time.monotonic() + timeout
    while not session.owned.finished:
        if time.monotonic() > deadline:
            raise AssertionError(
                f"{timeout}s 内没结束: state={session.state} owned={session.owned.snapshot()}"
            )
        if session.state == nativesession.BARRIER:
            with contextlib.suppress(Exception):
                session.resume()
        time.sleep(0.05)


def wait_until(predicate, timeout: float = 60.0, what: str = "条件") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"{timeout}s 内没等到 {what}")


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pid_alive(pid: int) -> bool:
    return procprobe.alive(pid)


def holds_lease(session) -> bool:
    return session.session_id in envlease.native_sessions_on(session.interpreter)


def install_is_refused(interpreter: str) -> str | None:
    """此刻往这个解释器装包会被拒吗？回拒绝的码，能装回 None（**不真装**，进去就出来）。"""
    try:
        with envlease.mutating(f"p06-probe:{interpreter}", python=interpreter):
            return None
    except envlease.EnvironmentBusy as exc:
        return exc.code


def tail(session, stream: str = "stdout") -> str:
    return getattr(session.owned, stream).text()


def current_python() -> str:
    return sys.executable if nativekit.USER_PYTHON is None else nativekit.USER_PYTHON


def listdir_names(path: Path) -> set[str]:
    return set(os.listdir(path)) if Path(path).exists() else set()
