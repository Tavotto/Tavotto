"""没有写入权限的项目：另存一份副本、记住「继续只读」、脚本写不出输出文件时说清原因。

四件事：

1. `/api/project` 的 `writable` 在真只读的文件夹上为假（前端据此在打开时提示一次）；
2. `/api/project/save-as` 把项目复制到用户选的文件夹：副本可写、不带源的只读权限位、不跟随符号链接、
   体量超限在写第一个字节前拒绝、目标不可用有各自的码、重名加序号；
3. 「继续只读」的选择按项目路径存进项目设置（`readonly_save_as_declined`）；
4. 脚本往没有写入权限的文件夹写输出文件，在真 worker 里得到稳定码 `output_folder_read_only`，
   而「读不了某个文件」仍是 `script_error`（判据的主语是接收文件的文件夹）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from support import bridgekit
from tavotto import app as m
from tavotto.engine import pool as engine_pool, projectcopy, workdir

_no_chmod = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX 权限位；root 无视权限位",
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "userdata"))
    m.app.config["TESTING"] = True
    project = tmp_path / "proj"
    project.mkdir()
    (project / "fig.py").write_text("print('hi')\n")
    (project / "data").mkdir()
    (project / "data" / "a.csv").write_text("x,y\n1,2\n")
    yield m.app.test_client(), project, tmp_path
    m.reset_projects()


def _open(project: Path) -> str:
    return m.open_project(str(project))["id"]


def _hdr(pj: str) -> dict:
    return {"X-Tavotto-Project": pj}


def _lock_tree(root: Path) -> None:
    for dirpath, _dirs, files in os.walk(root, topdown=False):
        for f in files:
            os.chmod(Path(dirpath) / f, 0o444)
        os.chmod(dirpath, 0o555)


def _unlock_tree(root: Path) -> None:
    for dirpath, dirs, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            os.chmod(Path(dirpath) / f, 0o644)


# ---------------------------------------------------------------- 1. 可写性事实


@_no_chmod
def test_project_status_reports_an_unwritable_folder(env):
    client, project, _ = env
    pj = _open(project)  # 先打开（首开要写注册表），再收回写权限
    assert client.get("/api/project", headers=_hdr(pj)).get_json()["writable"] is True
    project.chmod(0o555)
    try:
        body = client.get("/api/project", headers=_hdr(pj)).get_json()
        assert body["writable"] is False
    finally:
        project.chmod(0o755)


# ---------------------------------------------------------------- 2. 另存为


def _save_as(client, pj, parent):
    return client.post("/api/project/save-as", json={"parent": str(parent)}, headers=_hdr(pj))


@_no_chmod
def test_save_as_copies_a_read_only_project_into_a_writable_copy(env):
    client, project, tmp = env
    pj = _open(project)
    _lock_tree(project)
    dest_parent = tmp / "mine"
    dest_parent.mkdir()
    try:
        r = _save_as(client, pj, dest_parent)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        copy = Path(body["path"])
        assert copy == (dest_parent / "proj").resolve()
        assert (copy / "fig.py").read_text() == "print('hi')\n"
        assert (copy / "data" / "a.csv").read_text() == "x,y\n1,2\n"
        # 副本可写：既不带源的只读目录位，也不带只读文件位
        assert os.access(copy, os.W_OK) and os.access(copy / "data", os.W_OK)
        (copy / "data" / "a.csv").write_text("changed")
        (copy / "new.txt").write_text("ok")
        # 源原样没动
        assert (project / "data" / "a.csv").read_text() == "x,y\n1,2\n"
        assert body["files"] >= 2 and body["skipped"] == 0
        # 副本能当项目打开，且是可写的
        assert m.open_project(body["path"])["writable"] is True
    finally:
        _unlock_tree(project)


def test_save_as_picks_a_free_name_when_the_target_exists(env):
    client, project, tmp = env
    pj = _open(project)
    parent = tmp / "mine"
    (parent / "proj").mkdir(parents=True)
    r = _save_as(client, pj, parent)
    assert r.status_code == 200
    assert Path(r.get_json()["path"]).name == "proj 2"
    assert list((parent / "proj").iterdir()) == []  # 原有同名文件夹一个字节没碰


def test_destination_taken_between_check_and_mkdir_survives_and_copy_goes_to_next_number(
    env, monkeypatch
):
    """另一个请求在我们选名之后、mkdir 之前占了 `proj`：它存好的项目一个字节不许被清，我们落到 `proj 2`。"""
    client, project, tmp = env
    pj = _open(project)
    parent = tmp / "mine"
    parent.mkdir()
    real_mkdir = Path.mkdir
    state = {"raced": False}

    def racing_mkdir(self, *a, **k):
        if self == parent.resolve() / "proj" and not state["raced"]:
            state["raced"] = True
            real_mkdir(self)  # 「另一个请求」抢先建好并存进了自己的项目
            (self / "winner.txt").write_text("keep me")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    r = _save_as(client, pj, parent)
    assert r.status_code == 200, r.get_json()
    assert state["raced"]
    assert (parent / "proj" / "winner.txt").read_text() == "keep me"
    assert Path(r.get_json()["path"]).name == "proj 2"
    assert (parent / "proj 2" / "fig.py").is_file()


def test_failure_never_removes_a_directory_this_call_did_not_create(env, monkeypatch):
    client, project, tmp = env
    pj = _open(project)
    parent = tmp / "mine"
    (parent / "proj").mkdir(parents=True)
    (parent / "proj" / "other.txt").write_text("x")

    def boom(src, dst, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(projectcopy.shutil, "copyfile", boom)
    r = _save_as(client, pj, parent)
    assert r.get_json()["code"] == "project_copy_failed"
    assert (parent / "proj" / "other.txt").read_text() == "x"  # 别人的目录还在
    assert not (parent / "proj 2").exists()  # 自己占的那个被清掉


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限位")
def test_executable_bit_is_kept_and_copy_is_writable(env):
    client, project, tmp = env
    helper = project / "run.sh"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o555)
    plain = project / "ro.txt"
    plain.write_text("x")
    plain.chmod(0o444)
    pj = _open(project)
    parent = tmp / "mine"
    parent.mkdir()
    r = _save_as(client, pj, parent)
    copy = Path(r.get_json()["path"])
    mode = (copy / "run.sh").stat().st_mode & 0o777
    assert mode & 0o100 and mode & 0o200 and mode & 0o400, oct(mode)
    assert os.access(copy / "run.sh", os.X_OK)
    assert not (copy / "ro.txt").stat().st_mode & 0o111  # 没有执行位的不凭空多出来
    assert os.access(copy / "ro.txt", os.W_OK)


@pytest.mark.skipif(sys.platform == "win32", reason="符号链接需要权限")
def test_save_as_does_not_follow_symlinks(env):
    client, project, tmp = env
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    (project / "link").symlink_to(outside, target_is_directory=True)
    (project / "loop").symlink_to(project, target_is_directory=True)
    pj = _open(project)
    parent = tmp / "mine"
    parent.mkdir()
    r = _save_as(client, pj, parent)
    assert r.status_code == 200
    copy = Path(r.get_json()["path"])
    assert not (copy / "link").exists() and not (copy / "loop").exists()
    assert not (copy / "link").is_symlink()


def test_save_as_size_guard_refuses_before_writing_anything(env, monkeypatch):
    client, project, tmp = env
    pj = _open(project)
    parent = tmp / "mine"
    parent.mkdir()
    monkeypatch.setattr(projectcopy, "MAX_BYTES", 4)
    r = _save_as(client, pj, parent)
    assert r.status_code == 413
    body = r.get_json()
    assert body["code"] == "project_copy_too_large" and "limit_mb" in body["params"]
    assert list(parent.iterdir()) == [], "超限的另存不许留下任何东西"


def test_save_as_file_count_guard(env, monkeypatch):
    client, project, tmp = env
    pj = _open(project)
    parent = tmp / "mine"
    parent.mkdir()
    monkeypatch.setattr(projectcopy, "MAX_FILES", 1)
    assert _save_as(client, pj, parent).get_json()["code"] == "project_copy_too_large"


def test_save_as_rejects_unusable_destinations(env):
    client, project, tmp = env
    pj = _open(project)
    assert _save_as(client, pj, tmp / "nope").get_json()["code"] == "project_copy_dest_invalid"
    # 目标在项目自己里面：副本会被复制进自己的源头
    assert _save_as(client, pj, project / "data").get_json()["code"] == "project_copy_dest_invalid"
    assert _save_as(client, pj, project).get_json()["code"] == "project_copy_dest_invalid"
    r = client.post("/api/project/save-as", json={"parent": ""}, headers=_hdr(pj))
    assert r.status_code == 400 and r.get_json()["code"] == "missing_path"


@_no_chmod
def test_save_as_rejects_an_unwritable_destination(env):
    client, project, tmp = env
    pj = _open(project)
    parent = tmp / "ro_parent"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        r = _save_as(client, pj, parent)
        assert r.status_code == 400
        assert r.get_json()["code"] == "project_copy_dest_unwritable"
    finally:
        parent.chmod(0o755)


@_no_chmod
def test_save_as_failure_midway_leaves_no_half_copy(env, monkeypatch):
    client, project, tmp = env
    pj = _open(project)
    parent = tmp / "mine"
    parent.mkdir()

    def boom(src, dst, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(projectcopy.shutil, "copyfile", boom)
    r = _save_as(client, pj, parent)
    assert r.status_code == 400 and r.get_json()["code"] == "project_copy_failed"
    assert list(parent.iterdir()) == []


@_no_chmod
def test_unreadable_single_file_is_skipped_and_counted(env):
    client, project, tmp = env
    secret = project / "data" / "locked.bin"
    secret.write_text("x")
    secret.chmod(0)
    pj = _open(project)
    parent = tmp / "mine"
    parent.mkdir()
    try:
        r = _save_as(client, pj, parent)
        assert r.status_code == 200
        body = r.get_json()
        assert body["skipped"] == 1
        assert body["skipped_paths"] == ["data/locked.bin"]
        assert not (Path(body["path"]) / "data" / "locked.bin").exists()
    finally:
        secret.chmod(0o644)


# ------------------------------------------- 2b. 从没打开过的只读文件夹


FIG = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nfig.savefig('out.png')\n"


def _snapshot(root: Path) -> list:
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*"))


@_no_chmod
def test_first_open_of_an_unwritable_folder_succeeds_and_writes_nothing(env):
    """共享盘 / 只读卷上从没打开过的文件夹（还没有 tavotto_registry.json）：照样打开，草稿留在内存里。"""
    _client, project, _ = env
    (project / "fig.py").write_text(FIG)
    assert not (project / "tavotto_registry.json").exists()
    _lock_tree(project)
    before = _snapshot(project)
    try:
        status = m.open_project(str(project))
        assert status["writable"] is False and status["drafted"] is True
        assert status["scripts"] >= 1  # 静态扫描的草稿在内存里，界面照常有脚本
        assert _snapshot(project) == before, "只读文件夹里一个字节都不许写"
        assert not (project / "tavotto_registry.json").exists()
    finally:
        _unlock_tree(project)


@_no_chmod
def test_save_as_from_a_first_open_read_only_folder_gives_a_writable_registered_copy(env):
    client, project, tmp = env
    (project / "fig.py").write_text(FIG)
    _lock_tree(project)
    parent = tmp / "mine"
    parent.mkdir()
    try:
        status = m.open_project(str(project))
        r = _save_as(client, status["id"], parent)
        assert r.status_code == 200, r.get_json()
        copy = Path(r.get_json()["path"])
        assert not (copy / "tavotto_registry.json").exists()  # 源里本来就没有，不凭空带
        opened = m.open_project(str(copy))
        assert opened["writable"] is True and opened["drafted"] is True
        assert (copy / "tavotto_registry.json").is_file(), "副本里注册表照常落盘"
        assert not (project / "tavotto_registry.json").exists()
    finally:
        _unlock_tree(project)


# ---------------------------------------------------------------- 3. 记住「继续只读」


def test_declining_is_remembered_per_project_path_and_clearable(env):
    client, project, _ = env
    pj = _open(project)
    assert (
        "readonly_save_as_declined"
        not in client.get("/api/project", headers=_hdr(pj)).get_json()["settings"]
    )
    r = client.patch(
        "/api/project/settings", json={"readonly_save_as_declined": True}, headers=_hdr(pj)
    )
    assert r.status_code == 200 and r.get_json()["settings"]["readonly_save_as_declined"] is True
    # 重新打开（换一轮后端状态）仍然记得——存的是用户配置，不是内存
    m.reset_projects()
    status = m.open_project(str(project))
    assert status["settings"]["readonly_save_as_declined"] is True
    # 别的项目不受影响
    other = project.parent / "other"
    other.mkdir()
    (other / "fig.py").write_text("")
    assert "readonly_save_as_declined" not in m.open_project(str(other))["settings"]
    r = client.patch(
        "/api/project/settings",
        json={"readonly_save_as_declined": False},
        headers=_hdr(status["id"]),
    )
    assert "readonly_save_as_declined" not in r.get_json()["settings"]


# ---------------------------------------------------------------- 4. 写不出输出文件


needs_worker = pytest.mark.skipif(
    bridgekit.USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器"
)

SCRIPT = """
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2, 3])
fig.savefig("fig.png")
{action}
"""


@pytest.fixture
def safe_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


def _run(root: Path, action: str):
    (root / "fig.py").write_text(SCRIPT.format(action=action))
    # 脚本目录档：相对路径的写真的落在项目里（默认沙盒档里 Tavotto 的写守卫根本不让它落盘，没有失败可言）
    workdir.set_mode(root, workdir.MODE_PROJECT)
    return engine_pool.build("fig.py", str(root), "__main__")


@_no_chmod
@needs_worker
def test_script_writing_into_a_read_only_folder_gets_the_stable_code(safe_project):
    out = safe_project / "out"
    out.mkdir()
    out.chmod(0o555)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            _run(safe_project, "open('out/table.csv', 'w').write('a')")
        assert err.value.code == engine_pool.OUTPUT_FOLDER_READ_ONLY_CODE
        assert "PermissionError" in err.value.traceback_text
        assert engine_pool.recovery_params(err.value) == {}
    finally:
        out.chmod(0o755)


@_no_chmod
@needs_worker
def test_reading_an_unreadable_file_stays_a_script_error(safe_project):
    """判据的主语是接收文件的文件夹：一个存在却读不了的文件在**可写**文件夹里，是读失败，不是只读项目。"""
    secret = safe_project / "secret.txt"
    secret.write_text("x")
    secret.chmod(0)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            _run(safe_project, "open('secret.txt').read()")
        assert err.value.code == "script_error"
    finally:
        secret.chmod(0o644)


@_no_chmod
@needs_worker
def test_reading_an_unreadable_file_in_a_read_only_folder_is_still_a_read_failure(safe_project):
    locked = safe_project / "ro"
    locked.mkdir()
    (locked / "secret.txt").write_text("x")
    (locked / "secret.txt").chmod(0)
    locked.chmod(0o555)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            _run(safe_project, "open('ro/secret.txt').read()")
        assert err.value.code == "script_error"
    finally:
        locked.chmod(0o755)
        (locked / "secret.txt").chmod(0o644)


@_no_chmod
@needs_worker
def test_reading_an_input_inside_an_untraversable_folder_is_not_an_output_failure(safe_project):
    """另一个账户的 0700 目录里的输入文件：读它是 EACCES，且 `lexists` 看不见它（进不去目录）。
    没有写意图，不许说成「输出文件没生成」。"""
    locked = safe_project / "locked"
    locked.mkdir()
    (locked / "in.csv").write_text("a,b\n")
    locked.chmod(0)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            _run(safe_project, "open('locked/in.csv').read()")
        assert err.value.code == "script_error"
    finally:
        locked.chmod(0o755)


@_no_chmod
@needs_worker
def test_write_intent_is_captured_for_os_open_flags_too(safe_project):
    out = safe_project / "out"
    out.mkdir()
    out.chmod(0o555)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            _run(
                safe_project,
                "import os\nos.close(os.open('out/t.bin', os.O_WRONLY | os.O_CREAT))",
            )
        assert err.value.code == engine_pool.OUTPUT_FOLDER_READ_ONLY_CODE
    finally:
        out.chmod(0o755)


def test_eacces_needs_a_recorded_write_attempt_for_that_exact_path(tmp_path):
    import errno

    from tavotto.engine import worker

    locked = tmp_path / "ro"
    locked.mkdir()
    if sys.platform != "win32" and not (hasattr(os, "geteuid") and os.geteuid() == 0):
        locked.chmod(0o555)
    target = str(locked / "x.csv")
    exc = PermissionError(errno.EACCES, "denied", target)
    try:
        if not os.access(locked, os.W_OK):
            assert worker._write_denied_by_folder(exc) is False  # 没记过写意图：读失败的脸
            assert worker._write_denied_by_folder(exc, {target: None}) is True
            assert worker._write_denied_by_folder(exc, {target + "2": None}) is False
    finally:
        locked.chmod(0o755)


@needs_worker
def test_a_writable_folder_never_gets_the_read_only_code(safe_project):
    (safe_project / "out").mkdir()
    _worker, resp = _run(safe_project, "open('out/table.csv', 'w').write('a')")
    assert resp  # 跑通了，没有错误


def test_erofs_counts_as_a_read_only_folder_even_when_access_says_writable(tmp_path):
    """只读卷上 `os.access(W_OK)` 在个别平台 / 网络盘上会说谎：EROFS 本身就是事实，不再二次核对。"""
    import errno

    from tavotto.engine import worker

    exc = OSError(errno.EROFS, "Read-only file system", str(tmp_path / "x.csv"))
    assert os.access(tmp_path, os.W_OK)
    assert worker._write_denied_by_folder(exc) is True
    # 没有文件名：判不了，不算
    assert worker._write_denied_by_folder(OSError(errno.EROFS, "ro")) is False
    # 别的 errno 不算
    assert worker._write_denied_by_folder(OSError(errno.ENOSPC, "full", "/x/y")) is False
    # EACCES 要看文件夹：可写的文件夹里的 EACCES（比如目标文件自己的权限）不是「文件夹只读」
    assert (
        worker._write_denied_by_folder(OSError(errno.EACCES, "denied", str(tmp_path / "x.csv")))
        is False
    )
    # 异常链里的也认（脚本把它包了一层）
    try:
        try:
            raise exc
        except OSError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert worker._write_denied_by_folder(outer) is True
