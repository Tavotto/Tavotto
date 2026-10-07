"""Import-time environment evidence must read project hint files without following links.

Thread PRRT_kwDOT51-YM6p56IM: a preserved symlink/junction or FIFO at `.vscode/settings.json` must not
be opened (an outside/UNC target can contact a server; an endless target hangs the scan).
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tavotto.engine import projscan, userenvs

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX link/FIFO semantics")


def _python(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / "python"
    exe.write_text("", encoding="utf-8")
    return exe


def _settings(path: Path, interpreter: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"python.defaultInterpreterPath": str(interpreter)}), encoding="utf-8"
    )
    return path


def _sources(root: Path, **kw) -> list[str]:
    return [e["python"] for e in userenvs.discover(root, None, ask_login_shell=False, **kw)]


@posix_only
def test_symlinked_settings_file_is_not_read_by_the_scan_path(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    exe = _python(tmp_path / "outside" / "env")
    target = _settings(tmp_path / "outside" / "settings.json", exe)
    (project / ".vscode").mkdir()
    (project / ".vscode" / "settings.json").symlink_to(target)

    assert str(exe) not in _sources(project, no_follow=True)
    # 非扫描的老路径（准备 / 依赖门）保持跟随用户自己的链接：dotfile 管理是常见布局
    assert str(exe) in _sources(project)


@posix_only
def test_symlinked_vscode_directory_is_not_traversed_by_the_scan_path(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    exe = _python(tmp_path / "outside" / "env")
    _settings(tmp_path / "outside" / "vscode" / "settings.json", exe)
    (project / ".vscode").symlink_to(tmp_path / "outside" / "vscode", target_is_directory=True)

    assert str(exe) not in _sources(project, no_follow=True)


@posix_only
def test_symlinked_python_version_and_environment_yml_are_not_read(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pv").write_text("3.12.1\n", encoding="utf-8")
    (outside / "env.yml").write_text("name: x\n", encoding="utf-8")
    (project / ".python-version").symlink_to(outside / "pv")
    (project / "environment.yml").symlink_to(outside / "env.yml")
    seen: list[str] = []
    real = userenvs._read_hint_text

    def spy(base, *parts, no_follow=False):
        try:
            return real(base, *parts, no_follow=no_follow)
        except OSError:
            seen.append("/".join(parts))
            raise

    monkeypatch.setattr(userenvs, "_read_hint_text", spy)
    userenvs.discover(project, None, ask_login_shell=False, no_follow=True)
    assert {".python-version", "environment.yml"} <= set(seen)


@posix_only
@pytest.mark.parametrize("no_follow", [True, False])
def test_fifo_settings_file_neither_blocks_nor_is_read(tmp_path, no_follow):
    project = tmp_path / "project"
    (project / ".vscode").mkdir(parents=True)
    os.mkfifo(project / ".vscode" / "settings.json")

    # 没有写端：普通 open/read 会永远阻塞。这里必须立刻返回空。
    assert userenvs._vscode_pythons([project], no_follow=no_follow) == []


@pytest.mark.parametrize("no_follow", [True, False])
def test_oversized_settings_file_is_ignored_not_half_read(tmp_path, no_follow):
    project = tmp_path / "project"
    exe = _python(tmp_path / "env")
    settings = _settings(project / ".vscode" / "settings.json", exe)
    with settings.open("a", encoding="utf-8") as fh:
        fh.write(" " * (userenvs.MAX_HINT_BYTES + 1))

    assert userenvs._vscode_pythons([project], no_follow=no_follow) == []


@pytest.mark.parametrize("no_follow", [True, False])
def test_regular_settings_file_still_yields_the_candidate(tmp_path, no_follow):
    project = tmp_path / "project"
    exe = _python(tmp_path / "env")
    _settings(project / ".vscode" / "settings.json", exe)

    assert userenvs._vscode_pythons([project], no_follow=no_follow) == [str(exe)]


def test_windows_shaped_junction_is_refused_before_any_open(tmp_path, monkeypatch):
    """Synthetic: a name-surrogate reparse point (junction) must be rejected from lstat metadata alone."""
    project = tmp_path / "project"
    exe = _python(tmp_path / "env")
    _settings(project / ".vscode" / "settings.json", exe)
    vscode = project / ".vscode"

    class Junction:
        st_mode = stat.S_IFDIR
        st_file_attributes = 0x400
        st_reparse_tag = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT

    real_lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda p: Junction() if p == vscode else real_lstat(p))
    opened: list[object] = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda *a, **k: opened.append(a[0]) or real_open(*a, **k))

    assert userenvs._vscode_pythons([project], no_follow=True) == []
    assert opened == []


def test_project_scan_asks_for_no_follow_environment_hints(tmp_path, monkeypatch):
    (tmp_path / "plot.py").write_text("import matplotlib.pyplot as plt\nplt.savefig('a.png')\n")
    seen: dict = {}

    def fake(root, script=None, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(userenvs, "discover", fake)
    projscan.environment_evidence(tmp_path, None)
    # T05：None = 不启动登录 shell（与 False 同样不问），只并入已被明确问过的答案
    assert seen == {"ask_login_shell": None, "no_follow": True}


@posix_only
def test_project_scan_report_ignores_a_redirected_settings_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    exe = _python(tmp_path / "outside" / "env")
    target = _settings(tmp_path / "outside" / "settings.json", exe)
    (project / ".vscode").mkdir()
    (project / ".vscode" / "settings.json").symlink_to(target)
    (project / "plot.py").write_text("import matplotlib.pyplot as plt\nplt.savefig('a.png')\n")

    report = projscan.scan(project)

    assert all("vscode" not in c["sources"] for c in report["environment"]["candidates"])
