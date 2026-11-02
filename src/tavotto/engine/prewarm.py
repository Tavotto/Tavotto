"""项目打开后的后台预热：把「起渲染会话」的冷启动挪到用户点图之前（ADR 0159）。

预热 = 只**取会话**（`app._engine_worker`，与 `/api/engine/render` 开头同一扇门）：起解释器、
开 workerd/Python 会话，**不 build**（不跑用户脚本）、不出图、不写项目目录、不碰注册表、
不发任何 SSE 事件、不进写回路径。用户在预热完成前真去渲染这张图时，`pool.acquire()` 在池锁上
排队、拿到的是同一条会话——不会再起第二个。

本模块只管**节流与去重**，不 import app / Flask；真正取会话的闭包由 `app.py` 传进来：

* 同一个 (项目, 面板) 本进程只预热一次（失败也算一次，不重试）；
* 同时最多一路预热；
* 前台渲染正在取会话时不开始（`foreground()`），开始前再查一次；
* 池里已活着 `cap` 个会话时不预热（给前台留位置，不触发 LRU 淘汰）；
* 任何失败只记日志，绝不抛、绝不弹。

纯标准库。
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Iterator

LOG = logging.getLogger("tavotto.prewarm")

STARTED = "started"
ALREADY = "already"
BUSY = "busy"
FOREGROUND = "foreground_busy"
POOL_FULL = "pool_full"


class Prewarmer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempted: set[tuple] = set()
        self._running = False
        self._foreground = 0
        self._thread: threading.Thread | None = None

    @contextlib.contextmanager
    def foreground(self) -> Iterator[None]:
        """用户请求的渲染正在取会话：预热让路（计数，可嵌套 / 并发）。"""
        with self._lock:
            self._foreground += 1
        try:
            yield
        finally:
            with self._lock:
                self._foreground -= 1

    def request(self, key: tuple, work: Callable[[], object], *, alive: int, cap: int) -> str:
        """尝试登记一次预热；返回上面的状态词。`work` 在后台线程里跑，异常只记日志。"""
        with self._lock:
            if key in self._attempted:
                return ALREADY
            if self._running:
                return BUSY
            if self._foreground:
                return FOREGROUND
            if alive >= cap:
                return POOL_FULL
            self._attempted.add(key)
            self._running = True
            thread = threading.Thread(
                target=self._run, args=(key, work), daemon=True, name="tavotto-prewarm"
            )
            self._thread = thread
        thread.start()
        return STARTED

    def _run(self, key: tuple, work: Callable[[], object]) -> None:
        try:
            with self._lock:
                if self._foreground:
                    # 登记之后前台抢先了：不抢，也不记「已预热」——下次还有机会
                    self._attempted.discard(key)
                    return
            work()
        except BaseException:  # noqa: BLE001 —— 预热是旁路：失败静默，只留日志
            LOG.info("后台预热失败（已忽略）", exc_info=True)
        finally:
            with self._lock:
                self._running = False

    def join(self, timeout: float = 5.0) -> None:
        """测试用：等当前这一路结束。"""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def reset(self) -> None:
        """测试用：忘掉「已预热」记录。"""
        with self._lock:
            self._attempted.clear()


PREWARM = Prewarmer()
