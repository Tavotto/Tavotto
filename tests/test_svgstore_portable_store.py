"""A package proves the whole Figure archive, including unreferenced members."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from tavotto.engine import svgstore
from test_svgstore import OWNER, PATCH, PDF, SOURCE, SVG, _args


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _publish(project, root, source=SOURCE, patch=PATCH, **kwargs):
    args = {**_args(source, patch), "execution_owner": OWNER, **kwargs}
    return svgstore.save(project, source, patch, storage_root=root, **args)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "unused-data"))
    project = tmp_path / "搬迁项目"
    project.mkdir()
    root = project / svgstore.STORE_DIRNAME
    saved = _publish(project, root)
    return project, root, saved


def _tree(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _metadata(root, directory):
    return next((root / directory).glob("*.json"))


def _resign(path, mutate):
    record = json.loads(path.read_bytes())
    mutate(record["payload"])
    record["sha256"] = hashlib.sha256(_json(record["payload"])).hexdigest()
    path.write_bytes(_json(record))
    return record["payload"]


def _content_id(payload):
    identity = {
        "identity": payload["identity"],
        "receipt": {
            key: payload["receipt"][key] for key in ("receipt_id", "receipt_identity", "generation")
        },
        "pdf": payload["pdf_artifact"]["bytes_sha256"],
        "svg": payload["svg_artifact"]["bytes_sha256"],
    }
    if "execution_owner" in payload:
        identity["execution_owner"] = payload["execution_owner"]
    return hashlib.sha256(_json(identity)).hexdigest()


def _rejects(root, match=None):
    before = _tree(root)
    with pytest.raises((ValueError, OSError), match=match):
        svgstore.inspect_portable_store(storage_root=root)
    assert _tree(root) == before


def test_producer_members_metadata_and_shared_blobs_are_all_checked_once(store, monkeypatch):
    project, root, first = store
    second = _publish(project, root, patch="sha256:" + "c" * 64)
    third = _publish(project, root, source="independent output.pdf")
    before = _tree(root)
    opened = Counter()
    original = svgstore._read_file

    def observed(path, limit):
        opened[path.relative_to(root).as_posix()] += 1
        return original(path, limit)

    def no_per_reference_load(*args, **kwargs):
        pytest.fail("whole-store inspection must not retain/re-read a pair per reference")

    monkeypatch.setattr(svgstore, "_read_file", observed)
    monkeypatch.setattr(svgstore, "inspect_portable_snapshot", no_per_reference_load)
    result = svgstore.inspect_portable_store(storage_root=root)
    assert set(result) == {"members", "entries", "sources", "snapshots", "blobs", "total_bytes"}
    assert result["members"] == tuple(sorted(before))
    assert result["total_bytes"] == sum(
        len(data) for name, data in before.items() if name != ".lock"
    )
    assert len(result["entries"]) == len(result["snapshots"]) == 3
    assert len(result["sources"]) == len(result["blobs"]) == 2
    assert opened == Counter({name: 1 for name in before})
    for saved in (first, second, third):
        facts = result["snapshots"][saved["snapshot_ref"]["snapshotId"]]
        assert set(facts) == {
            "payload",
            "reference",
            "identity",
            "receipt",
            "pdf_artifact",
            "svg_artifact",
            "execution_owner",
        }
        assert facts["reference"] == saved["snapshot_ref"]
        assert facts["execution_owner"] == OWNER
        assert facts["identity"] == facts["payload"]["identity"]
        assert facts["receipt"] == saved["receipt"] == facts["payload"]["receipt"]
        assert facts["pdf_artifact"] == saved["pdf_artifact"]
        assert facts["svg_artifact"] == saved["svg_artifact"]
        assert facts["payload"]["version"] == 1
        assert facts["payload"]["created_at"] > 0
        assert _content_id(facts["payload"]) == facts["reference"]["snapshotId"]
    for kind, data in (("pdf", PDF), ("svg", SVG)):
        digest = hashlib.sha256(data).hexdigest()
        assert result["blobs"][digest + "." + kind] == {
            "bytes_sha256": digest,
            "size_bytes": len(data),
            "kind": kind,
        }

    def metadata_only(value):
        assert not isinstance(value, (bytes, Path))
        if isinstance(value, dict):
            for item in value.values():
                metadata_only(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                metadata_only(item)

    metadata_only(result)
    assert _tree(root) == before


def test_ownerless_payload_is_not_normalized_and_results_are_detached(store):
    project, root, _ = store
    saved = _publish(project, root, execution_owner=None)
    result = svgstore.inspect_portable_store(storage_root=root)
    facts = result["snapshots"][saved["snapshot_ref"]["snapshotId"]]
    original = copy.deepcopy(facts)
    assert facts["execution_owner"] is None
    assert "execution_owner" not in facts["payload"] and "executionOwner" not in facts["reference"]
    facts["payload"]["receipt"]["generation"] = -1
    facts["reference"]["sourceId"] = "changed"
    assert (
        svgstore.inspect_portable_store(storage_root=root)["snapshots"][
            saved["snapshot_ref"]["snapshotId"]
        ]
        == original
    )


def test_marker_only_is_a_real_producer_state_before_capture(tmp_path):
    project = tmp_path / "project"
    root = project / svgstore.STORE_DIRNAME
    svgstore.activate(project, SOURCE, _args()["receipt"], storage_root=root)
    result = svgstore.inspect_portable_store(storage_root=root)
    assert len(result["sources"]) == 1
    assert result["entries"] == result["snapshots"] == result["blobs"] == {}
    assert set(result["members"]) == {".lock", "sources/" + next(iter(result["sources"])) + ".json"}


def test_later_marker_receipt_can_revoke_entries_and_keep_historical_archives(store):
    project, root, saved = store
    receipt = _args(generation=2)["receipt"]
    svgstore.activate(project, SOURCE, receipt, storage_root=root)
    result = svgstore.inspect_portable_store(storage_root=root)
    assert not result["entries"] and len(result["sources"]) == 1
    assert next(iter(result["sources"].values()))["receipt"]["generation"] == 2
    assert result["snapshots"][saved["snapshot_ref"]["snapshotId"]]["receipt"]["generation"] == 1
    assert svgstore.load(project, SOURCE, PATCH, storage_root=root) is None


def test_archive_only_moved_copy_remains_valid_without_markers_or_lock(store, tmp_path):
    project, root, saved = store
    moved = tmp_path / "已搬迁"
    project.rename(moved)
    root = moved / root.name
    shutil.rmtree(root / "entries")
    shutil.rmtree(root / "sources")
    (root / ".lock").unlink()
    result = svgstore.inspect_portable_store(storage_root=root)
    assert result["entries"] == result["sources"] == {}
    assert (
        result["snapshots"][saved["snapshot_ref"]["snapshotId"]]["reference"]
        == saved["snapshot_ref"]
    )
    assert set(result["members"]) == set(_tree(root))


@pytest.mark.parametrize("data", [b"", b"\0", b"uninterpreted advisory lock bytes"])
def test_lock_is_optional_bounded_regular_data_not_a_new_schema(store, data):
    _, root, _ = store
    (root / ".lock").write_bytes(data)
    result = svgstore.inspect_portable_store(storage_root=root)
    assert ".lock" in result["members"]
    assert result["total_bytes"] == sum(len(v) for k, v in _tree(root).items() if k != ".lock")


@pytest.mark.parametrize(
    "name",
    [
        "extra",
        "cache/unrelated",
        "snapshots/notes.json",
        "sources/README",
        "entries/.DS_Store",
        "blobs/" + "a" * 64 + ".png",
        "blobs/" + "a" * 64 + ".svg.1.2.tmp",
    ],
)
def test_unknown_files_are_not_hidden_by_referenced_snapshot_success(store, name):
    _, root, saved = store
    path = root / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"extra")
    assert svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    _rejects(root, "unknown")


@pytest.mark.parametrize("directory", ["entries", "snapshots", "sources"])
def test_unreferenced_bad_metadata_with_a_legal_name_is_rejected(store, directory):
    _, root, saved = store
    path = root / directory / ("e" * 64 + ".json")
    record = {"version": 999}
    path.write_bytes(
        _json({"payload": record, "sha256": hashlib.sha256(_json(record)).hexdigest()})
    )
    assert svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    _rejects(root)


@pytest.mark.parametrize("directory", ["entries", "snapshots", "sources"])
def test_duplicate_metadata_under_a_resigned_wrong_key_is_not_an_alias(store, directory):
    _, root, _ = store
    path = _metadata(root, directory)
    (path.parent / ("d" * 64 + ".json")).write_bytes(path.read_bytes())
    _rejects(root, "filename|identity")


@pytest.mark.parametrize("directory", ["entries", "snapshots", "sources"])
def test_consistently_resigned_and_renamed_nonportable_records_are_rejected(store, directory):
    project, root, _ = store
    path = _metadata(root, directory)
    payload = _resign(
        path,
        lambda p: p["identity"].update(project=svgstore.config.normalize_path_identity(project)),
    )
    key = (
        _content_id(payload)
        if directory == "snapshots"
        else hashlib.sha256(_json(payload["identity"])).hexdigest()
    )
    path.rename(path.parent / (key + ".json"))
    _rejects(root, "not portable")


@pytest.mark.parametrize("directory", ["entries", "snapshots", "sources"])
def test_each_metadata_namespace_reuses_duplicate_key_parser(store, directory):
    _, root, _ = store
    _metadata(root, directory).write_bytes(b'{"payload":{},"payload":{},"sha256":"x"}')
    _rejects(root, "duplicate")


def test_entry_requires_its_marker_even_though_archive_is_independently_readable(store):
    _, root, saved = store
    _metadata(root, "sources").unlink()
    assert svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    _rejects(root, "matching source marker")


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", 2),
        ("receipt_id", "sha256:" + "d" * 64),
        ("receipt_identity", "sha256:" + "d" * 64),
    ],
)
def test_entry_requires_the_entire_marker_receipt_key(store, field, value):
    _, root, _ = store
    _resign(_metadata(root, "sources"), lambda p: p["receipt"].update({field: value}))
    _rejects(root, "matching source marker")


def test_entry_requires_its_complete_immutable_archive(store):
    _, root, _ = store
    _metadata(root, "snapshots").unlink()
    _rejects(root, "matching immutable archive")


def test_legacy_current_only_loading_is_not_redefined_by_strict_package_inspection(store):
    project, root, _ = store
    shutil.rmtree(root / "snapshots")
    loaded = svgstore.load(project, SOURCE, PATCH, storage_root=root)
    assert loaded["pdf"] == PDF and loaded["svg"] == SVG and loaded["snapshot_ref"] is None
    _rejects(root)


def test_marker_only_nonportable_identity_cannot_hide_without_a_panel_reference(tmp_path):
    project = tmp_path / "project"
    root = project / svgstore.STORE_DIRNAME
    svgstore.activate(project, SOURCE, _args()["receipt"], storage_root=root)
    path = _metadata(root, "sources")
    payload = _resign(
        path,
        lambda p: p["identity"].update(project=svgstore.config.normalize_path_identity(project)),
    )
    path.rename(path.with_name(hashlib.sha256(_json(payload["identity"])).hexdigest() + ".json"))
    _rejects(root, "not portable")


@pytest.mark.parametrize(
    "field,value",
    [("created_at", 1), ("receipt", {**_args()["receipt"], "source_revision": "changed"})],
)
def test_same_content_id_does_not_hide_entry_archive_metadata_differences(store, field, value):
    _, root, saved = store
    _resign(_metadata(root, "entries"), lambda p: p.update({field: value}))
    assert svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    _rejects(root, "matching immutable archive")


@pytest.mark.parametrize("kind", ["pdf", "svg"])
@pytest.mark.parametrize("fault", ["missing", "same-size", "size", "name"])
def test_all_referenced_blobs_have_real_sizes_and_content_addresses(store, kind, fault):
    _, root, saved = store
    path = saved[kind + "_path"]
    if fault == "missing":
        path.unlink()
    elif fault == "same-size":
        path.write_bytes(b"X" * path.stat().st_size)
    elif fault == "size":
        path.write_bytes(path.read_bytes() + b"X")
    else:
        path.rename(path.with_name("d" * 64 + "." + kind))
    _rejects(root, "blob")


@pytest.mark.parametrize("kind", ["pdf", "svg"])
def test_unreferenced_blob_is_checked_and_reported_without_being_promoted(store, kind):
    _, root, _ = store
    data = b"orphan bytes after interrupted publication"
    digest = hashlib.sha256(data).hexdigest()
    path = root / "blobs" / (digest + "." + kind)
    path.write_bytes(data)
    result = svgstore.inspect_portable_store(storage_root=root)
    assert result["blobs"][path.name] == {
        "bytes_sha256": digest,
        "size_bytes": len(data),
        "kind": kind,
    }
    assert "blobs/" + path.name in result["members"]
    assert len(result["snapshots"]) == 1
    path.write_bytes(b"X" * len(data))
    _rejects(root, "content address")


def test_real_interrupted_save_orphans_are_inspected_then_successful_producer_cleans_them(
    store, monkeypatch
):
    project, root, _ = store
    original = svgstore.atomicio.write_bytes

    def interrupted(path, data, **kwargs):
        if path.parent.name == "snapshots":
            raise OSError("simulated archive commit failure")
        return original(path, data, **kwargs)

    monkeypatch.setattr(svgstore.atomicio, "write_bytes", interrupted)
    with pytest.raises(OSError, match="commit failure"):
        _publish(project, root, **_args(pdf=b"new PDF", svg=b"new SVG"))
    result = svgstore.inspect_portable_store(storage_root=root)
    assert len(result["snapshots"]) == len(result["entries"]) == 1
    assert len(result["blobs"]) == 4
    monkeypatch.setattr(svgstore.atomicio, "write_bytes", original)
    _publish(project, root)
    assert len(svgstore.inspect_portable_store(storage_root=root)["blobs"]) == 2


@pytest.mark.parametrize("kind", ["pdf", "svg"])
def test_shared_blob_cannot_have_conflicting_resigned_artifact_sizes(store, kind):
    project, root, _ = store
    second = _publish(project, root, patch="sha256:" + "c" * 64)
    snapshot = root / "snapshots" / (second["snapshot_ref"]["snapshotId"] + ".json")
    payload = _resign(snapshot, lambda p: p[kind + "_artifact"].update(size_bytes=1))
    entry = root / "entries" / (hashlib.sha256(_json(payload["identity"])).hexdigest() + ".json")
    _resign(entry, lambda p: p[kind + "_artifact"].update(size_bytes=1))
    _rejects(root, "inconsistent durable Figure blob size")


@pytest.mark.parametrize(
    "constant,value",
    [
        ("MAX_SNAPSHOTS", 1),
        ("MAX_SOURCES", 0),
        ("MAX_TOTAL_BYTES", 1),
        ("MAX_METADATA_BYTES", 1),
        ("MAX_PDF_BYTES", 1),
        ("MAX_SVG_BYTES", 1),
        ("_MAX_DIRECTORY_ENTRIES", 1),
    ],
)
def test_existing_count_directory_and_byte_budgets_are_enforced_before_read(
    store, monkeypatch, constant, value
):
    _, root, _ = store
    monkeypatch.setattr(svgstore, constant, value)

    def unexpected(*args, **kwargs):
        pytest.fail("over-budget store was read before census rejection")

    monkeypatch.setattr(svgstore, "_read_file", unexpected)
    _rejects(root, "budget|oversized")


def test_total_budget_counts_whitespace_and_orphans_but_not_lock(store, monkeypatch):
    _, root, _ = store
    path = _metadata(root, "snapshots")
    path.write_bytes(path.read_bytes() + b"\n \n")
    data = b"orphan"
    (root / "blobs" / (hashlib.sha256(data).hexdigest() + ".svg")).write_bytes(data)
    exact = sum(len(v) for k, v in _tree(root).items() if k != ".lock")
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", exact)
    assert svgstore.inspect_portable_store(storage_root=root)["total_bytes"] == exact
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", exact - 1)
    _rejects(root, "budget")


def test_oversized_lock_is_rejected_independently_of_store_quota(store, monkeypatch):
    _, root, _ = store
    (root / ".lock").write_bytes(b"x" * 1000)
    largest_metadata = max(p.stat().st_size for p in root.glob("*/*.json"))
    monkeypatch.setattr(svgstore, "MAX_METADATA_BYTES", max(largest_metadata, 1000))
    svgstore.inspect_portable_store(storage_root=root)
    (root / ".lock").write_bytes(b"x" * (max(largest_metadata, 1000) + 1))
    _rejects(root, "lock")


@pytest.mark.parametrize(
    "name", [".lock", "entries/" + "e" * 64 + ".json", "blobs/" + "e" * 64 + ".pdf"]
)
def test_a_legal_file_name_cannot_be_a_directory(store, name):
    _, root, _ = store
    path = root / name
    if path.exists():
        path.unlink()
    path.mkdir()
    _rejects(root, "unsafe")


@pytest.mark.parametrize("target", ["root", "entries", "snapshots", "sources", "blobs"])
@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_real_windows_junctions_are_rejected_without_symlink_privileges(store, tmp_path, target):
    _, root, _ = store
    path = root if target == "root" else root / target
    outside = tmp_path / "junction-target"
    path.rename(outside)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(path), str(outside)], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    before = _tree(outside)
    with pytest.raises(ValueError, match="unsafe"):
        svgstore.inspect_portable_store(storage_root=root)
    assert _tree(outside) == before


@pytest.mark.parametrize("fault", ["add", "delete", "replace", "resize"])
def test_detected_inventory_changes_during_read_cannot_produce_success(store, monkeypatch, fault):
    _, root, _ = store
    original = svgstore._read_file
    changed = False

    def racing(path, limit):
        nonlocal changed
        data = original(path, limit)
        if path.parent.name == "blobs" and not changed:
            changed = True
            if fault == "add":
                (root / "extra").write_bytes(b"unexpected")
            elif fault == "delete":
                (root / ".lock").unlink()
            elif fault == "replace":
                target = _metadata(root, "entries")
                replacement = root.parent / "replacement"
                replacement.write_bytes(target.read_bytes())
                os.replace(replacement, target)
            else:
                target = _metadata(root, "snapshots")
                target.write_bytes(target.read_bytes() + b" ")
        return data

    monkeypatch.setattr(svgstore, "_read_file", racing)
    with pytest.raises((ValueError, OSError)):
        svgstore.inspect_portable_store(storage_root=root)
    assert changed


def test_readonly_whole_store_never_opens_write_lock_clock_or_cache(store, monkeypatch):
    _, root, _ = store
    before = _tree(root)
    original = os.open

    def readonly(path, flags, *args, **kwargs):
        assert not flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_TRUNC)
        return original(path, flags, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("archive inspection consulted mutable execution authority")

    monkeypatch.setattr(svgstore.os, "open", readonly)
    monkeypatch.setattr(svgstore, "_guard", forbidden)
    monkeypatch.setattr(svgstore.config, "data_dir", forbidden)
    monkeypatch.setattr(svgstore.time, "time", forbidden)
    monkeypatch.setattr(svgstore.atomicio, "write_bytes", forbidden)
    assert svgstore.inspect_portable_store(storage_root=root)["members"] == tuple(sorted(before))
    assert _tree(root) == before


def test_cold_process_inspects_all_members_after_move_without_dependencies_or_writes(
    store, tmp_path
):
    project, root, saved = store
    moved = tmp_path / "冷进程"
    project.rename(moved)
    root = moved / root.name
    before = _tree(root)
    code = r"""
import json, os, sys
from tavotto.engine import svgstore
def readonly(event, args):
    if event == 'open':
        mode, flags = args[1:3]
        assert not (isinstance(mode, str) and any(c in mode for c in 'wax+'))
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
    assert event not in {'os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'subprocess.Popen', 'os.system'}
sys.addaudithook(readonly)
result = svgstore.inspect_portable_store(storage_root=sys.argv[1])
assert not any(k in sys.modules for k in ('matplotlib', 'numpy', 'tavotto.engine.pool'))
print(json.dumps({'module':svgstore.__file__, 'result':result}))
"""
    child = subprocess.run(
        [sys.executable, "-B", "-c", code, str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "TAVOTTO_DATA_DIR": str(tmp_path / "unused-cold-data"),
        },
    )
    assert child.returncode == 0, child.stderr
    actual = json.loads(child.stdout)
    assert Path(actual["module"]).resolve() == Path(svgstore.__file__).resolve()
    assert (
        actual["result"]["snapshots"][saved["snapshot_ref"]["snapshotId"]]["reference"]
        == saved["snapshot_ref"]
    )
    assert actual["result"]["members"] == sorted(before)
    assert _tree(root) == before and not (tmp_path / "unused-cold-data").exists()


def test_missing_root_does_not_create_anything_and_no_cache_fallback(store, tmp_path):
    _, _, _ = store
    missing = tmp_path / "missing" / svgstore.STORE_DIRNAME
    with pytest.raises(FileNotFoundError):
        svgstore.inspect_portable_store(storage_root=missing)
    with pytest.raises(ValueError, match="storage root"):
        svgstore.inspect_portable_store(storage_root=None)
    assert not missing.parent.exists()
