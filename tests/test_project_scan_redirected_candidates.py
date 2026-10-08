"""导入即扫描：项目派生的候选解释器路径经过重定向时，不得在探测前跟随（Codex 线程 4210815135）。

项目里普通的 `.vscode/settings.json` 指向 `env/python(.exe)`，而 `env` 是指向攻击者 UNC 共享的
junction / 符号链接：`is_file` / `realpath` / `stat` / 指纹都会跟随它（SMB / WebDAV 认证泄露或挂起）。
这里用「路径一被跟随就记一笔」的绊线断言：目标（以及穿过重定向的路径）从头到尾没被任何会跟随链接
的谓词碰过，结果被拒并进账本（`symlinked_dir`，partial）。
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import projectenv, projscan, scanbudget, userenvs

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")

_FOLLOWING = ("stat", "access")
_FOLLOWING_PATH = ("realpath", "isfile", "exists", "isdir")


class Tripwire:
    """记录所有「会跟随链接」的谓词被问到的、穿过 `watched` 的路径。lstat 不记（它不跟随）。"""

    def __init__(self, monkeypatch, watched: list[Path]):
        self.hits: list[str] = []
        self._watched = [os.path.normcase(str(w)) for w in watched]
        real_stat, real_access = os.stat, os.access

        def _note(path):
            try:
                text = os.path.normcase(os.fspath(path))
            except TypeError:
                return
            if any(text == w or text.startswith(w + os.sep) for w in self._watched):
                self.hits.append(text)

        def stat_(path, *a, **k):
            if k.get("follow_symlinks", True):
                _note(path)
            return real_stat(path, *a, **k)

        def access_(path, *a, **k):
            _note(path)
            return real_access(path, *a, **k)

        monkeypatch.setattr(os, "stat", stat_)
        monkeypatch.setattr(os, "access", access_)
        for name in _FOLLOWING_PATH:
            real = getattr(os.path, name)

            def wrap(path, *a, _real=real, **k):
                _note(path)
                return _real(path, *a, **k)

            monkeypatch.setattr(os.path, name, wrap)

        def popen(*a, **k):
            self.hits.append("spawn:" + repr(a[:1]))
            raise AssertionError("scan must not start a process")

        monkeypatch.setattr(subprocess, "Popen", popen)


def _script(project: Path) -> None:
    (project / "plot.py").write_text("import matplotlib.pyplot as plt\nplt.savefig('a.png')\n")


def _settings(project: Path, value: str) -> None:
    (project / ".vscode").mkdir(parents=True, exist_ok=True)
    (project / ".vscode" / "settings.json").write_text(
        json.dumps({"python.defaultInterpreterPath": value}), encoding="utf-8"
    )


def _outside_venv(tmp_path: Path) -> Path:
    outside = tmp_path / "outside" / "env"
    (outside / "bin").mkdir(parents=True)
    (outside / "bin" / "python").write_text("", encoding="utf-8")
    (outside / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
    return outside


def _find_ledger(report: dict) -> list[dict]:
    return report["issues"]


@posix_only
def test_vscode_hint_through_redirected_env_dir_is_rejected_without_following(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    outside = _outside_venv(tmp_path)
    (project / "env").symlink_to(outside, target_is_directory=True)
    _settings(project, "env/bin/python")
    _script(project)
    root = Path(os.path.realpath(project))  # 先算好：绊线只盯 `env` 之下与目标

    trip = Tripwire(monkeypatch, [root / "env", outside])
    report = projscan.scan(root)

    assert trip.hits == []
    candidates = report["environment"]["candidates"]
    assert candidates == []
    rows = _find_ledger(report)
    assert {"code": "symlinked_dir", "path": "env"}.items() <= next(
        r for r in rows if r["code"] == "symlinked_dir"
    ).items()
    assert report["state"] == "partial"


@posix_only
def test_discover_without_hints_does_not_probe_a_redirected_env_venv(tmp_path, monkeypatch):
    """`env` 在 VENV_DIRNAMES 里：连 settings.json 都不需要，项目 venv 发现本身也不能跟随它。"""
    project = tmp_path / "project"
    project.mkdir()
    outside = _outside_venv(tmp_path)
    (project / "env").symlink_to(outside, target_is_directory=True)
    root = Path(os.path.realpath(project))
    budget = scanbudget.Budget()

    trip = Tripwire(monkeypatch, [root / "env", outside])
    assert projectenv.discover(root, None, no_follow=True, budget=budget) == []

    assert trip.hits == []
    assert [i["path"] for i in budget.issues() if i["code"] == "symlinked_dir"] == ["env"]


@posix_only
def test_userenvs_discover_drops_redirected_project_candidate_before_is_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = _outside_venv(tmp_path)
    (project / "env").symlink_to(outside, target_is_directory=True)
    _settings(project, "${workspaceFolder}/env/bin/python")
    root = Path(os.path.realpath(project))
    budget = scanbudget.Budget()

    trip = Tripwire(monkeypatch, [root / "env", outside])
    found = userenvs.discover(root, None, ask_login_shell=False, no_follow=True, budget=budget)

    assert trip.hits == []
    assert not [e for e in found if "env" in e["python"].split(os.sep)[len(root.parts) :]]
    assert any(i["code"] == "symlinked_dir" for i in budget.issues())


@posix_only
def test_remembered_decision_through_redirect_is_not_probed(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    outside = _outside_venv(tmp_path)
    (project / "env").symlink_to(outside, target_is_directory=True)
    root = Path(os.path.realpath(project))
    monkeypatch.setattr(
        projectenv.config,
        "project_settings",
        lambda _p: {
            projectenv.SETTINGS_KEY: {"python_relative": "env/bin/python", "mode": "pinned"}
        },
    )
    budget = scanbudget.Budget()

    trip = Tripwire(monkeypatch, [root / "env", outside])
    evidence = projscan.environment_evidence(root, None, budget)

    assert trip.hits == []
    assert evidence["candidates"] == []
    assert evidence["remembered"]["exists"] is False
    assert any(i["code"] == "symlinked_dir" for i in budget.issues())


@posix_only
def test_venv_python_symlink_inside_a_clean_venv_is_still_a_candidate(tmp_path):
    """合法 venv 的 `bin/python` 本来就是软链接：最后一级放行，目录级重定向才拒。"""
    project = tmp_path / "project"
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
    base = tmp_path / "base-python"
    base.write_text("", encoding="utf-8")
    (project / ".venv" / "bin" / "python").symlink_to(base)
    root = Path(os.path.realpath(project))

    assert (
        scanbudget.redirected_component(
            root, root / ".venv" / "bin" / "python", allow_final_link=True
        )
        is None
    )
    assert projectenv.discover(root, None, no_follow=True) == [str(root / ".venv")]


@posix_only
def test_machine_level_candidates_are_unchanged(tmp_path):
    outside = _outside_venv(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    _settings(project, str(outside / "bin" / "python"))
    # 项目外的绝对路径：不是项目派生，仍按老路径探测并保留
    found = userenvs.discover(project, None, ask_login_shell=False, no_follow=True)
    assert str(outside / "bin" / "python") in [e["python"] for e in found]
    assert scanbudget.redirected_component(project, outside / "bin" / "python") is None


@posix_only
def test_dotdot_through_a_link_is_refused_not_collapsed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = _outside_venv(tmp_path)
    (project / "env").symlink_to(outside, target_is_directory=True)
    assert scanbudget.redirected_component(project, project / "env" / ".." / "x") == "env"
    (project / "d").mkdir()
    assert scanbudget.redirected_component(project, project / "d" / ".." / "x") == "d/.."


class _Junction:
    st_mode = stat.S_IFDIR
    st_file_attributes = 0x400
    st_reparse_tag = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT


def test_windows_shaped_junction_env_is_rejected_from_lstat_metadata(tmp_path, monkeypatch):
    """合成：`env` 是名称替身重解析点（junction，目标 UNC）。只凭 lstat 元数据拒，目标不被探。"""
    project = tmp_path / "project"
    (project / "env" / "bin").mkdir(parents=True)
    (project / "env" / "bin" / "python").write_text("", encoding="utf-8")
    (project / "env" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    _settings(project, "env/bin/python")
    _script(project)
    root = Path(os.path.realpath(project))
    junction = os.path.normcase(str(root / "env"))
    real_lstat = os.lstat
    monkeypatch.setattr(
        os,
        "lstat",
        lambda p, *a, **k: (
            _Junction() if os.path.normcase(os.fspath(p)) == junction else real_lstat(p, *a, **k)
        ),
    )

    trip = Tripwire(monkeypatch, [root / "env"])
    report = projscan.scan(root)

    assert trip.hits == []
    assert report["environment"]["candidates"] == []
    assert any(
        r["code"] == "symlinked_dir" and r.get("path") == "env" for r in _find_ledger(report)
    )
