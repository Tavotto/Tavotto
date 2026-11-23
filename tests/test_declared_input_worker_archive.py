"""Real safe/workerd input bytes; separate shape-only tests cover parent bookkeeping."""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from tavotto.engine import declaredinputs, executionfact, inputdeclaration, pool, receipt
from test_declared_input_worker import (
    SCRIPT,
    approve,
    implicit_render,
    parent_capture,
    project as project,
)
from test_workerd_pool import _worker as workerd_worker

LIMITS = {"max_files": 100, "max_bytes": 1024 * 1024}


def materialized(worker, response):
    assert {"captured_inputs", "captured_build", "captured_input_archive"} <= response.keys()
    fact = response["captured_inputs"]
    descriptor = response["captured_input_archive"]
    assert fact["execution_id"] == response["execution_id"] == worker.execution_id
    assert fact == worker.last_captured_inputs
    assert descriptor == worker.last_captured_input_archive
    assert inputdeclaration.validate_archive_descriptor(descriptor, fact=fact) == descriptor
    assert "captured_input_archive" not in response["captured_build"]
    archive = inputdeclaration.verify_archive(
        descriptor,
        fact,
        archive_root=worker.out_dir / inputdeclaration.ARCHIVE_DIRNAME,
        limits=LIMITS,
    )
    assert archive is not None
    assert json.loads(archive.graph_bytes) == fact["declaration"]
    stored = {}
    for resource in archive.resources:
        data = resource.path.read_bytes()
        assert len(data) == resource.size_bytes
        assert hashlib.sha256(data).hexdigest() == resource.sha256
        stored[resource.sha256] = data
    return fact, descriptor, stored


def test_actual_worker_materializes_original_source_and_quoted_binary_input(project):
    root, make = project
    quoted = b'"category","value"\r\n"comma, tab\t quote"" line\nnext",1\r\n\0\xff'
    (root / "data/quoted.csv").write_bytes(quoted)
    approve(root, ("data/input.txt", "data/quoted.csv"))
    source = (root / "plot.py").read_bytes()
    data = (root / "data/input.txt").read_bytes()
    worker = make()
    response = worker.ensure_built()
    fact, descriptor, stored = materialized(worker, response)
    assert set(descriptor) == {"version", "key", "revision"}
    assert descriptor["revision"] == fact["revision"]
    assert set(stored.values()) == {source, data, quoted}
    expected = copy.deepcopy(descriptor)
    response["captured_input_archive"]["key"] = "0" * 48
    assert worker.last_captured_input_archive == expected
    assert worker.last_captured_input_archive["key"] != "0" * 48


def test_cached_figure_retains_original_archive_after_inputs_and_source_are_deleted(project):
    root, make = project
    approve(root, ("data/input.txt", "data/unused.txt"))
    worker = make()
    first = worker.ensure_built()
    fact, descriptor, original = materialized(worker, first)
    fact, descriptor = copy.deepcopy(fact), copy.deepcopy(descriptor)
    for relative in ("plot.py", "data/input.txt", "data/unused.txt"):
        (root / relative).unlink()
    response = worker.override("F", [])
    assert response["manifest"]
    after_fact, after_descriptor, after = materialized(worker, response)
    assert (after_fact, after_descriptor, after) == (fact, descriptor, original)
    assert worker.last_input_declaration_issue == "unavailable"
    cached = worker.ensure_built()
    assert cached["captured_input_archive"] == descriptor


def test_current_approval_cannot_rewrite_an_existing_figure_archive(project):
    root, make = project
    approve(root)
    worker = make()
    fact, descriptor, stored = materialized(worker, worker.ensure_built())
    original = copy.deepcopy((fact, descriptor, stored))
    (root / "data/input.txt").write_bytes(b"2\n")
    approve(root, ("data/input.txt", "data/unused.txt"))
    assert materialized(worker, worker.ensure_built()) == original
    assert materialized(worker, worker.override("F", [])) == original


def test_implicit_build_has_the_same_execution_bound_archive(project):
    root, make = project
    approve(root)
    worker = make()
    fact, descriptor, stored = materialized(worker, implicit_render(worker))
    assert descriptor["revision"] == fact["revision"]
    assert (root / "data/input.txt").read_bytes() in stored.values()
    assert worker.last_build_execution_id == fact["execution_id"]


def test_no_declaration_has_no_archive_wire_metadata_or_resource_directory(project):
    _root, make = project
    worker = make()
    response = worker.ensure_built()
    assert not {"captured_inputs", "captured_build", "captured_input_archive"} & response.keys()
    assert worker.last_captured_input_archive is None
    assert not (worker.out_dir / inputdeclaration.ARCHIVE_DIRNAME).exists()


def test_approval_after_a_cached_build_cannot_materialize_old_figure_inputs(project):
    root, make = project
    worker = make()
    old_id = worker.ensure_built()["execution_id"]
    approve(root)
    response = worker.override("F", [])
    assert response["execution_id"] == old_id
    assert "captured_input_archive" not in response
    assert worker.last_captured_input_archive is None
    assert not (worker.out_dir / inputdeclaration.ARCHIVE_DIRNAME).exists()


@pytest.mark.parametrize("failure", ["changed_input", "script_failure", "archive_root_file"])
def test_unsuccessful_materialization_does_not_publish_an_archive_or_break_the_figure(
    project, failure
):
    root, make = project
    approve(root)
    source = SCRIPT
    if failure == "changed_input":
        source += "with open('data/input.txt', 'w') as out: out.write('2\\n')\n"
    elif failure == "script_failure":
        source += "raise RuntimeError('archive test failure after Figure')\n"
    worker = make(text=source)
    archive_root = worker.out_dir / inputdeclaration.ARCHIVE_DIRNAME
    if failure == "archive_root_file":
        worker.out_dir.mkdir(parents=True, exist_ok=True)
        archive_root.write_bytes(b"existing file must stay intact")
    if failure == "script_failure":
        with pytest.raises(pool.WorkerError, match="archive test failure"):
            worker.ensure_built()
    else:
        response = worker.ensure_built()
        assert response["stems"]
        if failure == "changed_input":
            assert (root / "data/input.txt").read_text() == "2\n"
        assert "captured_input_archive" not in response and "captured_inputs" not in response
        assert worker.override("F", [])["manifest"]
    assert worker.last_captured_input_archive is None
    if failure == "archive_root_file":
        assert archive_root.read_bytes() == b"existing file must stay intact"
    else:
        assert not archive_root.exists() or not any(archive_root.iterdir())


def test_one_shot_has_its_own_archive_and_discard_keeps_the_hot_worker_archive(project):
    root, make = project
    approve(root)
    hot, replay = make(), make(oneshot=True)
    fact, descriptor, original = materialized(hot, hot.ensure_built())
    new_fact, new_descriptor, copied = materialized(replay, replay.ensure_built())
    assert new_fact["execution_id"] != fact["execution_id"]
    assert new_descriptor["key"] != descriptor["key"]
    assert new_descriptor["revision"] == descriptor["revision"] and copied == original
    replay_root = replay.out_dir / inputdeclaration.ARCHIVE_DIRNAME
    pool.discard(replay)
    assert not replay_root.exists()
    assert materialized(hot, hot.override("F", [])) == (fact, descriptor, original)


def test_unknown_execution_cannot_grant_the_old_archive_in_a_new_receipt(project):
    root, make = project
    approve(root)
    worker = make()
    response = worker.ensure_built()
    _fact, descriptor, _stored = materialized(worker, response)
    worker.execution_observation_unknown = True
    control = "workerd" if isinstance(worker, pool.WorkerdWorker) else "python_pool"
    result = receipt.from_worker(worker, response, control_plane=control, grant=None)
    assert result.captured_inputs is None and result.captured_input_archive is None
    assert worker.last_captured_input_archive == descriptor  # Historical metadata is retained.


@pytest.fixture
def parent_response(project):
    """Shape-only parent tests: this fixture makes no scientific-execution claim."""
    root, make = project
    approve(root)
    candidate = declaredinputs.payload_for(make())["input_declaration"]
    identity = executionfact.new_id()
    response = parent_capture(root, candidate, identity)
    response["captured_input_archive"] = {
        "version": 1,
        "key": "ab" * 24,
        "revision": response["captured_inputs"]["revision"],
    }
    worker = SimpleNamespace()
    pool._execution_state(worker)
    pool._record_execution(worker, identity)
    pool._accept_captured_inputs(worker, response)
    assert worker.last_captured_input_archive == response["captured_input_archive"]
    return worker, response


def test_parent_archive_shape_acceptance_never_hashes_resources_or_retains_caller_dict(
    parent_response, monkeypatch
):
    worker, response = parent_response

    def forbidden(*_a, **_kw):
        pytest.fail("parent receive lock must not perform archive resource IO")

    monkeypatch.setattr(inputdeclaration, "verify_archive", forbidden)
    expected = copy.deepcopy(response["captured_input_archive"])
    pool._accept_captured_inputs(worker, response)
    response["captured_input_archive"]["key"] = "0" * 48
    assert worker.last_captured_input_archive == expected


@pytest.mark.parametrize("damage", ["missing", "null", "future", "path", "key", "revision"])
def test_parent_invalid_or_absent_descriptor_never_inherits_previous_archive(
    parent_response, damage
):
    worker, response = parent_response
    if damage == "missing":
        response.pop("captured_input_archive")
    elif damage == "null":
        response["captured_input_archive"] = None
    elif damage == "future":
        response["captured_input_archive"]["version"] += 1
    elif damage == "path":
        response["captured_input_archive"]["path"] = "/private/untrusted"
    elif damage == "key":
        response["captured_input_archive"]["key"] = "../untrusted"
    else:
        response["captured_input_archive"]["revision"] = "sha256:" + "0" * 64
    pool._accept_captured_inputs(worker, response)
    assert worker.last_captured_inputs == response["captured_inputs"]
    assert worker.last_captured_input_archive is None


@pytest.mark.parametrize("damage", ["no_fact", "bad_fact", "no_capsule", "archive_in_capsule"])
def test_parent_archive_is_revoked_when_its_matching_fact_or_closed_capsule_is_lost(
    parent_response, damage
):
    worker, response = parent_response
    if damage == "no_fact":
        response.pop("captured_inputs")
    elif damage == "bad_fact":
        response["captured_inputs"]["execution_id"] = executionfact.new_id()
    elif damage == "no_capsule":
        response.pop("captured_build")
    else:
        response["captured_build"]["captured_input_archive"] = response["captured_input_archive"]
    pool._accept_captured_inputs(worker, response)
    assert worker.last_captured_inputs is None
    assert worker.last_captured_build is None
    assert worker.last_captured_input_archive is None


def test_parent_new_execution_clears_the_archive_and_late_old_reply_cannot_replace_it(
    parent_response,
):
    worker, response = parent_response
    newer = executionfact.new_id()
    pool._record_execution(worker, newer)
    assert worker.last_captured_input_archive is None
    current = {"version": 1, "key": "cd" * 24, "revision": "sha256:" + "1" * 64}
    worker.last_captured_input_archive = copy.deepcopy(current)
    pool._accept_captured_inputs(worker, response)
    assert worker.execution_id == newer and worker.last_captured_input_archive == current
    response["execution_id"] = None
    pool._accept_captured_inputs(worker, response)
    assert worker.last_captured_input_archive == current


def test_workerd_unknown_session_replay_keeps_the_same_archive_descriptor(project, monkeypatch):
    from tavotto.engine import workerd_client

    root, make = project
    approve(root)
    candidate = declaredinputs.payload_for(make())["input_declaration"]
    response = parent_capture(root, candidate, executionfact.new_id())
    response["captured_input_archive"] = {
        "version": 1,
        "key": "ef" * 24,
        "revision": response["captured_inputs"]["revision"],
    }
    worker, _client = workerd_worker(
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
    assert result["captured_input_archive"] == worker.last_captured_input_archive
    assert worker.last_captured_input_archive == response["captured_input_archive"]
