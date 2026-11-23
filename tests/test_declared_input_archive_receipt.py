"""Actual cached bytes enter only the receipt's private, verified attachment."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from support import desktopkit, nativekit
from tavotto.engine import (
    declaredinputs,
    desktopproc,
    execspec,
    executionfact,
    inputdeclaration,
    nativesession,
    receipt,
)
from test_declared_input_receipt import _runtime

DATA = "数值,样本\n1,原始\n".encode()


def _frozen(tmp_path, *, native=False):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "plot.py"
    source.write_bytes(b"pass\n")
    data = root / "selected.csv"
    data.write_bytes(DATA)
    out_dir = tmp_path / "controlled-cache" / "out"
    out_dir.mkdir(parents=True)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    if native:
        spec = execspec.desktop_native_spec(
            str(source), interpreter=sys.executable, cwd=str(root), project_root=str(root)
        )
    else:
        spec = execspec.safe_spec(
            "plot.py", root, "main", interpreter=sys.executable, sandbox=str(sandbox)
        )
    mode = "project_root" if native else "sandbox"
    candidate = {
        "version": 1,
        "scope": {"script": "plot.py", "cwd_mode": mode, "run_config": None},
        "script_path": str(source),
        "files": [{"id": "selected.csv", "path": str(data)}],
        "prefixes": [],
        "mappings": [],
        "rules": [],
        "limits": {"max_files": 20, "max_bytes": 100_000},
    }
    capture = inputdeclaration.begin(
        candidate,
        script="plot.py",
        script_path=str(source),
        cwd_mode=mode,
        run_config=None,
        actual_cwd=str(root if native else sandbox),
        project_root=str(root),
        sandbox=str(sandbox),
    )
    assert capture is not None
    execution_id = executionfact.new_id()
    capture.entered(execution_id)
    fact = capture.finish(
        hashlib.sha256(source.read_bytes()).hexdigest(),
        archive_root=out_dir / inputdeclaration.ARCHIVE_DIRNAME,
    )
    assert fact is not None
    descriptor = capture.archive_descriptor()
    assert descriptor is not None
    runtime = {**_runtime(), "cwd": str(root if native else sandbox)}
    worker = SimpleNamespace(
        spec=spec,
        out_dir=out_dir,
        child_pid=runtime["pid"],
        generation=1,
        script_sha1="original-source",
        python_source="project_venv",
        execution_id=execution_id,
        last_build_execution_id=execution_id,
        last_build_runtime=runtime,
        last_captured_inputs=fact,
        last_captured_input_archive=descriptor,
        execution_observation_unknown=False,
        descriptors=[],
        session_id="controlled-native",
        snapshot_epoch=3,
        active=True,
    )
    worker.captured_inputs_for_receipt = lambda **_: (
        copy.deepcopy(worker.last_captured_inputs) if worker.active else None
    )
    worker.captured_input_archive_for_receipt = lambda **_: (
        copy.deepcopy(worker.last_captured_input_archive) if worker.active else None
    )
    return worker, data


def _from_worker(worker, **overrides):
    return receipt.from_worker(
        worker,
        {"runtime": worker.last_build_runtime, "execution_id": worker.execution_id, **overrides},
        control_plane=receipt.CONTROL_PLANE_PYTHON,
        grant=None,
    )


def _data_bytes(archive):
    resource = next(r for r in archive.resources if r.sha256 == hashlib.sha256(DATA).hexdigest())
    return resource.path.read_bytes()


@pytest.mark.parametrize("native", [False, True])
def test_verified_attachment_retains_original_bytes_after_input_is_deleted(tmp_path, native):
    worker, data = _frozen(tmp_path, native=native)
    data.unlink()
    result = _from_worker(worker)
    archive = result.captured_input_archive
    assert archive is not None
    assert _data_bytes(archive) == DATA
    assert json.loads(archive.graph_bytes) == worker.last_captured_inputs["declaration"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        archive.graph_bytes = b"{}"
    without = dataclasses.replace(result, captured_input_archive=None)
    for private in (False, True):
        assert json.dumps(result.to_payload(include_private=private)) == json.dumps(
            without.to_payload(include_private=private)
        )
        assert "captured_input_archive" not in result.to_payload(include_private=private)
    assert result.public_facts() == without.public_facts()
    assert result.public_identity() == without.public_identity()
    assert result.private_invalidation_key() == without.private_invalidation_key()
    assert result.receipt_id == without.receipt_id
    assert "captured_input_archive=" not in repr(result)


@pytest.mark.parametrize("change", ["key", "revision", "path", "missing", "corrupt", "root"])
def test_unavailable_archive_does_not_revoke_valid_historical_graph(tmp_path, change):
    worker, _ = _frozen(tmp_path)
    descriptor = worker.last_captured_input_archive
    directory = worker.out_dir / inputdeclaration.ARCHIVE_DIRNAME / descriptor["key"]
    blob = directory / hashlib.sha256(DATA).hexdigest()
    if change == "key":
        descriptor["key"] = "../outside"
    elif change == "revision":
        descriptor["revision"] = "sha256:" + "0" * 64
    elif change == "path":
        descriptor["path"] = str(tmp_path / "outside")
    elif change == "missing":
        blob.unlink()
    elif change == "corrupt":
        blob.write_bytes(b"X" * len(DATA))
    else:
        worker.out_dir = "relative-cache"
    result = _from_worker(worker)
    assert result.captured_inputs == inputdeclaration.public_fact(worker.last_captured_inputs)
    assert result.captured_input_archive is None


@pytest.mark.parametrize("unknown", [False, True])
def test_unaccepted_runtime_or_unknown_execution_never_reads_archive(
    tmp_path, monkeypatch, unknown
):
    worker, _ = _frozen(tmp_path)
    if unknown:
        worker.execution_observation_unknown = True
    else:
        worker.last_build_runtime["pid"] += 1

    def forbidden(*args, **kwargs):
        pytest.fail("Archive must not be read before execution qualification")

    monkeypatch.setattr(inputdeclaration, "verify_archive", forbidden)
    result = _from_worker(worker)
    assert result.captured_inputs is None
    assert result.captured_input_archive is None


@pytest.mark.parametrize("change", ["execution", "unknown", "descriptor"])
def test_safe_execution_is_rechecked_after_streaming_archive(tmp_path, monkeypatch, change):
    worker, _ = _frozen(tmp_path)
    verify = inputdeclaration.verify_archive

    def interleaved(*args, **kwargs):
        result = verify(*args, **kwargs)
        assert result is not None
        if change == "execution":
            worker.execution_id = executionfact.new_id()
        elif change == "unknown":
            worker.execution_observation_unknown = True
        else:
            worker.last_captured_input_archive = None
        return result

    monkeypatch.setattr(inputdeclaration, "verify_archive", interleaved)
    result = _from_worker(worker)
    assert result.captured_inputs is None
    assert result.captured_input_archive is None


@pytest.mark.parametrize("entry", ["direct", "preparation"])
@pytest.mark.parametrize("change", ["eof", "epoch", "execution"])
def test_native_barrier_is_rechecked_after_streaming_archive(tmp_path, monkeypatch, entry, change):
    session, _ = _frozen(tmp_path, native=True)
    verify = inputdeclaration.verify_archive
    epoch = session.snapshot_epoch

    def interleaved(*args, **kwargs):
        result = verify(*args, **kwargs)
        assert result is not None
        if change == "eof":
            session.active = False
        elif change == "epoch":
            session.snapshot_epoch += 1
        else:
            session.execution_id = executionfact.new_id()
        return result

    monkeypatch.setattr(inputdeclaration, "verify_archive", interleaved)
    result = (
        receipt.from_native_session(session, "plot.py")
        if entry == "direct"
        else _from_worker(session)
    )
    assert result.captured_inputs is None
    assert result.captured_input_archive is None
    if entry == "direct":
        assert result.instance == "controlled-native:" + str(epoch)


def test_receipt_rejects_attachment_without_matching_public_revision(tmp_path):
    worker, _ = _frozen(tmp_path)
    result = _from_worker(worker)
    assert result.captured_input_archive is not None
    for summary in (None, {"version": 1, "revision": "sha256:" + "0" * 64}):
        with pytest.raises(ValueError, match="Invalid captured input archive"):
            dataclasses.replace(result, captured_inputs=summary)
    bad = dataclasses.replace(
        result.captured_input_archive, graph_bytes=b" " + result.captured_input_archive.graph_bytes
    )
    with pytest.raises(ValueError, match="Invalid captured input archive"):
        dataclasses.replace(result, captured_input_archive=bad)


@nativekit.needs_user_python
def test_real_owned_native_both_receipts_retain_original_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "app-data"))
    root = tmp_path / "中文项目"
    root.mkdir()
    data = root / "data.csv"
    data.write_bytes(b"1,2\n")
    script = root / "plot.py"
    script.write_text(
        desktopkit.FIGURE_HEADER
        + "from pathlib import Path\n"
        + "value = int(Path('data.csv').read_text().split(',')[1])\n"
        + "fig, ax = plt.subplots()\nax.plot([0, 1], [0, value])\nplt.show()\n",
        encoding="utf-8",
    )
    declaredinputs.declare(
        root,
        script="plot.py",
        cwd_mode="project_root",
        files=["data.csv"],
        expected_revision=declaredinputs.state(root)["revision"],
    )
    spec = execspec.desktop_native_spec(
        str(script),
        interpreter=nativekit.USER_PYTHON,
        cwd=str(root),
        project_root=str(root),
    )
    session = desktopproc.start(desktopproc.OwnedLaunch(spec, policy=desktopkit.FAST))
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        build = session.ensure_built()
        data.unlink()
        first = receipt.from_native_session(session, "plot.py")
        second = receipt.from_worker(
            session, build, control_plane=receipt.CONTROL_PLANE_NATIVE, grant=None
        )
        for result in (first, second):
            assert result.captured_input_archive is not None
            resource = next(
                r
                for r in result.captured_input_archive.resources
                if r.sha256 == hashlib.sha256(b"1,2\n").hexdigest()
            )
            assert resource.path.read_bytes() == b"1,2\n"
        assert first.captured_inputs == second.captured_inputs
        desktopkit.finish(session)
    finally:
        if not session.owned.finished:
            session.cancel_owned()
