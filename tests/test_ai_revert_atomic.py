"""AI 回滚写的是**用户自己写的 `.py` 源文件**，所以它必须是原子的。

判据的主语从头到尾只有一个：**磁盘上那份用户脚本的字节**。不是「有没有抛
异常」——`shutil.copy2` 中途失败照样会抛，而它抛的时候用户的脚本已经被截断了。
断言只问一件事：回滚失败之后，那份脚本是不是**一个字节都没变**。

故障注入选在「往图库目录里写文件」这件事上，而不是钉在某一个实现的调用上：
`copy2` 走 `open(script, "wb")`，`atomicio.write_bytes` 走
`open(<同目录临时文件>, "wb")`，两条路都要经过它。这样这批用例对两种实现
**同样看得见**——换回 `copy2` 时它们必须红（见 PR 正文的反证记录）。
"""

from __future__ import annotations

import builtins
import errno
import os
import stat
import sys
from pathlib import Path

import pytest

from tavotto.engine import ai_bridge, atomicio

#: 快照里那份「AI 改之前」的脚本。**故意带 CRLF 与不带结尾换行**：回滚必须
#: 逐字节还原，任何一次文本模式往返都会把 `\r\n` 变成 `\n`（Windows 上写文本
#: 模式破坏完整性是这个仓库栽过的坑），而换行数量变了 size 也就变了。
BEFORE = (
    b"import matplotlib.pyplot as plt\r\n\r\nplt.plot([1, 2], [3, 4])\r\nplt.title('\xe5\x9b\xbe')"
)
#: AI 改完之后磁盘上那份。长度与 BEFORE 不同，好让 size 这把尺子也用得上。
AFTER = b"import matplotlib.pyplot as plt\nplt.plot([9], [9])\n"


@pytest.fixture
def session(tmp_path, monkeypatch):
    """一个可回滚的会话：脚本在 figures 目录，快照在另一个目录。

    快照与脚本**不同目录**是真实形态（快照在数据目录的 cache 下），也顺带
    保证故障注入只打到图库这一侧，读快照不受影响。
    """
    figures = tmp_path / "figures"
    figures.mkdir()
    script_path = figures / "fig1.py"
    script_path.write_bytes(AFTER)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    snap = snap_dir / "abc123__fig1.py"
    snap.write_bytes(BEFORE)

    sid = "abc123"
    ai_bridge.SESSIONS[sid] = {
        "id": sid,
        "script": "fig1.py",
        "script_path": str(script_path),
        "snapshot": str(snap),
        "status": "done",
    }
    monkeypatch.setattr(ai_bridge.ai_history, "update_status", lambda *a, **k: None)
    try:
        yield {"sid": sid, "script": script_path, "snap": snap, "figures": figures}
    finally:
        ai_bridge.SESSIONS.pop(sid, None)


class _FullDisk:
    """写到一半就撞上 ENOSPC 的文件对象（磁盘满 / 写一半崩的形态）。"""

    def __init__(self, real):
        self._real = real

    def write(self, data):
        self._real.write(data[: len(data) // 2])  # 半份真的落到磁盘上
        self._real.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._real.close()
        return False


def _fail_writes_into(monkeypatch, directory: Path) -> None:
    """让**任何**往 `directory` 里的写入在写到一半时失败。

    钉的是目录而不是文件名：`atomicio` 的临时文件名带 pid 与进程内序号，
    按名字挑就等于给新实现放行，那样的判据在旧实现上也不会红。
    """
    real_open = builtins.open

    def _open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        try:
            same_dir = Path(file).parent == directory
        except TypeError:  # 文件描述符等非路径入参
            return handle
        if same_dir and ("w" in mode or "a" in mode or "+" in mode):
            return _FullDisk(handle)
        return handle

    monkeypatch.setattr(builtins, "open", _open)


def test_a_failed_revert_leaves_the_user_script_byte_for_byte_intact(session, monkeypatch):
    """写到一半失败：用户的 `.py` 一个字节都没变，目录里也没留下半成品。"""
    _fail_writes_into(monkeypatch, session["figures"])

    try:
        ai_bridge.revert(session["sid"])
    except OSError as exc:
        caught: OSError | None = exc
    else:
        caught = None

    # **这条断言排在最前面是有意的**：判据的主语是磁盘上那份用户脚本，不是
    # 「抛没抛异常」。换回 `copy2` 时第一条红的必须是它，而不是「没抛异常」。
    assert session["script"].read_bytes() == AFTER
    # 失败要说得出是哪一类（ADR 0023：结构化错误，不是一个 OSError 字符串）
    assert isinstance(caught, atomicio.AtomicWriteError)
    assert caught.code == "write_failed"
    # 图库目录里除了脚本本身什么都不该剩（半个临时文件也算半成品）
    assert sorted(p.name for p in session["figures"].iterdir()) == ["fig1.py"]
    # 失败的回滚不许把会话记成已回滚
    assert ai_bridge.SESSIONS[session["sid"]]["status"] == "done"


def test_a_successful_revert_restores_the_snapshot_byte_for_byte(session):
    """成功的回滚逐字节还原，CRLF 与「没有结尾换行」都不许被改写。"""
    assert ai_bridge.revert(session["sid"]) == {"ok": True, "script": "fig1.py"}
    assert session["script"].read_bytes() == BEFORE
    assert ai_bridge.SESSIONS[session["sid"]]["status"] == "reverted"


def test_revert_leaves_a_newer_mtime_so_the_watcher_can_see_it(session):
    """回滚之后的 mtime 必须比回滚之前**新**。

    watcher 的判据是 `(size, mtime_ns)`（ADR 0026 §2a）。`copy2` 会把 mtime
    一并倒回 AI 改之前，于是「回滚」有可能对 watcher 完全隐形——而
    `revert()` 的文档第一句承诺的正是 watcher 会作废渲染会话。
    """
    # 两个时间戳都写死成明显的过去，中间隔 10 秒：这样判据不依赖文件系统的
    # 时间戳精度（FAT32 / 部分网络盘只到 1~2 秒，靠「刚刚写的比刚才新」会偶发）。
    snap_ns = 1_600_000_000_000_000_000
    script_ns = snap_ns + 10_000_000_000
    os.utime(session["snap"], ns=(snap_ns, snap_ns))
    os.utime(session["script"], ns=(script_ns, script_ns))

    ai_bridge.revert(session["sid"])

    after_ns = session["script"].stat().st_mtime_ns
    assert after_ns != snap_ns  # 没把 mtime 一并倒回 AI 改之前
    assert after_ns > script_ns  # 落盘时间取「现在」，watcher 的签名必然变了


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 上 chmod 只有只读位")
def test_revert_restores_the_permission_bits_from_the_snapshot(session):
    """权限位取自快照（= AI 改之前那份）。

    `os.replace` 换上来的是临时文件的 inode，它的模式来自 umask；不还原的话
    用户脚本上的可执行位/私有位会在一次回滚里被悄悄改掉。
    """
    os.chmod(session["snap"], 0o750)
    os.chmod(session["script"], 0o644)

    ai_bridge.revert(session["sid"])

    assert stat.S_IMODE(session["script"].stat().st_mode) == 0o750
