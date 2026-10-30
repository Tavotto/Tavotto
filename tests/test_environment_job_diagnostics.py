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
            "started_at": time.time() - 3,
            "finished_at": time.time() - 1,
            "activated_at": time.time() - 2,
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
    envdiag.record_job(project, {"outcome": "failed", "finished_at": time.time() - 1})
    envdiag.record_render(project, "bundled", "")
    envdiag.record_render(project, "managed_project_env", "0123456789abcdef")
    (row,) = envdiag.recent(project)
    assert row["installed"] is None and row["failed"] is None and row["requested"] is None
    assert row["render"]["source"] == "bundled" and row["render"]["generation"] is None


def test_retention_expires_individual_rows_and_clears_cached_and_persisted_data(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(envdiag.config, "data_dir", lambda: tmp_path / "data")
    now = time.time()
    project = tmp_path / "project"
    key = envdiag._key(project)
    path = envdiag._path(key)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            [
                {"finished_at": now - envdiag._RETENTION_S - 1, "requested": ["old-package"]},
                {"finished_at": now, "requested": ["new-package"]},
            ]
        )
    )
    envdiag._cache.pop(key, None)
    assert [row["requested"] for row in envdiag.recent(project)] == [["new-package"]]

    def wait_for_disk(expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if json.loads(path.read_text()) == expected:
                return
            time.sleep(0.01)
        pytest.fail("expired package data remained on disk")

    wait_for_disk(envdiag._cache[key])
    monkeypatch.setattr(envdiag.time, "time", lambda: now + envdiag._RETENTION_S + 1)
    assert envdiag.recent(project) == []
    wait_for_disk([])


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


@pytest.mark.parametrize("code", ["missing_dependency", "script_error", "missing_input"])
def test_worker_is_observed_even_when_cold_admission_fails(monkeypatch, code):
    worker = object()
    seen = []
    monkeypatch.setattr(
        app,
        "_admit_artifact",
        lambda *args: (_ for _ in ()).throw(app.engine_pool.WorkerError("failed build", code=code)),
    )
    monkeypatch.setattr(app, "_svg_execution_result", lambda worker: {})
    monkeypatch.setattr(app, "_switched_to_project_env", lambda worker, error: False)
    with pytest.raises(app.engine_pool.WorkerError):
        app._engine_attempt("id", worker, "stem", lambda *args: None, observe_worker=seen.append)
    assert seen == [worker]


@pytest.mark.parametrize("code", ["queue_superseded", "session_dead"])
def test_unexecuted_render_is_not_recorded(monkeypatch, code):
    worker = object()
    seen = []
    monkeypatch.setattr(
        app,
        "_admit_artifact",
        lambda *args: (_ for _ in ()).throw(app.engine_pool.WorkerError("not executed", code=code)),
    )
    monkeypatch.setattr(app, "_svg_execution_result", lambda worker: {})
    monkeypatch.setattr(app, "_switched_to_project_env", lambda worker, error: False)
    with pytest.raises(app.engine_pool.WorkerError):
        app._engine_attempt("id", worker, "stem", lambda *args: None, observe_worker=seen.append)
    assert seen == []


def test_stale_artifact_does_not_mask_the_next_admitted_render(monkeypatch):
    seen = []
    old_worker, new_worker = object(), object()

    def admit(worker, stem, context):
        if worker is old_worker:
            raise app.engine_artifactcontext.ArtifactContextError("source_changed", "stale")

    monkeypatch.setattr(app, "_admit_artifact", admit)
    monkeypatch.setattr(app, "_svg_execution_result", lambda worker: {})
    with pytest.raises(app.engine_artifactcontext.ArtifactContextError):
        app._engine_attempt("id", old_worker, "stem", lambda *args: {}, observe_worker=seen.append)
    app._engine_attempt("id", new_worker, "stem", lambda *args: {}, observe_worker=seen.append)
    assert seen == [new_worker]


@pytest.mark.parametrize("failed_build", [False, True])
def test_render_observation_failure_never_changes_render_result(monkeypatch, caplog, failed_build):
    worker = object()
    monkeypatch.setattr(app, "_admit_artifact", lambda *_: None)
    monkeypatch.setattr(app, "_svg_execution_result", lambda _worker: {})
    monkeypatch.setattr(app, "_switched_to_project_env", lambda _worker, _error: False)

    def observe(_worker):
        raise RuntimeError("private path or output must not be logged")

    def run(*_args):
        if failed_build:
            raise app.engine_pool.WorkerError("missing", code="missing_dependency")
        return {"result": "ok"}

    if failed_build:
        with pytest.raises(app.engine_pool.WorkerError) as exc:
            app._engine_attempt("id", worker, "stem", run, observe_worker=observe)
        assert exc.value.code == "missing_dependency"
    else:
        _, _, result = app._engine_attempt("id", worker, "stem", run, observe_worker=observe)
        assert result == {"result": "ok"}
    assert "private path or output must not be logged" not in caplog.text


@pytest.mark.parametrize("entry", ["repair", "package"])
@pytest.mark.parametrize("outcome", ["done", "failed", "cancelled"])
def test_already_locked_generation_callers_record_terminal_jobs(
    tmp_path, monkeypatch, entry, outcome
):
    from types import SimpleNamespace

    monkeypatch.setattr(envdiag.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(deprepair, "_emit", lambda *a, **kw: None)
    monkeypatch.setattr(deprepair, "_emit_job", lambda *a, **kw: None)
    monkeypatch.setattr(deprepair, "_emit_repair_step", lambda *a, **kw: None)
    monkeypatch.setattr(deprepair, "_emit_generation_step", lambda *a, **kw: None)
    monkeypatch.setattr(deprepair.pool, "note_unreadable_install", lambda *a, **kw: None)
    monkeypatch.setattr(deprepair.managedenv, "read_manifest", lambda *a: {})
    monkeypatch.setattr(
        deprepair.pool,
        "mutating_environment",
        lambda *a, **kw: pytest.fail("already-locked caller must not reacquire the lease"),
    )
    project = str(tmp_path / "project")
    requirement = deprepair.depresolve.DependencyRequirement("openpyxl", "openpyxl")
    plan = SimpleNamespace(
        plan_id="repair",
        project=project,
        project_id="pj",
        script="plot.py",
        python="",
        target_kind=deprepair.TARGET_MANAGED,
        requirement=requirement,
        widened=None,
        pip_inputs=deprepair.PipInputs(("openpyxl",), (), {}, False, ()),
        private_python=None,
        impact={},
    )
    job = SimpleNamespace(
        job_id="package",
        project=project,
        op="install",
        distribution="openpyxl",
        requirement="openpyxl",
        python="",
        creates_environment=True,
        private_python=None,
    )

    def generation(generation_job, _cancel, _key):
        generation_job.diagnostic.update(
            requested=["openpyxl"],
            installed=[{"name": "openpyxl", "version": "3.1.5"}],
            committed=outcome == "done",
        )
        generation_job.emit(outcome)
        if outcome == "failed":
            raise deprepair.RepairError(deprepair.ERROR_FAILED, "private pip output")
        if outcome == "cancelled":
            return {"ok": False, "state": "cancelled"}
        return {
            "ok": True,
            "installed": {"openpyxl": "3.1.5"},
            "python": "python",
            "generation": "a" * 16,
        }

    monkeypatch.setattr(deprepair, "_run_generation_locked", generation)

    def invoke():
        if entry == "repair":
            return deprepair._run_install(plan, "held", None, None)
        return deprepair._run_package_job(job, "held", None, None)

    if outcome == "failed":
        with pytest.raises(deprepair.RepairError):
            invoke()
    else:
        invoke()
    rows = envdiag.recent(project)
    assert len(rows) == 1
    assert rows[0]["outcome"] == outcome
    assert rows[0]["requested"] == ["openpyxl"]
    assert rows[0]["installed"] == [{"name": "openpyxl", "version": "3.1.5"}]
    assert "private pip output" not in json.dumps(rows)
