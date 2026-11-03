"""脚本调外部命令却找不到可执行文件：worker 里量的闭集事实 + 出门口径（bundle schema 15，TVD-1FCJ）。"""

from __future__ import annotations

import os
import stat
import subprocess
import sys

import pytest

from tavotto.engine import cmddiag, deprepair, diagnostics

BIN = "_tvtcmd_demo_tool"  # 非白名单：出门要哈希
PUBLIC = "rsvg-convert"


def _spawn_error(name: str) -> FileNotFoundError:
    try:
        subprocess.run([name], check=False, env={"PATH": ""})
    except FileNotFoundError as exc:
        return exc
    raise AssertionError("expected FileNotFoundError")


def _exe(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    f = directory / name
    f.write_text("#!/bin/sh\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return f


@pytest.fixture
def table(tmp_path, monkeypatch):
    """用 tmp 目录当「固定常见目录」：键是出门的枚举名，模式只在本机量。"""
    t = {
        "homebrew_bin": (str(tmp_path / "brew" / "bin"),),
        "usr_local_bin": (str(tmp_path / "local" / "bin"),),
        "homebrew_opt": (str(tmp_path / "brew" / "opt" / "*" / "bin"),),
        "homebrew_cellar": (str(tmp_path / "brew" / "Cellar" / "*" / "*" / "bin"),),
    }
    monkeypatch.setattr(cmddiag, "DIR_TABLE", {sys.platform: t})
    return t


@pytest.mark.skipif(sys.platform == "win32", reason="posix 目录表")
class TestCollect:
    def test_spawn_of_missing_command_is_a_command(self):
        assert cmddiag.command_of(_spawn_error(PUBLIC)) == PUBLIC

    def test_open_of_bare_file_name_is_not_a_command(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError) as info:
            open("data.csv")
        assert cmddiag.command_of(info.value) is None
        assert cmddiag.collect(info.value) == {}

    def test_path_with_separator_is_not_a_bare_command(self):
        with pytest.raises(FileNotFoundError) as info:
            subprocess.run(["/no/such/dir/tool"])
        assert cmddiag.command_of(info.value) is None

    def test_not_on_path_and_not_in_any_dir(self, table, tmp_path):
        env = {"PATH": os.pathsep.join([str(tmp_path / "x"), str(tmp_path / "brew" / "bin")])}
        fact = cmddiag.collect(_spawn_error(PUBLIC), env=env)["missing_command"]
        assert fact["command"] == PUBLIC
        assert fact["on_path"] is False
        assert fact["found_in"] == []
        assert fact["keg_present"] is False and fact["keg_unlinked"] is False
        assert fact["path_segments"] == 2
        assert fact["path_has"]["homebrew_bin"] is True
        assert fact["path_has"]["usr_local_bin"] is False

    def test_present_in_homebrew_bin_but_not_on_path(self, table, tmp_path):
        _exe(tmp_path / "brew" / "bin", PUBLIC)
        fact = cmddiag.collect(_spawn_error(PUBLIC), env={"PATH": "/usr/bin"})["missing_command"]
        assert fact["on_path"] is False
        assert fact["found_in"] == ["homebrew_bin"]
        assert fact["keg_unlinked"] is False

    def test_on_path_uses_the_given_env(self, table, tmp_path):
        d = tmp_path / "onpath"
        _exe(d, PUBLIC)
        fact = cmddiag.collect(_spawn_error(PUBLIC), env={"PATH": str(d)})["missing_command"]
        assert fact["on_path"] is True

    def test_keg_present_but_unlinked(self, table, tmp_path):
        _exe(tmp_path / "brew" / "Cellar" / "librsvg" / "2.63.4" / "bin", PUBLIC)
        fact = cmddiag.collect(_spawn_error(PUBLIC), env={"PATH": "/usr/bin"})["missing_command"]
        assert fact["found_in"] == ["homebrew_cellar"]
        assert fact["keg_present"] is True and fact["keg_unlinked"] is True

    def test_keg_present_and_linked_is_not_unlinked(self, table, tmp_path):
        _exe(tmp_path / "brew" / "Cellar" / "librsvg" / "2.63.4" / "bin", PUBLIC)
        _exe(tmp_path / "brew" / "bin", PUBLIC)
        fact = cmddiag.collect(_spawn_error(PUBLIC), env={"PATH": "/usr/bin"})["missing_command"]
        assert fact["keg_present"] is True and fact["keg_unlinked"] is False

    def test_sensitive_run_blanks_non_public_names(self, table):
        fact = cmddiag.collect(_spawn_error(BIN), env={"PATH": ""}, sensitive=True)
        assert fact["missing_command"]["command"] == ""
        pub = cmddiag.collect(_spawn_error(PUBLIC), env={"PATH": ""}, sensitive=True)
        assert pub["missing_command"]["command"] == PUBLIC


@pytest.mark.skipif(sys.platform == "win32", reason="posix 目录表")
def test_export_is_closed_set_and_has_no_user_paths(table, tmp_path):
    secret_dir = tmp_path / "user_secret_name"
    _exe(secret_dir, BIN)
    raw = cmddiag.collect(_spawn_error(BIN), env={"PATH": str(secret_dir)})["missing_command"]
    raw["found_in"].append("not_a_known_dir")  # 未知枚举必须被滤掉
    raw["path_has"]["/Users/zhang/private"] = True
    out = diagnostics._missing_command_for_export({"at": 1.0, **raw})
    assert out["command"] == "cmd:" + diagnostics._digest(BIN)
    assert out["on_path"] is True
    assert set(out["path"]["has"]) <= cmddiag.dir_names()
    assert out["found_in"] == []
    blob = repr(out)
    assert "user_secret_name" not in blob and str(tmp_path) not in blob and BIN not in blob
    pub = diagnostics._missing_command_for_export({"command": PUBLIC})
    assert pub["command"] == PUBLIC


class _Err(Exception):
    def __init__(self, extra):
        super().__init__("x")
        self.extra = extra


def test_store_is_per_project_and_surfaces_in_project_section(tmp_path):
    deprepair.clear_missing_dependency_evidence()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    fact = {
        "command": PUBLIC,
        "on_path": False,
        "path_segments": 3,
        "path_has": {"homebrew_bin": False},
        "found_in": ["homebrew_cellar"],
        "keg_present": True,
        "keg_unlinked": True,
    }
    assert deprepair.note_missing_command_of(a, _Err({"missing_command": fact})) is True
    assert deprepair.note_missing_command_of(a, _Err({})) is False
    assert deprepair.recent_missing_commands(b) == []
    recs = deprepair.recent_missing_commands(a)
    assert len(recs) == 1
    section = diagnostics._project_section(
        {"open": True, "figures_dir": str(a), "missing_commands": recs}, []
    )
    [row] = section["missing_commands"]
    assert row["command"] == PUBLIC and row["keg_unlinked"] is True
    assert row["found_in"] == ["homebrew_cellar"]
    assert str(a) not in repr(section["missing_commands"])
    deprepair.clear_missing_dependency_evidence()


def test_readme_mentions_missing_commands():
    # 诊断包 README 中英文两段都要说明新增内容
    import inspect

    src = inspect.getsource(diagnostics)
    assert src.count("project.missing_commands") >= 2
