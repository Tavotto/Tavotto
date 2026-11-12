"""Durable input bytes, typed quotas, no-clobber publication and reference cleanup."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import fields, replace
from pathlib import Path

import pytest

from tavotto.engine import (
    atomicio,
    executionfact,
    inputdeclaration,
    projectcopy,
    svgstore,
    svgstore_inputs,
)
from test_svgstore import PATCH, SOURCE, _args


@pytest.fixture
def budget():
    return svgstore_inputs.default_budget(max_graphs=256)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "中文store"
    root.mkdir()
    return root


def _capture(root, *, data=b"1,2\n", prefix=False):
    root.mkdir(parents=True)
    source = root / "plot.py"
    source.write_bytes(b"# executed source\n")
    (root / "data.csv").write_bytes(data)
    (root / "empty.txt").write_bytes(b"")
    prefixes = []
    if prefix:
        directory = root / "selected"
        (directory / "empty dir").mkdir(parents=True)
        (directory / "中文.csv").write_bytes(data)
        prefixes.append({"id": "selected", "path": str(directory)})
    candidate = {
        "version": 1,
        "scope": {"script": "plot.py", "cwd_mode": "project_root", "run_config": None},
        "script_path": str(source),
        "files": [{"id": name, "path": str(root / name)} for name in ("data.csv", "empty.txt")],
        "prefixes": prefixes,
        "mappings": [],
        "rules": [],
        "limits": {"max_files": projectcopy.MAX_FILES, "max_bytes": projectcopy.MAX_BYTES},
    }
    capture = inputdeclaration.begin(
        candidate,
        script="plot.py",
        script_path=str(source),
        cwd_mode="project_root",
        run_config=None,
        actual_cwd=str(root),
        project_root=str(root),
    )
    capture.entered(executionfact.new_id())
    cache = root / inputdeclaration.ARCHIVE_DIRNAME
    fact = capture.finish(hashlib.sha256(source.read_bytes()).hexdigest(), archive_root=cache)
    archive = inputdeclaration.verify_archive(
        capture.archive_descriptor(), fact, archive_root=cache, limits=candidate["limits"]
    )
    assert archive is not None
    return (
        archive,
        {"captured_inputs": inputdeclaration.public_fact(fact)},
        {
            "script": "plot.py",
            "run_config": None,
        },
    )


def _payload(archive, receipt, owner, budget):
    return {
        "version": 2,
        "input_archive": svgstore_inputs.descriptor(
            archive, receipt=receipt, execution_owner=owner, budget=budget
        ),
        "receipt": receipt,
        "execution_owner": owner,
    }


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _save(root, archive, receipt, owner, budget):
    payload = _payload(archive, receipt, owner, budget)
    svgstore_inputs.admit(root, [payload], incoming=archive, budget=budget)
    created = svgstore_inputs.publish(root, archive, budget=budget)
    return payload, created


def test_default_policy_consumes_existing_project_limits_and_separate_graph_limit(budget):
    assert budget.capture_max_files == budget.max_blob_files == projectcopy.MAX_FILES
    assert budget.capture_max_bytes == budget.max_blob_bytes == projectcopy.MAX_BYTES
    assert budget.max_graphs == 256
    assert budget.max_graph_bytes == budget.max_total_graph_bytes == 64 * 1024 * 1024
    with pytest.raises(AttributeError):
        budget.max_graphs = 1
    with pytest.raises(TypeError):
        svgstore_inputs.InputStoreBudget(**{**budget.__dict__, "unknown": 1})


@pytest.mark.parametrize("name", [f.name for f in fields(svgstore_inputs.InputStoreBudget)])
@pytest.mark.parametrize("invalid", [True, -1, 1.5])
def test_budget_rejects_nonintegers_boolean_and_negative(budget, name, invalid):
    with pytest.raises(ValueError):
        replace(budget, **{name: invalid})


def test_descriptor_is_detached_metadata_only_and_matches_receipt_scope(
    tmp_path, budget, monkeypatch
):
    archive, receipt, owner = _capture(tmp_path / "capture", prefix=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("descriptor opened a resource")

    monkeypatch.setattr(inputdeclaration, "_OPEN", forbidden)
    reference = svgstore_inputs.descriptor(
        archive, receipt=receipt, execution_owner=owner, budget=budget
    )
    assert reference == {
        "version": 1,
        "revision": receipt["captured_inputs"]["revision"],
        "graph_size_bytes": len(archive.graph_bytes),
    }
    detached = svgstore_inputs.validate_descriptor(reference)
    detached["version"] = 2
    assert reference["version"] == 1
    for wrong_receipt, wrong_owner in [
        ({}, owner),
        ({"captured_inputs": {"version": 1, "revision": "sha256:" + "0" * 64}}, owner),
        (receipt, {**owner, "script": "different.py"}),
        (receipt, {**owner, "run_config": "rc_other"}),
        (receipt, {**owner, "extra": True}),
    ]:
        with pytest.raises(ValueError):
            svgstore_inputs.descriptor(
                archive, receipt=wrong_receipt, execution_owner=wrong_owner, budget=budget
            )


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"revision": "sha256:" + "A" * 64},
        {"revision": "../outside"},
        {"graph_size_bytes": True},
        {"graph_size_bytes": -1},
        {"graph_size_bytes": 0},
        {"extra": "path"},
    ],
)
def test_descriptor_closed_shape(change):
    raw = {"version": 1, "revision": "sha256:" + "a" * 64, "graph_size_bytes": 20}
    with pytest.raises(ValueError):
        svgstore_inputs.validate_descriptor({**raw, **change})


def test_roundtrip_zero_bytes_empty_prefix_directories_and_dedup(store, tmp_path, budget):
    archive, receipt, owner = _capture(tmp_path / "capture", prefix=True)
    payload, created = _save(store, archive, receipt, owner, budget)
    loaded = svgstore_inputs.read(
        store, payload["input_archive"], receipt=receipt, execution_owner=owner, budget=budget
    )
    assert loaded.graph_bytes == archive.graph_bytes
    assert {r.sha256: r.path.read_bytes() for r in loaded.resources} == {
        r.sha256: r.path.read_bytes() for r in archive.resources
    }
    graph = json.loads(loaded.graph_bytes)
    assert graph["prefixes"][0]["directories"] == ["", "empty dir"]
    assert len(loaded.resources) == 3
    assert any(r.size_bytes == 0 and r.path.stat().st_size == 0 for r in loaded.resources)
    first_bytes = _snapshot(store)
    assert svgstore_inputs.publish(store, archive, budget=budget) == ()
    assert _snapshot(store) == first_bytes
    assert len(created) == 4
    facts = svgstore_inputs.inspect(store, [payload, payload], budget=budget)
    assert facts["blob_bytes"] == sum(r.size_bytes for r in archive.resources)
    assert facts["graph_bytes"] == len(archive.graph_bytes)
    assert len(facts["inventory"]) == 4


def test_empty_tree_inspection_does_not_create_input_directories(store, budget):
    svgstore_inputs.admit(store, [{"version": 1}], budget=budget)
    assert svgstore_inputs.inventory(store, budget=budget) == {}
    assert svgstore_inputs.inspect(store, [], budget=budget)["graphs"] == {}
    svgstore_inputs.cleanup(store, [], budget=budget)
    assert list(store.iterdir()) == []


@pytest.mark.parametrize("name", [f.name for f in fields(svgstore_inputs.InputStoreBudget)])
def test_lower_read_policy_rejects_single_attachment(store, tmp_path, budget, name):
    archive, receipt, owner = _capture(tmp_path / "capture")
    payload, _ = _save(store, archive, receipt, owner, budget)
    with pytest.raises(ValueError):
        svgstore_inputs.read(
            store,
            payload["input_archive"],
            receipt=receipt,
            execution_owner=owner,
            budget=replace(budget, **{name: 0}),
        )


@pytest.mark.parametrize(
    "name", ["max_blob_files", "max_blob_bytes", "max_graphs", "max_total_graph_bytes"]
)
def test_admission_and_publisher_capacity_refuse_before_any_write(store, tmp_path, budget, name):
    archive, receipt, owner = _capture(tmp_path / "capture")
    payload = _payload(archive, receipt, owner, budget)
    refused = replace(budget, **{name: 0})
    for operation in (
        lambda: svgstore_inputs.admit(store, [payload], incoming=archive, budget=refused),
        lambda: svgstore_inputs.publish(store, archive, budget=refused),
    ):
        with pytest.raises(ValueError):
            operation()
        assert list(store.iterdir()) == []


def test_pending_incoming_cannot_repair_a_corrupt_existing_graph(store, tmp_path, budget):
    archive, receipt, owner = _capture(tmp_path / "capture")
    payload, _ = _save(store, archive, receipt, owner, budget)
    resource = next(r for r in archive.resources if r.size_bytes)
    path = store / "inputs" / "blobs" / resource.sha256[:2] / resource.sha256[2:]
    path.unlink()
    before = _snapshot(store)
    with pytest.raises(ValueError):
        svgstore_inputs.admit(store, [payload], incoming=archive, budget=budget)
    assert _snapshot(store) == before


def test_all_orphans_checked_but_read_only_checks_the_requested_attachment(store, tmp_path, budget):
    archive, receipt, owner = _capture(tmp_path / "capture")
    payload, _ = _save(store, archive, receipt, owner, budget)
    digest = hashlib.sha256(b"orphan").hexdigest()
    orphan = store / "inputs" / "blobs" / digest[:2] / digest[2:]
    orphan.parent.mkdir(exist_ok=True)
    orphan.write_bytes(b"broken")  # Same size, different bytes.
    assert svgstore_inputs.read(
        store, payload["input_archive"], receipt=receipt, execution_owner=owner, budget=budget
    )
    with pytest.raises(ValueError):
        svgstore_inputs.admit(store, [payload], budget=budget)
    with pytest.raises(ValueError):
        svgstore_inputs.inspect(store, [payload], budget=budget)


@pytest.mark.parametrize("variant", ["duplicate", "noncanonical"])
def test_graph_duplicate_keys_and_noncanonical_json_are_rejected(store, tmp_path, budget, variant):
    archive, _, _ = _capture(tmp_path / "capture")
    svgstore_inputs.publish(store, archive, budget=budget)
    raw = (
        b'{"version":1,' + archive.graph_bytes[1:]
        if variant == "duplicate"
        else json.dumps(json.loads(archive.graph_bytes), sort_keys=True, indent=2).encode()
    )
    digest = hashlib.sha256(raw).hexdigest()
    directory = store / "inputs" / "graphs"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (digest + ".json")).write_bytes(raw)
    reference = {"version": 1, "revision": "sha256:" + digest, "graph_size_bytes": len(raw)}
    with pytest.raises(ValueError):
        svgstore_inputs.read(
            store,
            reference,
            receipt={"captured_inputs": {"version": 1, "revision": reference["revision"]}},
            execution_owner=None,
            budget=budget,
        )


@pytest.mark.parametrize(
    "name", ["unexpected", "graphs/not-a-hash.json", "blobs/AA/blob", "blobs/00/nested/file"]
)
def test_inventory_rejects_unknown_structure(store, budget, name):
    path = store / "inputs" / name
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        svgstore_inputs.inventory(store, budget=budget)


def test_real_directory_alias_is_not_followed(store, tmp_path, budget):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"untouched")
    alias = store / "inputs"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(outside)],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            svgstore_inputs.inventory(store, budget=budget)
        assert (outside / "secret").read_bytes() == b"untouched"
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


def test_publisher_rechecks_mutated_capsule_even_when_identical_blob_already_exists(
    store, tmp_path, budget
):
    archive, receipt, owner = _capture(tmp_path / "capture")
    _save(store, archive, receipt, owner, budget)
    before = _snapshot(store)
    resource = next(r for r in archive.resources if r.size_bytes)
    resource.path.write_bytes(b"x" * resource.size_bytes)
    with pytest.raises(ValueError):
        svgstore_inputs.publish(store, archive, budget=budget)
    assert _snapshot(store) == before


def test_large_resource_uses_core_stream_and_no_path_whole_read(
    store, tmp_path, budget, monkeypatch
):
    archive, receipt, owner = _capture(tmp_path / "capture", data=b"1234" * 100_000)
    payload = _payload(archive, receipt, owner, budget)
    original = inputdeclaration._READ
    requests = []

    def read(fd, count):
        requests.append(count)
        return original(fd, count)

    def forbidden(*args, **kwargs):
        raise AssertionError("Whole-file Path.read_bytes is forbidden")

    monkeypatch.setattr(inputdeclaration, "_READ", read)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    svgstore_inputs.publish(store, archive, budget=budget)
    svgstore_inputs.admit(store, [payload], budget=budget)
    loaded = svgstore_inputs.read(
        store, payload["input_archive"], receipt=receipt, execution_owner=owner, budget=budget
    )
    assert len(loaded.resources) == 3
    assert len(requests) > 12 and max(requests) <= inputdeclaration._CHUNK_BYTES


@pytest.mark.parametrize("after_publish", [False, True])
def test_failed_publication_removes_only_own_members_and_unpublished_temps(
    store, tmp_path, budget, monkeypatch, after_publish
):
    archive, _, _ = _capture(tmp_path / "capture")
    original_copy = inputdeclaration.copy_resource
    original_publish = atomicio.publish_file_if_absent
    calls = 0

    def copy(resource, sink):
        nonlocal calls
        calls += 1
        if calls == 2:
            sink.write(b"partial")
            raise OSError(errno.ENOSPC, "injected disk full")
        return original_copy(resource, sink)

    def publish(temporary, destination):
        original_publish(temporary, destination)
        raise OSError(errno.EIO, "injected failure after exclusive publication")

    monkeypatch.setattr(
        atomicio if after_publish else inputdeclaration,
        "publish_file_if_absent" if after_publish else "copy_resource",
        publish if after_publish else copy,
    )
    with pytest.raises(OSError) as error:
        svgstore_inputs.publish(store, archive, budget=budget)
    assert error.value.errno == (errno.EIO if after_publish else errno.ENOSPC)
    assert list(store.iterdir()) == []


def test_existing_damaged_blob_is_never_overwritten(store, tmp_path, budget):
    archive, _, _ = _capture(tmp_path / "capture")
    resource = archive.resources[0]
    path = store / "inputs" / "blobs" / resource.sha256[:2] / resource.sha256[2:]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * resource.size_bytes or b"wrong")
    before = _snapshot(store)
    with pytest.raises(ValueError):
        svgstore_inputs.publish(store, archive, budget=budget)
    assert _snapshot(store) == before


@pytest.mark.parametrize("valid", [False, True])
def test_exclusive_publication_race_never_claims_or_overwrites_other_publishers_inode(
    store, tmp_path, budget, monkeypatch, valid
):
    archive, receipt, owner = _capture(tmp_path / "capture")
    original = atomicio.publish_file_if_absent
    raced = []

    def publish(temporary, destination):
        if not raced:
            # Another writer owns this inode, despite its content-addressed name.
            data = temporary.read_bytes()
            destination.write_bytes(data if valid else b"x" * len(data) or b"wrong")
            raced.append((destination, destination.read_bytes(), destination.stat().st_ino))
            raise FileExistsError(destination)
        return original(temporary, destination)

    monkeypatch.setattr(atomicio, "publish_file_if_absent", publish)
    if valid:
        created = svgstore_inputs.publish(store, archive, budget=budget)
        path, data, inode = raced[0]
        assert all(token.relative != path.relative_to(store).as_posix() for token in created)
        reference = _payload(archive, receipt, owner, budget)["input_archive"]
        assert svgstore_inputs.read(
            store, reference, receipt=receipt, execution_owner=owner, budget=budget
        )
        svgstore_inputs.cleanup(store, [], budget=budget, created=created)
    else:
        with pytest.raises(ValueError, match="mismatch"):
            svgstore_inputs.publish(store, archive, budget=budget)
        path, data, inode = raced[0]
    assert path.read_bytes() == data and path.stat().st_ino == inode
    assert _snapshot(store) == {path.relative_to(store).as_posix(): data}


def test_cleanup_preserves_historical_and_committed_attempt_references(store, tmp_path, budget):
    first, first_receipt, owner = _capture(tmp_path / "first")
    first_payload, _ = _save(store, first, first_receipt, owner, budget)
    second, second_receipt, _ = _capture(tmp_path / "second", data=b"9,8\n")
    second_payload = _payload(second, second_receipt, owner, budget)
    svgstore_inputs.admit(store, [first_payload, second_payload], incoming=second, budget=budget)
    created = svgstore_inputs.publish(store, second, budget=budget)
    before = _snapshot(store)
    svgstore_inputs.cleanup(
        store, (p for p in [first_payload, second_payload]), budget=budget, created=created
    )
    assert _snapshot(store) == before
    svgstore_inputs.cleanup(store, [first_payload], budget=budget)
    assert svgstore_inputs.read(
        store,
        first_payload["input_archive"],
        receipt=first_receipt,
        execution_owner=owner,
        budget=budget,
    )
    with pytest.raises(FileNotFoundError):
        svgstore_inputs.read(
            store,
            second_payload["input_archive"],
            receipt=second_receipt,
            execution_owner=owner,
            budget=budget,
        )


def test_attempt_cleanup_does_not_delete_replacement_file_identity(store, tmp_path, budget):
    archive, receipt, owner = _capture(tmp_path / "capture")
    _, created = _save(store, archive, receipt, owner, budget)
    token = next(t for t in created if "/blobs/" in t.relative)
    path = store / token.relative
    data = path.read_bytes()
    atomicio.write_bytes(path, data)
    assert os.stat(path).st_ino != token.identity[1]
    svgstore_inputs.cleanup(store, [], budget=budget, created=created)
    assert _snapshot(store) == {token.relative: data}


def test_actual_single_shard_over_4096_is_bounded_by_input_policy_not_render_policy(store, budget):
    directory = store / "inputs" / "blobs" / "00"
    directory.mkdir(parents=True)
    found = attempt = 0
    while found < 4097:
        content = str(attempt).encode("ascii")
        digest = hashlib.sha256(content).hexdigest()
        attempt += 1
        if digest.startswith("00"):
            (directory / digest[2:]).write_bytes(content)
            found += 1
    assert len(svgstore_inputs.inventory(store, budget=budget)) == 4097
    facts = svgstore_inputs.inspect(store, [], budget=budget)
    assert len(facts["blobs"]) == 4097 and not facts["graphs"]


def _pending(store, *, graph=False, data=b"partial"):
    destination = (
        store / "inputs" / "graphs" / ("a" * 64 + ".json")
        if graph
        else store / "inputs" / "blobs" / "ab" / ("c" * 62)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    path = atomicio._next_tmp(destination)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("graph", [False, True])
def test_readonly_inspection_rejects_owned_pending_and_recovery_only_removes_it(
    store, budget, graph
):
    pending = _pending(store, graph=graph)
    before = _snapshot(store)
    for operation in (
        svgstore_inputs.inventory,
        lambda root, **kw: svgstore_inputs.inspect(root, [], **kw),
    ):
        with pytest.raises(ValueError, match="Unknown"):
            operation(store, budget=budget)
        assert _snapshot(store) == before
    svgstore_inputs.recover(store, budget=budget)
    assert not pending.exists() and _snapshot(store) == {}


@pytest.mark.parametrize(
    "name",
    [
        "graphs/unknown.123.1.tmp",
        "graphs/" + "a" * 64 + ".json.random.tmp",
        "graphs/" + "a" * 62 + ".123.1.tmp",
        "blobs/ab/" + "a" * 64 + ".json.123.1.tmp",
    ],
)
def test_recovery_rejects_unknown_temps_without_deleting_even_a_valid_pending(store, budget, name):
    _pending(store)
    unknown = store / "inputs" / name
    unknown.parent.mkdir(parents=True, exist_ok=True)
    unknown.write_bytes(b"foreign")
    before = _snapshot(store)
    with pytest.raises(ValueError):
        svgstore_inputs.recover(store, budget=budget)
    assert _snapshot(store) == before


def test_recovery_rejects_more_than_one_pending_member_before_deleting(store, budget):
    _pending(store)
    _pending(store, graph=True)
    before = _snapshot(store)
    with pytest.raises(ValueError, match="pending member budget"):
        svgstore_inputs.recover(store, budget=budget)
    assert _snapshot(store) == before


def test_recovery_does_not_delete_replaced_pending_identity(store, budget, monkeypatch):
    pending = _pending(store)
    original = svgstore_inputs._stat
    calls = 0

    def info(path, **kwargs):
        nonlocal calls
        if path == pending:
            calls += 1
            if calls == 2:
                atomicio.write_bytes(pending, b"replacement")
        return original(path, **kwargs)

    monkeypatch.setattr(svgstore_inputs, "_stat", info)
    with pytest.raises(ValueError, match="changed during recovery"):
        svgstore_inputs.recover(store, budget=budget)
    assert pending.read_bytes() == b"replacement"


def test_recovery_rejects_pending_reparse_directory_without_following_or_deleting(
    store, tmp_path, budget
):
    pending = _pending(store, graph=True)
    pending.unlink()
    outside = tmp_path / "outside-recovery"
    outside.mkdir()
    (outside / "secret").write_bytes(b"untouched")
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(pending), str(outside)],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    else:
        pending.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="Unsafe"):
            svgstore_inputs.recover(store, budget=budget)
        assert pending.exists() and (outside / "secret").read_bytes() == b"untouched"
    finally:
        pending.rmdir() if os.name == "nt" else pending.unlink()


def test_recovery_pending_size_is_bounded_independently_of_committed_census(store, budget):
    pending = _pending(store, data=b"12345")
    with pytest.raises(ValueError, match="pending member budget"):
        svgstore_inputs.recover(store, budget=replace(budget, capture_max_bytes=4))
    assert pending.read_bytes() == b"12345"


def test_exclusive_temp_creation_failure_never_deletes_other_file(
    store, tmp_path, budget, monkeypatch
):
    archive, _, _ = _capture(tmp_path / "capture")
    original = atomicio._next_tmp
    collision = []

    def next_tmp(destination):
        path = original(destination)
        path.write_bytes(b"owned by another writer")
        collision.append(path)
        return path

    monkeypatch.setattr(atomicio, "_next_tmp", next_tmp)
    with pytest.raises(FileExistsError):
        svgstore_inputs.publish(store, archive, budget=budget)
    assert len(collision) == 1
    assert _snapshot(store) == {
        collision[0].relative_to(store).as_posix(): b"owned by another writer"
    }


_CRASH_CHILD = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'src'))
from tavotto.engine import atomicio, inputdeclaration, svgstore, svgstore_inputs
request = json.loads(Path(sys.argv[2]).read_bytes())
root = Path(request['root'])
archive = inputdeclaration.InputArchive(
    bytes.fromhex(request['graph']),
    tuple(inputdeclaration.InputResource(row['sha256'], row['size_bytes'], Path(row['path']))
          for row in request['resources']))
mode = sys.argv[3]
if mode != 'save':
    root = svgstore._root(create=True, storage_root=root)
    original = atomicio.publish_file_if_absent
    def publish(temporary, destination):
        graph = destination.parent.name == 'graphs'
        if (mode == 'graph_tmp' and graph) or (mode == 'blob_tmp' and not graph):
            os._exit(77)
        result = original(temporary, destination)
        if mode == 'published_graph' and graph:
            os._exit(77)
        return result
    atomicio.publish_file_if_absent = publish
    with svgstore._guard(root):
        svgstore_inputs.publish(root, archive, budget=svgstore_inputs.default_budget(max_graphs=256))
    raise AssertionError('Crash injection did not reach its boundary')
args = request['save_args']
for key in ('pdf', 'svg'):
    args[key] = bytes.fromhex(args[key])
if request['attach']:
    args['captured_input_archive'] = archive
saved = svgstore.save(request['project'], request['source'], request['patch'],
                      storage_root=root, **args)
assert not any(name in sys.modules for name in ('matplotlib', 'numpy', 'tavotto.engine.pool'))
print(json.dumps(saved['snapshot_ref']))
"""


@pytest.mark.parametrize("phase", ["blob_tmp", "graph_tmp", "published_graph"])
@pytest.mark.parametrize("attach", [False, True])
def test_killed_guarded_publisher_recovers_via_fresh_process_public_save_and_keeps_history(
    store, tmp_path, budget, phase, attach
):
    first, receipt, owner = _capture(tmp_path / "first")
    args = _args()
    args["receipt"].update(receipt)
    saved = svgstore.save(
        tmp_path,
        SOURCE,
        PATCH,
        storage_root=store,
        execution_owner=owner,
        captured_input_archive=first,
        **args,
    )
    historical = _snapshot(store)
    second, second_receipt, _ = _capture(tmp_path / "second", data=b"9,8\n")
    patch = "sha256:" + "c" * 64
    args = _args(patch=patch)
    if attach:
        args["receipt"].update(second_receipt)
        args["execution_owner"] = owner
    args["pdf"], args["svg"] = args["pdf"].hex(), args["svg"].hex()
    request = tmp_path / "child-request.json"
    request.write_text(
        json.dumps(
            {
                "root": str(store),
                "project": str(tmp_path),
                "source": SOURCE,
                "patch": patch,
                "graph": second.graph_bytes.hex(),
                "attach": attach,
                "save_args": args,
                "resources": [
                    {"sha256": row.sha256, "size_bytes": row.size_bytes, "path": str(row.path)}
                    for row in second.resources
                ],
            }
        ),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-B",
        "-c",
        _CRASH_CHILD,
        str(Path(__file__).resolve().parents[1]),
        str(request),
    ]
    killed = subprocess.run(command + [phase], capture_output=True, timeout=30)
    assert killed.returncode == 77, killed.stderr.decode(errors="replace")
    if phase != "published_graph":
        before = _snapshot(store)
        with pytest.raises(ValueError, match="Unknown"):
            svgstore_inputs.inspect(store, [], budget=budget)
        assert _snapshot(store) == before
    recovered = subprocess.run(command + ["save"], capture_output=True, timeout=30)
    assert recovered.returncode == 0, recovered.stderr.decode(errors="replace")
    current_ref = json.loads(recovered.stdout)
    old = svgstore.load_input_archive(saved["snapshot_ref"], storage_root=store)
    assert old.graph_bytes == first.graph_bytes
    assert {row.sha256: row.path.read_bytes() for row in old.resources} == {
        row.sha256: row.path.read_bytes() for row in first.resources
    }
    # Current source markers legitimately refresh updated_at on a new save;
    # immutable archives and both typed resource trees must retain every byte.
    assert all(
        (store / name).read_bytes() == data
        for name, data in historical.items()
        if not name.startswith("sources/")
    )
    current = svgstore.load_input_archive(current_ref, storage_root=store)
    assert (current.graph_bytes if current is not None else None) == (
        second.graph_bytes if attach else None
    )
    facts = svgstore.inspect_portable_store(storage_root=store)
    assert len(facts["inputs"]["graphs"]) == (2 if attach else 1)
    assert not list((store / "inputs").rglob("*.tmp"))
