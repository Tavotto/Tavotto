"""safe worker：脚本写项目里**已有的**文件被系统拒绝时，这一笔改写进本次运行的覆盖层，图照常出（ADR 0127 修订 2026-11-28）。

现场：脚本先画完图，再把画图数据另存进项目里一份已有的 CSV；这份 CSV 正被别的程序（Excel）独占打开着，Windows 拒绝写入
（`[Errno 13] Permission denied`）——以前整次运行失败、图出不来、画布上的图没法编辑。现在：项目里那份一个字节不动，这次运行
照常走完，`locked_writes` 点名这条路径，界面一枚胶囊说清楚「这次没写进去」。

用只读文件（`chmod 0444`；Windows 上是只读属性）复现「打开写入被拒」：与独占锁同一个异常、同一个打开时刻。
没有覆盖层的模式、不是普通文件、父文件夹本身不可写的，一律保持原来的失败。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tavotto.engine import pool as engine_pool, workdir

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

pytestmark = [
    pytest.mark.skipif(
        WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
    ),
    pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0, reason="root 无视只读权限，复现不了被拒"
    ),
]

HDR = """\
import os, json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
here = Path(__file__).resolve().parent
OUT = Path({out!r})
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
"""


def _readonly(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def _writable(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    locked = root / "plotted.csv"
    locked.write_text("old\n", encoding="utf-8")
    _readonly(locked)
    out = tmp_path / "observed.json"
    yield root, out
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)
    for p in root.rglob("*"):
        _writable(p)
    _writable(root)


def _snapshot(root: Path) -> dict:
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.name == "script.py" or "__pycache__" in p.parts:
            continue
        st = p.stat()
        snap[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return snap


def _build(world, body: str):
    root, out = world
    (root / "script.py").write_text(HDR.format(out=str(out)) + body, encoding="utf-8")
    worker, resp = engine_pool.build("script.py", str(root), "__main__")
    return worker, resp


def _ok(world, body: str):
    root, out = world
    before = _snapshot(root)
    worker, resp = _build(world, body)
    assert _snapshot(root) == before, "用户项目被改动了"
    observed = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return worker, resp, observed


WRITES = {
    "open_w": 'with open(here / "plotted.csv", "w", encoding="utf-8") as f:\n    f.write("new\\n")\n',
    "pathlib_write_bytes": '(here / "plotted.csv").write_bytes(b"new\\n")\n',
    "os_open_trunc": (
        'fd = os.open(here / "plotted.csv", os.O_WRONLY | os.O_TRUNC)\n'
        'os.write(fd, b"new\\n")\nos.close(fd)\n'
    ),
}


@pytest.mark.parametrize("how", sorted(WRITES))
def test_a_denied_write_to_an_existing_file_still_renders_and_names_the_file(world, how):
    body = (
        WRITES[how]
        + 'OUT.write_text(json.dumps({"back": (here / "plotted.csv").read_text(encoding="utf-8")}))\n'
    )
    worker, resp, observed = _ok(world, body)
    assert resp.get("stems"), resp
    assert resp.get("locked_writes") == ["plotted.csv"]
    assert engine_pool.locked_writes_of_build(worker) == ["plotted.csv"]
    # 本次运行里读回看到的是脚本刚写的（与它以为写成功的世界一致）；项目里那份没动（_ok 逐字节核过）
    assert observed == {"back": "new\n"}


def test_append_keeps_the_original_content_in_the_run(world):
    body = (
        'with open(here / "plotted.csv", "a", encoding="utf-8") as f:\n    f.write("more\\n")\n'
        'OUT.write_text(json.dumps({"back": (here / "plotted.csv").read_text(encoding="utf-8")}))\n'
    )
    _worker, resp, observed = _ok(world, body)
    assert resp.get("locked_writes") == ["plotted.csv"]
    assert observed == {"back": "old\nmore\n"}


def test_the_next_run_starts_clean_and_reports_nothing_once_the_file_is_free(world):
    root, _out = world
    body = WRITES["open_w"]
    worker, resp, _observed = _ok(world, body)
    assert resp.get("locked_writes") == ["plotted.csv"]
    _writable(root / "plotted.csv")
    engine_pool.shutdown_all(str(root), wait=True)
    worker, resp = _build(world, body)
    assert "locked_writes" not in resp
    assert engine_pool.locked_writes_of_build(worker) is None
    assert (root / "plotted.csv").read_text(encoding="utf-8") == "new\n"


def test_a_write_that_succeeds_reports_nothing(world):
    root, _out = world
    _writable(root / "plotted.csv")
    worker, resp = _build(world, WRITES["open_w"])
    assert "locked_writes" not in resp
    assert (root / "plotted.csv").read_text(encoding="utf-8") == "new\n"


def _fails_with_permission(world, body: str) -> None:
    with pytest.raises(engine_pool.WorkerError) as err:
        _build(world, body)
    # 原来的失败原样保留（只读文件夹另有自己的分类，消息里是异常类名）
    assert any(t in str(err.value) for t in ("Permission denied", "Errno 13", "PermissionError")), (
        str(err.value)
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 上目录的只读属性不挡新建文件")
def test_a_read_only_folder_keeps_the_original_failure(world):
    root, _out = world
    sub = root / "ro"
    sub.mkdir()
    (sub / "x.csv").write_text("old", encoding="utf-8")
    _readonly(sub / "x.csv")
    os.chmod(sub, stat.S_IREAD | stat.S_IEXEC)
    _fails_with_permission(world, 'open(here / "ro" / "x.csv", "w").write("new")\n')


def test_a_file_outside_the_project_keeps_the_original_failure(world, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text("old", encoding="utf-8")
    _readonly(outside)
    try:
        _fails_with_permission(world, f'open({str(outside)!r}, "w").write("new")\n')
    finally:
        _writable(outside)


def test_the_overlay_switch_off_keeps_the_original_failure(world, monkeypatch):
    monkeypatch.setenv("TAVOTTO_SAFE_OVERLAY", "0")
    _fails_with_permission(world, WRITES["open_w"])


@pytest.mark.skipif(sys.platform != "win32", reason="独占打开（共享模式不许写）只有 Windows 有")
def test_a_file_held_open_by_another_program_on_windows(world):
    """现场的原样：别的程序（Excel）以「只许别人读」的共享模式打开着这份 CSV，Python 写打开它得 `[Errno 13]`。"""
    import ctypes
    from ctypes import wintypes

    root, _out = world
    target = root / "plotted.csv"
    _writable(target)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    generic_read, generic_write, share_read, open_existing = 0x80000000, 0x40000000, 0x1, 3
    handle = kernel32.CreateFileW(
        str(target), generic_read | generic_write, share_read, None, open_existing, 0, None
    )
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    try:
        body = (
            WRITES["open_w"]
            + 'OUT.write_text(json.dumps({"back": (here / "plotted.csv").read_text(encoding="utf-8")}))\n'
        )
        _worker, resp, observed = _ok(world, body)
        assert resp.get("locked_writes") == ["plotted.csv"]
        assert observed == {"back": "new\n"}
    finally:
        kernel32.CloseHandle(handle)
    assert target.read_text(encoding="utf-8") == "old\n"
