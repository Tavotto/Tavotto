"""AI 回滚写的是**用户自己写的 `.py` 源文件**，所以它必须原子、而且写对对象。

判据的主语从头到尾只有一个：**用户那份脚本实体的字节**。两个维度都要量，
少一个就会恒真：

* **原子**——不是「有没有抛异常」。`shutil.copy2` 中途失败照样会抛，而它抛的
  时候用户的脚本已经被截断了。故障注入钉在**目录**上（「任何往项目目录里的
  写入都在写到一半时 ENOSPC」），不钉在某个实现的调用上：`copy2` 走
  `open(script,"wb")`、`atomicio` 走 `open(<同目录 tmp>,"wb")`，两条都必经过它。
* **写对对象**——脚本是符号链接时，「实体」不是那条路径。`copy2` 穿过链接写
  真文件；`os.replace` 换掉链接本身，真文件仍带着 AI 的改动，而**读那条路径
  仍然读得到快照内容**。所以符号链接那条用例断言的是 `real.read_bytes()`，
  不是 `script.read_bytes()`——后者在两种实现下都成立，量它等于没量。
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
    """一个可回滚的会话：脚本在项目里，快照在项目外的另一个目录。

    快照与脚本**不同目录**是真实形态（快照在数据目录的 cache 下），也顺带
    保证故障注入只打到项目这一侧，读快照不受影响。
    """
    project = tmp_path / "project"
    project.mkdir()
    script_path = project / "fig1.py"
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
        "project": str(project.resolve()),
        "snapshot": str(snap),
        "status": "done",
    }
    monkeypatch.setattr(ai_bridge.ai_history, "update_status", lambda *a, **k: None)
    try:
        yield {"sid": sid, "script": script_path, "snap": snap, "project": project}
    finally:
        ai_bridge.SESSIONS.pop(sid, None)


def _relink(session, target: Path) -> Path:
    """把脚本换成一条指向 `target` 的符号链接（AI 改动落在 `target` 上）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(AFTER)
    session["script"].unlink()
    try:
        session["script"].symlink_to(target)
    except OSError as exc:  # Windows 未开开发者模式时建不了
        pytest.skip(f"本机不允许创建符号链接：{exc}")
    return target


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
    _fail_writes_into(monkeypatch, session["project"])

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
    # 项目目录里除了脚本本身什么都不该剩（半个临时文件也算半成品）
    assert sorted(p.name for p in session["project"].iterdir()) == ["fig1.py"]
    # 失败的回滚不许把会话记成已回滚
    assert ai_bridge.SESSIONS[session["sid"]]["status"] == "done"


def test_a_successful_revert_restores_the_snapshot_byte_for_byte(session):
    """成功的回滚逐字节还原，CRLF 与「没有结尾换行」都不许被改写。"""
    assert ai_bridge.revert(session["sid"]) == {"ok": True, "script": "fig1.py"}
    assert session["script"].read_bytes() == BEFORE
    assert ai_bridge.SESSIONS[session["sid"]]["status"] == "reverted"


def test_revert_writes_through_a_symlink_to_the_real_file(session):
    """脚本是符号链接时，回滚要落在**它指向的真文件**上，链接本身留着。

    `os.replace` 不穿链接：不先 realpath 的话，链接被换成一个普通文件，
    真正的源文件仍带着 AI 的改动——而读那条路径**照样**读得到快照内容，
    于是「script.read_bytes() == BEFORE」这种判据在坏实现下也是绿的。
    """
    real = _relink(session, session["project"] / "shared" / "real.py")

    ai_bridge.revert(session["sid"])

    assert real.read_bytes() == BEFORE  # 真文件真的被回滚了
    assert session["script"].is_symlink()  # 链接没有被替换成普通文件
    assert session["script"].resolve() == real.resolve()


def test_revert_refuses_a_symlink_that_escapes_the_project(session):
    """链接指到项目外：拒绝，且项目外那份文件一个字节都没变。

    先 realpath 就意味着链接可以把写入带出项目——`copy2` 当年正是这么写的。
    落地之后必须重判边界，判据与 `app.py` 试运行端点的
    `script_path_outside_project` 是同一条。
    """
    outside = _relink(session, session["project"].parent / "outside" / "real.py")

    with pytest.raises(ai_bridge.AgentError) as caught:
        ai_bridge.revert(session["sid"])

    assert outside.read_bytes() == AFTER  # 主语：项目外那份文件的字节
    assert caught.value.code == "script_path_outside_project"
    assert session["script"].is_symlink()
    assert ai_bridge.SESSIONS[session["sid"]]["status"] == "done"


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


def test_an_old_sidecar_without_a_project_field_still_reverts(session):
    """升级前写的 sidecar 没有 `project` 字段，回滚仍要成立（从 script_path 反推）。"""
    del ai_bridge.SESSIONS[session["sid"]]["project"]

    ai_bridge.revert(session["sid"])

    assert session["script"].read_bytes() == BEFORE
