"""Actual safe execution captures; transport doubles qualify only parent bookkeeping."""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from tavotto.engine import (
    declaredinputs,
    executionfact,
    inputdeclaration,
    pool,
    workdir,
    workerd_client,
)
from test_workerd_pool import _worker as workerd_worker

SCRIPT = """from pathlib import Path
import matplotlib.pyplot as plt
value = float(Path('data/input.txt').read_text())
fig, ax = plt.subplots(figsize=(1, 1))
ax.plot([0, 1], [value, value])
fig.savefig('F.svg')
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    python = pool.find_worker_python()
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", python)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "engine")
    monkeypatch.setattr(pool.workdir, "mode_for", lambda *_a, **_kw: workdir.MODE_PROJECT_ROOT)
    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    (root / "data/input.txt").write_text("1\n", encoding="utf-8")
    (root / "data/unused.txt").write_text("not read by user code\n", encoding="utf-8")
    workers = []
    supervisor = workerd_client.find_workerd()

    def make(*, text=None, entry="__main__", oneshot=False):
        if text is not None:
            (root / "plot.py").write_text(text, encoding="utf-8")
        worker = (
            pool.one_shot("plot.py", str(root), entry)
            if oneshot
            else (pool.WorkerdWorker if supervisor else pool.EngineWorker)(
                "plot.py", str(root), entry, base_dir=tmp_path / f"worker-{len(workers)}"
            )
        )
        assert isinstance(worker, pool.WorkerdWorker if supervisor else pool.EngineWorker)
        workers.append(worker)
        return worker

    yield root, make
    for worker in workers:
        if worker.base in [pool.ENGINE_CACHE, *pool.ENGINE_CACHE.parents]:
            pytest.fail("fixture must never discard an engine cache ancestor")
        if str(worker.base) in pool._oneshot_bases:
            pool.discard(worker)
        else:
            worker.force_kill()
    if supervisor:
        workerd_client.reset_client()


def approve(root, files=("data/input.txt",)):
    return declaredinputs.declare(
        root,
        script="plot.py",
        cwd_mode=workdir.MODE_PROJECT_ROOT,
        files=list(files),
        expected_revision=declaredinputs.state(root)["revision"],
    )


def capture(worker, response):
    fact = response["captured_inputs"]
    assert inputdeclaration.validate_fact(fact, execution_id=response["execution_id"]) == fact
    assert fact["execution_id"] == worker.execution_id == worker.last_build_execution_id
    assert fact == worker.last_captured_inputs
    capsule = response["captured_build"]
    assert capsule == worker.last_captured_build
    assert capsule["execution_id"] == fact["execution_id"]
    assert capsule["source_sha256"] == fact["declaration"]["script"]["sha256"]
    assert capsule["runtime"]["pid"] > 0
    assert worker.last_build_runtime == capsule["runtime"]
    assert worker.last_build_descriptors == capsule["descriptors"]
    return fact


def implicit_render(worker):
    if isinstance(worker, pool.WorkerdWorker):
        return worker._call("render", pool.REQUEST_TIMEOUT, stem="F", payload={"patches": []})
    return worker.request({"cmd": "override", "stem": "F", "patches": []})


def test_actual_safe_capture_is_bound_to_the_new_execution_and_has_no_private_paths(project):
    root, make = project
    approve(root)
    worker = make()
    response = worker.ensure_built()
    fact = capture(worker, response)
    assert fact["declaration"]["files"][0]["id"] == "data/input.txt"
    assert (
        fact["declaration"]["files"][0]["sha256"]
        == hashlib.sha256((root / "data/input.txt").read_bytes()).hexdigest()
    )
    assert str(root) not in json.dumps(fact)
    assert str(root.as_posix()) not in json.dumps(fact)
    response["captured_build"]["runtime"]["pid"] = -1
    response["captured_inputs"]["declaration"]["files"].clear()
    assert worker.last_build_runtime["pid"] > 0
    assert worker.last_captured_inputs["declaration"]["files"]


def test_no_declaration_keeps_both_response_fields_absent(project):
    _root, make = project
    worker = make()
    response = worker.ensure_built()
    assert "captured_inputs" not in response and "captured_build" not in response
    assert worker.last_captured_inputs is None
    assert declaredinputs.payload_for(worker) == {}


def test_declaring_after_a_hot_build_cannot_certify_the_old_figure(project):
    root, make = project
    worker = make()
    old = worker.ensure_built()["execution_id"]
    approve(root)
    (root / "data/input.txt").write_text("2\n", encoding="utf-8")
    response = worker.ensure_built()
    assert response["execution_id"] == old
    assert "captured_inputs" not in response and worker.last_captured_inputs is None
    rendered = worker.override("F", [])
    assert rendered["execution_id"] == old and "captured_inputs" not in rendered


def test_cached_build_preserves_the_original_capture_after_new_inputs_are_approved(project):
    root, make = project
    approve(root)
    worker = make()
    original = copy.deepcopy(capture(worker, worker.ensure_built()))
    (root / "data/input.txt").write_text("2\n", encoding="utf-8")
    approve(root, ("data/input.txt", "data/unused.txt"))
    response = worker.ensure_built()
    assert capture(worker, response) == original
    assert capture(worker, worker.override("F", [])) == original


def test_cached_ensure_built_keeps_the_frozen_runtime_capsule(project, monkeypatch):
    root, make = project
    approve(root)
    worker = make()
    first = worker.ensure_built()
    original = copy.deepcopy(first["captured_build"])
    name = "_call" if isinstance(worker, pool.WorkerdWorker) else "request"
    actual = getattr(worker, name)

    def observed(*args, **kwargs):
        result = actual(*args, **kwargs)
        result["runtime"] = {"pid": -1, "current_fact": "must not replace the old capture"}
        return result

    monkeypatch.setattr(worker, name, observed)
    second = worker.ensure_built()
    assert second["runtime"]["pid"] == -1
    assert second["captured_build"] == original
    assert worker.last_build_runtime == original["runtime"]
    worker.last_captured_build["runtime"]["inputs"] = {"unrelated": True}
    assert worker.last_build_runtime == original["runtime"]


def test_unavailable_declaration_keeps_the_captured_hot_figure_editable(project):
    root, make = project
    approve(root, ("data/input.txt", "data/unused.txt"))
    worker = make()
    original = copy.deepcopy(capture(worker, worker.ensure_built()))
    (root / "data/unused.txt").unlink()
    rendered = worker.override("F", [])
    assert rendered["manifest"] and capture(worker, rendered) == original
    assert worker.last_input_declaration_issue == "unavailable"
    output = worker.base / "export.pdf"
    exported = worker.export("F", [], str(output), require_built=True)
    assert capture(worker, exported) == original and output.read_bytes().startswith(b"%PDF-")


def test_unavailable_declaration_cannot_certify_a_new_implicit_build(project):
    root, make = project
    approve(root, ("data/input.txt", "data/unused.txt"))
    (root / "data/unused.txt").unlink()
    worker = make()
    rendered = implicit_render(worker)
    assert rendered["manifest"] and executionfact.valid_id(worker.execution_id)
    assert "captured_inputs" not in rendered and worker.last_captured_inputs is None
    assert worker.last_input_declaration_issue == "unavailable"


def test_actual_implicit_render_build_delivers_the_matching_build_capsule(project):
    root, make = project
    approve(root)
    worker = make()
    assert not worker.built and worker.last_build_runtime is None
    response = implicit_render(worker)
    fact = capture(worker, response)
    assert response["manifest"] and worker.built
    assert fact["declaration"]["scope"]["script"] == "plot.py"


def test_actual_changed_input_during_execution_has_no_successful_capture(project):
    root, make = project
    approve(root)
    worker = make(text=SCRIPT + "with open('data/input.txt', 'w') as out: out.write('2\\n')\n")
    response = worker.ensure_built()
    assert response["stems"] and executionfact.valid_id(response["execution_id"])
    assert (root / "data/input.txt").read_text() == "2\n"
    assert "captured_inputs" not in response and worker.last_captured_inputs is None


def test_actual_failed_execution_never_publishes_a_capture(project):
    root, make = project
    approve(root)
    worker = make(text=SCRIPT + "raise RuntimeError('after Figure capture')\n")
    with pytest.raises(pool.WorkerError, match="after Figure capture"):
        worker.ensure_built()
    assert executionfact.valid_id(worker.execution_id)
    assert worker.last_captured_inputs is None


def test_actual_retry_after_failure_binds_a_new_id_in_the_same_pool_generation(project):
    root, make = project
    approve(root)
    text = (
        SCRIPT
        + """if not Path('once').exists():
 with open('once', 'w') as out: out.write('yes')
 raise RuntimeError('first execution only')
"""
    )
    worker = make(text=text)
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    old, generation = worker.execution_id, worker.generation
    assert worker.last_captured_inputs is None
    fact = capture(worker, worker.ensure_built())
    assert fact["execution_id"] != old and worker.generation == generation


def test_declared_but_unread_input_does_not_become_an_observed_python_read(project):
    root, make = project
    approve(root, ("data/input.txt", "data/unused.txt"))
    worker = make()
    response = worker.ensure_built()
    fact = capture(worker, response)
    assert {row["id"] for row in fact["declaration"]["files"]} == {
        "data/input.txt",
        "data/unused.txt",
    }
    observed = json.dumps(response["runtime"]["inputs"])
    assert "data/input.txt" in observed and "unused.txt" not in observed
    assert response["runtime"]["inputs"]["observation"] == "partial"


def test_actual_one_shot_uses_the_same_declaration_payload_and_a_fresh_execution(project):
    root, make = project
    approve(root)
    hot = make()
    original = capture(hot, hot.ensure_built())
    replay = make(oneshot=True)
    reproduced = capture(replay, replay.ensure_built())
    assert original["declaration"] == reproduced["declaration"]
    assert original["revision"] == reproduced["revision"]
    assert original["execution_id"] != reproduced["execution_id"]


def parent_capture(root, candidate, identity):
    """Use the real leaf for parent-only transport tests, without claiming a worker run."""
    attempt = inputdeclaration.begin(
        candidate,
        script="plot.py",
        script_path=str(root / "plot.py"),
        cwd_mode=workdir.MODE_PROJECT_ROOT,
        run_config=None,
        actual_cwd=str(root),
        project_root=str(root),
        sandbox=str(root / "sandbox"),
    )
    assert attempt is not None
    attempt.entered(identity)
    source_sha = hashlib.sha256((root / "plot.py").read_bytes()).hexdigest()
    fact = attempt.finish(source_sha)
    assert fact is not None
    return {
        "execution_id": identity,
        "captured_inputs": fact,
        "captured_build": {
            "execution_id": identity,
            "source_sha256": source_sha,
            "runtime": {"report_origin": "build", "pid": 123},
            "descriptors": [{"stem": "F"}],
        },
    }


def test_workerd_unknown_session_retries_the_exact_same_frozen_candidate(project, monkeypatch):
    from tavotto.engine import workerd_client

    root, make = project
    approve(root)
    candidate = declaredinputs.payload_for(make())["input_declaration"]
    identity = executionfact.new_id()
    response = parent_capture(root, candidate, identity)
    calls = []

    def payload(_worker):
        calls.append(True)
        return {"input_declaration": candidate}

    monkeypatch.setattr(declaredinputs, "payload_for", payload)
    worker, client = workerd_worker(
        monkeypatch,
        root,
        [
            {"session_id": "old"},
            workerd_client.WorkerdError("lost", code="unknown_session"),
            {"session_id": "new"},
            response,
        ],
    )
    worker.built = True
    result = worker.override("F", [])
    sent = [kw["payload"] for op, kw in client.calls if op == "render"]
    assert len(calls) == 1 and len(sent) == 2
    assert sent[0]["input_declaration"] == sent[1]["input_declaration"] == candidate
    assert capture(worker, result)["execution_id"] == identity


@pytest.mark.parametrize("damage", ["capsule_source", "capsule_id", "unknown_fact_field"])
def test_parent_rejects_mixed_or_malformed_capture_capsules(project, damage):
    root, make = project
    approve(root)
    worker = make()
    candidate = declaredinputs.payload_for(worker)["input_declaration"]
    identity = executionfact.new_id()
    response = parent_capture(root, candidate, identity)
    if damage == "capsule_source":
        response["captured_build"]["source_sha256"] = "0" * 64
    elif damage == "capsule_id":
        response["captured_build"]["execution_id"] = executionfact.new_id()
    else:
        response["captured_inputs"]["private_path"] = str(root)
    pool._execution_state(worker)
    pool._record_execution(worker, identity)
    pool._accept_captured_inputs(worker, response)
    assert worker.last_captured_inputs is None


def test_parent_late_old_capture_cannot_replace_a_newer_execution(project):
    root, make = project
    approve(root)
    worker = make()
    candidate = declaredinputs.payload_for(worker)["input_declaration"]
    old = executionfact.new_id()
    response = parent_capture(root, candidate, old)
    new = executionfact.new_id()
    pool._execution_state(worker)
    pool._record_execution(worker, new)
    worker.last_captured_inputs = {"execution_id": new}
    pool._accept_captured_inputs(worker, response)
    assert worker.last_captured_inputs == {"execution_id": new}


def test_parent_new_execution_marker_revokes_the_previous_successful_fact():
    old, new = executionfact.new_id(), executionfact.new_id()
    worker = SimpleNamespace()
    pool._execution_state(worker)
    pool._record_execution(worker, old)
    worker.last_captured_inputs = {"execution_id": old}
    worker.last_captured_build = {"execution_id": old}
    pool._record_execution(worker, new)
    assert worker.last_captured_inputs is None and worker.last_captured_build is None


@pytest.mark.parametrize("via", ["supervisor", "runtime_only", "engine_body"])
def test_only_current_workerd_supervisor_metadata_can_update_the_trusted_pid(project, via):
    root, make = project
    approve(root)
    source_worker = make()
    candidate = declaredinputs.payload_for(source_worker)["input_declaration"]
    worker = (
        pool.EngineWorker.__new__(pool.EngineWorker)
        if via == "engine_body"
        else pool.WorkerdWorker.__new__(pool.WorkerdWorker)
    )
    if via == "engine_body":
        worker.proc = SimpleNamespace(pid=42)
    else:
        worker.child_pid = 42
    identity = executionfact.new_id()
    response = parent_capture(root, candidate, identity)
    if via != "runtime_only":
        response["supervisor_child_pid"] = 123
    pool._execution_state(worker)
    pool._record_execution(worker, identity)
    pool._accept_captured_inputs(worker, response)
    assert worker.child_pid == (123 if via == "supervisor" else 42)
    newer = executionfact.new_id()
    pool._record_execution(worker, newer)
    response["supervisor_child_pid"] = 999
    pool._accept_captured_inputs(worker, response)
    assert worker.child_pid == (123 if via == "supervisor" else 42)
    assert worker.last_captured_inputs is None
