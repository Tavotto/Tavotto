"""Portable inspection proves archive integrity, never current execution rights.

Like test_svgstore, opaque PDF/SVG bytes isolate this storage contract from the
caller's separate render admission. No worker or source script participates.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import figcapture, patchspec, svgstore

SOURCE = "figures/图.pdf"
PATCH = patchspec.patch_hash([])
PDF = b"%PDF-portable-snapshot"
SVG = b"<svg>portable snapshot</svg>"
STATIC = "c" * 64
REMAP = "archived-machine-input-map"
OWNER = {"script": "plots/图.py", "run_config": "rc_archived"}
RECEIPT = {
    "receipt_id": "sha256:" + "a" * 64,
    "receipt_identity": "sha256:" + "b" * 64,
    "generation": 7,
    "source_revision": "archived revision",
}


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _save(project, root, *, owner=OWNER, static=STATIC, remap=REMAP):
    artifact = figcapture.SourceArtifact(
        source_id=SOURCE,
        origin=figcapture.ORIGIN_EXECUTION,
        kind="pdf",
        bytes_sha256=hashlib.sha256(PDF).hexdigest(),
        size_bytes=len(PDF),
        receipt_id=RECEIPT["receipt_id"],
        receipt_identity=RECEIPT["receipt_identity"],
        generation=RECEIPT["generation"],
        patch_hash=PATCH,
    )
    return svgstore.save(
        project,
        SOURCE,
        PATCH,
        pdf=PDF,
        svg=SVG,
        artifact=artifact.to_payload(),
        receipt=RECEIPT,
        static_sha256=static,
        remap_fingerprint=remap,
        execution_owner=owner,
        storage_root=root,
    )


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "unused-app-data"))
    project = tmp_path / "原项目"
    project.mkdir()
    root = project / "快照暂存"
    saved = _save(project, root)
    return project, root, saved


def _metadata(root, reference):
    return root / "snapshots" / (reference["snapshotId"] + ".json")


def _rewrite(root, reference, mutate, *, resign=True):
    path = _metadata(root, reference)
    record = json.loads(path.read_bytes())
    mutate(record)
    if resign:
        record["sha256"] = hashlib.sha256(_json(record["payload"])).hexdigest()
    path.write_bytes(_json(record))
    return record["payload"]


def _reference(payload):
    # Independent canonical content-ID calculation for deliberately re-signed
    # test archives, not the production private helper under test.
    identity = {
        "identity": payload["identity"],
        "receipt": {key: payload["receipt"][key] for key in RECEIPT if key != "source_revision"},
        "pdf": payload["pdf_artifact"]["bytes_sha256"],
        "svg": payload["svg_artifact"]["bytes_sha256"],
    }
    result = {
        "version": 1,
        "sourceId": payload["identity"]["source_id"],
        "patchHash": payload["identity"]["patch_hash"],
        "receiptId": payload["receipt"]["receipt_id"],
    }
    if "execution_owner" in payload:
        identity["execution_owner"] = payload["execution_owner"]
        result["executionOwner"] = copy.deepcopy(payload["execution_owner"])
    return {**result, "snapshotId": hashlib.sha256(_json(identity)).hexdigest()}


def _tree(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("owner", [OWNER, None])
@pytest.mark.parametrize("static,remap", [(STATIC, REMAP), (None, "")])
def test_exact_archive_returns_detached_facts_without_current_authority(
    tmp_path, monkeypatch, owner, static, remap
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "unused"))
    root = project / "archive"
    saved = _save(project, root, owner=owner, static=static, remap=remap)
    reference = copy.deepcopy(saved["snapshot_ref"])
    before = _tree(root)
    result = svgstore.inspect_portable_snapshot(reference, storage_root=root)
    assert set(result) == {
        "reference",
        "identity",
        "receipt",
        "pdf_artifact",
        "svg_artifact",
        "pdf",
        "svg",
        "execution_owner",
    }
    assert result["reference"] == reference
    assert result["identity"] == {
        "project": ".",
        "source_id": SOURCE,
        "patch_hash": PATCH,
        "static_sha256": static,
        "remap_fingerprint": remap,
    }
    assert result["execution_owner"] == owner
    assert result["receipt"] == RECEIPT
    assert result["pdf"] == PDF and result["svg"] == SVG
    assert result["pdf_artifact"] == saved["pdf_artifact"]
    assert result["svg_artifact"] == saved["svg_artifact"]
    reference["sourceId"] = "changed input"
    assert result["reference"] == saved["snapshot_ref"]
    result["identity"]["project"] = "changed result"
    result["receipt"]["generation"] = -1
    result["pdf_artifact"]["size_bytes"] = 0
    if owner is not None:
        result["execution_owner"]["script"] = "changed.py"
        result["reference"]["executionOwner"]["run_config"] = None
    fresh = svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    assert fresh["identity"]["project"] == "."
    assert fresh["execution_owner"] == owner
    assert fresh["reference"] == saved["snapshot_ref"]
    assert fresh["receipt"] == RECEIPT and fresh["pdf_artifact"] == saved["pdf_artifact"]
    assert _tree(root) == before
    assert not (tmp_path / "unused").exists()


def test_full_reference_owner_is_required_without_changing_legacy_loader(archive):
    project, root, saved = archive
    reference = {
        key: value for key, value in saved["snapshot_ref"].items() if key != "executionOwner"
    }
    with pytest.raises(ValueError, match="reference mismatch"):
        svgstore.inspect_portable_snapshot(reference, storage_root=root)
    assert (
        svgstore.load_snapshot(
            project,
            reference,
            source_id=SOURCE,
            patch_hash=PATCH,
            static_sha256=STATIC,
            remap_fingerprint=REMAP,
            storage_root=root,
        )
        == saved
    )


@pytest.mark.parametrize(
    "change",
    [
        {"sourceId": "another source"},
        {"patchHash": "sha256:" + "d" * 64},
        {"receiptId": "sha256:" + "d" * 64},
        {"executionOwner": {"script": "other.py", "run_config": OWNER["run_config"]}},
        {"executionOwner": {"script": OWNER["script"], "run_config": None}},
    ],
)
def test_valid_but_mismatched_reference_is_rejected(archive, change):
    _, root, saved = archive
    with pytest.raises(ValueError, match="reference mismatch"):
        svgstore.inspect_portable_snapshot({**saved["snapshot_ref"], **change}, storage_root=root)


def test_ownerless_archive_cannot_gain_reference_owner(archive):
    project, root, _ = archive
    saved = _save(project, root, owner=None)
    with pytest.raises(ValueError, match="reference mismatch"):
        svgstore.inspect_portable_snapshot(
            {**saved["snapshot_ref"], "executionOwner": OWNER}, storage_root=root
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 999),
        ("sourceId", ""),
        ("sourceId", "bad\0source"),
        ("patchHash", "d" * 64),
        ("receiptId", "sha256:" + "D" * 64),
        ("snapshotId", "../outside"),
        ("snapshotId", "a" * 63),
        ("executionOwner", None),
        ("executionOwner", {"script": "../outside.py", "run_config": None}),
        ("executionOwner", {**OWNER, "extra": True}),
        ("extra", True),
    ],
)
def test_reference_schema_is_checked_before_any_archive_read(archive, monkeypatch, field, value):
    _, root, saved = archive

    def unexpected(*args, **kwargs):
        pytest.fail("invalid reference reached the file system")

    monkeypatch.setattr(svgstore, "_root", unexpected)
    with pytest.raises(ValueError):
        svgstore.inspect_portable_snapshot(
            {**saved["snapshot_ref"], field: value}, storage_root=root
        )


def test_explicit_storage_root_is_mandatory_and_missing_root_is_not_created(archive, tmp_path):
    _, _, saved = archive
    with pytest.raises(ValueError, match="storage root"):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=None)
    missing = tmp_path / "missing" / "archive"
    with pytest.raises(FileNotFoundError):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=missing)
    assert not missing.parent.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(extra=True),
        lambda r: r["payload"].update(version=999),
        lambda r: r["payload"].update(extra=True),
        lambda r: r["payload"]["identity"].update(static_sha256="not a hash"),
        lambda r: r["payload"]["receipt"].update(generation=8),
        lambda r: r["payload"]["receipt"].update(receipt_identity="sha256:" + "d" * 64),
        lambda r: r["payload"]["receipt"].update(public_identity="sha256:" + "d" * 64),
        lambda r: r["payload"]["receipt"].update(private_invalidation_key="must not leak"),
        lambda r: r["payload"]["receipt"].update(runtime={"executable": "private"}),
        lambda r: r["payload"]["pdf_artifact"].update(origin="declarative"),
        lambda r: r["payload"]["svg_artifact"].update(generation=8),
        lambda r: r["payload"]["pdf_artifact"].update(source_id="other"),
        lambda r: r["payload"]["pdf_artifact"].update(kind="svg"),
        lambda r: r["payload"]["svg_artifact"].update(bytes_sha256="not a hash"),
        lambda r: r["payload"].update(execution_owner={**OWNER, "script": "../outside.py"}),
    ],
)
def test_resigned_structurally_invalid_archive_is_rejected(archive, mutate):
    _, root, saved = archive
    _rewrite(root, saved["snapshot_ref"], mutate)
    before = _tree(root)
    with pytest.raises(ValueError):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    assert _tree(root) == before


@pytest.mark.parametrize(
    "field,value",
    [("static_sha256", "d" * 64), ("remap_fingerprint", "other"), ("source_id", "other")],
)
def test_valid_identity_changes_cannot_hide_under_old_content_id(archive, field, value):
    _, root, saved = archive
    _rewrite(root, saved["snapshot_ref"], lambda r: r["payload"]["identity"].update({field: value}))
    with pytest.raises(ValueError):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)


def test_consistently_resigned_nonportable_archive_is_still_rejected(archive):
    project, root, saved = archive
    old_reference = saved["snapshot_ref"]
    payload = _rewrite(
        root,
        old_reference,
        lambda r: r["payload"]["identity"].update(
            project=svgstore.config.normalize_path_identity(project)
        ),
    )
    reference = _reference(payload)
    _metadata(root, old_reference).rename(_metadata(root, reference))
    with pytest.raises(ValueError, match="not portable"):
        svgstore.inspect_portable_snapshot(reference, storage_root=root)


def test_archive_filename_must_be_its_actual_content_id(archive):
    _, root, saved = archive
    old = saved["snapshot_ref"]
    alias = {**old, "snapshotId": "d" * 64}
    _metadata(root, old).rename(_metadata(root, alias))
    with pytest.raises(ValueError, match="filename mismatch"):
        svgstore.inspect_portable_snapshot(alias, storage_root=root)


def test_metadata_checksum_is_independent_of_reference_content_id(archive):
    _, root, saved = archive
    # source_revision is public provenance, outside the reference's receipt key.
    _rewrite(
        root,
        saved["snapshot_ref"],
        lambda r: r["payload"]["receipt"].update(source_revision="changed"),
        resign=False,
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)


@pytest.mark.parametrize("data", [b"{", b"\xff", b'{"payload":{},"payload":{},"sha256":"x"}'])
def test_invalid_json_and_duplicate_fields_reuse_strict_parser(archive, data):
    _, root, saved = archive
    _metadata(root, saved["snapshot_ref"]).write_bytes(data)
    with pytest.raises(ValueError):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)


def test_deep_json_is_invalid_metadata_not_an_unbounded_parser_failure(archive):
    _, root, saved = archive
    _metadata(root, saved["snapshot_ref"]).write_bytes(b"[" * 2000 + b"0" + b"]" * 2000)
    with pytest.raises(ValueError, match="malformed"):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)


@pytest.mark.parametrize("kind", ["pdf", "svg"])
@pytest.mark.parametrize("fault", ["missing", "empty", "same-size", "truncated", "longer"])
def test_both_blob_bytes_and_sizes_are_checked(archive, kind, fault):
    _, root, saved = archive
    path = saved[kind + "_path"]
    old = path.read_bytes()
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(
            {
                "empty": b"",
                "same-size": b"X" * len(old),
                "truncated": old[:-1],
                "longer": old + b"X",
            }[fault]
        )
    with pytest.raises((ValueError, OSError)):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)


@pytest.mark.parametrize("kind", ["pdf", "svg"])
def test_resigned_artifact_size_cannot_replace_actual_blob_size_check(archive, kind):
    _, root, saved = archive
    _rewrite(
        root, saved["snapshot_ref"], lambda r: r["payload"][kind + "_artifact"].update(size_bytes=1)
    )
    with pytest.raises(ValueError, match="blob does not match"):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)


@pytest.mark.parametrize("constant", ["MAX_METADATA_BYTES", "MAX_PDF_BYTES", "MAX_SVG_BYTES"])
def test_same_existing_read_budgets_apply(archive, monkeypatch, constant):
    _, root, saved = archive
    monkeypatch.setattr(svgstore, constant, 1)
    with pytest.raises(ValueError):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)


@pytest.mark.parametrize("target", ["root", "snapshots", "blobs", "archive", "pdf", "svg"])
def test_symlinked_archive_components_are_not_followed(archive, tmp_path, target):
    _, root, saved = archive
    path = {
        "root": root,
        "snapshots": root / "snapshots",
        "blobs": root / "blobs",
        "archive": _metadata(root, saved["snapshot_ref"]),
        "pdf": saved["pdf_path"],
        "svg": saved["svg_path"],
    }[target]
    outside = tmp_path / "outside"
    path.rename(outside)
    try:
        path.symlink_to(outside, target_is_directory=outside.is_dir())
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    before = _tree(outside) if outside.is_dir() else outside.read_bytes()
    with pytest.raises((ValueError, OSError)):
        svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    assert (_tree(outside) if outside.is_dir() else outside.read_bytes()) == before


def test_current_remap_authorization_and_later_runs_remain_separate(archive):
    project, root, saved = archive
    reference = saved["snapshot_ref"]
    assert (
        svgstore.load_snapshot(
            project,
            reference,
            source_id=SOURCE,
            patch_hash=PATCH,
            static_sha256=STATIC,
            remap_fingerprint="this-machine-different-map",
            storage_root=root,
        )
        is None
    )
    next_receipt = {**RECEIPT, "generation": RECEIPT["generation"] + 1}
    svgstore.activate(project, SOURCE, next_receipt, remap_fingerprint=REMAP, storage_root=root)
    assert (
        svgstore.load(
            project, SOURCE, PATCH, static_sha256=STATIC, remap_fingerprint=REMAP, storage_root=root
        )
        is None
    )
    result = svgstore.inspect_portable_snapshot(reference, storage_root=root)
    assert result["identity"]["remap_fingerprint"] == REMAP
    assert result["receipt"] == RECEIPT and result["pdf"] == PDF


def test_readonly_moved_archive_ignores_markers_clock_and_app_data(archive, tmp_path, monkeypatch):
    project, root, saved = archive
    moved = tmp_path / "搬迁后"
    project.rename(moved)
    root = moved / root.name
    shutil.rmtree(root / "entries")
    shutil.rmtree(root / "sources")
    (root / ".lock").unlink()
    before = _tree(root)
    original_open = os.open

    def readonly(path, flags, *args, **kwargs):
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("portable inspection consulted mutable execution/cache state")

    monkeypatch.setattr(svgstore.os, "open", readonly)
    for name in ("_guard", "_read_current", "_current_path"):
        monkeypatch.setattr(svgstore, name, forbidden)
    monkeypatch.setattr(svgstore.config, "data_dir", forbidden)
    monkeypatch.setattr(svgstore.time, "time", forbidden)
    monkeypatch.setattr(svgstore.atomicio, "write_bytes", forbidden)
    result = svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    assert result["reference"] == saved["snapshot_ref"]
    assert result["pdf"] == PDF and result["svg"] == SVG
    assert _tree(root) == before
    assert not (root / ".lock").exists()


def test_cold_process_inspects_moved_package_with_file_writes_denied(archive, tmp_path):
    project, root, saved = archive
    moved = tmp_path / "冷进程包"
    project.rename(moved)
    root = moved / root.name
    shutil.rmtree(root / "entries")
    shutil.rmtree(root / "sources")
    (root / ".lock").unlink()
    before = _tree(root)
    code = r"""
import json, os, sys
from tavotto.engine import svgstore
def readonly(event, args):
    if event == 'open':
        mode, flags = args[1:3]
        assert not (isinstance(mode, str) and any(c in mode for c in 'wax+'))
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
    assert event not in {'os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.chmod',
                         'os.truncate', 'os.link', 'os.symlink', 'subprocess.Popen', 'os.system'}
sys.addaudithook(readonly)
result = svgstore.inspect_portable_snapshot(json.loads(sys.argv[2]), storage_root=sys.argv[1])
assert not any(name in sys.modules for name in ('matplotlib', 'numpy', 'tavotto.engine.pool'))
print(json.dumps({k: (v.hex() if isinstance(v, bytes) else v) for k, v in result.items()}))
"""
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            code,
            str(root),
            json.dumps(saved["snapshot_ref"], ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "TAVOTTO_DATA_DIR": str(tmp_path / "cold-unused-app-data"),
        },
    )
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result["reference"] == saved["snapshot_ref"] and result["execution_owner"] == OWNER
    assert result["identity"]["remap_fingerprint"] == REMAP
    assert bytes.fromhex(result["pdf"]) == PDF and bytes.fromhex(result["svg"]) == SVG
    assert _tree(root) == before and not (tmp_path / "cold-unused-app-data").exists()


def test_frozen_bytes_do_not_depend_on_store_paths_after_inspection(archive):
    _, root, saved = archive
    result = svgstore.inspect_portable_snapshot(saved["snapshot_ref"], storage_root=root)
    shutil.rmtree(root)
    assert result["pdf"] == PDF and result["svg"] == SVG
    assert result["reference"] == saved["snapshot_ref"]
    assert result["receipt"] == RECEIPT and result["execution_owner"] == OWNER
