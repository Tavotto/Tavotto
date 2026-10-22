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
* `ai_figure_edit.commit`（TAF 改图，ADR 0138 Phase 3）：锁内核项目里的脚本仍是起跑时那一版 → `write_script` 写回
  候选；会话此刻仍是 `running`，别的改写 / 复原照旧按 `script_busy` 让路。
* `ai_data_task.commit`（TAF 数据出图，ADR 0138 Phase 5b）：项目锁内核 stem 未被占用 → `create_script` **新建**模板脚本
  （已存在就失败，永不覆盖）→ 登记。
* `ai_convert.run_validation`（生成可编辑版本，ADR 0143）：紧挨原脚本 `create_script` **新建**临时候选
  `tavotto_convert_probe_<sid>.py`（O_EXCL）→ 带写入根绊线跑一次 → 删掉；原脚本不碰。
* `ai_convert.adopt`（同上）：持**原脚本**的锁，锁内调 `_adopt_locked`——先核原脚本字节仍是起跑基线，再 `create_script`
  **新建**兄弟脚本；锁序固定：原脚本在外、目标兄弟脚本在内，两步之间别处的编辑被锁挡住。
* `ai_convert._adopt_locked`（`adopt` 的锁内部分）：逐个候选名持**目标兄弟脚本**的锁 `create_script`
  （`<名>.py` / `<名>_2.py` …，已存在换下一个号，永不覆盖）。

写脚本字节只经 `write_script()` / `create_script()`：没持有这份脚本的锁就抛——门禁落在写的那一刻，不靠调用方记得。
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


def create_script(script_path: str | os.PathLike, data: bytes) -> None:
    """**新建**一份用户脚本：目标已存在（文件、目录、链接）就抛 `FileExistsError`，永不覆盖；**必须**在它的 `script_guard` 里调。

    `O_CREAT | O_EXCL` 由文件系统原子判定「不存在才建」（并发的两个新建只有一个成功，链接不会被跟随）。写到一半失败就删掉
    这份**刚由自己建出来的**文件，不留半截。"""
    if not held(script_path):
        raise RuntimeError(f"写用户脚本必须持有它的 script_guard：{Path(script_path).name}")
    path = Path(script_path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        atomicio.fsync_dir(path.parent)  # 名字本身落盘；失败同样整体放弃
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
