"""Historical publication preserves exact archives without creating current authority.

Real Capture input bytes are paired with the original opaque admitted render
fixture. Storage tests do not claim PDF/SVG safety or scientific rendering.
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
from pathlib import Path

import pytest

from tavotto.engine import svgstore
from test_svgstore import PATCH, PDF, SOURCE, _args
from test_svgstore_input_archive import (
    _capture,
    _contents,
    _input_paths,
    _load,
    _publish,
    _tree,
    world as world,
)


def _current_bytes(root):
    return {
        name: data
        for name, data in _tree(root).items()
        if name.startswith(("entries/", "sources/"))
    }


def test_archive_only_never_publishes_current_and_keeps_distinct_histories(world, monkeypatch):
    _, archive = _capture(world)
    first_contents = _contents(archive)
    writer = svgstore.atomicio.write_bytes
    current_writes = []

    def observed_write(target, data, *args, **kwargs):
        if Path(target).parent.name in {"entries", "sources"}:
            current_writes.append(Path(target))
        return writer(target, data, *args, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", observed_write)
    first = _publish(world, archive, archive_only=True)
    assert current_writes == []
    assert _current_bytes(world[1]) == {}
    (world[2] / "data.csv").write_bytes(b"second historical bytes\n")
    _, second_archive = _capture(world)
    second = _publish(world, second_archive, generation=2, archive_only=True)
    third = _publish(
        world, second_archive, generation=2, patch="sha256:" + "c" * 64, archive_only=True
    )
    assert current_writes == []
    assert _current_bytes(world[1]) == {}
    refs = [row["snapshot_ref"] for row in (first, second, third)]
    assert len({row["snapshotId"] for row in refs}) == 3
    assert refs[0]["sourceId"] == refs[1]["sourceId"] == refs[2]["sourceId"]
    assert refs[0]["patchHash"] == refs[1]["patchHash"] != refs[2]["patchHash"]
    assert _contents(_load(world, refs[0])["captured_input_archive"]) == first_contents
    for saved in (second, third):
        loaded = _load(world, saved["snapshot_ref"])
        assert loaded["receipt"] == saved["receipt"]
        assert loaded["pdf"] == PDF
        assert _contents(loaded["captured_input_archive"]) == _contents(second_archive)
    report = svgstore.inspect_portable_store(storage_root=world[1])
    assert len(report["snapshots"]) == 3
    assert report["entries"] == report["sources"] == {}
    assert svgstore.load(world[0], SOURCE, PATCH, storage_root=world[1]) is None


def test_archive_only_does_not_touch_existing_current_or_rewrite_immutable_bytes(
    world, monkeypatch
):
    current = svgstore.save(world[0], SOURCE, PATCH, **_args(generation=9), storage_root=world[1])
    before = _tree(world[1])
    current_bytes = _current_bytes(world[1])
    writer, unlink = svgstore.atomicio.write_bytes, Path.unlink

    def guarded_write(target, data, *args, **kwargs):
        assert Path(target).parent.name not in {"entries", "sources"}
        return writer(target, data, *args, **kwargs)

    def guarded_unlink(path, *args, **kwargs):
        assert path.parent.name not in {"entries", "sources"}
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", guarded_write)
    monkeypatch.setattr(Path, "unlink", guarded_unlink)
    _, archive = _capture(world)
    historical = _publish(world, archive, archive_only=True)
    same = _publish(world, archive, archive_only=True)
    assert same["snapshot_ref"] == historical["snapshot_ref"]
    after = _tree(world[1])
    assert {name: after[name] for name in before} == before
    assert _current_bytes(world[1]) == current_bytes
    assert (
        svgstore.load(world[0], SOURCE, PATCH, storage_root=world[1])["receipt"]
        == current["receipt"]
    )
    assert _load(world, historical["snapshot_ref"])["receipt"]["generation"] == 1
    assert len(svgstore.inspect_portable_store(storage_root=world[1])["snapshots"]) == 2


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_archive_mode_requires_a_boolean_before_creating_any_store(world, invalid):
    before = _tree(world[0])
    with pytest.raises(ValueError, match="archive"):
        svgstore.save(
            world[0], SOURCE, PATCH, **_args(), storage_root=world[1], archive_only=invalid
        )
    assert _tree(world[0]) == before
    assert not world[1].exists()


@pytest.mark.parametrize("mode", ["cache", "activate"])
def test_archive_mode_requires_durable_nonactivating_publication_before_io(
    world, monkeypatch, mode
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("An invalid archive mode must be rejected before store IO")

    monkeypatch.setattr(svgstore, "_root", forbidden)
    monkeypatch.setattr(svgstore.atomicio, "write_bytes", forbidden)
    with pytest.raises(ValueError, match="archive"):
        svgstore.save(
            world[0],
            SOURCE,
            PATCH,
            **_args(),
            archive_only=True,
            **({"storage_root": world[1], "activate": True} if mode == "activate" else {}),
        )


def test_archive_metadata_counts_once_but_all_history_still_consumes_budget(world, monkeypatch):
    monkeypatch.setattr(svgstore, "MAX_SNAPSHOTS", 1)
    first = svgstore.save(
        world[0], SOURCE, PATCH, **_args(), storage_root=world[1], archive_only=True
    )
    before = _tree(world[1])
    with pytest.raises(ValueError, match="budget"):
        svgstore.save(
            world[0],
            SOURCE,
            PATCH,
            **_args(generation=2),
            storage_root=world[1],
            archive_only=True,
        )
    assert _tree(world[1]) == before
    assert _load(world, first["snapshot_ref"])["pdf"] == PDF


@pytest.mark.parametrize("budget_kind", ["render_bytes", "graph_count", "input_bytes"])
def test_archive_capacity_refusal_preserves_current_and_all_old_archives(
    world, monkeypatch, budget_kind
):
    _, archive = _capture(world)
    first = _publish(world, archive)
    before = _tree(world[1])
    (world[2] / "data.csv").write_bytes(b"new history with distinct inputs\n")
    _, second = _capture(world)
    kwargs = {}
    if budget_kind == "render_bytes":
        report = svgstore.inspect_portable_store(storage_root=world[1])
        monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", report["total_bytes"])
    elif budget_kind == "graph_count":
        kwargs["input_budget"] = dataclasses.replace(svgstore.input_store_budget(), max_graphs=1)
    else:
        kwargs["input_budget"] = dataclasses.replace(
            svgstore.input_store_budget(),
            max_blob_bytes=sum(row.size_bytes for row in archive.resources),
        )
    with pytest.raises(ValueError, match="budget"):
        _publish(world, second, generation=2, archive_only=True, **kwargs)
    assert _tree(world[1]) == before
    assert _load(world, first["snapshot_ref"])["pdf"] == PDF


@pytest.mark.parametrize("damage", ["incoming_missing", "incoming_tampered", "historical_tampered"])
def test_archive_mode_still_rejects_bad_input_closure_and_checks_old_history(world, damage):
    _, archive = _capture(world)
    first = _publish(world, archive, archive_only=True)
    (world[2] / "data.csv").write_bytes(b"another capture\n")
    _, incoming = _capture(world)
    if damage == "incoming_missing":
        incoming = dataclasses.replace(incoming, resources=incoming.resources[:-1])
    elif damage == "incoming_tampered":
        row = next(row for row in incoming.resources if row.size_bytes)
        row.path.write_bytes(b"!" * row.size_bytes)
    else:
        _, blobs = _input_paths(world[1], archive)
        path = next(path for path in blobs.values() if path.stat().st_size)
        path.write_bytes(b"!" * path.stat().st_size)
    before = _tree(world[1])
    with pytest.raises((ValueError, OSError)):
        _publish(world, incoming, generation=2, archive_only=True)
    after = _tree(world[1])
    assert {name: after[name] for name in before} == before
    assert set(name for name in after if name.startswith("snapshots/")) == {
        name for name in before if name.startswith("snapshots/")
    }
    assert _current_bytes(world[1]) == {}
    if damage != "historical_tampered":
        assert _load(world, first["snapshot_ref"])["pdf"] == PDF


@pytest.mark.parametrize("phase", ["metadata", "after_commit"])
def test_archive_interruption_keeps_prior_bytes_and_committed_input_closure(
    world, monkeypatch, phase
):
    current = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    before = _tree(world[1])
    _, archive = _capture(world)
    writer, result = svgstore.atomicio.write_bytes, svgstore._result
    hits = []

    def fail_metadata(target, data, *args, **kwargs):
        if Path(target).parent.name == "snapshots":
            _input_paths(world[1], archive)
            hits.append(Path(target))
            raise OSError(errno.ENOSPC, "archive test interruption")
        return writer(target, data, *args, **kwargs)

    def fail_result(*args, **kwargs):
        hits.append(kwargs["reference"])
        raise OSError(errno.ENOSPC, "archive test interruption")

    if phase == "metadata":
        monkeypatch.setattr(svgstore.atomicio, "write_bytes", fail_metadata)
    else:
        monkeypatch.setattr(svgstore, "_result", fail_result)
    with pytest.raises(OSError, match="archive test interruption"):
        _publish(world, archive, generation=2, archive_only=True)
    assert len(hits) == 1
    monkeypatch.setattr(svgstore, "_result", result)
    after = _tree(world[1])
    assert {name: after[name] for name in before} == before
    report = svgstore.inspect_portable_store(storage_root=world[1])
    assert len(report["snapshots"]) == (1 if phase == "metadata" else 2)
    assert _load(world, current["snapshot_ref"])["pdf"] == PDF
    if phase == "after_commit":
        loaded = _load(world, hits[0])
        assert _contents(loaded["captured_input_archive"]) == _contents(archive)


def test_default_and_explicit_false_keep_original_bytes_and_admission_call_shape(
    world, monkeypatch
):
    monkeypatch.setattr(svgstore.time, "time", lambda: 1234.0)
    admission = svgstore._durable_admission
    calls = []

    def observed_admission(*args, **kwargs):
        calls.append(kwargs)
        return admission(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A legacy save must not evaluate capture input policy")

    monkeypatch.setattr(svgstore, "_durable_admission", observed_admission)
    monkeypatch.setattr(svgstore, "input_store_budget", forbidden)
    first = svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    other = world[0] / "second-store"
    second = svgstore.save(
        world[0], SOURCE, PATCH, **_args(), storage_root=other, archive_only=False
    )
    assert _tree(world[1]) == _tree(other)
    assert first["snapshot_ref"] == second["snapshot_ref"]
    assert _current_bytes(world[1])
    assert all("archive_only" not in call for call in calls)


def test_document_reference_loads_each_static_history_without_claiming_current_file(
    world, monkeypatch
):
    source = "同名图.pdf"
    first_pdf, second_pdf = b"%PDF-first-admitted-pair", b"%PDF-second-admitted-pair"
    saved = []
    for data in (first_pdf, second_pdf):
        saved.append(
            svgstore.save(
                world[0],
                source,
                PATCH,
                **_args(source=source, pdf=data),
                static_sha256=hashlib.sha256(data).hexdigest(),
                remap_fingerprint="approved-table",
                storage_root=world[1],
                archive_only=True,
            )
        )
    # Ordinary lookup still requires the caller's exact current static bytes.
    assert (
        svgstore.load_snapshot(
            world[0],
            saved[0]["snapshot_ref"],
            source_id=source,
            patch_hash=PATCH,
            static_sha256=hashlib.sha256(second_pdf).hexdigest(),
            remap_fingerprint="approved-table",
            storage_root=world[1],
        )
        is None
    )
    (world[1] / ".lock").unlink()
    before = _tree(world[1])

    def forbidden(*_args, **_kwargs):
        pytest.fail("An explicit document history read must not write or enter the current store")

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", forbidden)
    monkeypatch.setattr(svgstore, "_guard", forbidden)
    monkeypatch.setattr(svgstore.config, "data_dir", forbidden)
    for row, data in zip(saved, (first_pdf, second_pdf), strict=True):
        loaded = svgstore.load_document_snapshot(
            world[0],
            row["snapshot_ref"],
            source_id=source,
            patch_hash=PATCH,
            remap_fingerprint="approved-table",
            storage_root=world[1],
        )
        assert loaded == row
        assert loaded["pdf"] == data
    assert not (world[0] / source).exists()
    assert _tree(world[1]) == before


@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "patch",
        "remap",
        "reference_receipt",
        "reference_owner",
        "missing_owner",
        "blob_missing",
        "blob_tampered",
        "input_tampered",
        "graph_missing",
        "root_missing",
        "root_absent",
    ],
)
def test_document_loader_keeps_full_reference_remap_pair_and_input_gates(world, damage):
    _, archive = _capture(world)
    saved = _publish(world, archive, archive_only=True, remap_fingerprint="approved-table")
    reference = dict(saved["snapshot_ref"])
    args = {
        "source_id": SOURCE,
        "patch_hash": PATCH,
        "remap_fingerprint": "approved-table",
        "storage_root": world[1],
    }
    if damage == "source":
        args["source_id"] = "another.pdf"
    elif damage == "patch":
        args["patch_hash"] = "sha256:" + "f" * 64
    elif damage == "remap":
        args["remap_fingerprint"] = "old-table"
    elif damage == "reference_receipt":
        reference["receiptId"] = "sha256:" + "f" * 64
    elif damage == "reference_owner":
        reference["executionOwner"] = {"script": "another.py", "run_config": None}
    elif damage == "missing_owner":
        reference.pop("executionOwner")
    elif damage == "blob_missing":
        saved["pdf_path"].unlink()
    elif damage == "blob_tampered":
        saved["svg_path"].write_bytes(b"!" * len(saved["svg"]))
    elif damage in {"input_tampered", "graph_missing"}:
        graph, blobs = _input_paths(world[1], archive)
        if damage == "graph_missing":
            graph.unlink()
        else:
            path = next(path for path in blobs.values() if path.stat().st_size)
            path.write_bytes(b"!" * path.stat().st_size)
    elif damage == "root_missing":
        args["storage_root"] = world[0] / "missing-store"
    else:
        args["storage_root"] = None
    before = _tree(world[0])
    assert svgstore.load_document_snapshot(world[0], reference, **args) is None
    assert _tree(world[0]) == before


def test_document_loader_returns_the_same_verified_private_input_attachment(world):
    _, archive = _capture(world)
    saved = _publish(world, archive, archive_only=True)
    loaded = svgstore.load_document_snapshot(
        world[0], saved["snapshot_ref"], source_id=SOURCE, patch_hash=PATCH, storage_root=world[1]
    )
    assert loaded == saved
    assert _contents(loaded["captured_input_archive"]) == _contents(archive)
