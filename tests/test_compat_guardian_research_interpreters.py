"""Scripts and workers run in the scientific interpreter, not Tavotto's own venv."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import compat_guardian as guardian  # noqa: E402
import compat_guardian_research as research  # noqa: E402


def test_default_is_this_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVOTTO_SCIENCE_PYTHON", raising=False)
    assert guardian.science_python() == sys.executable
    assert guardian.isolated_env(tmp_path)["TAVOTTO_WORKER_PYTHON"] == sys.executable


def test_selected_science_python_is_the_worker_and_reaches_the_child(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_SCIENCE_PYTHON", "/science/bin/python")
    env = guardian.isolated_env(tmp_path)
    assert env["TAVOTTO_WORKER_PYTHON"] == "/science/bin/python"
    assert env["TAVOTTO_SCIENCE_PYTHON"] == "/science/bin/python"


def test_preflight_and_environment_describe_the_science_interpreter(monkeypatch):
    # Same interpreter stands in for a separate venv; the subprocess paths are what is exercised.
    monkeypatch.setattr(guardian, "science_python", lambda: "/science/bin/python")
    calls = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        calls.append(cmd[0])
        return real([sys.executable, *cmd[1:]], *a, **k)

    with mock.patch.object(subprocess, "run", spy):
        assert not [m for m in guardian.preflight() if m.startswith("science_")]
        facts = guardian.environment()
    assert facts["fingerprint"]
    assert calls and set(calls) == {"/science/bin/python"}


def test_preflight_names_a_science_interpreter_without_matplotlib(monkeypatch):
    monkeypatch.setattr(guardian, "science_python", lambda: "/science/bin/python")
    done = subprocess.CompletedProcess([], 0, stdout="matplotlib\n", stderr="")
    with mock.patch.object(subprocess, "run", return_value=done):
        assert "science_module:matplotlib" in guardian.preflight()


def test_render_packages_are_reported_when_present():
    # The development venv carries RenderCore, so the check must see it ...
    assert set(research.renderer_packages(sys.executable)) >= {"uharfbuzz", "pikepdf"}


def test_clean_interpreter_passes_and_dirty_one_fails_the_cli(capsys):
    with mock.patch.object(research, "renderer_packages", return_value=[]):
        assert research.main(["verify-science-environment", "--python", "/x/python"]) == 0
    with mock.patch.object(research, "renderer_packages", return_value=["uharfbuzz"]):
        assert research.main(["verify-science-environment", "--python", "/x/python"]) == 1
    assert "uharfbuzz" in capsys.readouterr().out
