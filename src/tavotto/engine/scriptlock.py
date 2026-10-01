"""改用户脚本：按脚本的锁与唯一的写入口（ADR 0110 §五）。

本机服务并发处理请求，「判一次再写」的每一段都得在同一把锁里做完，否则判过的结论在写的那一刻已经
不成立（Codex 评 #730 两条 P1：Agent 起在改写的快照之后；回滚的「是不是 AI 那一版」判在改写落地之前）。
锁按脚本的真实路径（realpath + normcase）分，同一线程可重入。

`script_guard` 的持有者——**全部**调用方（`tests/test_script_lock.py` 从源码里枚举出来与这张表对账；
新增写脚本的入口先加到这里）：

* `ai_bridge.run`：从「脚本在不在」到快照、起 Agent 进程、登记进 `SESSIONS`。之后由 Agent 进程自己写，
  它 `running` 期间改写 / 复原在锁里判 `script_busy` 让路。
* `ai_bridge.revert`：「是不是 AI 改完的那一版」的判定到写回快照。
* `scriptbackup.replace`：校验和 → 两处备份 → 替换（改写与复原共用）。
* `app.api_script_edit_commit`：锁内复判 `script_busy` → 重算 → `scriptbackup.replace`。
* `app.api_script_backups_restore`：锁内复判 `script_busy` → 算复原字节 → `scriptbackup.replace`。

写脚本字节只经 `write_script()`：没持有这份脚本的锁就抛——门禁落在写的那一刻，不靠调用方记得。
纯标准库 + `atomicio`；Flask 父进程 import。
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

from . import atomicio

_LOCKS: dict[str, threading.RLock] = {}
_OWNERS: dict[str, list[int]] = {}  # key -> 持有线程的 ident（可重入：同一线程可叠几层）
_GUARD = threading.Lock()


def script_key(script_path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(script_path))


@contextlib.contextmanager
def script_guard(script_path: str | os.PathLike):
    """持有这份脚本的锁（同一真实路径同一把，同一线程可重入）。持有者见模块说明。"""
    key = script_key(script_path)
    with _GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        _OWNERS.setdefault(key, []).append(threading.get_ident())
        try:
            yield
        finally:
            _OWNERS[key].pop()


def held(script_path: str | os.PathLike) -> bool:
    """当前线程是不是持有这份脚本的锁。"""
    return threading.get_ident() in _OWNERS.get(script_key(script_path), ())


def write_script(script_path: str | os.PathLike, data: bytes, *, mode: int | None = None) -> None:
    """原子地写用户脚本（`atomicio.write_bytes`）；**必须**在这份脚本的 `script_guard` 里调。"""
    if not held(script_path):
        raise RuntimeError(f"写用户脚本必须持有它的 script_guard：{Path(script_path).name}")
    atomicio.write_bytes(Path(script_path), data, mode=mode)
