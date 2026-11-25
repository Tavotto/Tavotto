"""文档落盘的跨进程互斥与「会不会盖掉别人的内容」判据：桌面后端（`app.py`）与插件（ADR 0208 第 2 期的排版写入口）共用这一份。

搬家之前它们只在 `app.py` 里；插件与桌面是两个进程、写的是同一个 `tavottofile/<名>.json`，各写一份锁或判据，
同一个问题在产品里就有两个答案（「我判没冲突」与「你判没冲突」之间没有互斥）。所以搬进引擎，`app.py` 原样引用。

* **锁带**：64 条「线程锁 + 跨进程文件锁」（`layoutsession.doclock_path(条带)`）。条带下标只取决于文件名
  （NFC + casefold 后的 crc32），不取决于路径怎么拼；**不可重入**。
* **判据** `revision_conflict`：两侧故意不对称（基线 `absent` 而磁盘上有 → 冲突；基线是 hash 而文件被删 → 放行）。
"""

from __future__ import annotations

import threading
import time
import unicodedata
import zlib
from pathlib import Path

from . import layoutsession

#: 文档落盘的「读修订号 → 判冲突 → 写」必须是一个原子段。用**固定条数的锁带**而不是「doc_id → 锁」的表：
#: 锁表要自己治理生命周期，锁带的内存是常数；不同文档偶尔共用一把锁只是多串行一点点。
LOCKS = [threading.Lock() for _ in range(64)]
HELD = threading.local()

#: 缺省等锁上限（秒）。超时抛 `layoutsession.SessionStateUnavailable`，正文一个字节没写。
DEFAULT_TIMEOUT_S = 30.0


class DocumentLock:
    """一个条带的「线程锁 + 跨进程文件锁」。**不可重入**：持锁期间不要再取任何文档锁。"""

    def __init__(self, stripe: int, timeout: float | None = None) -> None:
        self._stripe = stripe
        self._timeout = DEFAULT_TIMEOUT_S if timeout is None else timeout
        self._fd: int | None = None

    def locked(self) -> bool:
        """这个条带此刻有人持着（本进程内；测试与自检用）。"""
        return LOCKS[self._stripe].locked()

    def __enter__(self) -> DocumentLock:
        deadline = time.monotonic() + self._timeout
        thread_lock = LOCKS[self._stripe]
        if not thread_lock.acquire(timeout=self._timeout):
            raise layoutsession.SessionStateUnavailable("槽位被本进程的另一个请求占着，等待超时")
        try:
            self._fd = layoutsession.acquire_file_lock(
                layoutsession.doclock_path(self._stripe), deadline
            )
        except BaseException:
            thread_lock.release()
            raise
        HELD.depth = getattr(HELD, "depth", 0) + 1
        return self

    def __exit__(self, *exc: object) -> None:
        fd, self._fd = self._fd, None
        HELD.depth = getattr(HELD, "depth", 1) - 1
        try:
            if fd is not None:
                layoutsession.release_file_lock(fd)
        finally:
            LOCKS[self._stripe].release()


def holds_lock() -> bool:
    """本线程此刻是否持着任一文档锁条带。"""
    return getattr(HELD, "depth", 0) > 0


def lock_stripe(path: Path) -> int:
    """条带下标只取决于**文件名**（Unicode NFC + casefold），不取决于路径怎么拼：两个进程经符号链接别名 / 大小写不同的写法
    走到同一个槽位时必须选到同一把锁。**不能用 `hash()`**（每个进程盐不同）。"""
    name = unicodedata.normalize("NFC", Path(path).name)
    name = unicodedata.normalize("NFC", name.casefold())
    return zlib.crc32(name.encode("utf-8")) % len(LOCKS)


def document_lock(path: Path, timeout: float | None = None) -> DocumentLock:
    """按**落盘路径**取锁（不按名字：两个不同的名字可能被归一成同一个文件）。"""
    return DocumentLock(lock_stripe(path), timeout)


#: `base_revision` 的哨兵：调用方**读过**，磁盘上当时没有这份文件。
REVISION_ABSENT = "absent"


def revision_conflict(base_revision: str, current: str | None) -> bool:
    """带着这个基线来写，会不会盖掉别人的内容？

    - 基线是 `absent`（我以为没有文件）而磁盘上有 → 冲突。
    - 基线是某个 hash 而磁盘上没有文件（被外部删了）→ **放行**：没有任何内容会因为这次写入而消失。
    - 基线是某个 hash 而磁盘上是另一个 hash → 冲突。
    """
    if base_revision == REVISION_ABSENT:
        return current is not None
    return current is not None and current != base_revision
