"""父进程走了：对端 reset 与对端 EOF 是**同一个结论**（#240）。

Ctrl+C 之后 `tavotto run` 撤掉 relay（`runcli._wait_for_child_process`：
`shutdown(SHUT_RDWR)` + `close()`），用户脚本收尾、Bridge Runner 进"脚本结束"
屏障——**先发 `barrier` 事件，再读**。对端已经关了，这一次发送换回来一个 RST；
紧接着的 `recv` 看到 EOF 还是 `ECONNRESET`，只看 RST 与 `recv` 谁先到。前者
放开屏障、透传脚本的 130；后者是一串 Tavotto 的 traceback + 退出码 1
（2026-09-25 三次合并组 macOS 腿）。端到端那条
`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`
只能碰运气撞上这个次序；这里**把次序钉死**。

怎么钉：假父进程用 `SO_LINGER(1, 0)` 关连接——**只发 RST、不发 FIN**。于是
runner 那边不存在"先读到 EOF"的可能：屏障上不论是 `sendall` 还是 `recv`
先碰到它，碰到的一定是 reset 一类。没有 sleep，同步点是 runner 自己发出的
`barrier` 事件（变体一）与用户脚本读 stdin（变体二）。

**主语**：用户的 Python（真 Bridge Runner 子进程，真 Matplotlib Figure），
在**父进程已经走了**之后，退出码是不是它脚本自己的、进程是不是自己退了、
stderr 里有没有 Tavotto 的 traceback。
"""

from __future__ import annotations

import json
import socket
import struct
import subprocess

import pytest

from support import bridgekit
from support.bridgekit import write
from tavotto.engine import bridge

pytestmark = bridgekit.needs_user_python

#: 用户脚本在 Ctrl+C 之后自己收尾：图已经画出来了（所以会进"脚本结束"屏障），
#: 然后以 130 退出——与 `test_ctrl_c_reaches_the_script_and_leaves_no_orphan`
#: 里 `except KeyboardInterrupt: sys.exit(130)` 同一个形状。`GATE` 为真时先读一行
#: stdin：那是变体二的同步点（父进程先走、脚本后收尾）。
SCRIPT_END = """\
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("Fig1.pdf")
if {gate}:
    sys.stdin.readline()
print("INTERRUPTED", flush=True)
sys.exit(130)
"""

#: 脚本中途的屏障（`plt.show()`）上父进程走了：ADR 0021 §10.2 的
#: detach-and-continue——脚本**接着跑完**、退出码是它自己的。
MID_SCRIPT = """\
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
plt.show()
print("AFTER-SHOW", flush=True)
sys.exit(7)
"""


class FakeParent:
    """一条认证过的控制连接的**父进程那一侧**（relay 在 runner 眼里就是它）。"""

    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(120)
        self.port = self.listener.getsockname()[1]
        self.token = "t" * 32
        self.conn: socket.socket | None = None
        self.rfile = None

    def accept_and_handshake(self) -> dict:
        self.conn, _ = self.listener.accept()
        self.conn.settimeout(120)
        self.rfile = self.conn.makefile("rb")
        hello = json.loads(self.rfile.readline())
        assert hello.get(bridge.HELLO_KEY) == 1 and hello.get("token") == self.token, hello
        self.conn.sendall(b'{"' + bridge.HELLO_KEY.encode() + b'":1,"ok":true}\n')
        return hello

    def read_event(self, name: str) -> dict:
        while True:
            line = self.rfile.readline()
            assert line, f"等 {name} 事件时 runner 那一侧先关了"
            frame = json.loads(line)
            if frame.get(bridge.EVENT_KEY) == name:
                return frame

    def reset(self) -> None:
        """**只发 RST、不发 FIN** 地关掉连接（abortive close）。"""
        self.conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.rfile.close()
        self.conn.close()

    def close(self) -> None:
        for s in (self.rfile, self.conn, self.listener):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def _spawn(tmp_path, parent: FakeParent, script: str) -> subprocess.Popen:
    target = write(tmp_path / "figure.py", script)
    cmd = [
        bridgekit.USER_PYTHON,
        str(bridge.RUNNER_PY),
        "--target-kind",
        "script",
        "--target",
        str(target),
        "--out-dir",
        str(tmp_path / "out"),
        "--control-host",
        "127.0.0.1",
        "--control-port",
        str(parent.port),
        "--",
    ]
    return subprocess.Popen(  # noqa: S603 — argv 是列表
        cmd,
        cwd=str(tmp_path),
        env=bridgekit.child_env({bridge.TOKEN_ENV: parent.token}),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _finish(proc: subprocess.Popen, stdin_text: str = "") -> tuple[str, str]:
    try:
        return proc.communicate(stdin_text, timeout=120)
    finally:
        if proc.poll() is None:  # pragma: no cover - 只在实现挂在屏障上时走到
            proc.kill()
            proc.wait(timeout=10)


def _assert_script_owned_exit(proc, out, err, *, code: int, marker: str) -> None:
    assert marker in out, f"脚本没跑到收尾那一步: {out!r}\n{err}"
    # 断言的是**结论**：退出码是脚本自己的、stderr 里没有 Tavotto 抛出来的异常。
    # 只判退出码的话，"traceback 打了一屏但碰巧还是 130"也会是绿的。
    assert proc.returncode == code, (
        f"父进程走了之后退出码不是脚本自己的: {proc.returncode}\nstdout={out!r}\nstderr={err!r}"
    )
    assert "Traceback" not in err, f"Tavotto 在用户的进程里抛了异常:\n{err}"
    # "进程自己退了"由 `_finish` 的 communicate 见证：它返回了（没有 TimeoutExpired），
    # 而我们没杀过它。事后再探一次 pid 是恒真的——communicate 已经把它 reap 了。


def test_reset_while_waiting_at_the_script_end_barrier_keeps_the_scripts_exit_code(tmp_path):
    """变体一：runner 已经发出 `barrier` 事件、正要读 / 正在读，父进程 reset。

    这正是 CI 上那三次的栈：`main → barrier("script_end") → readline →
    ConnectionResetError`。
    """
    parent = FakeParent()
    try:
        proc = _spawn(tmp_path, parent, SCRIPT_END.format(gate=False))
        parent.accept_and_handshake()
        ev = parent.read_event("barrier")
        assert ev.get("reason") == "script_end", ev
        parent.reset()
        out, err = _finish(proc)
    finally:
        parent.close()
    _assert_script_owned_exit(proc, out, err, code=130, marker="INTERRUPTED")


def test_reset_before_the_script_end_barrier_keeps_the_scripts_exit_code(tmp_path):
    """变体二：父进程**先**走（Ctrl+C 时 CLI 撤 relay 快过脚本收尾），runner 后进屏障。

    这时 `barrier` 事件本身就写进了一条已经 reset 的连接：`sendall` 抛
    `ECONNRESET` / `EPIPE`，或者写成功、下一个 `recv` 抛——哪一个先碰到取决于
    RST 什么时候被处理，两种都必须归成"父进程走了"。
    """
    parent = FakeParent()
    try:
        proc = _spawn(tmp_path, parent, SCRIPT_END.format(gate=True))
        parent.accept_and_handshake()
        parent.reset()
        out, err = _finish(proc, "go\n")
    finally:
        parent.close()
    _assert_script_owned_exit(proc, out, err, code=130, marker="INTERRUPTED")


def test_reset_at_a_mid_script_barrier_lets_the_script_continue(tmp_path):
    """`plt.show()` 的屏障上父进程 reset：脚本**接着跑完**，退出码是它自己的。

    与 EOF 同一条路径（ADR 0021 §10.2 detach-and-continue）。归错的表现是
    `ConnectionResetError` 从用户的 `plt.show()` 里抛出来——Tavotto 的断线
    变成了用户脚本的崩溃。
    """
    parent = FakeParent()
    try:
        proc = _spawn(tmp_path, parent, MID_SCRIPT)
        parent.accept_and_handshake()
        ev = parent.read_event("barrier")
        assert ev.get("reason") != "script_end", ev
        parent.reset()
        out, err = _finish(proc)
    finally:
        parent.close()
    _assert_script_owned_exit(proc, out, err, code=7, marker="AFTER-SHOW")


def test_the_fake_parent_really_resets_rather_than_closing(tmp_path):
    """**尺子是活的**：`FakeParent.reset()` 让对端读到的是 reset，不是 EOF。

    这条不成立的话，上面三条量的就是早就处理了的 EOF 路径，恒绿。阻塞的
    `recv` 在没有 FIN 的连接上只可能等到 RST，所以这里不需要任何等待。
    """
    parent = FakeParent()
    peer = socket.create_connection(("127.0.0.1", parent.port), timeout=30)
    try:
        parent.conn, _ = parent.listener.accept()
        parent.rfile = parent.conn.makefile("rb")
        parent.reset()
        with pytest.raises(ConnectionError):
            peer.recv(1)
    finally:
        peer.close()
        parent.close()
