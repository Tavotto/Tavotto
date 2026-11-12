"""脚本调外部命令却找不到可执行文件：worker 里量的闭集事实 + 出门口径（bundle schema 15，TVD-1FCJ）。"""

from __future__ import annotations

import os
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


def _winlike_error(tmp_path, args, executable=None) -> FileNotFoundError:
    """造一个 Windows 形状的错误：filename 为空，traceback 里有 subprocess.py::_execute_child 帧，局部变量里有 args。"""
    import importlib.util

    src = tmp_path / "fake" / "subprocess.py"
    src.parent.mkdir(exist_ok=True)
    src.write_text(
        "def _execute_child(args, executable):\n"
        "    e = FileNotFoundError(2, 'The system cannot find the file specified')\n"
        "    e.winerror = 2\n"
        "    raise e\n"
    )
    spec = importlib.util.spec_from_file_location("_fake_subprocess", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod._execute_child(args, executable)
    except FileNotFoundError as exc:
        assert exc.filename is None
        return exc
    raise AssertionError("expected FileNotFoundError")


class TestWindowsShapedError:
    def test_list_args(self, tmp_path):
        assert cmddiag.command_of(_winlike_error(tmp_path, ["rsvg-convert", "x"])) == PUBLIC

    def test_str_args_first_word(self, tmp_path):
        assert cmddiag.command_of(_winlike_error(tmp_path, "rsvg-convert x y")) == PUBLIC

    def test_str_args_quoted(self, tmp_path):
        assert cmddiag.command_of(_winlike_error(tmp_path, '"rsvg-convert" "a b"')) == PUBLIC

    def test_executable_wins(self, tmp_path):
        assert cmddiag.command_of(_winlike_error(tmp_path, ["other"], "rsvg-convert")) == PUBLIC

    def test_path_with_separator_rejected(self, tmp_path):
        assert cmddiag.command_of(_winlike_error(tmp_path, ["C:\\x\\tool.exe"])) is None

    def test_no_spawn_frame_no_recovery(self):
        exc = FileNotFoundError(2, "x")
        assert cmddiag.command_of(exc) is None


def _exe(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    f = directory / name
    f.write_text("#!/bin/sh\n")
    f.chmod(0o755)
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

    def test_cellar_with_many_packages_finds_command_in_the_last(self, table, tmp_path):
        cellar = tmp_path / "brew" / "Cellar"
        for i in range(65):
            (cellar / f"pkg{i:02d}" / "1.0" / "bin").mkdir(parents=True)
        _exe(cellar / "pkg64" / "1.0" / "bin", PUBLIC)
        fact = cmddiag.collect(_spawn_error(PUBLIC), env={"PATH": "/usr/bin"})["missing_command"]
        assert fact["found_in"] == ["homebrew_cellar"]
        assert fact["keg_present"] is True
        assert "scan_truncated" not in fact

    def test_scan_cap_reports_truncated_and_no_definite_no(self, table, tmp_path, monkeypatch):
        monkeypatch.setattr(cmddiag, "_GLOB_LIMIT", 3)
        cellar = tmp_path / "brew" / "Cellar"
        for i in range(5):  # 命令文件在，但都不可执行：触顶也找不到
            d = cellar / f"pkg{i}" / "1.0" / "bin"
            d.mkdir(parents=True)
            (d / PUBLIC).write_text("x")
        fact = cmddiag.collect(_spawn_error(PUBLIC), env={"PATH": "/usr/bin"})["missing_command"]
        assert fact["scan_truncated"] is True
        assert fact["found_in"] == []
        assert fact["keg_present"] is None and fact["keg_unlinked"] is None
        exported = diagnostics._missing_command_for_export({**fact, "at": 1.0})
        assert exported["scan_truncated"] is True
        assert exported["keg_present"] is None and exported["keg_unlinked"] is None

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


# --- native 的桥（TVD-V7R4）：判据看**真桥进程**发回的 script_error，父进程看进程内记录
@pytest.mark.parametrize(
    ("name", "argv", "expected"),
    [
        (PUBLIC, (), PUBLIC),
        (PUBLIC, ("x",), PUBLIC),
        ("tavotto-private-tool", (), "tavotto-private-tool"),
        ("tavotto-private-tool", ("x",), ""),  # 带参数运行：不在公开白名单的名字置空，口径同 worker
    ],
)
def test_native_bridge_reports_the_missing_command(tmp_path, monkeypatch, name, argv, expected):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # 开发机可能真装了这个命令
    from support import bridgekit
    from tavotto.engine import bridge

    script = tmp_path / "fig.py"
    script.write_text(  # 现场：图已画好，之后的外部命令才失败
        "import subprocess\nimport matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\nplt.figure()\n"
        f"subprocess.run([{name!r}, 'x'])\n",
        encoding="utf-8",
    )
    spec = bridge.spec_for(
        str(script),
        interpreter=bridgekit.USER_PYTHON,
        cwd=str(tmp_path),
        project_root=str(tmp_path),
        argv=argv,
    )
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
    sess.start()
    try:
        error = sess.wait_event("barrier")["script_error"]
    finally:
        sess.close()
    assert error["missing_command"]["command"] == expected
    assert error["missing_command"]["on_path"] is False


def test_native_script_error_lands_in_the_same_record(tmp_path):
    from tavotto import app

    fact = {"command": PUBLIC, "on_path": False, "path_segments": 3, "path_has": {}, "found_in": []}
    fact |= {"keg_present": False, "keg_unlinked": False}
    app._note_native_missing_command(str(tmp_path), {"message": "x", "missing_command": fact})
    assert deprepair.recent_missing_commands(str(tmp_path))[-1]["command"] == PUBLIC
    app._note_native_missing_command(str(tmp_path), {"message": "没有这份事实"})
    assert len(deprepair.recent_missing_commands(str(tmp_path))) == 1
