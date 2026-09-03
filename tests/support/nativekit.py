"""`tavotto run` 控制面用例的公共助手（Session 9，ADR 0021）。

与 `bridgekit`（Session 8）的分工：那边验的是**机制**（注入模型、捕获、
invocation 对拍），这边验的是**产品控制面**（CLI 契约、descriptor、relay、
会话生命周期、屏障基准）。

**这里的"假桌面"是真 attach 客户端**：它读那份 0600 的 descriptor、走
`nativesession.REGISTRY.attach()`、成功之后才把凭据墓碑化。也就是说除了
"没有窗口"以外，走的是与真桌面完全相同的一条路——descriptor 校验、token
认证、单 reader 传输、状态机、以及那三步的**顺序**全在里面。用一个 mock
会话代替它，这些主张一条都验不到。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto.engine import nativehandoff, nativesession, pool

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"

try:
    USER_PYTHON = pool.find_worker_python()
except pool.WorkerError:  # pragma: no cover - 取决于开发机
    USER_PYTHON = None

needs_user_python = pytest.mark.skipif(
    USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def cli_argv(*args: str) -> list[str]:
    """`python -m tavotto run …` 的完整命令。

    走 `-m tavotto` 而不是直接 import：**CLI 契约的主张有一半是进程级的**
    （stdout 归谁、退出码是多少、Ctrl+C 怎么走），在进程内调函数验不到。
    """
    return [sys.executable, "-m", "tavotto", *args]


def run_cli(*args: str, cwd=None, env=None, timeout=120, stdin_text: str | None = None):
    """真起一个 `tavotto run` 进程，返回 CompletedProcess。"""
    return subprocess.run(  # noqa: S603 — 列表 argv，shell=False
        cli_argv(*args),
        cwd=str(cwd) if cwd else None,
        env=cli_env(env),
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def cli_env(extra: dict | None = None) -> dict:
    """CLI 子进程的环境：本仓库的 `src` 进 PYTHONPATH，数据目录跟着测试走。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    if extra:
        env.update(extra)
    return env


def pending_ids() -> set:
    return {n[: -len(".json")] for n in os.listdir(_native_dir()) if n.endswith(".json")}


def wait_for_pending(known: set | None = None, timeout: float = 60.0, *, poll: float = 0.05) -> str:
    """等 CLI 写出那份 pending descriptor，返回 `native_id`。

    **按目录里出现的文件判**，不按 CLI 的 stdout——那条流是用户的
    （ADR 0021 §10.1），拿它当同步点就等于承认 Tavotto 在往上面写东西。

    `known` 是启动前的快照：只认**新**出现的那个。同一个数据目录里可能还留着
    上一条用例的墓碑，认错了会 attach 到一条早就结束的会话上。
    """
    known = set() if known is None else set(known)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for native_id in sorted(pending_ids() - known):
            try:
                nativehandoff.peek(native_id)
            except Exception:  # noqa: BLE001 — 墓碑 / 过期的跳过
                continue
            return native_id
        time.sleep(poll)
    raise AssertionError(f"{timeout}s 内没有等到 pending descriptor（{_native_dir()}）")


def _native_dir() -> str:
    d = nativehandoff.native_dir()
    os.makedirs(d, exist_ok=True)
    return d


def attach_as_desktop(native_id: str) -> nativesession.NativeSession:
    """假桌面：peek descriptor → attach → **成功之后**才墓碑化。走真控制面。

    三步的顺序与 `app.py::api_native_approve()` 逐条相同，包括 #190 改掉的
    那一条：凭据在 attach 成功之前不烧。假桌面照着旧顺序写的话，产品链上
    那条端点的顺序就没有任何用例走过了。
    """
    session = nativesession.REGISTRY.attach(nativehandoff.peek(native_id))
    nativehandoff.mark_consumed(native_id)
    return session


def wait_state(session, states, timeout: float = 60.0) -> str:
    got = session.wait_for_state(states, timeout)
    assert got in set(states), (
        f"{timeout}s 内没等到 {states}，停在 {got}"
        f"（terminal_error={session.terminal_error} exit_code={session.exit_code}）"
    )
    return got


FIGURE_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title("Script")
fig.savefig("Fig1.pdf")
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@contextlib.contextmanager
def product_run(
    *invocation: str,
    cwd,
    tavotto_args=(),
    stdin=None,
    timeout: float = 120.0,
    attach: bool = True,
):
    """**完整产品链**：真 `tavotto run` 进程 → 假桌面 attach → 真用户 Python
    → 真 Bridge Runner → 真 Matplotlib Figure。

    唯一被替掉的是"有没有窗口"：假桌面走的是 `nativehandoff.peek()` +
    `nativesession.REGISTRY.attach()` + `nativehandoff.mark_consumed()`，
    也就是真桌面走的那条路（descriptor 校验、token 认证、单 reader 传输、
    状态机全在里面）。

    yield `(session, proc)`；`session` 在 `attach=False` 时是 None（那是给
    "确认之前一行代码都没跑"这类判据用的）。
    """
    before = pending_ids()
    proc = subprocess.Popen(  # noqa: S603 — 列表 argv
        cli_argv("run", "--x-no-desktop", *tavotto_args, "--", *invocation),
        cwd=str(cwd),
        env=cli_env(),
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    session = None
    try:
        native_id = wait_for_pending(before, timeout=timeout)
        if attach:
            session = attach_as_desktop(native_id)
        if stdin is not None:
            proc.stdin.write(stdin)
            proc.stdin.flush()
        yield session, proc, native_id
    finally:
        if session is not None:
            try:
                session.shutdown()
            except Exception:  # noqa: BLE001
                pass
            nativesession.REGISTRY.forget(session.session_id)
        if proc.poll() is None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover - 只在实现挂了时走到
                proc.kill()
                proc.wait(timeout=10)


def finish(session, proc, timeout: float = 180.0):
    """把**剩下的每一个屏障**逐个应答掉，再等 CLI 退出。回 `(code, out, err)`。

    **每个屏障都必须被应答**（ADR 0020 §5.4）：一次运行里屏障出现多次——
    脚本中间每个 `plt.show()` 一次，脚本跑完再一次。只应答前面几个然后去等
    进程退出，两边就各等各的（本机在写这批用例时挂过一次，和 Session 8 记的
    是同一个坑）。
    """
    while session is not None and session.state not in nativesession.TERMINAL_STATES:
        state = session.wait_for_state([nativesession.BARRIER], 120)
        if state != nativesession.BARRIER:
            break
        session.resume()
    out, err = proc.communicate(timeout=timeout)
    return proc.returncode, out, err
