"""原子落盘 —— Flask 父进程一侧**唯一**的文档类写入实现。

起始状态（2026-08-29）是九处各自手写的 `tmp + replace`：`app.py` 里四处、
`config.py` / `runspec.py` / `runtimeasset.py` / `locate.py` / `session_client.py` /
`nativehandoff.py` 各一处，而**最显眼的那次保存**（`POST /api/layouts/<name>`，
用户的「另存为」）根本没有 tmp，是直接 `write_text` 盖上去。九份实现里没有一份
同时做到：fsync、失败时清掉临时文件、返回结构化错误。

九份不是「重复代码」这么简单——它们的**行为不一样**，于是"保存是不是原子的"
这个问题在本仓库没有唯一答案。这个模块就是那个答案。

写入序列（对应 Prompt 02 §七）：

1. 同目录临时文件（跨设备 rename 不是原子的，必须同目录）；
2. 写入并 `flush`；
3. `os.fsync` 文件——`os.replace` 只保证「要么旧要么新」，不保证新内容已经
   离开页缓存；掉电时没有这一步会 replace 出一个**空文件**；
4. `os.replace` 原子替换；
5. 目录 fsync（POSIX 上让「这个名字现在指向新 inode」本身落盘；Windows 上
   打不开目录，忽略）；
6. 任何一步失败都清掉临时文件，抛结构化错误。

**非有限数在序列化那一步就被挡下。** `json.dumps` 默认 `allow_nan=True`，
会写出 `NaN` / `Infinity` 这三个**不是 JSON 的**字面量：Python 自己读得回来，
浏览器的 `JSON.parse` 读不动。落进 `_autosave/<doc>.json` 的后果是这份文档在
前端表现为"读不出来"，静默退回本机兜底副本——用户看到的是"我的改动没了"，
而磁盘上那份文件看起来好端端的。所以判据放在写入边界上，**响亮地失败**，
而不是写一份没人能读的文件。（同一条规则 `patchspec.py` 已经在补丁线格式上
用了很久：规范化剔非有限值 + `allow_nan=False` 兜底，Rust 侧 `pyfloat.rs` 同款。）
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

#: 同一进程内并发写同一个路径时临时文件不能重名（外面的锁只保证同一份数据
#: 的顺序，不保证不同路径/不同调用点之间互斥）。
_SEQ_LOCK = threading.Lock()
_seq = 0


class AtomicWriteError(OSError):
    """写入失败的结构化形态。

    `code` 是给 HTTP 层直接映射用的稳定枚举，**不要去解析 message**
    （它是给人看的，措辞会变）。
    """

    def __init__(self, code: str, message: str, path: Path) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path

    def as_payload(self) -> dict[str, str]:
        """给 `jsonify` 的载荷（不含路径——路径是本机信息，不进给前端的错误体）。"""
        return {"error": self.message, "code": self.code}


def _next_tmp(path: Path) -> Path:
    global _seq
    with _SEQ_LOCK:
        _seq += 1
        n = _seq
    return path.with_name(f"{path.name}.{os.getpid()}.{n}.tmp")


#: `_next_tmp` 起的名字：`<目标文件名>.<pid>.<序号>.tmp`。回收只认这一种形状，
#: 别人放在同一目录里的 `*.tmp` 不归这里管。
_TMP_NAME = re.compile(r"^.+\.(?P<pid>\d+)\.\d+\.tmp$")
#: 比这个年轻的临时文件一律不碰：一次原子写从建 tmp 到 replace 是毫秒级，一小时
#: 还没收尾的只可能是写它的进程已经死在半路（QA 2026-09-24 SCI-05-B2）。
ORPHAN_TMP_MIN_AGE_S = 3600
#: 过了这个年龄不再问 pid 死活：pid 会被复用，Windows 上也量不了存活。
ORPHAN_TMP_MAX_AGE_S = 24 * 3600


def _pid_alive(pid: int) -> bool | None:
    """`True` / `False`；量不了回 None（Windows 上 `os.kill(pid, 0)` 不是探测）。"""
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def reap_orphan_tmps(directory: Path, *, now: float | None = None) -> list[str]:
    """回收 `directory` 里被杀在 `os.replace` 之前的进程留下的临时文件，返回删掉的名字。

    `write_bytes` 的失败路径会清 tmp，但进程在第 1 步与第 4 步之间被杀（SIGKILL /
    断电 / 强退）时没有任何代码有机会跑，`<名字>.<pid>.<n>.tmp` 就永久留在盘上。
    判据两条腿：**年龄**（`ORPHAN_TMP_MIN_AGE_S` 之内一律不碰，正在写的那份
    不可能活这么久）+ **pid 已死**；过了 `ORPHAN_TMP_MAX_AGE_S` 只看年龄。
    尽力而为：读不动 / 删不掉都跳过，绝不让调用方因此失败。
    """
    now = time.time() if now is None else now
    removed: list[str] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return removed
    for entry in entries:
        match = _TMP_NAME.match(entry.name)
        if match is None:
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            age = now - entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        if age < ORPHAN_TMP_MIN_AGE_S:
            continue
        if age < ORPHAN_TMP_MAX_AGE_S and _pid_alive(int(match["pid"])) is not False:
            continue
        try:
            os.unlink(entry.path)
        except OSError:
            continue
        removed.append(entry.name)
    return removed


def _discard(tmp: Path) -> None:
    """清掉半成品。失败只可能是权限/竞态，此时抛出去会掩盖真正的错误。"""
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


#: 「这个平台/文件系统没有目录 fsync 这一步」的 errno。**只有这一类可以忽略。**
#: 有些文件系统（部分网络盘、旧的 FAT 家族）对目录 fd 的 fsync 直接回
#: EINVAL/ENOTSUP —— 那是「这一步在这里不存在」，不是「这一步失败了」。
_DIR_FSYNC_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}


def _fsync_dir(directory: Path) -> None:
    """目录项落盘。

    两个 `except` 挡的**不是同一件事**，所以处置也不同：

    * `os.open` 失败 —— Windows 上 `O_RDONLY` 打开一个目录直接报错，那里
      根本没有这一步，忽略；
    * `os.fsync` 失败 —— 真正的 I/O 错误意味着「这个名字现在指向新 inode」
      这件事**可能撑不过一次掉电**。以前这里一并 `pass` 掉了：调用方于是
      收到一个成功，而前端拿到成功就会把本机兜底副本删掉——用户手上从此
      只剩这一份可能没落盘的文件。ADR 0023 与 `src/tavotto/AGENTS.md` 写的
      是「失败清 tmp + 抛 `AtomicWriteError`」，这里要照做。

    注意此刻 `os.replace` **已经成功**：文件内容对任何读者都已经可见，抛出去
    是在说「落得不够牢」，不是「没写进去」。宁可让用户看到一次可重试的失败，
    也不要在一份可能丢的文件上说"已保存"（共享规则 §2 的第一条优先级）。
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in _DIR_FSYNC_UNSUPPORTED:
            raise AtomicWriteError("dir_fsync_failed", f"目录项落盘失败：{exc}", directory) from exc
    finally:
        os.close(fd)


def fsync_file(path: Path) -> None:
    """把一个**已经写好**的文件内容落盘，不做 replace。失败原样抛 `OSError`。

    给「replace 由调用方自己做」的事务用（原图写回，issue #252）：调用方要在
    **碰任何目标之前**确认所有临时文件都落了盘，失败时才能干净地整体放弃。
    可写方式打开的理由同 `publish_file`：Windows 的 `os.fsync()` 只接受可写句柄。
    """
    with open(path, "rb+") as handle:
        os.fsync(handle.fileno())


def fsync_dir(directory: Path) -> None:
    """目录项落盘，给「落盘失败就整体放弃」的事务用（原图写回，issue #252）。

    与 `_fsync_dir` 的区别只在打开目录失败时：那里一律当「这个平台没有目录
    fsync」忽略；这里只在 Windows（确实打不开目录）忽略，POSIX 上打不开
    （不可读的目录、EIO……）原样抛 `OSError`——否则调用方以为名字已落盘，
    接着就去替换原图。fsync 本身失败照旧抛 `AtomicWriteError`（`OSError` 子类）。
    """
    if os.name == "nt":
        _fsync_dir(Path(directory))
        return
    # 同一个描述符打开即 fsync：不先「验证能打开」再交给会吞掉打开失败的
    # `_fsync_dir` 二次打开——两次打开之间的瞬时 EIO / 改名会让它静默返回。
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in _DIR_FSYNC_UNSUPPORTED:
            raise AtomicWriteError("dir_fsync_failed", f"目录项落盘失败：{exc}", directory) from exc
    finally:
        os.close(fd)


def dumps_json(obj: Any, *, indent: int | None = None) -> bytes:
    """序列化成 **RFC 8259 合法**的 JSON 字节。

    `allow_nan=False` 让 NaN / Infinity 在这里就抛 `ValueError`，
    而不是被写成非标准字面量交给下游。
    """
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=indent, allow_nan=False)
    except ValueError as exc:
        raise AtomicWriteError(
            "non_finite_number",
            f"文档里含有无法保存的数值（NaN 或 ∞）：{exc}",
            Path("."),
        ) from exc
    return text.encode("utf-8")


def write_bytes(path: Path, data: bytes) -> None:
    """原子写字节。见模块文档的六步序列。"""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AtomicWriteError("mkdir_failed", f"无法创建目录：{exc}", path) from exc

    tmp = _next_tmp(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        _discard(tmp)
        raise AtomicWriteError("write_failed", f"写入失败：{exc}", path) from exc

    try:
        os.replace(tmp, path)
    except OSError as exc:
        _discard(tmp)
        raise AtomicWriteError("replace_failed", f"替换失败：{exc}", path) from exc

    _fsync_dir(path.parent)


def publish_file(tmp: Path, dest: Path) -> None:
    """把一个**已经写好**的临时文件原子地放到最终位置。

    `write_bytes` 管的是"内容在内存里"的情况；导出不是——PDF/PNG 是渲染后端
    直接 `save()` 到一个路径上的，字节从来没经过我们的手。那条路径上仍然要有
    同一份纪律：先 fsync 文件本身（`os.replace` 只保证"要么旧要么新"，不保证
    新内容已经离开页缓存），再 replace，再 fsync 目录。

    临时文件必须与目标**同一个目录**（跨设备 rename 不是原子的）。失败时临时
    文件被清掉——导出目录里绝不留半个文件（共享规则 §8：输出失败不得留下
    半文件）。
    """
    tmp = Path(tmp)
    dest = Path(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _discard(tmp)
        raise AtomicWriteError("mkdir_failed", f"无法创建目录：{exc}", dest) from exc
    # **必须以可写方式打开。** Windows 上 `os.fsync()` 走 MSVCRT 的 `_commit()`，
    # 而它只接受**可写**句柄：对一个 `O_RDONLY` 的 fd 调用直接回
    # `[Errno 9] Bad file descriptor`。POSIX 上只读 fd 照样 fsync 得了，
    # 所以这条缺陷在 mac/Linux 上**一次都不会现形**——它是在合并队列的
    # Windows 那条腿上第一次被看见的，形态是**每一次导出全失败**
    # （70 条用例连带红，打包版冒烟里 `POST /api/export` 直接 500）。
    # `write_bytes()` 没撞上它，是因为那边 fsync 的是 `open(tmp,"wb")` 的可写 fd。
    try:
        handle = open(tmp, "rb+")
    except OSError as exc:
        _discard(tmp)
        raise AtomicWriteError("write_failed", f"临时文件读不出来：{exc}", dest) from exc
    try:
        os.fsync(handle.fileno())
    except OSError as exc:
        handle.close()
        _discard(tmp)
        raise AtomicWriteError("write_failed", f"临时文件落盘失败：{exc}", dest) from exc
    else:
        handle.close()
    try:
        os.replace(tmp, dest)
    except OSError as exc:
        _discard(tmp)
        raise AtomicWriteError("replace_failed", f"替换失败：{exc}", dest) from exc
    _fsync_dir(dest.parent)


def write_json(path: Path, obj: Any, *, indent: int | None = None) -> None:
    """原子写 JSON。序列化在**碰磁盘之前**完成——载荷有问题时原文件一字未动。"""
    write_bytes(Path(path), dumps_json(obj, indent=indent))


def revision_of(data: bytes) -> str:
    """这段字节的修订号。**hash 只有这一个实现**——路径版与字节版分头写一遍，
    迟早会有一处改了另一处没改，而那时两个修订号会静默地对不上。"""
    return hashlib.sha256(data).hexdigest()[:32]


def content_revision(path: Path) -> str | None:
    """文件的内容修订号；文件不存在或读不出来返回 `None`。

    **只取内容 hash，不掺 mtime。** 修订号回答的是"内容变了没有"，而 mtime
    回答的是"文件被动过没有"——把 mtime 拌进去，一次 `touch`、一次
    从备份原样恢复、一次跨机器拷贝都会变出一个新修订号，于是外部修改检测
    （Prompt 03）会对着一份**逐字节相同**的文件报冲突。要"被动过没有"就单独
    去 `stat`，别把两个维度揉成一个数。
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return revision_of(data)
