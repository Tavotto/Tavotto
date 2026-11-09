"""safe worker 的暂存覆盖层（#1813）：项目内的 `Path.write_text` / 改名 / 删除不再被静默跳过后读回判成 blocked。

以前：守卫把这些操作跳过（不落盘），脚本随后读回受影响路径 → 账本判 material → `safe_file_operation_blocked` → 退回 native；
native 按 ADR 0106 不用数据改指，读原本缺失的路径 `FileNotFoundError`。常见的「写临时文件 → os.replace → 读回」就会这样。

现在：这几类操作改道进**本次运行的暂存覆盖层**（`writetripwire.install_overlay`，复用校验运行改道层的视图实现，
运行结束丢弃），Python 层的读（open / `Path.read_*` / `exists` / `is_file` / `is_dir` / `getsize` / numpy `DataSource`）看到覆盖后的
视图；**用户项目一个字节不改**。覆盖层够不着的观察（`os.stat` / `listdir` / `scandir` / 子进程 / C 层读）仍按守卫账本的老规矩：
`os.stat` 之类命中确定偏差（blocked），列目录是弱信号（suspect），子进程 / C 层是写在 `unobserved` 里的盲区。

全部用真 safe worker 子进程与真文件；每条都逐字节核项目前后（含 mtime）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import figcapture, inputremap, pool as engine_pool, workdir, writetripwire

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

CODE = "safe_file_operation_blocked"
HDR = """\
import os, sys, shutil, json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
here = Path(__file__).resolve().parent
OUT = Path({out!r})
def note(**kw):
    OUT.write_text(json.dumps(kw))
fig, ax = plt.subplots()
ax.plot([0, 1])
"""


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "cache.csv").write_text("1", encoding="utf-8")
    (root / "seed.txt").write_text("abc", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "values.csv").write_text("7", encoding="utf-8")
    (root / "old_dir").mkdir()
    (root / "old_dir" / "x.txt").write_text("x", encoding="utf-8")
    out = tmp_path / "observed.json"
    yield root, out
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


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


def _run(world, body: str, *, project_mode: bool = False):
    root, out = world
    (root / "script.py").write_text(HDR.format(out=str(out)) + body, encoding="utf-8")
    workdir.set_mode(root, workdir.MODE_PROJECT if project_mode else workdir.MODE_SANDBOX)
    return engine_pool.build("script.py", str(root), "__main__")


def _ok(world, body: str, *, project_mode: bool = False) -> dict:
    """成功（不退回 native）、项目逐字节不变，回观察到的值。"""
    root, out = world
    before = _snapshot(root)
    _worker, resp = _run(world, body, project_mode=project_mode)
    assert _snapshot(root) == before, "用户项目被改动了"
    assert not (root.parent / "overlay").exists()
    return json.loads(out.read_text()) if out.exists() else {}, resp


def _blocked(world, body: str, *, project_mode: bool = False) -> engine_pool.WorkerError:
    with pytest.raises(engine_pool.WorkerError) as err:
        _run(world, body, project_mode=project_mode)
    assert err.value.code == CODE, (err.value.code, str(err.value))
    return err.value


def _worker_version() -> tuple[int, int]:
    """渲染解释器（不是跑 pytest 的这个）的版本。"""
    out = subprocess.run(
        [WORKER_PY, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return int(out[0]), int(out[1])


MODES = pytest.mark.parametrize("project_mode", [False, True], ids=["sandbox", "project"])

#: issue #1813 的合成复现：写临时文件 → os.replace 覆盖项目里已有的缓存 → 读回算值。
ISSUE = """\
tmp, final = here / "cache.tmp", here / "cache.csv"
tmp.write_text("2")
os.replace(tmp, final)
value = int(final.read_text())
note(value=value, tmp=tmp.exists(), final=final.exists())
"""


@needs_worker
def test_the_issue_script_runs_in_safe_and_leaves_the_project_untouched(world):
    seen, resp = _ok(world, ISSUE)
    assert seen == {"value": 2, "tmp": False, "final": True}, "读回的是脚本写的那份"
    assert (world[0] / "cache.csv").read_text() == "1"
    # 说得清这次有操作被覆盖层接住，且没有确定偏差
    fid = resp["runtime"]["fidelity"]
    assert fid["level"] == "none_observed" and fid["blocked"] == []
    assert {o["op"] for o in fid["intercepted"]} == {"Path.write_text", "os.replace"}


@needs_worker
@MODES
@pytest.mark.parametrize(
    "readback",
    [
        "int(open(final).read())",
        "int(io.open(final).read())",
        "int(final.read_text())",
        "int(final.read_bytes())",
        "int(final.open().read())",
        "int(os.fdopen(os.open(final, os.O_RDONLY)).read())",
    ],
    ids=["open", "io.open", "read_text", "read_bytes", "Path.open", "os.open"],
)
def test_every_python_level_read_sees_the_overlay(world, project_mode, readback):
    rel = "'cache.tmp'" if project_mode else "here / 'cache.tmp'"
    fin = "'cache.csv'" if project_mode else "here / 'cache.csv'"
    body = (
        "import io\n"
        f"tmp, final = Path({rel}), Path({fin})\n"
        "tmp.write_text('2')\n"
        "os.replace(tmp, final)\n"
        f"note(value={readback})\n"
    )
    seen, _ = _ok(world, body, project_mode=project_mode)
    assert seen == {"value": 2}


@needs_worker
@MODES
def test_delete_makes_exists_false_in_every_python_level_check(world, project_mode):
    p = "Path('cache.csv')" if project_mode else "here / 'cache.csv'"
    body = (
        f"p = {p}\n"
        "os.remove(p)\n"
        "try:\n    open(p)\n    opened = True\nexcept FileNotFoundError:\n    opened = False\n"
        "note(exists=p.exists(), isfile=p.is_file(), isdir=p.is_dir(), ope=os.path.exists(p),"
        " opf=os.path.isfile(p), opened=opened)\n"
    )
    seen, _ = _ok(world, body, project_mode=project_mode)
    assert seen == {
        "exists": False,
        "isfile": False,
        "isdir": False,
        "ope": False,
        "opf": False,
        "opened": False,
    }


@needs_worker
def test_unlink_and_a_second_delete_follow_real_semantics(world):
    body = (
        "p = here / 'cache.csv'\n"
        "p.unlink()\n"
        "try:\n    p.unlink()\n    again = 'ok'\nexcept FileNotFoundError:\n    again = 'enoent'\n"
        "p.unlink(missing_ok=True)\n"
        "note(exists=p.exists(), again=again)\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"exists": False, "again": "enoent"}


@needs_worker
def test_renaming_a_project_original_gives_a_consistent_view(world):
    body = (
        "src, dst = here / 'cache.csv', here / 'cache.bak'\n"
        "os.rename(src, dst)\n"
        "note(src=src.exists(), dst=dst.read_text(), dst_exists=dst.is_file())\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"src": False, "dst": "1", "dst_exists": True}


@needs_worker
def test_shutil_move_of_a_project_original(world):
    body = (
        "shutil.move(str(here / 'seed.txt'), str(here / 'moved.txt'))\n"
        "note(old=(here / 'seed.txt').exists(), new=(here / 'moved.txt').read_text())\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"old": False, "new": "abc"}


@needs_worker
def test_repeated_writes_and_write_delete_write(world):
    body = (
        "p = here / 'cache.csv'\n"
        "p.write_text('a'); p.write_text('b')\n"
        "first = p.read_text()\n"
        "os.remove(p)\n"
        "gone = not p.exists()\n"
        "p.write_text('c')\n"
        "note(first=first, gone=gone, last=p.read_text(), size=os.path.getsize(p))\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"first": "b", "gone": True, "last": "c", "size": 1}


@needs_worker
def test_open_write_after_an_overlay_write_reads_back_consistently(world):
    """覆盖层里已有状态的路径，后来的 `open('w')` 也进覆盖层：不会写进真实文件、读回更早的那份。"""
    body = (
        "p = here / 'cache.csv'\n"
        "p.write_text('a')\n"
        "with open(p, 'w') as f:\n    f.write('bb')\n"
        "note(value=p.read_text())\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"value": "bb"}


@needs_worker
def test_open_write_over_an_existing_file_still_writes_it_for_real(world):
    """既有契约不变：`open('w')` 改写已有文件不拦（那是脚本自己的输出）。"""
    root, out = world
    _run(world, "with open(here / 'cache.csv', 'w') as f:\n    f.write('9')\n")
    assert (root / "cache.csv").read_text() == "9"


@needs_worker
def test_a_missing_parent_directory_is_a_real_file_not_found(world):
    body = (
        "try:\n    (here / 'nope' / 'x.txt').write_text('x')\n    r = 'ok'\n"
        "except FileNotFoundError as e:\n    r = 'enoent' if 'overlay' not in str(e) else 'leak'\n"
        "note(r=r)\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"r": "enoent"}


@needs_worker
def test_a_new_file_in_an_existing_project_directory(world):
    body = (
        "(here / 'data' / 'new.csv').write_text('n')\n"
        "note(read=(here / 'data' / 'new.csv').read_text(), has=(here / 'data' / 'new.csv').exists())\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"read": "n", "has": True}


@needs_worker
def test_numpy_loadtxt_reads_the_overlay_version(world):
    body = (
        "import numpy as np\n"
        "p = here / 'vals.txt'\n"
        "p.write_text('3\\n4\\n')\n"
        "note(vals=np.loadtxt(p).tolist())\n"
    )
    try:
        seen, _ = _ok(world, body)
    except engine_pool.WorkerError as exc:  # numpy 不在渲染解释器里
        pytest.skip(f"numpy 不可用: {exc}")
    assert seen == {"vals": [3.0, 4.0]}


# ---------------------------------------------------------------- 反例：覆盖层够不着的观察仍命中
@needs_worker
@MODES
@pytest.mark.parametrize(
    "probe",
    ["os.stat(final)", "final.stat()", "os.lstat(final)"],
    ids=["os.stat", "Path.stat", "os.lstat"],
)
def test_a_stat_readback_is_still_blocked(world, project_mode, probe):
    if probe == "final.stat()" and _worker_version() < (3, 11):
        pytest.skip("3.10 的 Path.stat 绑的是导入时的 os.stat，账本从来观察不到（与覆盖层无关）")
    fin = "Path('cache.csv')" if project_mode else "here / 'cache.csv'"
    body = (
        f"final = {fin}\n"
        "final.with_name('cache.tmp').write_text('2')\n"
        "os.replace(final.with_name('cache.tmp'), final)\n"
        f"{probe}\n"
    )
    # os.lstat 不在账本的包装清单里（今天也不观察）：只断言前两条
    if probe == "os.lstat(final)":
        _ok(world, body, project_mode=project_mode)
        return
    err = _blocked(world, body, project_mode=project_mode)
    assert {o["op"] for o in err.extra["operations"]} <= {"Path.write_text", "os.replace"}
    assert (world[0] / "cache.csv").read_text() == "1"


@needs_worker
def test_a_listing_readback_is_the_same_weak_signal_as_before(world):
    body = "(here / 'fresh.txt').write_text('f')\nnote(n=len(os.listdir(here)))\n"
    _seen, resp = _ok(world, body)
    fid = resp["runtime"]["fidelity"]
    assert fid["level"] == "suspect" and fid["blocked"] == []
    assert any(s["via"] == "listing" for s in fid["signals"])


@needs_worker
def test_a_subprocess_readback_stays_in_the_declared_blind_spot(world):
    body = (
        "import subprocess\n"
        "(here / 'fresh.txt').write_text('f')\n"
        "r = subprocess.run([sys.executable, '-c', 'import sys,os; print(os.path.exists(sys.argv[1]))',"
        " str(here / 'fresh.txt')], capture_output=True, text=True)\n"
        "note(seen=r.stdout.strip())\n"
    )
    seen, resp = _ok(world, body)
    assert seen == {"seen": "False"}, "子进程看到的是真实文件系统——盲区，没有覆盖层"
    assert "subprocess" in resp["runtime"]["fidelity"]["unobserved"]


@needs_worker
def test_removing_a_project_directory_is_still_a_blocked_operation(world):
    body = "shutil.rmtree(here / 'old_dir')\nnote(has=(here / 'old_dir' / 'x.txt').exists())\n"
    err = _blocked(world, body)
    assert [o["op"] for o in err.extra["operations"]] == ["shutil.rmtree"]
    assert (world[0] / "old_dir" / "x.txt").read_text() == "x"


@needs_worker
def test_renaming_a_project_directory_is_still_blocked(world):
    err = _blocked(
        world,
        "os.rename(here / 'old_dir', here / 'new_dir')\nnote(has=(here / 'new_dir').exists())\n",
    )
    assert [o["op"] for o in err.extra["operations"]] == ["os.rename"]


@needs_worker
def test_deleting_a_path_that_is_nowhere_stays_a_guard_skip(world):
    """哪里都没有的源：覆盖层不替脚本编出 `FileNotFoundError`，交回守卫（今天的行为：跳过并记账）。"""
    body = "os.remove(here / 'ghost.txt')\nnote(done=True)\n"
    seen, _ = _ok(world, body)
    assert seen == {"done": True}


@needs_worker
def test_a_project_symlink_is_not_simulated(world):
    root, _out = world
    try:
        os.symlink(root / "seed.txt", root / "alias.txt")
    except (OSError, NotImplementedError):
        pytest.skip("不能建符号链接")
    err = _blocked(
        world, "os.remove(here / 'alias.txt')\nnote(has=(here / 'alias.txt').exists())\n"
    )
    assert [o["op"] for o in err.extra["operations"]] == ["os.remove"]
    assert (root / "seed.txt").read_text() == "abc"


@needs_worker
def test_a_symlink_that_leaves_the_project_is_written_through_as_before(world, tmp_path):
    root, _out = world
    outside = tmp_path / "outside.txt"
    outside.write_text("old")
    try:
        os.symlink(outside, root / "out_link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("不能建符号链接")
    body = (
        "(here / 'out_link.txt').write_text('new')\nnote(v=(here / 'out_link.txt').read_text())\n"
    )
    (root / "script.py").write_text(HDR.format(out=str(_out)) + body, encoding="utf-8")
    engine_pool.build("script.py", str(root), "__main__")
    assert outside.read_text() == "new", "指向项目外的链接：守卫从来不拦，覆盖层也不接"
    assert json.loads(_out.read_text()) == {"v": "new"}


@needs_worker
def test_case_variants_of_a_path_are_the_same_overlay_file_on_a_folding_volume(world):
    root, _out = world
    if not writetripwire.folds_case(str(root)):
        pytest.skip("卷区分大小写")
    body = (
        "(here / 'Cache.CSV').write_text('2')\n"
        "note(lower=(here / 'cache.csv').read_text(), upper=(here / 'CACHE.csv').read_text())\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"lower": "2", "upper": "2"}


@needs_worker
def test_the_overlay_does_not_outlive_the_run(world):
    """脚本跑完之后引擎自己的读写看到真实的项目：覆盖层窗口已关、暂存区已丢。"""
    root, out = world
    _run(world, "(here / 'cache.csv').write_text('2')\nnote(v=1)\n")
    assert (root / "cache.csv").read_text() == "1"
    assert not (root.parent / "overlay").exists()


# ---------------------------------------------------------------- 账本：覆盖层事件的语义
def test_overlay_events_are_not_blamed_for_a_later_file_not_found(tmp_path):
    ledger = figcapture.BlockedFileOps()
    gone = tmp_path / "a.csv"
    ledger.blocked(
        "os.rename",
        ledger.KIND_RENAME,
        gone,
        tmp_path / "b.csv",
        dests=(tmp_path / "b.csv",),
        overlay=True,
    )
    exc = FileNotFoundError(2, "No such file", str(gone))
    assert ledger.explains(exc) is False
    plain = figcapture.BlockedFileOps()
    plain.blocked(
        "os.rename", plain.KIND_RENAME, gone, tmp_path / "b.csv", dests=(tmp_path / "b.csv",)
    )
    assert plain.explains(exc) is True


def test_ledger_ignores_probes_while_the_overlay_is_probing(tmp_path):
    ledger = figcapture.BlockedFileOps()
    target = tmp_path / "a.csv"
    ledger.blocked("Path.write_text", ledger.KIND_WRITE, target, overlay=True)
    flag = {"on": True}
    ledger.quiet_if = lambda: flag["on"]
    ledger.observe_stat(target)
    assert ledger.material() == []
    flag["on"] = False
    ledger.observe_stat(target)
    assert [m["op"] for m in ledger.material()] == ["Path.write_text"]


@needs_worker
def test_the_ops_switch_restores_the_old_guard_behaviour(world, monkeypatch):
    """`TAVOTTO_SAFE_OVERLAY=0`：回到守卫「跳过 + 记账」——同一个脚本重新判 blocked，项目照旧不动（修复前的行为）。"""
    monkeypatch.setenv("TAVOTTO_SAFE_OVERLAY", "0")
    err = _blocked(world, ISSUE)
    assert {o["op"] for o in err.extra["operations"]} == {"Path.write_text", "os.replace"}
    assert (world[0] / "cache.csv").read_text() == "1"


@needs_worker
def test_a_linked_input_and_an_overlay_intermediate_work_together(world, tmp_path):
    """数据改指（ADR 0106）与覆盖层同时生效：缺失的输入经改指读到链接的数据，脚本自己写的中间文件经覆盖层读回。
    覆盖层在改指外面：脚本本次写 / 改名 / 删除过的路径，覆盖层里的版本优先（直接运行时脚本写了就读到自己写的），
    没碰过的路径落到改指层，改指只在原路径缺失时才查表。"""
    root, out = world
    missing = tmp_path / "absent" / "values.csv"
    inputremap.add_rule(
        str(root), {"kind": "file", "from": str(missing), "to": str(root / "data" / "values.csv")}
    )
    body = (
        f"data = int(open({str(missing)!r}).read())\n"
        "tmp, final = here / 'cache.tmp', here / 'cache.csv'\n"
        "tmp.write_text(str(data * 2))\n"
        "os.replace(tmp, final)\n"
        "note(data=data, value=int(final.read_text()), cache_exists=final.exists())\n"
    )
    seen, _ = _ok(world, body)
    assert seen == {"data": 7, "value": 14, "cache_exists": True}
    assert (root / "cache.csv").read_text() == "1"
