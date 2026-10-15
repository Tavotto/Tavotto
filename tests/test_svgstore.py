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
    record = json.loads(path.read_text())
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


def test_marker_receipt_corruption_is_a_miss_and_not_ordinary_reactivation(store):
    _save(store)
    marker = next((store[1] / "sources").glob("*.json"))
    record = json.loads(marker.read_text())
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
    monkeypatch.setattr(svgstore, "MAX_SNAPSHOTS", 2)
    before = sum(len(data) for name, data in _tree_bytes(durable).items() if name != ".lock")
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", before + 100)
    _save(store, storage_root=durable)
    assert svgstore.load(store[0], "other", PATCH, storage_root=durable)
    assert len(list((durable / "entries").glob("*.json"))) == 2


def test_durable_activation_removes_only_superseded_generation_and_orphan_blobs(store, durable):
    old = _save(store, storage_root=durable)
    independent = _save(
        store,
        source="other",
        **_args(source="other", pdf=b"other-pdf", svg=b"other-svg"),
        storage_root=durable,
    )
    svgstore.activate(store[0], SOURCE, _args(generation=2)["receipt"], storage_root=durable)
    assert svgstore.load(store[0], SOURCE, PATCH, storage_root=durable) is None
    assert not old["pdf_path"].exists() and not old["svg_path"].exists()
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
