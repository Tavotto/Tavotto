"""Automatic environment hints must not touch UNC or device paths before execution."""

import json
from pathlib import Path

import pytest

from tavotto.engine import userenvs


@pytest.mark.parametrize(
    "hint",
    [
        r"\\attacker\share\python.exe",
        "//attacker/share/python.exe",
        r"\\?\UNC\attacker\share\python.exe",
        r"\\.\UNC\attacker\share\python.exe",
        r"\\?\C:\env\python.exe",
        r"\??\UNC\attacker\share\python.exe",
    ],
)
def test_vscode_network_and_device_hints_are_rejected_before_path_expansion(tmp_path, hint):
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"python.defaultInterpreterPath": hint}), encoding="utf-8")
    assert userenvs._vscode_pythons([tmp_path]) == []


def test_network_hint_never_reaches_a_filesystem_predicate(monkeypatch):
    seen = []
    monkeypatch.setattr(Path, "is_file", lambda path: seen.append(str(path)) or True)
    assert not userenvs._is_python_file("//attacker/share/python.exe")
    assert seen == []


def test_network_environment_prefix_never_reaches_a_filesystem_predicate(monkeypatch):
    seen = []
    monkeypatch.setattr(Path, "is_file", lambda path: seen.append(str(path)) or True)
    assert userenvs._prefix_python("//attacker/share") is None
    assert seen == []


def test_local_vscode_hint_and_symlinked_interpreter_remain_candidates(tmp_path):
    env = tmp_path / "env"
    env.mkdir()
    python = env / "python"
    python.write_text("", encoding="utf-8")
    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"python.defaultInterpreterPath": "env/python"}), encoding="utf-8"
    )
    assert userenvs._vscode_pythons([tmp_path]) == [str(python)]
    assert userenvs._is_python_file(str(python))
    alias = env / "python3"
    try:
        alias.symlink_to(python)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert userenvs._is_python_file(str(alias))


def test_project_scan_never_stats_a_network_interpreter_hint(tmp_path, monkeypatch):
    from tavotto.engine import projscan

    settings = tmp_path / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"python.defaultInterpreterPath": "//attacker/share/python.exe"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(userenvs, "_conda_prefixes", lambda *a, **k: [])
    monkeypatch.setattr(userenvs, "_pyenv_pythons", lambda *a, **k: [])
    # T05：导入即扫描会并入「已被明确问过」的登录 shell 答案（进程级缓存）；同进程里别的用例问过真 shell 就会漏进来
    monkeypatch.setattr(userenvs, "_login_shell_cache", {})
    real_is_file = Path.is_file

    def is_file(path):
        assert "attacker" not in str(path), "project scan touched the network hint"
        return real_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file)
    assert projscan.environment_evidence(tmp_path, None)["candidates"] == []
