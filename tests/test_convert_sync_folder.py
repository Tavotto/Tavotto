"""项目放在同步盘里时，“校验运行没动项目”的复核不被同步客户端误伤（ADR 0143 修订）。"""

# ruff: noqa: F811 - imported pytest fixture

import os
import time

import pytest

from tavotto.engine import ai_convert, ai_figure_edit, syncnoise
from test_ai_convert import _project, _run, _wait, env, ok_runner  # noqa: F401

NOISE = [
    ".DS_Store",
    "._fig.R",
    "Icon\r",
    "~$report.docx",
    ".~lock.table.ods#",
    "~WRD0000.tmp",
    ".syncthing.data.csv.tmp",
    ".nutstore_abc",
    "fig-NSConflict-2026.R",
    "fig-冲突-Windows.R",
    "data (conflicted copy 2026-10-28).csv",
    "data (Case Conflict).csv",
    ".data.csv.icloud",
    ".dropbox.cache",
    "desktop.ini",
    "Thumbs.db",
    "notes.txt.sb-1a2b3c4d-AbCdEf",
]
REAL = ["result.tmp", "out.png", "fig.R", "data.csv", "run.log", "notes.txt", "sb-notes.txt"]


@pytest.mark.parametrize("name", NOISE)
def test_sync_and_system_files_are_in_the_closed_set(name):
    assert syncnoise.is_sync_noise(name)


@pytest.mark.parametrize("name", REAL)
def test_real_user_files_are_never_noise(name):
    """`*.tmp` 不是闭集成员：用户脚本自己写的 result.tmp 是真实的项目改动。"""
    assert not syncnoise.is_sync_noise(name)


def test_fingerprint_ignores_noise_and_mtime_only_touches(tmp_path):
    (tmp_path / "a.csv").write_text("1\n")
    big = tmp_path / "sub"
    big.mkdir()
    (big / "b.csv").write_text("2\n")
    before = ai_figure_edit.project_fingerprint(tmp_path)
    for n in ("._a.csv", ".DS_Store", "a (conflicted copy).csv"):
        (tmp_path / n).write_text("x")
    (big / ".dropbox.cache").mkdir()
    (big / ".dropbox.cache" / "z").write_text("x")
    later = time.time() + 30
    os.utime(tmp_path / "a.csv", (later, later))  # 同步客户端只碰 mtime
    assert (
        ai_figure_edit.fingerprint_changes(before, ai_figure_edit.project_fingerprint(tmp_path))
        == []
    )


def test_fingerprint_still_sees_real_changes(tmp_path):
    """反证：内容变、新增用户文件、删除、`*.tmp` 都仍然是改动。"""
    (tmp_path / "a.csv").write_text("1\n")
    (tmp_path / "b.csv").write_text("2\n")
    before = ai_figure_edit.project_fingerprint(tmp_path)
    (tmp_path / "a.csv").write_text("changed\n")
    (tmp_path / "new.png").write_bytes(b"x")
    (tmp_path / "result.tmp").write_text("x")
    (tmp_path / "b.csv").unlink()
    assert ai_figure_edit.fingerprint_changes(
        before, ai_figure_edit.project_fingerprint(tmp_path)
    ) == ["a.csv", "b.csv", "new.png", "result.tmp"]


def test_sync_folder_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(syncnoise, "_home", lambda: tmp_path)
    nut = tmp_path / "Nutstore Files" / "我的坚果云" / "A CDH3 分组火山图"
    nut.mkdir(parents=True)
    plain = tmp_path / "Documents" / "proj"
    plain.mkdir(parents=True)
    cloud = tmp_path / "Library" / "CloudStorage" / "OneDrive-Personal" / "p"
    cloud.mkdir(parents=True)
    assert syncnoise.in_sync_folder(nut)
    assert syncnoise.in_sync_folder(cloud)
    assert not syncnoise.in_sync_folder(plain)
    # 同步根放在别处时，路径里的典型目录名也认
    assert syncnoise.in_sync_folder(tmp_path / "x" / "Dropbox" / "p")


def test_sync_noise_during_validation_does_not_fail_the_task(env):
    figs = env["figs"]

    def noisy_runner(figures_dir, rel, write_roots=None):
        (figs / ".DS_Store").write_bytes(b"x")
        (figs / "._fig.py").write_bytes(b"x")
        (figs / "data (conflicted copy 2026).csv").write_text("x,y\n")
        later = time.time() + 60
        os.utime(figs / "data.csv", (later, later))
        return ok_runner(figures_dir, rel, write_roots)

    env["runner"] = noisy_runner
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "completed", sess["task"]
    status = ai_convert.status(sid, _project(env))
    assert status["state"] == "ready"
    # 采用只会新增这一个文件，原脚本不动
    assert status["adopt_adds"] == ["fig_matplotlib.py"]


def test_ready_adds_follow_the_adoption_numbering(env):
    env["runner"] = ok_runner
    sid = _run(env)
    _wait(sid)
    (env["figs"] / "fig_matplotlib.py").write_text("# taken\n", encoding="utf-8")
    assert ai_convert.status(sid, _project(env))["adopt_adds"] == ["fig_matplotlib_2.py"]
    made = ai_convert.adopt(sid, _project(env))
    assert made["script"] == "fig_matplotlib_2.py"


@pytest.mark.parametrize("in_sync", [False, True])
def test_a_real_user_file_change_still_fails_and_names_the_files(env, monkeypatch, in_sync):
    figs = env["figs"]
    monkeypatch.setattr(syncnoise, "in_sync_folder", lambda _p: in_sync)

    def writing_runner(figures_dir, rel, write_roots=None):
        (figs / "out.png").write_bytes(b"clobbered")
        (figs / "mine.csv").write_text("new")
        return ok_runner(figures_dir, rel, write_roots)

    env["runner"] = writing_runner
    sid = _run(env)
    sess = _wait(sid)
    assert sess["task"]["outcome"] == "failed"
    expected = "project_files_changed_sync" if in_sync else "project_files_changed"
    assert ("project.untouched", expected) in {
        (u["constraint_id"], u["reason_code"]) for u in sess["task"]["unmet"]
    }


@pytest.mark.parametrize("in_sync", [False, True])
def test_failed_check_carries_changed_files_capped_at_fifty_and_the_sync_flag(
    tmp_path, monkeypatch, in_sync
):
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "cands")
    monkeypatch.setattr(syncnoise, "in_sync_folder", lambda _p: in_sync)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.R").write_text("plot(1)\n")
    cand = ai_convert.create_candidate("abcdef012345", root / "fig.R", root, "fig.R", library=None)
    for i in range(60):
        (root / f"user_{i:02d}.csv").write_text("x")
    (root / ".DS_Store").write_text("noise")
    failed = [
        c
        for c in ai_convert.ADAPTER._project_untouched(cand, "precommit")
        if c.id == "project.untouched"
    ]
    assert len(failed) == 1 and failed[0].verdict == "failed"
    assert failed[0].reason_code == (
        "project_files_changed_sync" if in_sync else "project_files_changed"
    )
    obs = failed[0].observed
    assert obs["sync_folder"] is in_sync and obs["total"] == 60 and len(obs["files"]) == 50
    assert ".DS_Store" not in obs["files"]
