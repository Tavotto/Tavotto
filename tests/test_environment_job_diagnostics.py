"""Managed-job observations are bounded, scoped and contain no pip text or paths."""

import json
import time
import zipfile
from contextlib import nullcontext
from io import BytesIO

import pytest

from tavotto import app
from tavotto.engine import deprepair, diagnostics, envdiag


def test_job_and_first_render_survive_reload_without_paths_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(envdiag.config, "data_dir", lambda: tmp_path / "data")
    project, other = tmp_path / "project", tmp_path / "other"
    envdiag.record_job(
        project,
        {
            "requested": ["openpyxl", "https://private.example/secret", "sk-Secret123"],
            "installed": [
                {"name": "openpyxl", "version": "3.1.5"},
                {"name": "/private/secret", "version": "1.0"},
            ],
            "failed": ["missing-wheel"],
            "source": "custom",
            "outcome": "done",
            "started_at": 100.0,
            "finished_at": 101.0,
            "activated_at": 100.5,
            "committed": True,
            "generation": "0123456789abcdef",
            "pip_output": "PRIVATE_PIP_OUTPUT",
            "index_url": "https://secret.example/token",
        },
    )
    envdiag.record_render(project, "managed_project_env", "0123456789abcdef")
    # Wait for the owned background writer so the reload checks disk, not the cache.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            raw = envdiag._path(envdiag._key(project)).read_text()
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        if '"render": {' in raw:
            break
        time.sleep(0.01)
    envdiag._cache.clear()
    (row,) = envdiag.recent(project)
    assert row["requested"] == ["openpyxl"]
    assert row["installed"] == [{"name": "openpyxl", "version": "3.1.5"}]
    assert row["failed"] == ["missing-wheel"] and row["committed"] is True
    assert row["render"]["generation"] == row["generation"]
    assert envdiag.recent(other) == []
    report = diagnostics._project_section({"open": True, "managed_environment_jobs": [row]}, [])
    exported = json.dumps(report)
    for secret in ("PRIVATE_PIP_OUTPUT", "/private/secret", "secret.example", "sk-Secret123"):
        assert secret not in exported


def test_unknown_values_stay_unknown_and_later_renders_do_not_replace_first(tmp_path, monkeypatch):
    monkeypatch.setattr(envdiag.config, "data_dir", lambda: tmp_path / "data")
    project = tmp_path / "project"
    envdiag.record_job(project, {"outcome": "failed", "finished_at": 200.0})
    envdiag.record_render(project, "bundled", "")
    envdiag.record_render(project, "managed_project_env", "0123456789abcdef")
    (row,) = envdiag.recent(project)
    assert row["installed"] is None and row["failed"] is None and row["requested"] is None
    assert row["render"]["source"] == "bundled" and row["render"]["generation"] is None


def test_finished_job_is_recorded_before_completion_event(tmp_path, monkeypatch):
    monkeypatch.setattr(envdiag.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(deprepair.managedenv, "python_of", lambda project: None)
    monkeypatch.setattr(deprepair.pool, "mutating_environment", lambda *a, **k: nullcontext())
    observed = []
    project = str(tmp_path / "project")

    def emit(state, **kwargs):
        observed.append((state, envdiag.recent(project)))
        return {"state": state, **kwargs}

    job = deprepair._GenerationJob(
        progress_id="test",
        project=project,
        script="",
        delta=(),
        constraints=(),
        hashes={},
        require_hashes=False,
        needed_imports=(),
        record=(),
        reason="",
        identity="",
        emit=emit,
        on_log=lambda text: None,
    )

    def run(entered, cancel, key):
        entered.diagnostic["requested"] = ["openpyxl"]
        entered.emit(deprepair.STATE_DONE, result={"ok": True})
        return {"ok": True}

    monkeypatch.setattr(deprepair, "_run_generation_locked", run)
    deprepair._run_generation(job, None)
    assert len(observed) == 1
    assert observed[0][0] == deprepair.STATE_DONE
    assert observed[0][1][0]["requested"] == ["openpyxl"]
    assert len(envdiag.recent(project)) == 1


def test_package_disclosure_is_limited_to_managed_job_section():
    name = "uncommon-research-distribution"
    progress = {"state": "failed", "result": {"installed": {name: "1.2.3"}}}
    assert name not in json.dumps(deprepair.diagnostic_projection(progress))
    report = diagnostics._project_section(
        {
            "open": True,
            "managed_environment_jobs": [{"installed": [{"name": name, "version": "1.2.3"}]}],
        },
        [],
    )
    assert report["managed_environment_jobs"][0]["installed"] == [
        {"name": name, "version": "1.2.3"}
    ]


def test_exported_bundle_discloses_only_the_allowed_job_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(envdiag.config, "data_dir", lambda: tmp_path / "data")
    project = {
        "open": True,
        "managed_environment_jobs": [
            {
                "requested": ["openpyxl", "ghp_12345678901234567890"],
                "installed": [{"name": "openpyxl", "version": "3.1.5"}],
                "pip_output": "PRIVATE_PIP_OUTPUT",
                "index_url": "https://user:secret@example.invalid/simple",
            }
        ],
    }
    section = diagnostics._redact_obj(diagnostics._project_section(project, []))
    bundle = diagnostics.build_bundle(report={"project": section})
    with zipfile.ZipFile(BytesIO(bundle)) as z:
        report = json.loads(z.read("report.json"))
        contents = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    assert report["project"]["managed_environment_jobs"][0]["requested"] == ["openpyxl"]
    assert report["project"]["managed_environment_jobs"][0]["installed"] == [
        {"name": "openpyxl", "version": "3.1.5"}
    ]
    for secret in ("PRIVATE_PIP_OUTPUT", "example.invalid", "ghp_12345678901234567890"):
        assert secret not in contents


def test_worker_is_observed_even_when_cold_admission_fails(monkeypatch):
    worker = object()
    seen = []
    monkeypatch.setattr(
        app,
        "_admit_artifact",
        lambda *args: (_ for _ in ()).throw(
            app.engine_pool.WorkerError("missing", code="missing_dependency")
        ),
    )
    monkeypatch.setattr(app, "_svg_execution_result", lambda worker: {})
    monkeypatch.setattr(app, "_switched_to_project_env", lambda worker, error: False)
    with pytest.raises(app.engine_pool.WorkerError):
        app._engine_attempt("id", worker, "stem", lambda *args: None, observe_worker=seen.append)
    assert seen == [worker]
