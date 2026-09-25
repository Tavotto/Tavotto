"""子进程 stderr 的并发排空（测试夹具用）。

夹具起一个常驻子进程（worker、MCP server），按行协议读它的 **stdout**；stderr 若开成
`PIPE` 却只在失败时才 `read()`，那不是排空——子进程往 stderr 写满管道缓冲（macOS / Linux
64 KiB，Windows 小得多）之后就阻塞在写日志上，stdout 的应答永远不来，用例报「超时」，
症状指向完全错误的方向（#549：缺字体的渲染往 stderr 刷了 25 KB 的 findfont 警告，只在
Windows 上卡死 180 秒）。

`StderrDrain(proc)` 在 `Popen` 之后立刻起一条读线程，把 stderr 持续读进内存（只留尾部，
有上限），失败信息用 `tail()` 取。`scripts/` 下的启动器另有更硬的判据（根本不许开 PIPE，
`tests/test_source_hygiene.py::test_no_launcher_leaves_a_child_pipe_undrained`）。
"""

from __future__ import annotations

import threading

#: 内存里最多留这么多（字符 / 字节）：只为失败时附在断言信息里
KEEP = 64 * 1024


class StderrDrain:
    def __init__(self, proc) -> None:
        # 文本模式的流也从底下那层二进制缓冲按块读（`read1` 有多少回多少）：文本层的
        # `read(n)` 会攒够 n 个字符才返回，子进程还活着时最后那一截就一直看不见
        self._stream = getattr(proc.stderr, "buffer", proc.stderr)
        self._chunks: list[bytes] = []
        self._size = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            while True:
                chunk = self._stream.read1(4096)
                if not chunk:
                    return
                with self._lock:
                    self._chunks.append(chunk)
                    self._size += len(chunk)
                    while self._size > KEEP and len(self._chunks) > 1:
                        self._size -= len(self._chunks.pop(0))
        except (OSError, ValueError):  # 管道被关掉：夹具收摊了
            return

    def join(self, timeout: float = 10.0) -> None:
        """等读线程读到 EOF（子进程已退出时）——关管道之前先让它收尾。"""
        self._thread.join(timeout)

    def tail(self, n: int = 4000, wait: float = 0.0) -> str:
        """stderr 最后 `n` 个字符（按 utf-8 宽容解码）。`wait` 给读线程一点时间追上
        子进程刚写的那一截（子进程已退出时它读到 EOF 就结束）。"""
        if wait:
            self._thread.join(wait)
        with self._lock:
            data = b"".join(self._chunks)
        return data.decode("utf-8", "replace")[-n:]
