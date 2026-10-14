"""#962：托管环境归属量的是解释器路径身份，不是软链接的基础解释器。"""

import os
from pathlib import Path

import pytest

from tavotto.engine import managedenv, pool


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))


def _register(project: Path, base: Path, generation="linked") -> Path:
    managedenv.register_generation(
        project,
        generation,
        requirements=[],
        constraints=[],
        identity=generation,
        base_python=str(base),
    )
    python = managedenv.generation_python(project, generation)
    python.parent.mkdir(parents=True, exist_ok=True)
    return python


@pytest.mark.skipif(os.name == "nt", reason="真实 POSIX venv 解释器软链接布局")
@pytest.mark.parametrize("layout", ["generation", "legacy"])
def test_user_interpreters_sharing_a_managed_base_keep_their_source(tmp_path, layout):
    project = tmp_path / "paper"
    project.mkdir()
    base = tmp_path / "usr" / "bin" / "python3"
    base.parent.mkdir(parents=True)
    base.write_text("", encoding="utf-8")
    if layout == "generation":
        python = _register(project, base)
    else:
        managedenv.write_manifest(project, managedenv.new_manifest(project, str(base)), strict=True)
        python = managedenv.generation_python(project, managedenv.LEGACY_GENERATION)
        python.parent.mkdir(parents=True)
    python.symlink_to(base)
    user_venv = project / ".venv" / "bin" / "python"
    user_venv.parent.mkdir(parents=True)
    user_venv.symlink_to(base)
    assert managedenv.is_managed_python(project, str(base)) is False
    assert managedenv.is_managed_python(project, str(user_venv)) is False
    assert pool.remembered_source(project, str(base)) == pool.SOURCE_SYSTEM
    assert pool.remembered_source(project, str(user_venv)) == pool.SOURCE_PROJECT_VENV
    assert managedenv.is_managed_python(project, str(python)) is True
    assert pool.remembered_source(project, str(python)) == pool.SOURCE_MANAGED_PROJECT


def test_all_registered_generations_keep_normalized_path_identity(tmp_path):
    project = tmp_path / "paper"
    project.mkdir()
    for generation in ["old", "new"]:
        python = _register(project, tmp_path / "base-python", generation)
        python.write_text("", encoding="utf-8")
        normalized_alias = str(python.parent) + os.sep + "." + os.sep + python.name
        assert managedenv.is_managed_python(project, normalized_alias) is True
        assert pool.remembered_source(project, normalized_alias) == pool.SOURCE_MANAGED_PROJECT
        assert managedenv.is_managed_python(tmp_path / "other-project", str(python)) is False
    assert managedenv.is_managed_python(project, "") is False
    assert managedenv.is_managed_python(project, None) is False
