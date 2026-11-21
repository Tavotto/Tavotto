"""Actual captured bytes survive durable Figure storage without granting execution.

PDF/SVG are the existing admitted-pair storage fixture, not a scientific oracle.
The input producer is real Capture; large census tests separately exercise the
public InputArchive contract without running arbitrary user code.
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import executionfact, inputdeclaration as inputs, svgstore
from test_svgstore import PATCH, PDF, SOURCE, SVG, _args

OWNER = {"script": "plot.py", "run_config": None}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _tree(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "app-data"))
    project = tmp_path / "移动项目"
    project.mkdir()
    original = tmp_path / "执行时原件"
    original.mkdir()
    (original / "plot.py").write_bytes(b"# exact source, not executed by this storage test\n")
    (original / "data.csv").write_bytes(b'"A\tB",2\r\n"C""D\nE",3\n\0\xff')
    (original / "empty.dat").write_bytes(b"")
    prefix = original / "prefix"
    (prefix / "empty" / "nested").mkdir(parents=True)
    (prefix / "helper.py").write_bytes(b"VALUE = 7\n")
    (prefix / "duplicate.csv").write_bytes((original / "data.csv").read_bytes())
    candidate = {
        "version": 1,
        "scope": {"script": "plot.py", "cwd_mode": "project_root", "run_config": None},
        "script_path": str(original / "plot.py"),
        "files": [
            {"id": "数据.csv", "path": str(original / "data.csv")},
            {"id": "zero", "path": str(original / "empty.dat")},
        ],
        "prefixes": [{"id": "remap:opaque-set", "path": str(prefix)}],
        "mappings": [{"kind": "prefix", "from": "../requested", "id": "remap:opaque-set"}],
        "rules": [{"kind": "prefix", "from": "../requested", "to": str(prefix)}],
        "limits": {"max_files": 100, "max_bytes": 1024 * 1024},
    }
    return project, project / svgstore.STORE_DIRNAME, original, candidate, tmp_path / "cache"


def _capture(world):
    _, _, original, candidate, cache = world
    capture = inputs.begin(
        candidate,
        script=candidate["scope"]["script"],
        script_path=candidate["script_path"],
        cwd_mode=candidate["scope"]["cwd_mode"],
        run_config=candidate["scope"]["run_config"],
        remap_rules=candidate["rules"],
        actual_cwd=str(original),
        project_root=str(original),
    )
    assert capture is not None
    capture.entered(executionfact.new_id())
    fact = capture.finish(_sha(Path(candidate["script_path"]).read_bytes()), archive_root=cache)
    assert fact is not None
    descriptor = capture.archive_descriptor()
    assert descriptor is not None
    archive = inputs.verify_archive(
        descriptor, fact, archive_root=cache, limits=candidate["limits"]
    )
    assert archive is not None
    return fact, archive


def _publish(world, archive, *, patch=PATCH, generation=1, **kwargs):
    project, root, *_ = world
    args = _args(patch=patch, generation=generation)
    args["receipt"]["captured_inputs"] = {
        "version": 1,
        "revision": "sha256:" + _sha(archive.graph_bytes),
    }
    args.update(execution_owner=OWNER, captured_input_archive=archive)
    args.update(kwargs)
    return svgstore.save(project, SOURCE, patch, storage_root=root, **args)


def _load(world, reference, **kwargs):
    return svgstore.load_snapshot(
        world[0],
        reference,
        source_id=SOURCE,
        patch_hash=reference["patchHash"],
        storage_root=world[1],
        **kwargs,
    )


def _contents(archive):
    return {row.sha256: row.path.read_bytes() for row in archive.resources}


def _input_paths(root, archive):
    graph_digest = _sha(archive.graph_bytes)
    graph = root / "inputs" / "graphs" / (graph_digest + ".json")
    assert graph.read_bytes() == archive.graph_bytes
    blobs = {
        row.sha256: root / "inputs" / "blobs" / row.sha256[:2] / row.sha256[2:]
        for row in archive.resources
    }
    assert all(path.is_file() for path in blobs.values())
    return graph, blobs


def test_no_attachment_preserves_prechange_bytes_reference_and_default_result(world, monkeypatch):
    # Golden values were produced by df7e67e8, independently of this implementation.
    monkeypatch.setattr(svgstore.time, "time", lambda: 1700000000.125)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A legacy snapshot must not evaluate the new input budget")

    monkeypatch.setattr(svgstore, "input_store_budget", forbidden)
    saved = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    expected = {
        ".lock": "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
        "blobs/42817cdb3b63b1af5e9e971333e8673b633624136f833a7a0870a6d84e914696.pdf": "42817cdb3b63b1af5e9e971333e8673b633624136f833a7a0870a6d84e914696",
        "blobs/8f318ede53040a23bf78e91d75398a716831bbfc86497b29e420cf35e04ba21c.svg": "8f318ede53040a23bf78e91d75398a716831bbfc86497b29e420cf35e04ba21c",
        "entries/5c1155778fbbc36905afacc2d9885e7aff37e971f8b59e50b238b101a8e9040b.json": "458a7c4132f957ff26b6945285fdb9ceca5c77196a9aff1b9b9c4cdef8f89284",
        "snapshots/58b0dd6978a91abf5144092f3afa907dbe4aa1ead7b0cdfe80bd8805f32a5a92.json": "458a7c4132f957ff26b6945285fdb9ceca5c77196a9aff1b9b9c4cdef8f89284",
        "sources/47e4f14e2954edd3451c8612a313034319ee31206252e1ea7a30a00bb8d1779c.json": "089af6ea497835c452e0dd88e0f5fb2ced3f414f44da44f187822b4a0c06ab7b",
    }
    assert {name: _sha(data) for name, data in _tree(world[1]).items()} == expected
    assert set(saved) == {
        "execution_owner",
        "pdf",
        "pdf_artifact",
        "pdf_path",
        "receipt",
        "snapshot_ref",
        "svg",
        "svg_artifact",
        "svg_path",
    }
    assert saved["snapshot_ref"] == {
        "version": 1,
        "sourceId": SOURCE,
        "patchHash": PATCH,
        "receiptId": "sha256:" + "a" * 64,
        "snapshotId": "58b0dd6978a91abf5144092f3afa907dbe4aa1ead7b0cdfe80bd8805f32a5a92",
    }
    assert svgstore.load(world[0], SOURCE, PATCH, storage_root=world[1]) == saved
    assert _load(world, saved["snapshot_ref"]) == saved
    single = svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=world[1])
    assert "captured_input_archive" not in single
    assert svgstore.load_input_archive(saved["snapshot_ref"], storage_root=world[1]) is None
    assert "inputs" not in svgstore.inspect_portable_store(storage_root=world[1])


def test_real_capture_roundtrips_exact_graph_blobs_zero_bytes_and_empty_directories(world):
    fact, archive = _capture(world)
    expected = _contents(archive)
    saved = _publish(world, archive)
    for loaded in (
        saved,
        svgstore.load(world[0], SOURCE, PATCH, storage_root=world[1]),
        _load(world, saved["snapshot_ref"]),
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=world[1]),
    ):
        assert loaded["captured_input_archive"].graph_bytes == archive.graph_bytes
        assert _contents(loaded["captured_input_archive"]) == expected
        assert loaded["pdf"] == PDF and loaded["svg"] == SVG
    cold = svgstore.load_input_archive(saved["snapshot_ref"], storage_root=world[1])
    assert cold.graph_bytes == archive.graph_bytes and _contents(cold) == expected
    assert b"" in expected.values()
    assert fact["declaration"]["prefixes"][0]["directories"] == ["", "empty", "empty/nested"]
    payload = json.loads(next((world[1] / "snapshots").glob("*.json")).read_bytes())["payload"]
    assert payload["version"] == 2
    assert payload["input_archive"] == {
        "version": 1,
        "revision": fact["revision"],
        "graph_size_bytes": len(archive.graph_bytes),
    }
    assert saved["snapshot_ref"]["version"] == 1
    marker = json.loads(next((world[1] / "sources").glob("*.json")).read_bytes())
    assert marker["payload"]["version"] == 1


def test_deleted_source_and_capture_cache_reopen_in_fresh_stdlib_parent(world, tmp_path):
    _, archive = _capture(world)
    saved = _publish(world, archive)
    expected = {key: value.hex() for key, value in _contents(archive).items()}
    reference = tmp_path / "ref.json"
    reference.write_text(json.dumps(saved["snapshot_ref"]), encoding="utf-8")
    shutil.rmtree(world[2])
    shutil.rmtree(world[4])
    code = """
import json,sys
from pathlib import Path
from tavotto.engine import config,svgstore
def forbidden(*a,**kw): raise AssertionError('historical input read used settings')
config.load=forbidden
ref=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
arc=svgstore.load_input_archive(ref,storage_root=Path(sys.argv[1]))
assert arc is not None
assert not any(k in sys.modules for k in ('matplotlib','numpy','tavotto.engine.pool','tavotto.engine.declaredinputs'))
print(json.dumps({'graph':arc.graph_bytes.hex(),'blobs':{r.sha256:r.path.read_bytes().hex() for r in arc.resources}}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", code, str(world[1]), str(reference)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"graph": archive.graph_bytes.hex(), "blobs": expected}


@pytest.mark.parametrize("mode", ["archive_only", "revoked_marker", "readonly"])
def test_historical_inputs_survive_without_current_execution_authority(world, monkeypatch, mode):
    _, archive = _capture(world)
    saved = _publish(world, archive)
    if mode == "archive_only":
        shutil.rmtree(world[1] / "entries")
        shutil.rmtree(world[1] / "sources")
        (world[1] / ".lock").unlink()
    elif mode == "revoked_marker":
        receipt = _args(generation=2)["receipt"]
        svgstore.activate(world[0], SOURCE, receipt, storage_root=world[1])
        assert svgstore.load(world[0], SOURCE, PATCH, storage_root=world[1]) is None
    before = _tree(world[1])

    def forbidden(*_args, **_kwargs):
        pytest.fail("Historical input reads may not acquire a write lock or publish")

    monkeypatch.setattr(svgstore, "_guard", forbidden)
    monkeypatch.setattr(svgstore.atomicio, "write_bytes", forbidden)
    if mode == "readonly":
        # This guard is effective on Windows too, unlike chmod-only assertions.
        monkeypatch.setattr(svgstore, "_set_current", forbidden)
    loaded = svgstore.load_input_archive(saved["snapshot_ref"], storage_root=world[1])
    assert loaded.graph_bytes == archive.graph_bytes
    assert _contents(loaded) == _contents(archive)
    assert _load(world, saved["snapshot_ref"])["captured_input_archive"] == loaded
    assert "inputs" in svgstore.inspect_portable_store(storage_root=world[1])
    assert _tree(world[1]) == before


def test_full_inventory_deduplicates_input_graph_and_preserves_render_byte_dimension(world):
    fact, archive = _capture(world)
    first = _publish(world, archive)
    second = _publish(world, archive, patch="sha256:" + "c" * 64)
    report = svgstore.inspect_portable_store(storage_root=world[1])
    tree = _tree(world[1])
    graph_path, blobs = _input_paths(world[1], archive)
    input_names = {
        graph_path.relative_to(world[1]).as_posix(),
        *(path.relative_to(world[1]).as_posix() for path in blobs.values()),
    }
    assert report["members"] == tuple(sorted(tree))
    assert report["inputs"] == {
        "graphs": {
            _sha(archive.graph_bytes): {
                "version": 1,
                "revision": fact["revision"],
                "graph_size_bytes": len(archive.graph_bytes),
                "graph": fact["declaration"],
            }
        },
        "blobs": {
            row.sha256: {"sha256": row.sha256, "size_bytes": row.size_bytes}
            for row in archive.resources
        },
        "total_bytes": len(archive.graph_bytes) + sum(row.size_bytes for row in archive.resources),
    }
    assert report["total_bytes"] == sum(
        len(data) for name, data in tree.items() if name not in input_names | {".lock"}
    )
    assert len(report["snapshots"]) == 2
    assert _load(world, first["snapshot_ref"]) and _load(world, second["snapshot_ref"])


def test_changed_input_revision_has_distinct_snapshot_and_old_versions_survive_cleanup(world):
    _, first_archive = _capture(world)
    first = _publish(world, first_archive)
    first_contents = _contents(first_archive)
    (world[2] / "data.csv").write_bytes(b"different input revision\n")
    _, second_archive = _capture(world)
    second = _publish(world, second_archive, generation=2, activate=True)
    assert first["snapshot_ref"]["snapshotId"] != second["snapshot_ref"]["snapshotId"]
    third = _publish(world, second_archive, patch="sha256:" + "d" * 64, generation=2)
    assert len(svgstore.inspect_portable_store(storage_root=world[1])["inputs"]["graphs"]) == 2
    assert (
        _contents(_load(world, first["snapshot_ref"])["captured_input_archive"]) == first_contents
    )
    assert _load(world, second["snapshot_ref"]) and _load(world, third["snapshot_ref"])


def test_input_descriptor_participates_in_snapshot_id_with_identical_render_receipt(world):
    _, first_archive = _capture(world)
    first = _publish(world, first_archive)
    before = _contents(first_archive)
    (world[2] / "data.csv").write_bytes(b"changed input, identical render bytes and receipt tuple")
    _, second_archive = _capture(world)
    second = _publish(world, second_archive)
    assert first["receipt"]["receipt_id"] == second["receipt"]["receipt_id"]
    assert first["pdf_artifact"] == second["pdf_artifact"]
    assert first["svg_artifact"] == second["svg_artifact"]
    assert first["snapshot_ref"]["snapshotId"] != second["snapshot_ref"]["snapshotId"]
    assert _contents(_load(world, first["snapshot_ref"])["captured_input_archive"]) == before
    assert (
        _load(world, second["snapshot_ref"])["captured_input_archive"].graph_bytes
        == second_archive.graph_bytes
    )


def _synthetic_archive(tmp_path, *, logical=1, distinct=1):
    resources = []
    for index in range(distinct):
        data = ("resource-%06d" % index).encode()
        path = tmp_path / ("resource-%06d" % index)
        path.write_bytes(data)
        resources.append(inputs.InputResource(_sha(data), len(data), path))
    graph = {
        "version": 1,
        "scope": {"script": "plot.py", "cwd_mode": "project_root", "run_config": None},
        "script": {"sha256": resources[0].sha256, "size_bytes": resources[0].size_bytes},
        "files": [
            {
                "id": "file-%06d" % i,
                "sha256": resources[(i + 1) % distinct].sha256,
                "size_bytes": resources[(i + 1) % distinct].size_bytes,
            }
            for i in range(logical - 1)
        ],
        "prefixes": [],
        "mappings": [],
    }
    return inputs.InputArchive(inputs.canonical_graph_bytes(graph), tuple(resources))


def test_default_policy_accepts_canonical_graph_above_one_mib_and_50000_logical_members(
    world, tmp_path
):
    archive = _synthetic_archive(tmp_path, logical=50000)
    assert len(archive.graph_bytes) > 1024 * 1024
    assert len(json.loads(archive.graph_bytes)["files"]) + 1 == 50000
    saved = _publish(world, archive)
    assert (
        _load(world, saved["snapshot_ref"])["captured_input_archive"].graph_bytes
        == archive.graph_bytes
    )
    report = svgstore.inspect_portable_store(storage_root=world[1])
    assert len(report["inputs"]["graphs"][_sha(archive.graph_bytes)]["graph"]["files"]) == 49999


def test_default_policy_accepts_4097_distinct_input_resources(world, tmp_path):
    archive = _synthetic_archive(tmp_path, logical=4097, distinct=4097)
    saved = _publish(world, archive)
    loaded = svgstore.load_input_archive(saved["snapshot_ref"], storage_root=world[1])
    assert len(loaded.resources) == 4097
    assert _contents(loaded) == _contents(archive)
    assert len(svgstore.inspect_portable_store(storage_root=world[1])["inputs"]["blobs"]) == 4097


@pytest.mark.parametrize(
    "field",
    [
        "capture_max_files",
        "capture_max_bytes",
        "max_blob_files",
        "max_blob_bytes",
        "max_graph_bytes",
        "max_total_graph_bytes",
    ],
)
def test_low_input_budgets_reject_before_existing_metadata_or_marker_changes(world, field):
    old = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    before = _tree(world[1])
    _, archive = _capture(world)
    budget = dataclasses.replace(svgstore.input_store_budget(), **{field: 1})
    with pytest.raises(ValueError):
        _publish(world, archive, generation=2, activate=True, input_budget=budget)
    assert _tree(world[1]) == before
    assert _load(world, old["snapshot_ref"])["pdf"] == PDF


def test_graph_count_budget_preserves_first_graph_and_all_existing_references(world):
    _, archive = _capture(world)
    first = _publish(world, archive)
    before = _tree(world[1])
    (world[2] / "data.csv").write_bytes(b"new graph")
    _, second = _capture(world)
    budget = dataclasses.replace(svgstore.input_store_budget(), max_graphs=1)
    with pytest.raises(ValueError):
        _publish(world, second, generation=2, activate=True, input_budget=budget)
    assert _tree(world[1]) == before
    assert _load(world, first["snapshot_ref"])


def test_readers_apply_explicit_input_budget_without_mutating_archive(world):
    _, archive = _capture(world)
    saved = _publish(world, archive)
    before = _tree(world[1])
    budget = dataclasses.replace(svgstore.input_store_budget(), max_graph_bytes=1)
    assert _load(world, saved["snapshot_ref"], input_budget=budget) is None
    assert (
        svgstore.load_input_archive(
            saved["snapshot_ref"], storage_root=world[1], input_budget=budget
        )
        is None
    )
    for reader in (
        lambda: svgstore.inspect_portable_snapshot(
            saved["snapshot_ref"], storage_root=world[1], input_budget=budget
        ),
        lambda: svgstore.inspect_portable_store(storage_root=world[1], input_budget=budget),
    ):
        with pytest.raises(ValueError):
            reader()
    assert _tree(world[1]) == before


@pytest.mark.parametrize("damage", ["script", "run_config", "revision", "missing_public_fact"])
def test_wrong_scope_or_public_capture_revision_never_retargets_existing_snapshot(world, damage):
    old = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    before = _tree(world[1])
    fact, archive = _capture(world)
    owner = dict(OWNER)
    receipt = {**_args(generation=2)["receipt"], "captured_inputs": inputs.public_fact(fact)}
    if damage == "script":
        owner["script"] = "different.py"
    elif damage == "run_config":
        owner["run_config"] = "rc_" + "a" * 16
    elif damage == "revision":
        receipt["captured_inputs"]["revision"] = "sha256:" + "e" * 64
    else:
        receipt.pop("captured_inputs")
    with pytest.raises(ValueError):
        _publish(
            world, archive, generation=2, activate=True, receipt=receipt, execution_owner=owner
        )
    assert _tree(world[1]) == before
    assert _load(world, old["snapshot_ref"])


@pytest.mark.parametrize("damage", ["missing", "extra", "noncanonical", "tampered_source"])
def test_invalid_archive_closure_or_source_is_rejected_before_metadata_publication(
    world, damage, tmp_path
):
    svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    before = _tree(world[1])
    _, archive = _capture(world)
    if damage == "missing":
        archive = dataclasses.replace(archive, resources=archive.resources[:-1])
    elif damage == "extra":
        path = tmp_path / "not-declared"
        path.write_bytes(b"extra")
        archive = dataclasses.replace(
            archive, resources=(*archive.resources, inputs.InputResource(_sha(b"extra"), 5, path))
        )
    elif damage == "noncanonical":
        archive = dataclasses.replace(archive, graph_bytes=b" " + archive.graph_bytes)
    else:
        resource = next(row for row in archive.resources if row.size_bytes)
        resource.path.write_bytes(b"!" * resource.size_bytes)
    with pytest.raises((ValueError, OSError)):
        _publish(world, archive, generation=2, activate=True)
    # Failed blob staging may leave immutable orphan inputs, but no Figure record changes.
    after = _tree(world[1])
    assert {name: after[name] for name in before} == before
    assert set(
        name for name in after if name.startswith(("entries/", "sources/", "snapshots/"))
    ) == {name for name in before if name.startswith(("entries/", "sources/", "snapshots/"))}


@pytest.mark.parametrize(
    "damage", ["blob_tamper", "blob_missing", "graph_tamper", "graph_missing", "extra"]
)
def test_cold_read_and_inventory_reject_missing_changed_or_unknown_input_members(world, damage):
    _, archive = _capture(world)
    saved = _publish(world, archive)
    graph_path, blobs = _input_paths(world[1], archive)
    if damage == "blob_tamper":
        path = next(path for path in blobs.values() if path.stat().st_size)
        path.write_bytes(b"?" * path.stat().st_size)
    elif damage == "blob_missing":
        next(iter(blobs.values())).unlink()
    elif damage == "graph_tamper":
        graph_path.write_bytes(b" " * graph_path.stat().st_size)
    elif damage == "graph_missing":
        graph_path.unlink()
    else:
        (graph_path.parent / "unknown-input-file").write_bytes(b"unaccounted")
    before = _tree(world[1])
    with pytest.raises((ValueError, OSError)):
        svgstore.inspect_portable_store(storage_root=world[1])
    if damage != "extra":
        assert svgstore.load_input_archive(saved["snapshot_ref"], storage_root=world[1]) is None
        assert _load(world, saved["snapshot_ref"]) is None
        with pytest.raises((ValueError, OSError)):
            svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=world[1])
    assert _tree(world[1]) == before


def test_attachment_is_durable_only_and_rejected_before_any_store_io(world, monkeypatch):
    fact, archive = _capture(world)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A transient input attachment must fail before store IO")

    monkeypatch.setattr(svgstore, "_root", forbidden)
    args = _args()
    args["receipt"]["captured_inputs"] = inputs.public_fact(fact)
    with pytest.raises(ValueError):
        svgstore.save(world[0], SOURCE, PATCH, **args, captured_input_archive=archive)


def test_input_publish_enospc_preserves_existing_pair_and_marker(world, monkeypatch):
    old = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    before = _tree(world[1])
    _, archive = _capture(world)
    original_publish = svgstore.atomicio.publish_file
    original_exclusive = svgstore.atomicio.publish_file_if_absent
    original_write = svgstore.atomicio.write_bytes
    hits = []

    def fail_publish(source, target, *args, **kwargs):
        if "inputs" in Path(target).relative_to(world[1]).parts:
            hits.append(Path(target))
            raise OSError(errno.ENOSPC, "test input store full")
        return original_publish(source, target, *args, **kwargs)

    def fail_write(target, data, *args, **kwargs):
        if "inputs" in Path(target).relative_to(world[1]).parts:
            hits.append(Path(target))
            raise OSError(errno.ENOSPC, "test input store full")
        return original_write(target, data, *args, **kwargs)

    def fail_exclusive(source, target, *args, **kwargs):
        if "inputs" in Path(target).relative_to(world[1]).parts:
            hits.append(Path(target))
            raise OSError(errno.ENOSPC, "test input store full")
        return original_exclusive(source, target, *args, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "publish_file", fail_publish)
    monkeypatch.setattr(svgstore.atomicio, "publish_file_if_absent", fail_exclusive)
    monkeypatch.setattr(svgstore.atomicio, "write_bytes", fail_write)
    with pytest.raises(OSError):
        _publish(world, archive, generation=2, activate=True)
    assert hits, "The injected disk error must hit actual input publication"
    after = _tree(world[1])
    assert {name: after[name] for name in before} == before
    assert _load(world, old["snapshot_ref"])["pdf"] == PDF


def test_snapshot_metadata_failure_cleans_only_this_attempt_unreferenced_inputs(world, monkeypatch):
    old = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    before = _tree(world[1])
    _, archive = _capture(world)
    original_write = svgstore.atomicio.write_bytes
    hits = []

    def fail_snapshot(target, data, *args, **kwargs):
        if Path(target).parent == world[1] / "snapshots":
            # This is after actual input publication, not a preflight rejection.
            graph_path, blobs = _input_paths(world[1], archive)
            assert graph_path.read_bytes() == archive.graph_bytes
            assert {digest: path.read_bytes() for digest, path in blobs.items()} == _contents(
                archive
            )
            hits.append(Path(target))
            raise OSError(errno.ENOSPC, "test immutable metadata full")
        return original_write(target, data, *args, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", fail_snapshot)
    with pytest.raises(OSError, match="immutable metadata full"):
        _publish(world, archive, patch="sha256:" + "f" * 64)
    assert len(hits) == 1
    after = _tree(world[1])
    # The existing source-marker timestamp may advance under the original API.
    stable = {name: data for name, data in before.items() if not name.startswith("sources/")}
    assert {name: data for name, data in after.items() if not name.startswith("sources/")} == stable
    assert not any(name.startswith("inputs/") for name in after)
    assert _load(world, old["snapshot_ref"])["pdf"] == PDF
    assert svgstore.load(world[0], SOURCE, PATCH, storage_root=world[1])["pdf"] == PDF


def test_entry_failure_after_archive_commit_keeps_cold_input_and_prior_reference(
    world, monkeypatch
):
    old = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    before = _tree(world[1])
    _, archive = _capture(world)
    expected = _contents(archive)
    original_write = svgstore.atomicio.write_bytes
    hits = []
    patch = "sha256:" + "f" * 64

    def fail_entry(target, data, *args, **kwargs):
        if Path(target).parent == world[1] / "entries":
            new_snapshots = [
                path
                for path in (world[1] / "snapshots").glob("*.json")
                if path.relative_to(world[1]).as_posix() not in before
            ]
            assert len(new_snapshots) == 1
            payload = json.loads(new_snapshots[0].read_bytes())["payload"]
            assert payload["identity"]["patch_hash"] == patch
            assert payload["input_archive"]["revision"] == "sha256:" + _sha(archive.graph_bytes)
            hits.append(Path(target))
            raise OSError(errno.ENOSPC, "test current entry full")
        return original_write(target, data, *args, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", fail_entry)
    with pytest.raises(OSError, match="current entry full"):
        _publish(world, archive, patch=patch)
    assert len(hits) == 1
    after = _tree(world[1])
    stable = {name: data for name, data in before.items() if not name.startswith("sources/")}
    assert {name: after[name] for name in stable} == stable
    report = svgstore.inspect_portable_store(storage_root=world[1])
    new = [
        row["reference"]
        for row in report["snapshots"].values()
        if row["reference"] != old["snapshot_ref"]
    ]
    assert len(new) == 1 and new[0]["patchHash"] == patch
    assert "inputs" in report
    shutil.rmtree(world[2])
    shutil.rmtree(world[4])
    cold = svgstore.load_input_archive(new[0], storage_root=world[1])
    assert cold.graph_bytes == archive.graph_bytes and _contents(cold) == expected
    assert _load(world, new[0])["pdf"] == PDF
    assert _load(world, old["snapshot_ref"])["pdf"] == PDF
