"""`support/pipedrain.StderrDrain` 真的在与子进程并发排空 stderr（#549 第十三轮）。

对照组先证明尺子是活的：同一个子进程、不排空时 stdout 那一行确实等不到（它卡在写 stderr
上）；排空之后照常到达，失败信息要用的尾部也拿得到。
"""

from __future__ import annotations

import subprocess
import sys
import threading

from support.pipedrain import StderrDrain

#: 先往 stderr 写 300 KB（远超任何平台的管道缓冲），再往 stdout 写一行
CHILD = (
    "import sys\n"
    "sys.stderr.write('e' * 300_000 + 'END-OF-STDERR\\n'); sys.stderr.flush()\n"
    "print('ready', flush=True)\n"
    "sys.stdin.readline()\n"
)


def _spawn():
    return subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _readline_within(proc, seconds: float) -> str | None:
    box: list = []
    t = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
    t.start()
    t.join(seconds)
    return box[0] if box else None


def _finish(proc) -> None:
    # 不用 communicate()：排空线程还在读 stderr，两边抢同一条流
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)
    for stream in (proc.stdin, proc.stdout):
        stream.close()


def test_without_draining_the_child_is_stuck_writing_stderr():
    """对照组：stderr 开成 PIPE 却不读，stdout 那一行等不到。"""
    proc = _spawn()
    try:
        assert _readline_within(proc, 3) is None
    finally:
        _finish(proc)


def test_the_drain_lets_the_stdout_protocol_through_and_keeps_the_tail():
    proc = _spawn()
    drain = StderrDrain(proc)
    try:
        assert _readline_within(proc, 30) == "ready\n"
        assert drain.tail(200, wait=0.5).rstrip().endswith("END-OF-STDERR")
    finally:
        _finish(proc)
