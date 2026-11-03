"""The exact admitted Figure pair survives restart without executing a script.

The store authenticates identities and bytes; geometric/SVG admission belongs to
its caller. Tiny opaque bytes deliberately keep these storage tests independent
of PDF, SVG, matplotlib and render-worker implementations.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import figcapture, patchspec, svgstore

SOURCE = "runtime:图.py#panel"
PATCH = patchspec.patch_hash([])
PDF = b"%PDF-paired-snapshot"
SVG = b"<svg>paired-snapshot</svg>"
RECEIPT_ID = "sha256:" + "a" * 64
RECEIPT_IDENTITY = "sha256:" + "b" * 64


@pytest.fixture
def store(tmp_path, monkeypatch):
    data = tmp_path / "app-data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    project = tmp_path / "project"
    project.mkdir()
    return project, data / "figure_snapshots"


def _args(source=SOURCE, patch=PATCH, pdf=PDF, svg=SVG, generation=1):
    artifact = figcapture.SourceArtifact(
        source_id=source,
        origin=figcapture.ORIGIN_EXECUTION,
        kind="pdf",
        bytes_sha256=hashlib.sha256(pdf).hexdigest(),
        size_bytes=len(pdf),
        receipt_id=RECEIPT_ID,
        receipt_identity=RECEIPT_IDENTITY,
        generation=generation,
        patch_hash=patch,
    ).to_payload()
    return {
        "pdf": pdf,
        "svg": svg,
        "artifact": artifact,
        "receipt": {
            "receipt_id": RECEIPT_ID,
            "receipt_identity": RECEIPT_IDENTITY,
            "generation": generation,
            "source_revision": "script-revision",
        },
    }


def _save(store, source=SOURCE, patch=PATCH, **kwargs):
    args = _args(source, patch)
    args.update(kwargs)
    return svgstore.save(store[0], source, patch, **args)


def _meta(store):
    return next((store[1] / "entries").glob("*.json"))


def _mutate_metadata(store, mutate, *, resign=True):
    path = _meta(store)
    record = json.loads(path.read_bytes())
    mutate(record["payload"])
    if resign:
        record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    path.write_bytes(svgstore._json(record))


def test_roundtrip_freezes_exact_pair_and_public_receipt(store):
    saved = _save(store)
    loaded = svgstore.load(store[0], SOURCE, PATCH)
    assert loaded == saved
    assert loaded["pdf"] == PDF and loaded["svg"] == SVG
    assert loaded["pdf_path"].parent == store[1] / "blobs"
    assert loaded["svg_path"].read_bytes() == SVG
    assert loaded["pdf_artifact"] == _args()["artifact"]
    assert loaded["svg_artifact"]["bytes_sha256"] == hashlib.sha256(SVG).hexdigest()
    assert loaded["svg_artifact"]["kind"] == "svg"
    assert loaded["receipt"]["source_revision"] == "script-revision"
    assert loaded["snapshot_ref"] is None  # Disposable cache never issues document refs.
    assert not list(store[0].iterdir())


def test_cold_process_reopens_snapshot_without_worker_or_source(store):
    _save(store)
    store[0].rmdir()  # Existence of the original script/project is not needed.
    code = """
import json, sys
from tavotto.engine import svgstore
result = svgstore.load(sys.argv[1], sys.argv[2], sys.argv[3])
assert not any(k in sys.modules for k in ('matplotlib', 'numpy', 'tavotto.engine.pool'))
print(json.dumps({'pdf': result['pdf'].hex(), 'svg': result['svg'].hex()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(store[0]), SOURCE, PATCH],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert json.loads(result.stdout) == {"pdf": PDF.hex(), "svg": SVG.hex()}


def test_each_lookup_dimension_is_exact_and_preview_is_unrelated(store):
    _save(store, static_sha256="c" * 64, remap_fingerprint="remap-1")
    assert svgstore.load(
        store[0], SOURCE, PATCH, static_sha256="c" * 64, remap_fingerprint="remap-1"
    )
    for project, source, patch, static, remap in [
        (store[0] / "other", SOURCE, PATCH, "c" * 64, "remap-1"),
        (store[0], SOURCE + "-2", PATCH, "c" * 64, "remap-1"),
        (store[0], SOURCE, "sha256:" + "d" * 64, "c" * 64, "remap-1"),
        (store[0], SOURCE, PATCH, "d" * 64, "remap-1"),
        (store[0], SOURCE, PATCH, None, "remap-1"),
        (store[0], SOURCE, PATCH, "c" * 64, "remap-2"),
        (store[0], SOURCE, PATCH, "c" * 64, ""),
    ]:
        assert (
            svgstore.load(project, source, patch, static_sha256=static, remap_fingerprint=remap)
            is None
        )
    assert svgstore.load(
        store[0] / ".." / "project",
        SOURCE,
        PATCH,
        static_sha256="c" * 64,
        remap_fingerprint="remap-1",
    )


def test_receipt_payload_identity_variant_and_mutation_detachment(store):
    args = _args()
    receipt = args["receipt"]
    receipt["public_identity"] = receipt.pop("receipt_identity")
    result = _save(store, receipt=receipt)
    receipt["source_revision"] = "changed"
    assert result["receipt"]["source_revision"] == "script-revision"
    assert svgstore.load(store[0], SOURCE, PATCH)["receipt"] == result["receipt"]


@pytest.mark.parametrize(
    "change",
    [
        {"source_id": SOURCE + "wrong"},
        {"patch_hash": "sha256:" + "e" * 64},
        {"receipt_identity": "sha256:" + "e" * 64},
        {"receipt_id": "sha256:" + "e" * 64},
        {"generation": 2},
        {"generation": True},
        {"bytes_sha256": "e" * 64},
        {"size_bytes": len(PDF) + 1},
        {"kind": "svg"},
        {"origin": "static"},
        {"source_artifact_version": 100},
        {"source_artifact_version": True},
        {"bytes_sha256": "../outside"},
    ],
)
def test_save_rejects_artifact_identity_or_content_mismatch(store, change):
    args = _args()
    args["artifact"].update(change)
    with pytest.raises(ValueError):
        _save(store, **args)
    assert svgstore.load(store[0], SOURCE, PATCH) is None


@pytest.mark.parametrize(
    "change",
    [
        {"receipt_identity": "sha256:" + "e" * 64},
        {"receipt_id": "wrong"},
        {"generation": 2},
        {"generation": True},
        {"project_root": "/private"},
        {"runtime": {"executable": "/private/python"}},
    ],
)
def test_save_rejects_mismatched_or_private_receipt(store, change):
    receipt = {**_args()["receipt"], **change}
    with pytest.raises(ValueError):
        _save(store, receipt=receipt)


@pytest.mark.parametrize(
    "field,value",
    [
        ("identity", {}),
        ("version", 99),
        ("version", True),
        ("created_at", -1),
        ("created_at", "now"),
        ("created_at", 10**1000),
        ("pdf_artifact", {}),
        ("svg_artifact", {}),
        ("receipt", []),
    ],
)
def test_malformed_metadata_is_a_cache_miss(store, field, value):
    _save(store)
    _mutate_metadata(store, lambda p: p.__setitem__(field, value))
    assert svgstore.load(store[0], SOURCE, PATCH) is None


def test_metadata_hash_and_paired_receipt_checked_independently(store):
    _save(store)
    _mutate_metadata(store, lambda p: p["receipt"].update(source_revision="tampered"), resign=False)
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    _save(store)
    _mutate_metadata(store, lambda p: p["svg_artifact"].update(receipt_id="sha256:" + "e" * 64))
    assert svgstore.load(store[0], SOURCE, PATCH) is None


def test_metadata_cannot_retarget_source_or_blob_path(store):
    _save(store)
    _mutate_metadata(store, lambda p: p["identity"].update(source_id="other"))
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    _save(store)
    _mutate_metadata(store, lambda p: p["svg_artifact"].update(bytes_sha256="../../private"))
    assert svgstore.load(store[0], SOURCE, PATCH) is None


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"payload":{},"payload":{}}'])
def test_unparseable_metadata_is_a_cache_miss(store, raw):
    _save(store)
    _meta(store).write_bytes(raw)
    assert svgstore.load(store[0], SOURCE, PATCH) is None


@pytest.mark.parametrize("kind", ["pdf", "svg"])
def test_hash_checked_even_when_corrupt_bytes_have_same_length(store, kind):
    result = _save(store)
    path = result[kind + "_path"]
    data = path.read_bytes()
    path.write_bytes(b"!" + data[1:])
    assert svgstore.load(store[0], SOURCE, PATCH) is None


def test_missing_store_does_not_create_directories(store):
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    assert not store[1].exists()


def _symlink(source, target, *, directory=False):
    try:
        source.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


@pytest.mark.parametrize("target", ["metadata", "pdf", "svg", "lock", "blobs", "entries", "root"])
def test_symlinks_are_never_read_or_written_through(store, tmp_path, target):
    result = _save(store)
    path = {
        "metadata": _meta(store),
        "pdf": result["pdf_path"],
        "svg": result["svg_path"],
        "lock": store[1] / ".lock",
        "blobs": store[1] / "blobs",
        "entries": store[1] / "entries",
        "root": store[1],
    }[target]
    outside = tmp_path / "outside"
    path.rename(outside)
    _symlink(path, outside, directory=outside.is_dir())
    before = outside.read_bytes() if outside.is_file() else None
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    with pytest.raises((ValueError, OSError)):
        _save(store)
    if before is not None:
        assert outside.read_bytes() == before


@pytest.mark.parametrize("constant", ["MAX_PDF_BYTES", "MAX_SVG_BYTES", "MAX_METADATA_BYTES"])
def test_save_and_load_obey_size_limits_before_unbounded_read(store, monkeypatch, constant):
    result = _save(store)
    monkeypatch.setattr(svgstore, constant, 4)
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    with pytest.raises(ValueError):
        _save(store)
    assert result["pdf"] == PDF


def test_ttl_expiry_is_miss_and_next_save_reclaims_old_pair(store, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(svgstore.time, "time", lambda: now[0])
    old = _save(store)
    now[0] += svgstore.MAX_AGE_SECONDS + 1
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    _save(store, source="other", **_args(source="other", pdf=b"new-pdf", svg=b"new-svg"))
    assert not old["pdf_path"].exists() and not old["svg_path"].exists()


def test_snapshot_count_budget_and_frozen_bytes_survive_eviction(store, monkeypatch):
    monkeypatch.setattr(svgstore, "MAX_SNAPSHOTS", 1)
    old = _save(store)
    _save(store)  # Replacing one key does not count as a second snapshot.
    args = _args(source="other", pdf=b"new-pdf", svg=b"new-svg")
    _save(store, source="other", **args)
    assert len(list((store[1] / "entries").iterdir())) == 1
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    assert not old["pdf_path"].exists()
    assert old["pdf"] == PDF and old["svg"] == SVG


def test_total_budget_reclaims_oldest_and_preserves_shared_blobs(store, monkeypatch):
    old = _save(store)
    _save(store, source="shared", **_args(source="shared"))
    monkeypatch.setattr(svgstore, "MAX_SNAPSHOTS", 2)
    new = _save(store, source="new", **_args(source="new", pdf=b"new", svg=b"new-svg"))
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    assert svgstore.load(store[0], "shared", PATCH)
    assert old["pdf_path"].exists()
    current = sum(
        p.stat().st_size for p in store[1].rglob("*") if p.is_file() and p.name != ".lock"
    )
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", current)
    _save(store, source="last", **_args(source="last", pdf=b"last", svg=b"last-svg"))
    total = sum(p.stat().st_size for p in store[1].rglob("*") if p.is_file() and p.name != ".lock")
    assert total <= current
    assert new["pdf"] == b"new"


def test_failed_commit_keeps_old_metadata_pair_then_reaps_orphan(store, monkeypatch):
    previous = _save(store)
    write = svgstore.atomicio.write_bytes
    seen = []

    def fail_metadata(path, data, **kwargs):
        if path.parent.name == "sources":
            return write(path, data, **kwargs)
        seen.append(path.suffix)
        if path.suffix == ".json":
            raise OSError("simulated interrupted commit")
        write(path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(svgstore.atomicio, "write_bytes", fail_metadata)
        with pytest.raises(OSError):
            _save(store, **_args(pdf=b"new", svg=b"new-svg"))
    assert seen == [".pdf", ".svg", ".json"]
    assert svgstore.load(store[0], SOURCE, PATCH) == previous
    orphan = store[1] / "blobs" / (hashlib.sha256(b"new").hexdigest() + ".pdf")
    assert orphan.exists()
    temporary = store[1] / "blobs" / ("f" * 64 + ".pdf.123.456.tmp")
    temporary.write_bytes(b"partial")
    _save(store)
    assert not orphan.exists() and not temporary.exists()


def test_concurrent_saves_and_loads_cannot_mix_pairs(store):
    _save(store)
    barrier = threading.Barrier(4)

    def write(index):
        barrier.wait(timeout=10)
        for revision in range(6):
            payload = f"pair-{index}-{revision}".encode()
            _save(store, **_args(pdf=payload, svg=payload, generation=1))

    def read():
        barrier.wait(timeout=10)
        for _ in range(24):
            result = svgstore.load(store[0], SOURCE, PATCH)
            assert result is not None
            assert (result["pdf"], result["svg"]) == (PDF, SVG) or result["pdf"] == result["svg"]
            assert result["receipt"]["generation"] == result["pdf_artifact"]["generation"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(write, i) for i in range(3)] + [executor.submit(read)]
        for future in futures:
            future.result(timeout=30)


def test_explicit_activation_revokes_every_old_patch_and_static_binding(store):
    other_patch = patchspec.patch_hash([{"id": "title", "prop": "text", "value": "edited"}])
    _save(store)
    _save(store, patch=other_patch)
    _save(store, static_sha256="c" * 64)
    next_args = _args(generation=2)
    svgstore.activate(store[0], SOURCE, next_args["receipt"])
    # The new run's capture has not succeeded yet; old bytes are not a fallback.
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    assert svgstore.load(store[0], SOURCE, other_patch) is None
    assert svgstore.load(store[0], SOURCE, PATCH, static_sha256="c" * 64) is None
    assert not list((store[1] / "entries").iterdir())
    with pytest.raises(ValueError, match="inactive execution"):
        _save(store)  # Late export from the previous worker cannot reactivate it.
    new = _save(store, **next_args)
    assert svgstore.load(store[0], SOURCE, PATCH) == new
    assert new["receipt"]["generation"] == 2


def test_activation_uses_receipt_not_globally_monotonic_generation(store):
    _save(store, **_args(generation=900))
    args = _args(generation=1)
    for part in ("artifact", "receipt"):
        args[part]["receipt_id"] = "sha256:" + "f" * 64
    new = _save(store, **args, activate=True)
    assert new["receipt"]["generation"] == 1
    with pytest.raises(ValueError, match="inactive execution"):
        _save(store, **_args(generation=900))
    assert svgstore.load(store[0], SOURCE, PATCH) == new


def test_activation_scoped_to_project_source_and_remap(store):
    _save(store)
    _save(store, source="other")
    _save(store, remap_fingerprint="remapped")
    another_project = store[0] / "second"
    svgstore.save(another_project, SOURCE, PATCH, **_args())
    svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"])
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    assert svgstore.load(store[0], "other", PATCH)
    assert svgstore.load(store[0], SOURCE, PATCH, remap_fingerprint="remapped")
    assert svgstore.load(another_project, SOURCE, PATCH)


@pytest.mark.parametrize("default_encoding", [None, "cp1252"])
def test_marker_receipt_corruption_is_a_miss_and_not_ordinary_reactivation(
    store, monkeypatch, default_encoding
):
    if default_encoding is not None:
        read_text = Path.read_text

        def legacy_default(path, encoding=None, errors=None, **kwargs):
            return read_text(path, encoding=encoding or default_encoding, errors=errors, **kwargs)

        monkeypatch.setattr(Path, "read_text", legacy_default)
    _save(store)
    marker = next((store[1] / "sources").glob("*.json"))
    record = json.loads(marker.read_bytes())
    assert record["payload"]["identity"]["source_id"] == SOURCE
    record["payload"]["receipt"]["generation"] = 2
    record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    marker.write_bytes(svgstore._json(record))
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    with pytest.raises(ValueError, match="inactive execution"):
        _save(store)


def test_activation_markers_have_bounded_count_even_when_capture_fails(store, monkeypatch):
    monkeypatch.setattr(svgstore, "MAX_SOURCES", 2)
    for source in ("one", "two", "three"):
        svgstore.activate(store[0], source, _args()["receipt"])
    assert len(list((store[1] / "sources").glob("*.json"))) == 2


def test_source_marker_symlink_never_read_or_written(store, tmp_path):
    _save(store)
    marker = next((store[1] / "sources").glob("*.json"))
    outside = tmp_path / "outside-marker"
    marker.rename(outside)
    _symlink(marker, outside)
    before = outside.read_bytes()
    assert svgstore.load(store[0], SOURCE, PATCH) is None
    with pytest.raises(ValueError, match="unsafe"):
        svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"])
    with pytest.raises(ValueError, match="unsafe"):
        _save(store)
    assert outside.read_bytes() == before


def test_process_lock_blocks_eviction_until_paired_read_finishes(store):
    _save(store)
    code = """
import sys
from tavotto.engine import svgstore
root = svgstore._root(create=False)
with svgstore._guard(root):
    print('locked', flush=True)
    assert sys.stdin.readline().strip() == 'release'
"""
    with subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    ) as process:
        assert process.stdout.readline().strip() == "locked"
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(svgstore.load, store[0], SOURCE, PATCH)
            with pytest.raises(concurrent.futures.TimeoutError):
                future.result(timeout=0.1)
            process.stdin.write("release\n")
            process.stdin.flush()
            loaded = future.result(timeout=10)
            assert loaded["pdf"] == PDF and loaded["svg"] == SVG
        process.communicate(timeout=10)
        assert process.returncode == 0


@pytest.fixture
def durable(store):
    return store[0] / "tavottofile" / "figure_snapshots"


def _tree_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_durable_snapshot_moves_with_project_and_ignores_cleared_cache(
    store, durable, tmp_path, monkeypatch
):
    now = [1000.0]
    monkeypatch.setattr(svgstore.time, "time", lambda: now[0])
    _save(store)  # Deliberately create a disposable cache as well.
    _save(store, storage_root=durable)
    record = json.loads(next((durable / "entries").glob("*.json")).read_bytes())
    assert record["payload"]["identity"]["project"] == "."
    assert str(store[0]) not in json.dumps(record)
    shutil.rmtree(store[1].parent)
    moved = tmp_path / "moved-project"
    store[0].rename(moved)
    now[0] += svgstore.MAX_AGE_SECONDS * 3
    relocated = moved / "tavottofile" / "figure_snapshots"
    result = svgstore.load(moved, SOURCE, PATCH, storage_root=relocated)
    assert result["pdf"] == PDF and result["svg"] == SVG
    assert svgstore.load(moved, SOURCE, PATCH) is None
    # A later addition must not prune the old durable record for its age.
    svgstore.save(moved, "other", PATCH, **_args(source="other"), storage_root=relocated)
    assert svgstore.load(moved, SOURCE, PATCH, storage_root=relocated)


def test_cold_process_reopens_moved_durable_snapshot_without_cache(store, durable, tmp_path):
    _save(store, storage_root=durable)
    moved = tmp_path / "renamed"
    store[0].rename(moved)
    code = """
import sys
from pathlib import Path
from tavotto.engine import svgstore
project = Path(sys.argv[1])
result = svgstore.load(project, sys.argv[2], sys.argv[3], storage_root=project / 'tavottofile' / 'figure_snapshots')
assert result['pdf'] == bytes.fromhex(sys.argv[4])
assert result['svg'] == bytes.fromhex(sys.argv[5])
assert 'tavotto.engine.pool' not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", code, str(moved), SOURCE, PATCH, PDF.hex(), SVG.hex()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "TAVOTTO_DATA_DIR": str(tmp_path / "empty-cache"),
        },
    )


@pytest.mark.parametrize("budget", ["MAX_TOTAL_BYTES", "MAX_SNAPSHOTS", "MAX_SOURCES"])
def test_durable_capacity_refuses_addition_without_changing_existing_bytes(
    store, durable, monkeypatch, budget
):
    _save(store, storage_root=durable)
    before = _tree_bytes(durable)
    if budget == "MAX_TOTAL_BYTES":
        limit = sum(len(data) for name, data in before.items() if name != ".lock")
    else:
        limit = 1
    monkeypatch.setattr(svgstore, budget, limit)
    with pytest.raises(ValueError, match="budget exceeded"):
        _save(store, source="other", storage_root=durable)
    assert _tree_bytes(durable) == before
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable)["pdf"] == PDF


def test_durable_capacity_refuses_generation_change_before_revocation(store, durable, monkeypatch):
    _save(store, storage_root=durable)
    before = _tree_bytes(durable)
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", sum(len(data) for data in before.values()))
    with pytest.raises(ValueError, match="budget exceeded"):
        _save(
            store, **_args(pdf=b"large" * 2000, generation=2), activate=True, storage_root=durable
        )
    assert _tree_bytes(durable) == before
    assert (
        svgstore.load(store[0], SOURCE, PATCH, storage_root=durable)["receipt"]["generation"] == 1
    )


def test_durable_capacity_activation_refusal_preserves_previous_markers(
    store, durable, monkeypatch
):
    svgstore.activate(store[0], SOURCE, _args()["receipt"], storage_root=durable)
    before = _tree_bytes(durable)
    monkeypatch.setattr(svgstore, "MAX_SOURCES", 1)
    with pytest.raises(ValueError, match="budget exceeded"):
        svgstore.activate(store[0], "other", _args()["receipt"], storage_root=durable)
    assert _tree_bytes(durable) == before


def test_durable_replacement_budget_counts_final_bytes_and_keeps_other_figures(
    store, durable, monkeypatch
):
    _save(store, storage_root=durable)
    _save(store, source="other", storage_root=durable)
    monkeypatch.setattr(svgstore, "MAX_SNAPSHOTS", 4)
    before = sum(len(data) for name, data in _tree_bytes(durable).items() if name != ".lock")
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", before + 100)
    _save(store, storage_root=durable)
    assert svgstore.load(store[0], "other", PATCH, storage_root=durable)
    assert len(list((durable / "entries").glob("*.json"))) == 2


def test_durable_activation_removes_current_lookup_but_retains_archived_generation(store, durable):
    old = _save(store, storage_root=durable)
    independent = _save(
        store,
        source="other",
        **_args(source="other", pdf=b"other-pdf", svg=b"other-svg"),
        storage_root=durable,
    )
    svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"], storage_root=durable)
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable) is None
    assert old["pdf_path"].exists() and old["svg_path"].exists()
    assert (
        svgstore.load_snapshot(
            store[0], old["snapshot_ref"], source_id=SOURCE, patch_hash=PATCH, storage_root=durable
        )
        == old
    )
    assert svgstore.load(store[0], "other", PATCH, storage_root=durable) == independent
    with pytest.raises(ValueError, match="inactive execution"):
        _save(store, storage_root=durable)


def test_durable_unreadable_metadata_is_never_treated_as_eviction_permission(store, durable):
    _save(store, storage_root=durable)
    path = next((durable / "entries").glob("*.json"))
    record = json.loads(path.read_bytes())
    record["payload"]["version"] = 999
    record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    path.write_bytes(svgstore._json(record))
    before = _tree_bytes(durable)
    with pytest.raises(ValueError, match="version"):
        _save(store, source="other", storage_root=durable)
    assert _tree_bytes(durable) == before


@pytest.mark.parametrize(
    "target", ["root", "entries", "sources", "blobs", "pdf", "svg", "metadata"]
)
def test_durable_storage_retains_symlink_rejection(store, durable, tmp_path, target):
    saved = _save(store, storage_root=durable)
    paths = {
        "root": durable,
        "entries": durable / "entries",
        "sources": durable / "sources",
        "blobs": durable / "blobs",
        "pdf": saved["pdf_path"],
        "svg": saved["svg_path"],
        "metadata": next((durable / "entries").glob("*.json")),
    }
    path = paths[target]
    outside = tmp_path / "outside"
    path.rename(outside)
    _symlink(path, outside, directory=outside.is_dir())
    before = _tree_bytes(outside) if outside.is_dir() else outside.read_bytes()
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable) is None
    with pytest.raises((ValueError, OSError)):
        _save(store, storage_root=durable)
    after = _tree_bytes(outside) if outside.is_dir() else outside.read_bytes()
    assert after == before


def test_durable_root_rejects_path_traversal(store):
    root = store[0] / "tavottofile" / ".." / "escape"
    with pytest.raises(ValueError, match="unsafe"):
        _save(store, storage_root=root)
    assert not (store[0] / "escape").exists()


def _load_snapshot(store, durable, reference, **kwargs):
    args = {"source_id": SOURCE, "patch_hash": PATCH, "storage_root": durable}
    args.update(kwargs)
    return svgstore.load_snapshot(store[0], reference, **args)


def _expected_reference(args, *, source=SOURCE, patch=PATCH, static=None, remap="", owner=None):
    # This is the published ID contract, independent of mutable timestamps.
    identity = {
        "project": ".",
        "source_id": source,
        "patch_hash": patch,
        "static_sha256": static,
        "remap_fingerprint": remap,
    }
    receipt = {
        key: args["receipt"][key] for key in ("receipt_id", "receipt_identity", "generation")
    }
    data = {
        "identity": identity,
        "receipt": receipt,
        "pdf": hashlib.sha256(args["pdf"]).hexdigest(),
        "svg": hashlib.sha256(args["svg"]).hexdigest(),
    }
    if owner is not None:
        data["execution_owner"] = owner
    reference = {
        "version": 1,
        "sourceId": source,
        "patchHash": patch,
        "receiptId": receipt["receipt_id"],
        "snapshotId": hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    if owner is not None:
        reference["executionOwner"] = owner
    return reference


def test_durable_reference_content_address_and_immutable_repeat(store, durable, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(svgstore.time, "time", lambda: now[0])
    saved = _save(store, storage_root=durable)
    reference = saved["snapshot_ref"]
    assert reference == _expected_reference(_args())
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable)["snapshot_ref"] == reference
    path = durable / "snapshots" / (reference["snapshotId"] + ".json")
    established = path.read_bytes()
    now[0] += 100
    again = _save(store, storage_root=durable)
    assert again == saved
    assert path.read_bytes() == established
    assert len(list((durable / "snapshots").glob("*.json"))) == 1
    assert _load_snapshot(store, durable, reference) == saved
    detached = svgstore.validate_reference(reference)
    detached["sourceId"] = "changed"
    assert reference["sourceId"] == SOURCE


@pytest.mark.parametrize(
    "change",
    [
        lambda ref: ref.update(extra=True),
        lambda ref: ref.pop("snapshotId"),
        lambda ref: ref.update(version=True),
        lambda ref: ref.update(version=2),
        lambda ref: ref.update(sourceId=""),
        lambda ref: ref.update(sourceId="bad\0source"),
        lambda ref: ref.update(sourceId=[SOURCE]),
        lambda ref: ref.update(sourceId="x" * 16385),
        lambda ref: ref.update(patchHash="a" * 64),
        lambda ref: ref.update(patchHash="sha256:" + "A" * 64),
        lambda ref: ref.update(receiptId=None),
        lambda ref: ref.update(receiptId="../receipt"),
        lambda ref: ref.update(snapshotId="../outside"),
        lambda ref: ref.update(snapshotId="sha256:" + "a" * 64),
        lambda ref: ref.update(snapshotId="A" * 64),
        lambda ref: ref.update(snapshotId="a" * 63),
    ],
)
def test_reference_schema_is_closed_strict_and_non_mutating(store, durable, change):
    reference = _save(store, storage_root=durable)["snapshot_ref"]
    change(reference)
    before = _tree_bytes(durable)
    with pytest.raises(ValueError, match="reference"):
        svgstore.validate_reference(reference)
    assert _load_snapshot(store, durable, reference) is None
    assert _tree_bytes(durable) == before


@pytest.mark.parametrize("reference", [None, [], "id", True, 1])
def test_reference_non_objects_are_rejected(reference):
    with pytest.raises(ValueError, match="reference"):
        svgstore.validate_reference(reference)


def test_snapshot_reference_every_lookup_dimension_is_exact(store, durable):
    reference = _save(
        store, static_sha256="c" * 64, remap_fingerprint="mapped", storage_root=durable
    )["snapshot_ref"]
    assert _load_snapshot(
        store, durable, reference, static_sha256="c" * 64, remap_fingerprint="mapped"
    )
    for options in (
        {},
        {"static_sha256": "c" * 64},
        {"static_sha256": "d" * 64, "remap_fingerprint": "mapped"},
        {"static_sha256": "c" * 64, "remap_fingerprint": "changed"},
        {"static_sha256": "c" * 64, "remap_fingerprint": "mapped", "source_id": "other"},
        {
            "static_sha256": "c" * 64,
            "remap_fingerprint": "mapped",
            "patch_hash": "sha256:" + "f" * 64,
        },
    ):
        assert _load_snapshot(store, durable, reference, **options) is None
    for field, value in (
        ("sourceId", "other"),
        ("patchHash", "sha256:" + "c" * 64),
        ("receiptId", "sha256:" + "c" * 64),
        ("snapshotId", "c" * 64),
    ):
        assert (
            _load_snapshot(
                store,
                durable,
                {**reference, field: value},
                static_sha256="c" * 64,
                remap_fingerprint="mapped",
            )
            is None
        )
    assert _load_snapshot(store, None, reference) is None  # No default-cache fallback.


def test_archived_pair_survives_new_generation_and_failed_new_capture(store, durable):
    old = _save(store, storage_root=durable)
    svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"], storage_root=durable)
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable) is None
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
    new = _save(
        store, **_args(pdf=b"second PDF", svg=b"second SVG", generation=2), storage_root=durable
    )
    assert new["snapshot_ref"] != old["snapshot_ref"]
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable) == new
    svgstore.activate(store[0], SOURCE, _args(generation=3)["receipt"], storage_root=durable)
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
    assert _load_snapshot(store, durable, new["snapshot_ref"]) == new
    with pytest.raises(ValueError, match="inactive execution"):
        _save(store, storage_root=durable)


@pytest.mark.parametrize("changed", ["pdf", "svg", "both"])
def test_same_receipt_and_patch_changed_bytes_never_rewrite_prior_version(store, durable, changed):
    old = _save(store, storage_root=durable)
    old_path = durable / "snapshots" / (old["snapshot_ref"]["snapshotId"] + ".json")
    prior_metadata = old_path.read_bytes()
    args = _args(
        pdf=b"new PDF" if changed != "svg" else PDF, svg=b"new SVG" if changed != "pdf" else SVG
    )
    new = _save(store, **args, storage_root=durable)
    assert new["snapshot_ref"] == _expected_reference(args)
    assert new["snapshot_ref"]["snapshotId"] != old["snapshot_ref"]["snapshotId"]
    assert new["snapshot_ref"]["receiptId"] == old["snapshot_ref"]["receiptId"]
    assert old_path.read_bytes() == prior_metadata
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
    assert _load_snapshot(store, durable, new["snapshot_ref"]) == new


def test_legacy_current_record_gets_no_fabricated_reference_until_explicit_save(store, durable):
    saved = _save(store, storage_root=durable)
    shutil.rmtree(durable / "snapshots")  # Represents an earlier, current-only store.
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable)["snapshot_ref"] is None
    assert _load_snapshot(store, durable, saved["snapshot_ref"]) is None
    again = _save(store, storage_root=durable)
    assert again["snapshot_ref"] == saved["snapshot_ref"]
    assert _load_snapshot(store, durable, again["snapshot_ref"]) == again


def test_archived_reference_loads_readonly_moved_project_after_cache_and_markers_removed(
    store, durable, tmp_path, monkeypatch
):
    old = _save(store, storage_root=durable)
    svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"], storage_root=durable)
    moved = tmp_path / "moved"
    store[0].rename(moved)
    relocated = moved / "tavottofile" / "figure_snapshots"
    shutil.rmtree(relocated / "entries")
    shutil.rmtree(relocated / "sources")
    (relocated / ".lock").unlink()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setattr(svgstore.time, "time", lambda: 1e11)
    original = os.open

    def readonly(path, flags, *args, **kwargs):
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY), "snapshot read attempted a write"
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(svgstore.os, "open", readonly)
    before = _tree_bytes(relocated)
    result = svgstore.load_snapshot(
        moved, old["snapshot_ref"], source_id=SOURCE, patch_hash=PATCH, storage_root=relocated
    )
    assert result["pdf"] == PDF and result["svg"] == SVG
    assert result["snapshot_ref"] == old["snapshot_ref"]
    assert _tree_bytes(relocated) == before
    assert not (tmp_path / "empty-cache").exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(version=2),
        lambda p: p.update(extra=True),
        lambda p: p["identity"].update(source_id="other"),
        lambda p: p["identity"].update(static_sha256="c" * 64),
        lambda p: p["identity"].update(remap_fingerprint="other"),
        lambda p: p["receipt"].update(generation=2),
        lambda p: p["receipt"].update(receipt_id="sha256:" + "c" * 64),
        lambda p: p["svg_artifact"].update(bytes_sha256="c" * 64),
        lambda p: p["pdf_artifact"].update(size_bytes=1),
    ],
)
def test_archive_resigned_metadata_tampering_never_matches_reference(store, durable, change):
    reference = _save(store, storage_root=durable)["snapshot_ref"]
    path = durable / "snapshots" / (reference["snapshotId"] + ".json")
    record = json.loads(path.read_bytes())
    change(record["payload"])
    record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    path.write_bytes(svgstore._json(record))
    assert _load_snapshot(store, durable, reference) is None


@pytest.mark.parametrize("kind", ["pdf", "svg"])
def test_archive_reads_recheck_both_blob_hashes(store, durable, kind):
    saved = _save(store, storage_root=durable)
    path = saved[kind + "_path"]
    path.write_bytes(b"X" * path.stat().st_size)
    assert _load_snapshot(store, durable, saved["snapshot_ref"]) is None


def test_archive_filename_is_not_an_alias_for_another_version(store, durable):
    saved = _save(store, storage_root=durable)
    reference = saved["snapshot_ref"]
    path = durable / "snapshots" / (reference["snapshotId"] + ".json")
    alias = "c" * 64
    path.rename(path.with_name(alias + ".json"))
    assert _load_snapshot(store, durable, {**reference, "snapshotId": alias}) is None
    # Publication must not adopt an alias as an established immutable record,
    # even though the read API independently checks the reference content ID.
    with pytest.raises(ValueError, match="filename"):
        svgstore.activate(store[0], SOURCE, _args()["receipt"], storage_root=durable)


@pytest.mark.parametrize("target", ["root", "snapshots", "blobs", "archive", "pdf", "svg"])
def test_archive_load_rejects_symlinks(store, durable, tmp_path, target):
    saved = _save(store, storage_root=durable)
    paths = {
        "root": durable,
        "snapshots": durable / "snapshots",
        "blobs": durable / "blobs",
        "archive": durable / "snapshots" / (saved["snapshot_ref"]["snapshotId"] + ".json"),
        "pdf": saved["pdf_path"],
        "svg": saved["svg_path"],
    }
    path = paths[target]
    outside = tmp_path / "outside"
    path.rename(outside)
    _symlink(path, outside, directory=outside.is_dir())
    assert _load_snapshot(store, durable, saved["snapshot_ref"]) is None


def test_archive_count_budget_includes_inactive_versions_and_current_metadata(
    store, durable, monkeypatch
):
    first = _save(store, storage_root=durable)
    monkeypatch.setattr(svgstore, "MAX_SNAPSHOTS", 3)
    second = _save(store, **_args(pdf=b"second"), storage_root=durable)
    assert len(list((durable / "snapshots").glob("*.json"))) == 2
    assert len(list((durable / "entries").glob("*.json"))) == 1
    before = _tree_bytes(durable)
    with pytest.raises(ValueError, match="budget exceeded"):
        _save(store, **_args(pdf=b"third"), storage_root=durable)
    assert _tree_bytes(durable) == before
    assert _load_snapshot(store, durable, first["snapshot_ref"]) == first
    assert _load_snapshot(store, durable, second["snapshot_ref"]) == second
    svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"], storage_root=durable)
    assert len(list((durable / "snapshots").glob("*.json"))) == 2
    # Clearing the mutable current slot does not make room for two new records.
    before = _tree_bytes(durable)
    with pytest.raises(ValueError, match="budget exceeded"):
        _save(store, **_args(pdf=b"third", generation=2), storage_root=durable)
    assert _tree_bytes(durable) == before


def test_archive_byte_budget_counts_retained_metadata_and_deduplicates_blobs(
    store, durable, monkeypatch
):
    now = [1000.0]
    monkeypatch.setattr(svgstore.time, "time", lambda: now[0])
    first = _save(store, storage_root=durable)
    before = _tree_bytes(durable)
    exact_size = sum(len(data) for name, data in before.items() if name != ".lock")
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", exact_size)
    assert _save(store, storage_root=durable) == first  # No double-counting shared blobs.
    # Existing archive whitespace still consumes physical bytes even on reuse.
    path = durable / "snapshots" / (first["snapshot_ref"]["snapshotId"] + ".json")
    path.write_bytes(path.read_bytes() + b"\n")
    before = _tree_bytes(durable)
    with pytest.raises(ValueError, match="budget exceeded"):
        _save(store, storage_root=durable)
    assert _tree_bytes(durable) == before


@pytest.mark.parametrize("stage", ["svg", "snapshots", "entries"])
def test_half_committed_durable_save_never_loses_prior_reference(
    store, durable, monkeypatch, stage
):
    old = _save(store, storage_root=durable)
    args = _args(pdf=b"new PDF", svg=b"new SVG")
    new_ref = _expected_reference(args)
    original = svgstore.atomicio.write_bytes

    def fail(path, data, **kwargs):
        if (stage == "svg" and path.suffix == ".svg") or path.parent.name == stage:
            raise OSError("simulated interrupted publication")
        return original(path, data, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", fail)
    with pytest.raises(OSError, match="interrupted"):
        _save(store, **args, storage_root=durable)
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable) == old
    committed = _load_snapshot(store, durable, new_ref)
    assert (committed is not None) == (stage == "entries")
    monkeypatch.setattr(svgstore.atomicio, "write_bytes", original)
    # Cleanup cannot erase an archive which committed before current publication failed.
    svgstore.activate(store[0], SOURCE, _args()["receipt"], storage_root=durable)
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
    assert (_load_snapshot(store, durable, new_ref) is not None) == (stage == "entries")
    result = _save(store, **args, storage_root=durable)
    assert result["snapshot_ref"] == new_ref
    assert _load_snapshot(store, durable, new_ref) == result


def test_archive_read_during_new_publication_is_complete_or_missing_without_write_lock(
    store, durable, monkeypatch
):
    old = _save(store, storage_root=durable)
    args = _args(pdf=b"new PDF", svg=b"new SVG")
    new_ref = _expected_reference(args)
    writing = threading.Event()
    release = threading.Event()
    original = svgstore.atomicio.write_bytes

    def pause(path, data, **kwargs):
        if path.parent.name == "snapshots":
            writing.set()
            assert release.wait(timeout=10)
        return original(path, data, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", pause)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_save, store, **args, storage_root=durable)
        try:
            assert writing.wait(timeout=10)
            assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
            assert _load_snapshot(store, durable, new_ref) is None
        finally:
            release.set()
        result = future.result(timeout=10)
    assert _load_snapshot(store, durable, new_ref) == result
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old


@pytest.mark.parametrize("constant", ["MAX_METADATA_BYTES", "MAX_PDF_BYTES", "MAX_SVG_BYTES"])
def test_archive_reads_keep_size_bounds(store, durable, monkeypatch, constant):
    saved = _save(store, storage_root=durable)
    monkeypatch.setattr(svgstore, constant, 1)
    assert _load_snapshot(store, durable, saved["snapshot_ref"]) is None


def test_corrupt_archive_never_yields_a_usable_reference_or_eviction_permission(store, durable):
    saved = _save(store, storage_root=durable)
    path = durable / "snapshots" / (saved["snapshot_ref"]["snapshotId"] + ".json")
    record = json.loads(path.read_bytes())
    record["payload"]["pdf_artifact"]["size_bytes"] = 1
    record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    path.write_bytes(svgstore._json(record))
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable)["snapshot_ref"] is None
    assert _load_snapshot(store, durable, saved["snapshot_ref"]) is None
    before = _tree_bytes(durable)
    with pytest.raises(ValueError):
        _save(store, storage_root=durable)
    assert _tree_bytes(durable) == before
    record["payload"]["version"] = 999
    record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    path.write_bytes(svgstore._json(record))
    before = _tree_bytes(durable)
    with pytest.raises(ValueError, match="version"):
        svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"], storage_root=durable)
    assert _tree_bytes(durable) == before


def test_concurrent_durable_saves_keep_every_issued_reference(store, durable):
    def publish(i):
        data = str(i).encode()
        return _save(store, **_args(pdf=b"PDF" + data, svg=b"SVG" + data), storage_root=durable)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        saved = list(executor.map(publish, range(12)))
    assert len({item["snapshot_ref"]["snapshotId"] for item in saved}) == 12
    for item in saved:
        assert _load_snapshot(store, durable, item["snapshot_ref"]) == item
    assert len(list((durable / "entries").glob("*.json"))) == 1
    assert len(list((durable / "snapshots").glob("*.json"))) == 12


OWNER = {"script": "plots/#图~case.py", "run_config": None}


def _owner_records(store, durable, owner=OWNER):
    return svgstore.source_records_for_owner(
        store[0], owner["script"], owner["run_config"], storage_root=durable
    )


def _source_receipt(source=SOURCE, generation=1, current=True):
    return {
        "source_id": source,
        "receipt_id": RECEIPT_ID,
        "receipt_identity": RECEIPT_IDENTITY,
        "generation": generation,
        "current": current,
    }


@pytest.mark.parametrize("persistent", [False, True])
def test_explicit_owner_roundtrips_detached_and_is_bound_to_receipt(store, durable, persistent):
    root = durable if persistent else None
    owner = dict(OWNER)
    saved = _save(store, execution_owner=owner, storage_root=root)
    owner["script"] = "changed.py"
    assert saved["execution_owner"] == OWNER
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=root) == saved
    assert _owner_records(store, root) == [_source_receipt()]
    if persistent:
        assert _load_snapshot(store, root, saved["snapshot_ref"]) == saved
        assert saved["snapshot_ref"] == _expected_reference(_args(), owner=OWNER)
        reference = saved["snapshot_ref"]
        second_args = _args(generation=2)
        second_args["artifact"]["receipt_id"] = "sha256:" + "d" * 64
        second_args["receipt"]["receipt_id"] = "sha256:" + "d" * 64
        second = _save(
            store, **second_args, execution_owner=OWNER, storage_root=root, activate=True
        )
        assert second["snapshot_ref"] == _expected_reference(second_args, owner=OWNER)
        assert second["snapshot_ref"] != reference
        assert _load_snapshot(store, root, reference) == saved
        assert _owner_records(store, root) == [
            _source_receipt(current=False),
            {**_source_receipt(generation=2), "receipt_id": "sha256:" + "d" * 64},
        ]


def test_ownerless_records_keep_original_id_and_never_gain_inferred_ownership(store, durable):
    old = _save(store, storage_root=durable)
    assert old["execution_owner"] is None
    assert old["snapshot_ref"] == _expected_reference(_args())
    assert _owner_records(store, durable, {"script": "图.py", "run_config": None}) == []
    assert _owner_records(store, durable) == []
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
    # Adding independently known provenance is a new immutable version, not a
    # mutation of the legacy reference, despite matching bytes and receipt.
    new = _save(store, storage_root=durable, execution_owner=OWNER)
    assert new["snapshot_ref"] != old["snapshot_ref"]
    assert _load_snapshot(store, durable, old["snapshot_ref"]) == old
    assert _load_snapshot(store, durable, new["snapshot_ref"]) == new
    assert _owner_records(store, durable) == [_source_receipt()]


def test_owner_query_keeps_two_configs_scripts_and_exact_static_ids_separate(store, durable):
    variants = [
        (OWNER, "output/figure B.pdf"),
        ({**OWNER, "run_config": "rc_alpha"}, "opaque:α#not-the-script~name"),
        ({**OWNER, "run_config": "rc_beta"}, "opaque:β#same-prefix"),
        ({"script": "different.py", "run_config": None}, "output/another.pdf"),
    ]
    for owner, source in variants:
        _save(store, source=source, execution_owner=owner, storage_root=durable)
    for owner, source in variants:
        assert _owner_records(store, durable, owner) == [_source_receipt(source)]
    assert _owner_records(store, durable, {**OWNER, "run_config": "rc_missing"}) == []
    # A shared filename is evidence for both historical owners, not a reason to
    # merge their receipt keys or infer the current owner from that filename.
    _save(
        store,
        source="output/figure B.pdf",
        **_args(source="output/figure B.pdf", generation=2),
        execution_owner={**OWNER, "run_config": "rc_alpha"},
        storage_root=durable,
        activate=True,
    )
    assert _owner_records(store, durable) == [_source_receipt("output/figure B.pdf", current=False)]
    assert _owner_records(store, durable, {**OWNER, "run_config": "rc_alpha"}) == [
        _source_receipt("opaque:α#not-the-script~name"),
        _source_receipt("output/figure B.pdf", generation=2),
    ]


def test_owner_query_reads_only_metadata_after_project_move_and_cache_clear(
    store, durable, tmp_path, monkeypatch
):
    _save(store, source="output/B.pdf", execution_owner=OWNER, storage_root=durable)
    svgstore.activate(
        store[0], "output/B.pdf", _args(generation=2)["receipt"], storage_root=durable
    )
    moved = tmp_path / "moved"
    store[0].rename(moved)
    root = moved / "tavottofile" / "figure_snapshots"
    shutil.rmtree(root / "sources")
    shutil.rmtree(root / "blobs")
    (root / ".lock").unlink()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "cleared-cache"))
    original = svgstore._read_file
    opened = []

    def only_metadata(path, limit):
        assert path.parent.name in {"entries", "snapshots"}
        opened.append(path)
        return original(path, limit)

    monkeypatch.setattr(svgstore, "_read_file", only_metadata)
    before = _tree_bytes(root)
    assert svgstore.source_records_for_owner(moved, OWNER["script"], None, storage_root=root) == [
        _source_receipt("output/B.pdf", current=False)
    ]
    assert opened and _tree_bytes(root) == before
    assert not (tmp_path / "cleared-cache").exists()
    assert not (root / ".lock").exists()


def test_owner_query_deduplicates_variants_and_isolates_default_cache_project(store, durable):
    _save(store, execution_owner=OWNER)
    _save(store, patch="sha256:" + "c" * 64, execution_owner=OWNER)
    other = store[0] / "other-project"
    svgstore.save(
        other, "other-source", PATCH, **_args(source="other-source"), execution_owner=OWNER
    )
    assert _owner_records(store, None) == [_source_receipt()]
    assert svgstore.source_records_for_owner(other, OWNER["script"], None) == [
        _source_receipt("other-source")
    ]
    _save(store, execution_owner=OWNER, storage_root=durable)
    _save(store, patch="sha256:" + "c" * 64, execution_owner=OWNER, storage_root=durable)
    assert _owner_records(store, durable) == [_source_receipt()]


@pytest.mark.parametrize(
    "owner",
    [
        {},
        {"script": "plot.py"},
        {"script": "plot.py", "run_config": None, "argv": ["secret"]},
        {"script": "plot.py", "run_config": None, "spec": {}},
        {"script": "plot.py", "run_config": ["secret"]},
        {"script": "plot.py", "run_config": {"argv": ["secret"]}},
        {"script": "plot.py", "run_config": ""},
        {"script": "plot.py", "run_config": "bad\0ref"},
        {"script": "plot.py", "run_config": "x" * 16385},
        {"script": "/private/plot.py", "run_config": None},
        {"script": "C:\\private\\plot.py", "run_config": None},
        {"script": "plots\\plot.py", "run_config": None},
        {"script": "../plot.py", "run_config": None},
        {"script": "plots/../plot.py", "run_config": None},
        {"script": "./plot.py", "run_config": None},
        {"script": "plots//plot.py", "run_config": None},
        {"script": "plots/./plot.py", "run_config": None},
        {"script": "plot.py/", "run_config": None},
        {"script": ".", "run_config": None},
        {"script": "", "run_config": None},
        {"script": " ", "run_config": None},
        {"script": "bad\0.py", "run_config": None},
        {"script": "x" * 16385, "run_config": None},
        {"script": [], "run_config": None},
        [],
        "plot.py",
    ],
)
def test_execution_owner_closed_private_data_free_schema_rejected_before_write(
    store, durable, owner
):
    with pytest.raises(ValueError):
        _save(store, storage_root=durable, execution_owner=owner)
    assert not durable.exists()


@pytest.mark.parametrize("resign", [False, True])
def test_owner_tampering_in_archive_is_detected_and_query_does_not_silently_skip(
    store, durable, resign
):
    saved = _save(store, storage_root=durable, execution_owner=OWNER)
    path = durable / "snapshots" / (saved["snapshot_ref"]["snapshotId"] + ".json")
    record = json.loads(path.read_bytes())
    record["payload"]["execution_owner"]["run_config"] = "rc_other"
    if resign:
        record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    path.write_bytes(svgstore._json(record))
    assert _load_snapshot(store, durable, saved["snapshot_ref"]) is None
    before = _tree_bytes(durable)
    with pytest.raises(ValueError):
        _owner_records(store, durable)
    assert _tree_bytes(durable) == before


@pytest.mark.parametrize("budget", ["MAX_SNAPSHOTS", "MAX_TOTAL_BYTES", "MAX_METADATA_BYTES"])
def test_owner_query_budget_errors_are_not_reported_as_empty_history(
    store, durable, monkeypatch, budget
):
    _save(store, execution_owner=OWNER, storage_root=durable)
    monkeypatch.setattr(svgstore, budget, 1)
    with pytest.raises(ValueError):
        _owner_records(store, durable)


def test_owner_query_missing_store_is_empty_without_side_effects(store, durable):
    assert _owner_records(store, durable) == []
    assert _owner_records(store, None) == []
    assert not durable.exists() and not store[1].exists()


@pytest.mark.parametrize("target", ["root", "entries", "snapshots", "metadata"])
def test_owner_query_rejects_symlinks_and_does_not_mutate(store, durable, tmp_path, target):
    saved = _save(store, execution_owner=OWNER, storage_root=durable)
    path = {
        "root": durable,
        "entries": durable / "entries",
        "snapshots": durable / "snapshots",
        "metadata": durable / "snapshots" / (saved["snapshot_ref"]["snapshotId"] + ".json"),
    }[target]
    outside = tmp_path / "outside"
    path.rename(outside)
    _symlink(path, outside, directory=outside.is_dir())
    before = _tree_bytes(outside) if outside.is_dir() else outside.read_bytes()
    with pytest.raises(ValueError):
        _owner_records(store, durable)
    assert (_tree_bytes(outside) if outside.is_dir() else outside.read_bytes()) == before


def test_current_owner_changes_cannot_rewrite_previously_issued_archive(store, durable):
    first = _save(store, storage_root=durable, execution_owner=OWNER)
    other_owner = {**OWNER, "run_config": "rc_other"}
    second = _save(store, storage_root=durable, execution_owner=other_owner)
    assert first["snapshot_ref"] != second["snapshot_ref"]
    assert (
        svgstore.load(store[0], SOURCE, PATCH, storage_root=durable)["execution_owner"]
        == other_owner
    )
    assert _load_snapshot(store, durable, first["snapshot_ref"])["execution_owner"] == OWNER
    assert _load_snapshot(store, durable, second["snapshot_ref"])["execution_owner"] == other_owner


@pytest.mark.parametrize("field", ["receipt_id", "receipt_identity", "generation"])
def test_owner_query_current_requires_entire_marker_receipt_key(store, durable, field):
    _save(store, storage_root=durable, execution_owner=OWNER)
    assert _owner_records(store, durable) == [_source_receipt()]
    path = next((durable / "sources").glob("*.json"))
    record = json.loads(path.read_bytes())
    record["payload"]["receipt"][field] = 2 if field == "generation" else "sha256:" + "c" * 64
    record["sha256"] = svgstore._digest(svgstore._json(record["payload"]))
    path.write_bytes(svgstore._json(record))
    assert _owner_records(store, durable) == [_source_receipt(current=False)]


def test_owner_query_same_receipt_different_owner_marks_only_actual_current_entry(store, durable):
    other_owner = {**OWNER, "run_config": "rc_other"}
    _save(store, storage_root=durable, execution_owner=OWNER)
    _save(store, storage_root=durable, execution_owner=other_owner)
    assert _owner_records(store, durable) == [_source_receipt(current=False)]
    assert _owner_records(store, durable, other_owner) == [_source_receipt()]


def test_owner_query_marker_missing_is_history_and_corrupt_marker_raises(store, durable):
    _save(store, storage_root=durable, execution_owner=OWNER)
    path = next((durable / "sources").glob("*.json"))
    original = path.read_bytes()
    path.unlink()
    assert _owner_records(store, durable) == [_source_receipt(current=False)]
    path.write_bytes(b"not JSON")
    before = _tree_bytes(durable)
    with pytest.raises(ValueError):
        _owner_records(store, durable)
    assert _tree_bytes(durable) == before
    path.write_bytes(original)
    assert _owner_records(store, durable) == [_source_receipt()]


def test_owner_query_reads_current_marker_without_touching_blobs_or_writable_lock(
    store, durable, monkeypatch
):
    _save(store, storage_root=durable, execution_owner=OWNER)
    shutil.rmtree(durable / "blobs")
    (durable / ".lock").unlink()
    original_read = svgstore._read_file
    original_open = os.open
    opened = []

    def metadata(path, limit):
        assert path.parent.name in {"entries", "snapshots", "sources"}
        opened.append(path.parent.name)
        return original_read(path, limit)

    def readonly(path, flags, *args, **kwargs):
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(svgstore, "_read_file", metadata)
    monkeypatch.setattr(svgstore.os, "open", readonly)
    before = _tree_bytes(durable)
    assert _owner_records(store, durable) == [_source_receipt()]
    assert set(opened) == {"entries", "snapshots", "sources"}
    assert _tree_bytes(durable) == before


def test_owned_current_record_without_matching_archive_is_not_ownership_proof(store, durable):
    _save(store, storage_root=durable, execution_owner=OWNER)
    shutil.rmtree(durable / "snapshots")
    with pytest.raises(ValueError, match="ownership archive is missing"):
        _owner_records(store, durable)


def test_owner_query_current_flag_deduplicates_across_remap_groups(store, durable):
    _save(store, storage_root=durable, execution_owner=OWNER)
    _save(store, storage_root=durable, execution_owner=OWNER, remap_fingerprint="other-remap")
    svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"], storage_root=durable)
    assert _owner_records(store, durable) == [_source_receipt()]
    svgstore.activate(
        store[0],
        SOURCE,
        _args(generation=2)["receipt"],
        storage_root=durable,
        remap_fingerprint="other-remap",
    )
    assert _owner_records(store, durable) == [_source_receipt(current=False)]


def test_reference_owner_is_derived_verified_and_deeply_detached(store, durable):
    saved = _save(store, storage_root=durable, execution_owner=OWNER)
    reference = saved["snapshot_ref"]
    assert reference["executionOwner"] == OWNER
    validated = svgstore.validate_reference(reference)
    validated["executionOwner"]["run_config"] = "rc_other"
    assert reference["executionOwner"] == OWNER
    assert _load_snapshot(store, durable, validated) is None
    assert _load_snapshot(store, durable, reference) == saved
    # Older callers can omit the optional hint; server output restores only
    # the independently validated metadata owner, not a client assertion.
    without_hint = {key: value for key, value in reference.items() if key != "executionOwner"}
    assert _load_snapshot(store, durable, without_hint) == saved


def test_legacy_reference_cannot_be_promoted_by_client_execution_owner(store, durable):
    saved = _save(store, storage_root=durable)
    assert "executionOwner" not in saved["snapshot_ref"]
    reference = {**saved["snapshot_ref"], "executionOwner": OWNER}
    assert svgstore.validate_reference(reference) == reference
    assert _load_snapshot(store, durable, reference) is None
    assert _load_snapshot(store, durable, saved["snapshot_ref"]) == saved
    assert _owner_records(store, durable) == []


@pytest.mark.parametrize(
    "owner",
    [
        None,
        {},
        {"script": "plot.py", "run_config": None, "argv": ["secret"]},
        {"script": "plot.py", "run_config": None, "spec": {}},
        {"script": "plot.py", "run_config": []},
        {"script": "../plot.py", "run_config": None},
        {"script": "/private/plot.py", "run_config": None},
        {"script": "plot.py", "run_config": ""},
    ],
)
def test_reference_optional_owner_uses_same_closed_privacy_schema(store, durable, owner):
    saved = _save(store, storage_root=durable, execution_owner=OWNER)
    reference = {**saved["snapshot_ref"], "executionOwner": owner}
    with pytest.raises(ValueError):
        svgstore.validate_reference(reference)
    assert _load_snapshot(store, durable, reference) is None
