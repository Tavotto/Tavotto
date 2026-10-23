"""Scripts and workers run in the scientific interpreter, not Tavotto's own venv."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import compat_guardian as guardian  # noqa: E402
import compat_guardian_research as research  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    guardian.reset_science_cache()
    yield
    guardian.reset_science_cache()


def _probe(missing=(), facts=None):
    env = {"fingerprint": "f" * 64, "facts": facts or {"python": "3.13"}}
    out = json.dumps({"env": env, "missing": list(missing)})
    return subprocess.CompletedProcess([], 0, stdout=out, stderr="")


def test_default_is_this_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("TAVOTTO_SCIENCE_PYTHON", raising=False)
    assert guardian.science_python() == sys.executable
    assert guardian.isolated_env(tmp_path)["TAVOTTO_WORKER_PYTHON"] == sys.executable


def test_selected_science_python_is_the_worker_and_reaches_the_child(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_SCIENCE_PYTHON", "/science/bin/python")
    env = guardian.isolated_env(tmp_path)
    assert env["TAVOTTO_WORKER_PYTHON"] == "/science/bin/python"
    assert env["TAVOTTO_SCIENCE_PYTHON"] == "/science/bin/python"


def test_science_interpreter_is_probed_once_for_fingerprint_and_preflight(monkeypatch):
    monkeypatch.setattr(guardian, "science_python", lambda: "/science/bin/python")
    with (
        mock.patch.object(subprocess, "run", return_value=_probe()) as run,
        mock.patch.object(guardian, "actual_environment", return_value={"facts": {}}),
    ):
        assert not [m for m in guardian.preflight() if "@science" in m]
        env = guardian.environment()
        guardian.preflight()
    assert env["fingerprint"] == guardian.canonical(env["facts"])
    assert [c.args[0][0] for c in run.call_args_list].count("/science/bin/python") == 1


def test_preflight_names_a_science_interpreter_without_matplotlib(monkeypatch):
    monkeypatch.setattr(guardian, "science_python", lambda: "/science/bin/python")
    with mock.patch.object(subprocess, "run", return_value=_probe(["matplotlib"])):
        assert "module:matplotlib@science" in guardian.preflight()


def test_render_packages_are_reported_when_present():
    # The development venv carries RenderCore, so the check must see it ...
    assert set(research.renderer_packages(sys.executable)) >= {"uharfbuzz", "pikepdf"}


def test_clean_interpreter_passes_and_dirty_one_fails_the_cli(capsys):
    with mock.patch.object(research, "renderer_packages", return_value=[]):
        assert research.main(["verify-science-environment", "--python", "/x/python"]) == 0
    with mock.patch.object(research, "renderer_packages", return_value=["uharfbuzz"]):
        assert research.main(["verify-science-environment", "--python", "/x/python"]) == 1
    assert "uharfbuzz" in capsys.readouterr().out


def test_preflight_checks_each_side_for_what_it_really_imports(monkeypatch):
    """The Guardian parent needs numpy/Pillow for pixel comparison; scripts/workers need them too."""
    monkeypatch.setattr(guardian, "science_python", lambda: "/science/bin/python")
    real_find = guardian.importlib.util.find_spec
    monkeypatch.setattr(
        guardian.importlib.util,
        "find_spec",
        lambda n, *a: None if n == "numpy" else real_find(n, *a),
    )
    with mock.patch.object(subprocess, "run", return_value=_probe()):
        assert "module:numpy@guardian" in guardian.preflight()
        assert not [m for m in guardian.preflight() if m.endswith("@science")]
    guardian.reset_science_cache()
    with mock.patch.object(subprocess, "run", return_value=_probe(["numpy"])):
        assert "module:numpy@science" in guardian.preflight()
    # matplotlib is never required of the Guardian parent when a science interpreter is selected.
    monkeypatch.setattr(
        guardian.importlib.util,
        "find_spec",
        lambda n, *a: None if n == "matplotlib" else real_find(n, *a),
    )
    guardian.reset_science_cache()
    with mock.patch.object(subprocess, "run", return_value=_probe()):
        assert guardian.preflight() == []


def test_environment_identity_changes_when_either_side_changes(monkeypatch):
    monkeypatch.setattr(guardian, "science_python", lambda: "/science/bin/python")
    science = {"python": "3.13", "key_distributions": {"matplotlib": "3.11.1"}}
    guard = {"key_distributions": {"uharfbuzz": "0.56.1"}}

    def identity(sci, gua):
        guardian.reset_science_cache()
        with (
            mock.patch.object(subprocess, "run", return_value=_probe(facts=sci)),
            mock.patch.object(guardian, "actual_environment", return_value={"facts": gua}),
        ):
            return guardian.environment()

    base = identity(science, guard)
    assert base["facts"]["guardian"] == guard
    assert base["facts"]["key_distributions"] == science["key_distributions"]
    assert base["fingerprint"] == guardian.canonical(base["facts"])
    changed_guard = {"key_distributions": {"uharfbuzz": "0.57.0"}}
    assert identity(science, changed_guard)["fingerprint"] != base["fingerprint"]
    assert identity({**science, "python": "3.13.1"}, guard)["fingerprint"] != base["fingerprint"]


def _unusable_interpreters(tmp_path):
    plain = tmp_path / "not-executable"
    plain.write_text("#!/bin/sh\nexit 0\n")
    plain.chmod(0o644)
    failing = tmp_path / "fails-at-start"
    failing.write_text("#!/bin/sh\necho boom >&2\nexit 1\n")
    failing.chmod(0o755)
    return {
        "missing": str(tmp_path / "does-not-exist"),
        "not_executable": str(plain),
        "exits_nonzero": str(failing),
    }


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell stubs")
@pytest.mark.parametrize("kind", ["missing", "not_executable", "exits_nonzero"])
def test_unusable_science_interpreter_is_environment_unavailable_not_a_crash(
    kind, monkeypatch, tmp_path
):
    python = _unusable_interpreters(tmp_path)[kind]
    monkeypatch.setenv("TAVOTTO_SCIENCE_PYTHON", python)
    env = guardian.environment()  # must not raise
    assert env["fingerprint"] == guardian.canonical(env["facts"])
    assert env["facts"]["science_interpreter"]["usable"] is False
    assert "science_python:unusable" in guardian.preflight()
    native_leg = mock.Mock(name="run_driver")
    cm = types.ModuleType("compat_matrix")
    cm.run_driver = cm.materialize = native_leg
    with (
        mock.patch.dict(sys.modules, {"compat_matrix": cm}),
        mock.patch.object(guardian, "selected_registry", return_value={"c": {"id": "c"}}),
        mock.patch.object(guardian, "finalize_row", side_effect=lambda row: row),
    ):
        row = guardian._execute(
            {
                "suite": "compat_engine",
                "case": "c",
                "work": str(tmp_path),
                "evidence": str(tmp_path),
            }
        )
    assert row["native"]["status"] == "environment_unavailable"
    assert "science_python:unusable" in row["raw"]["missing_prerequisites"]
    assert row["raw"]["science_python_unusable"]
    native_leg.assert_not_called()


@pytest.mark.parametrize(
    "missing",
    [
        ["module:numpy@science"],
        ["module:matplotlib@science"],
        ["science_python:unusable"],
        ["module:numpy@guardian"],
        ["module:PIL@guardian"],
        ["module:numpy"],
    ],
)
def test_blocking_prerequisites_stop_before_the_native_leg(missing, tmp_path):
    native_leg = mock.Mock(name="run_driver")
    cm = types.ModuleType("compat_matrix")
    cm.run_driver = native_leg
    cm.materialize = native_leg
    with (
        mock.patch.dict(sys.modules, {"compat_matrix": cm}),
        mock.patch.object(guardian, "preflight", return_value=missing),
        mock.patch.object(guardian, "environment", return_value={}),
        mock.patch.object(guardian, "selected_registry", return_value={"c": {"id": "c"}}),
        mock.patch.object(guardian, "finalize_row", side_effect=lambda row: row),
    ):
        row = guardian._execute(
            {
                "suite": "compat_engine",
                "case": "c",
                "work": str(tmp_path),
                "evidence": str(tmp_path),
            }
        )
    assert row["native"]["status"] == "environment_unavailable"
    assert row["raw"] == {"missing_prerequisites": missing}
    native_leg.assert_not_called()


def test_non_blocking_gaps_do_not_stop_the_native_leg():
    for item in ("font:x.ttf", "module:flask@guardian", "module:pikepdf"):
        assert not guardian.blocks_execution(item)
    assert guardian.parse_prerequisite("module:numpy@science") == ("module", "numpy", "science")
