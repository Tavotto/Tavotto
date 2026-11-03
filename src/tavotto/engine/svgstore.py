"""Bounded export-grade Figure snapshots, separate from preview caches.

The caller validates the PDF/SVG pair before saving it. This store never runs a
script, guesses a neighboring file, or promotes a preview into an export. An
optional static hash records an independently validated binding to disk bytes.

All processes use one advisory lock for publication, reads and eviction. Blobs
are content addressed, and metadata is the final atomic commit. ``load`` freezes
both byte strings before releasing the lock: returned paths are conveniences,
not leases against later eviction. Only public execution receipts belong here.

The default store is a local, expiring cache. With an application-validated
``storage_root``, these are durable project assets: identities use project=".",
whole-project moves preserve lookups, and valid exports never expire or lose
their place to capacity eviction. Capacity refuses additions before mutation.
Durable saves also publish immutable, content-addressed versions for explicit
document references. Activation only changes current lookups, never these
versions. The record budget counts current and archived metadata separately
(normally at most 128 active variants); identical blobs share their byte cost.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import stat
import threading
import time
from pathlib import Path, PurePosixPath

from . import atomicio, config, figcapture

VERSION = 1
MAX_SVG_BYTES = 64 * 1024 * 1024
MAX_PDF_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_SNAPSHOTS = 256
MAX_SOURCES = 256
MAX_AGE_SECONDS = 30 * 24 * 60 * 60
_LOCK_TIMEOUT_SECONDS = 5.0
_MAX_DIRECTORY_ENTRIES = 4096
_LOCK = threading.Lock()
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_BLOB = re.compile(r"[0-9a-f]{64}\.(?:pdf|svg)\Z")
_METADATA = re.compile(r"[0-9a-f]{64}\.json\Z")
_TEMP = re.compile(r"[0-9a-f]{64}\.(?:pdf|svg|json)\.\d+\.\d+\.tmp\Z")
_PRIVATE_RECEIPT_FIELDS = frozenset(
    {"private_invalidation_key", "interpreter", "project_root", "launcher", "instance"}
)


def _json(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value, *, empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (empty or bool(value))
        and len(value) <= 16384
        and "\0" not in value
    )


def _hash(value, *, prefixed: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    if prefixed:
        if not value.startswith("sha256:"):
            return False
        value = value[7:]
    return _HEX.fullmatch(value) is not None


def _identity(
    project_root, source_id, patch_hash, static_sha256, remap_fingerprint, *, portable=False
) -> dict:
    if not _text(source_id) or not _hash(patch_hash, prefixed=True):
        raise ValueError("invalid Figure snapshot identity")
    if static_sha256 is not None and not _hash(static_sha256):
        raise ValueError("invalid static source hash")
    if not _text(remap_fingerprint, empty=True):
        raise ValueError("invalid input remap fingerprint")
    project = (
        "."
        if portable
        else config.normalize_path_identity(Path(project_root).expanduser().resolve())
    )
    return {
        "project": project,
        "source_id": source_id,
        "patch_hash": patch_hash,
        "static_sha256": static_sha256,
        "remap_fingerprint": remap_fingerprint,
    }


def _key(identity: dict) -> str:
    return _digest(_json(identity))


def _execution_owner(value) -> dict:
    """The caller supplies provenance; opaque source IDs never imply ownership."""
    if not isinstance(value, dict) or set(value) != {"script", "run_config"}:
        raise ValueError("invalid Figure execution owner")
    script = value["script"]
    run_config = value["run_config"]
    if not _text(script) or (run_config is not None and not _text(run_config)):
        raise ValueError("invalid Figure execution owner")
    normalized = figcapture.normalize_relative_script(script)
    path = PurePosixPath(normalized)
    if normalized != script or path.as_posix() != script or not path.parts or ".." in path.parts:
        raise ValueError("Figure execution owner script must be a canonical project path")
    return {"script": script, "run_config": run_config}


def _directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("unsafe Figure snapshot directory")


def _root(
    *,
    create: bool,
    storage_root: Path | None = None,
    archived: bool = False,
    metadata_only: bool = False,
) -> Path:
    # The configured data parent is trusted and may itself resolve through an OS
    # alias (e.g. /var on macOS). No symlinks inside our store are admitted.
    if storage_root is None:
        parent = config.data_dir().expanduser().resolve()
        root = parent / "figure_snapshots"
    else:
        requested = Path(storage_root).expanduser().absolute()
        if ".." in requested.parts:
            raise ValueError("unsafe Figure snapshot storage root")
        # The application supplies and validates the project-owned parent;
        # still refuse a symlink at the store itself and every owned subdirectory.
        parent = requested.parent.resolve()
        root = parent / requested.name
    if create:
        parent.mkdir(parents=True, exist_ok=True)
    _directory(root, create=create)
    if metadata_only:
        for name in ("entries", "snapshots", "sources"):
            directory = root / name
            if directory.exists() or directory.is_symlink():
                _directory(directory)
        return root
    for name in ("blobs", "snapshots") if archived else ("entries", "blobs", "sources"):
        _directory(root / name, create=create)
    if storage_root is not None and not archived:
        snapshots = root / "snapshots"
        if create or snapshots.exists() or snapshots.is_symlink():
            _directory(snapshots, create=create)
    return root


def _read_file(path: Path, limit: int) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError("unsafe or oversized Figure snapshot file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise ValueError("Figure snapshot file changed during open")
        data = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    if len(data) != before.st_size or len(data) > limit or after.st_size != before.st_size:
        raise ValueError("Figure snapshot file changed during read")
    if not os.path.samestat(before, path.lstat()):
        raise ValueError("Figure snapshot file replaced during read")
    return data


@contextlib.contextmanager
def _guard(root: Path):
    # flock is process-wide on some systems; the thread lock also serializes
    # handles within this process. Locks time out rather than blocking export.
    if not _LOCK.acquire(timeout=_LOCK_TIMEOUT_SECONDS):
        raise TimeoutError("Figure snapshot store is busy")
    fd = None
    locked = False
    try:
        for directory in (root, root / "entries", root / "blobs", root / "sources"):
            _directory(directory)
        path = root / ".lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        if not stat.S_ISREG(path.lstat().st_mode) or not os.path.samestat(
            path.lstat(), os.fstat(fd)
        ):
            raise ValueError("unsafe Figure snapshot lock")
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Figure snapshot store is busy") from None
                time.sleep(0.02)
        yield
    finally:
        if fd is not None:
            try:
                if locked:
                    if os.name == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        _LOCK.release()


def _artifact(payload, identity: dict, kind: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("missing Figure source artifact")
    raw = dict(payload)
    version = raw.pop("source_artifact_version", None)
    if type(version) is not int or version != figcapture.SOURCE_ARTIFACT_VERSION:
        raise ValueError("unknown Figure source artifact version")
    try:
        artifact = figcapture.SourceArtifact(**raw)
    except TypeError as exc:
        raise ValueError("malformed Figure source artifact") from exc
    if (
        artifact.origin != figcapture.ORIGIN_EXECUTION
        or artifact.source_id != identity["source_id"]
        or artifact.patch_hash != identity["patch_hash"]
        or artifact.kind != kind
        or not _hash(artifact.receipt_id, prefixed=True)
        or not _hash(artifact.receipt_identity, prefixed=True)
        or type(artifact.generation) is not int
        or artifact.generation < 1
        or artifact.size_bytes > (MAX_PDF_BYTES if kind == "pdf" else MAX_SVG_BYTES)
    ):
        raise ValueError("Figure source artifact identity mismatch")
    return artifact.to_payload()


def _validate_receipt(receipt, artifact: dict) -> None:
    if not isinstance(receipt, dict) or _PRIVATE_RECEIPT_FIELDS.intersection(receipt):
        raise ValueError("a public execution receipt is required")
    identities = [receipt[k] for k in ("receipt_identity", "public_identity") if k in receipt]
    if (
        not identities
        or any(value != artifact["receipt_identity"] for value in identities)
        or receipt.get("receipt_id") != artifact["receipt_id"]
        or type(receipt.get("generation")) is not int
        or receipt["generation"] != artifact["generation"]
    ):
        raise ValueError("Figure receipt identity mismatch")
    runtime = receipt.get("runtime")
    if isinstance(runtime, dict) and {
        "executable",
        "prefix",
        "base_prefix",
        "cwd",
        "argv0",
    }.intersection(runtime):
        raise ValueError("private execution runtime cannot enter the snapshot store")


def _validate_record(record, *, expected: dict | None = None) -> dict:
    if not isinstance(record, dict) or set(record) != {"payload", "sha256"}:
        raise ValueError("malformed Figure snapshot metadata")
    payload = record["payload"]
    if not isinstance(payload, dict) or record["sha256"] != _digest(_json(payload)):
        raise ValueError("Figure snapshot metadata checksum mismatch")
    fields = {"version", "identity", "created_at", "pdf_artifact", "svg_artifact", "receipt"}
    if (
        set(payload) not in (fields, fields | {"execution_owner"})
        or type(payload["version"]) is not int
        or payload["version"] != VERSION
    ):
        raise ValueError("unknown Figure snapshot metadata version")
    if "execution_owner" in payload:
        _execution_owner(payload["execution_owner"])
    identity = payload["identity"]
    if not isinstance(identity, dict) or set(identity) != {
        "project",
        "source_id",
        "patch_hash",
        "static_sha256",
        "remap_fingerprint",
    }:
        raise ValueError("malformed Figure snapshot identity")
    if (
        not _text(identity["project"])
        or identity
        != _identity(
            identity["project"],
            identity["source_id"],
            identity["patch_hash"],
            identity["static_sha256"],
            identity["remap_fingerprint"],
            portable=identity["project"] == ".",
        )
        or (expected is not None and identity != expected)
    ):
        raise ValueError("Figure snapshot identity mismatch")
    timestamp = payload["created_at"]
    if (
        type(timestamp) not in (float, int)
        or not 0 <= timestamp <= 1e12
        or not math.isfinite(timestamp)
    ):
        raise ValueError("invalid Figure snapshot timestamp")
    pdf = _artifact(payload["pdf_artifact"], identity, "pdf")
    svg = _artifact(payload["svg_artifact"], identity, "svg")
    for field in ("receipt_id", "receipt_identity", "generation"):
        if pdf[field] != svg[field]:
            raise ValueError("Figure snapshot pair identity mismatch")
    _validate_receipt(payload["receipt"], pdf)
    return payload


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate Figure snapshot metadata field")
        result[name] = value
    return result


def _read_record(path: Path, *, expected: dict | None = None) -> dict:
    record = json.loads(_read_file(path, MAX_METADATA_BYTES), object_pairs_hook=_unique_object)
    payload = _validate_record(record, expected=expected)
    if path.name != _key(payload["identity"]) + ".json":
        raise ValueError("Figure snapshot metadata filename mismatch")
    return payload


def _snapshot_id(payload: dict) -> str:
    identity = {
        "identity": payload["identity"],
        "receipt": _receipt_key(payload["receipt"]),
        "pdf": payload["pdf_artifact"]["bytes_sha256"],
        "svg": payload["svg_artifact"]["bytes_sha256"],
    }
    # Omission preserves every pre-owner immutable reference verbatim. A new
    # owner is bound to this exact receipt and pair, never attached retroactively.
    if "execution_owner" in payload:
        identity["execution_owner"] = _execution_owner(payload["execution_owner"])
    return _digest(_json(identity))


def _snapshot_reference(payload: dict) -> dict:
    reference = {
        "version": VERSION,
        "sourceId": payload["identity"]["source_id"],
        "patchHash": payload["identity"]["patch_hash"],
        "receiptId": payload["receipt"]["receipt_id"],
        "snapshotId": _snapshot_id(payload),
    }
    if "execution_owner" in payload:
        reference["executionOwner"] = _execution_owner(payload["execution_owner"])
    return reference


def validate_reference(reference) -> dict:
    """Validate the closed document/wire schema and detach it from its caller."""
    fields = {"version", "sourceId", "patchHash", "receiptId", "snapshotId"}
    if (
        not isinstance(reference, dict)
        or set(reference) not in (fields, fields | {"executionOwner"})
        or type(reference["version"]) is not int
        or reference["version"] != VERSION
        or not _text(reference["sourceId"])
        or not _hash(reference["patchHash"], prefixed=True)
        or not _hash(reference["receiptId"], prefixed=True)
        or not _hash(reference["snapshotId"])
    ):
        raise ValueError("invalid Figure snapshot reference")
    detached = dict(reference)
    if "executionOwner" in reference:
        detached["executionOwner"] = _execution_owner(reference["executionOwner"])
    return detached


def _read_snapshot(path: Path, *, expected: dict | None = None) -> dict:
    record = json.loads(_read_file(path, MAX_METADATA_BYTES), object_pairs_hook=_unique_object)
    payload = _validate_record(record, expected=expected)
    if path.name != _snapshot_id(payload) + ".json":
        raise ValueError("immutable Figure snapshot filename mismatch")
    return payload


def _existing_reference(root: Path, payload: dict) -> dict | None:
    # Legacy current entries do not magically acquire document references.
    try:
        archived = _read_snapshot(
            root / "snapshots" / (_snapshot_id(payload) + ".json"), expected=payload["identity"]
        )
        if any(archived[name] != payload[name] for name in ("pdf_artifact", "svg_artifact")):
            raise ValueError("immutable Figure snapshot artifact mismatch")
        return _snapshot_reference(archived)
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def _blob_path(root: Path, artifact: dict) -> Path:
    return root / "blobs" / (artifact["bytes_sha256"] + "." + artifact["kind"])


def _result(
    root: Path, payload: dict, pdf: bytes, svg: bytes, *, reference: dict | None = None
) -> dict:
    return {
        "pdf_path": _blob_path(root, payload["pdf_artifact"]),
        "svg_path": _blob_path(root, payload["svg_artifact"]),
        "pdf_artifact": payload["pdf_artifact"],
        "svg_artifact": payload["svg_artifact"],
        "receipt": payload["receipt"],
        "execution_owner": payload.get("execution_owner"),
        "pdf": pdf,
        "svg": svg,
        "snapshot_ref": reference,
    }


def _entries(directory: Path) -> list[Path]:
    entries = []
    with os.scandir(directory) as iterator:
        for item in iterator:
            if len(entries) >= _MAX_DIRECTORY_ENTRIES:
                raise ValueError("Figure snapshot directory is oversized")
            entries.append(Path(item.path))
    return entries


def _source_identity(identity: dict) -> dict:
    return {key: identity[key] for key in ("project", "source_id", "remap_fingerprint")}


def _receipt_key(receipt: dict) -> dict:
    if not isinstance(receipt, dict):
        raise ValueError("a public execution receipt is required")
    result = {
        "receipt_id": receipt.get("receipt_id"),
        "receipt_identity": receipt.get("receipt_identity", receipt.get("public_identity")),
        "generation": receipt.get("generation"),
    }
    if (
        not _hash(result["receipt_id"], prefixed=True)
        or not _hash(result["receipt_identity"], prefixed=True)
        or type(result["generation"]) is not int
        or result["generation"] < 1
    ):
        raise ValueError("invalid Figure receipt identity")
    _validate_receipt(receipt, result)
    return result


def _current_path(root: Path, identity: dict) -> Path:
    return root / "sources" / (_key(_source_identity(identity)) + ".json")


def _read_current(path: Path) -> dict:
    record = json.loads(_read_file(path, MAX_METADATA_BYTES), object_pairs_hook=_unique_object)
    if not isinstance(record, dict) or set(record) != {"payload", "sha256"}:
        raise ValueError("malformed Figure source marker")
    payload = record["payload"]
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "identity", "receipt", "updated_at"}
        or type(payload["version"]) is not int
        or payload["version"] != VERSION
        or record["sha256"] != _digest(_json(payload))
    ):
        raise ValueError("invalid Figure source marker")
    identity = payload["identity"]
    if (
        not isinstance(identity, dict)
        or set(identity) != {"project", "source_id", "remap_fingerprint"}
        or not _text(identity["project"])
        or not _text(identity["source_id"])
        or not _text(identity["remap_fingerprint"], empty=True)
        or path.name != _key(identity) + ".json"
    ):
        raise ValueError("invalid Figure source marker identity")
    _receipt_key(payload["receipt"])
    timestamp = payload["updated_at"]
    if (
        type(timestamp) not in (int, float)
        or not 0 <= timestamp <= 1e12
        or not math.isfinite(timestamp)
    ):
        raise ValueError("invalid Figure source marker timestamp")
    return payload


def _set_current(
    root: Path, identity: dict, receipt: dict, *, advance: bool, encoded: bytes | None = None
) -> None:
    path = _current_path(root, identity)
    if path.is_symlink():
        raise ValueError("unsafe Figure source marker")
    wanted = _receipt_key(receipt)
    if not advance and path.exists():
        current = _read_current(path)
        if current["identity"] != _source_identity(identity) or current["receipt"] != wanted:
            raise ValueError("Figure snapshot belongs to an inactive execution")
    payload = {
        "version": VERSION,
        "identity": _source_identity(identity),
        "receipt": wanted,
        "updated_at": time.time(),
    }
    atomicio.write_bytes(
        path, encoded or _json({"payload": payload, "sha256": _digest(_json(payload))}), mode=0o600
    )


def _durable_state(root: Path) -> tuple[dict, dict, dict]:
    """Read established records without turning a read failure into deletion."""
    markers = {}
    records = {}
    snapshots = {}
    for path in _entries(root / "sources"):
        if _METADATA.fullmatch(path.name):
            markers[path.stem] = (path, _read_current(path))
    for path in _entries(root / "entries"):
        if _METADATA.fullmatch(path.name):
            records[path.stem] = (path, _read_record(path))
    for path in _entries(root / "snapshots"):
        if _METADATA.fullmatch(path.name):
            snapshots[path.stem] = (path, _read_snapshot(path))
    return markers, records, snapshots


def _durable_admission(
    root: Path,
    identity: dict,
    receipt: dict,
    *,
    advance: bool,
    payload: dict | None = None,
    encoded: bytes | None = None,
) -> bytes:
    """Preflight the final state; capacity refusal changes no established bytes.

    Valid records are never sacrificed for space. Only obsolete generations
    and blobs no longer referenced by the final state may be reclaimed.
    """
    markers, records, snapshots = _durable_state(root)
    group = _key(_source_identity(identity))
    wanted = _receipt_key(receipt)
    previous = markers.get(group)
    if previous is not None and not advance and previous[1]["receipt"] != wanted:
        raise ValueError("Figure snapshot belongs to an inactive execution")
    marker = {
        "version": VERSION,
        "identity": _source_identity(identity),
        "receipt": wanted,
        "updated_at": time.time(),
    }
    marker_bytes = _json({"payload": marker, "sha256": _digest(_json(marker))})
    markers[group] = (_current_path(root, identity), marker)
    kept = {}
    for key, (path, record) in records.items():
        owner = markers.get(_key(_source_identity(record["identity"])))
        if owner is None:
            raise ValueError("durable Figure snapshot has no source marker")
        if owner[1]["receipt"] == _receipt_key(record["receipt"]):
            kept[key] = (path, record)
    if payload is not None:
        kept[_key(identity)] = (root / "entries" / (_key(identity) + ".json"), payload)
        snapshot = _snapshot_id(payload)
        snapshots.setdefault(snapshot, (root / "snapshots" / (snapshot + ".json"), payload))
    if len(markers) > MAX_SOURCES or len(kept) + len(snapshots) > MAX_SNAPSHOTS:
        raise ValueError("durable Figure snapshot budget exceeded")
    total = sum(
        len(marker_bytes) if key == group else path.lstat().st_size
        for key, (path, _) in markers.items()
    )
    blobs = {}
    for path, record in (*kept.values(), *snapshots.values()):
        total += len(encoded) if record is payload else path.lstat().st_size
        for name in ("pdf_artifact", "svg_artifact"):
            artifact = record[name]
            blob = _blob_path(root, artifact)
            if blob.name in blobs and blobs[blob.name] != artifact["size_bytes"]:
                raise ValueError("inconsistent durable Figure blob size")
            blobs[blob.name] = artifact["size_bytes"]
    incoming = (
        {_blob_path(root, payload[name]).name for name in ("pdf_artifact", "svg_artifact")}
        if payload is not None
        else set()
    )
    for name, size in blobs.items():
        try:
            info = (root / "blobs" / name).lstat()
        except FileNotFoundError:
            if name in incoming:
                continue
            raise ValueError("missing durable Figure snapshot blob") from None
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError("unsafe or changed durable Figure snapshot blob")
    total += sum(blobs.values())
    if total > MAX_TOTAL_BYTES:
        raise ValueError("durable Figure snapshot budget exceeded")
    return marker_bytes


def _durable_cleanup(root: Path) -> None:
    markers, records, snapshots = _durable_state(root)
    referenced = {
        _blob_path(root, record[name]).name
        for _, record in snapshots.values()
        for name in ("pdf_artifact", "svg_artifact")
    }
    for path, record in records.values():
        owner = markers.get(_key(_source_identity(record["identity"])))
        if owner is None:
            # An unreadable or absent authority is not permission to erase an
            # established portable export. Fail closed and retain its bytes.
            raise ValueError("durable Figure snapshot has no source marker")
        if owner[1]["receipt"] != _receipt_key(record["receipt"]):
            path.unlink()
        else:
            referenced.update(
                _blob_path(root, record[name]).name for name in ("pdf_artifact", "svg_artifact")
            )
    for path in _entries(root / "blobs"):
        if _BLOB.fullmatch(path.name) and path.name not in referenced:
            path.unlink(missing_ok=True)
    for directory in (root / "entries", root / "blobs", root / "sources", root / "snapshots"):
        for path in _entries(directory):
            if _TEMP.fullmatch(path.name):
                path.unlink(missing_ok=True)


def _prune(
    root: Path, *, protected: str = "", protected_source: str = "", reserve: int = 0
) -> None:
    now = time.time()
    # Holding the store lock proves no writer owns these interrupted writes.
    for directory in (root / "entries", root / "blobs", root / "sources"):
        for path in _entries(directory):
            if _TEMP.fullmatch(path.name):
                path.unlink(missing_ok=True)
    current = {}
    for path in _entries(root / "sources"):
        if not _METADATA.fullmatch(path.name):
            continue
        try:
            marker = _read_current(path)
            if not -60 <= now - marker["updated_at"] <= MAX_AGE_SECONDS:
                raise ValueError("expired Figure source marker")
        except (OSError, ValueError, TypeError, RecursionError):
            path.unlink(missing_ok=True)
            continue
        current[path.stem] = (path, marker)
    records = []
    for path in _entries(root / "entries"):
        if not _METADATA.fullmatch(path.name):
            continue
        try:
            payload = _read_record(path)
            group = _key(_source_identity(payload["identity"]))
            if (
                not -60 <= now - payload["created_at"] <= MAX_AGE_SECONDS
                or group not in current
                or current[group][1]["receipt"] != _receipt_key(payload["receipt"])
            ):
                raise ValueError("inactive or expired Figure snapshot")
        except (OSError, ValueError, TypeError, RecursionError):
            path.unlink(missing_ok=True)
            continue
        records.append((path, payload))
    records.sort(key=lambda entry: entry[1]["created_at"])

    def collect() -> int:
        referenced = {
            _blob_path(root, payload[name]).name
            for _, payload in records
            for name in ("pdf_artifact", "svg_artifact")
        }
        total = sum(path.lstat().st_size for path, _ in records)
        total += sum(path.lstat().st_size for path, _ in current.values())
        for path in _entries(root / "blobs"):
            if not _BLOB.fullmatch(path.name):
                continue
            info = path.lstat()
            if path.name not in referenced or not stat.S_ISREG(info.st_mode):
                path.unlink(missing_ok=True)
            else:
                total += info.st_size
        return total

    total = collect()
    new_entry = bool(reserve) and not any(path.stem == protected for path, _ in records)
    while (
        total + reserve > MAX_TOTAL_BYTES
        or len(records) + new_entry > MAX_SNAPSHOTS
        or len(current) > MAX_SOURCES
    ):
        candidate = next((i for i, (path, _) in enumerate(records) if path.stem != protected), None)
        if candidate is not None and len(current) <= MAX_SOURCES:
            path, _ = records.pop(candidate)
            path.unlink()
        else:
            groups = sorted(current, key=lambda key: current[key][1]["updated_at"])
            group = next((key for key in groups if key != protected_source), None)
            if group is None:
                raise ValueError("Figure snapshot cache budget exceeded")
            current.pop(group)[0].unlink()
            kept = []
            for path, payload in records:
                if _key(_source_identity(payload["identity"])) == group:
                    path.unlink()
                else:
                    kept.append((path, payload))
            records[:] = kept
        total = collect()


def activate(
    project_root,
    source_id: str,
    receipt: dict,
    *,
    remap_fingerprint: str = "",
    storage_root: Path | None = None,
) -> None:
    """Admit an explicit new run before attempting its snapshot capture.

    This revokes all older current patch/static lookups, including when new
    capture fails. Explicit immutable document references remain readable.
    Ordinary late exports cannot reactivate a superseded generation.
    Generations are scoped to a worker, so comparison uses the whole receipt
    identity and never assumes generation numbers are globally monotonic.
    """
    identity = _identity(
        project_root,
        source_id,
        "sha256:" + "0" * 64,
        None,
        remap_fingerprint,
        portable=storage_root is not None,
    )
    _receipt_key(receipt)
    root = _root(create=True, storage_root=storage_root)
    with _guard(root):
        marker = (
            _durable_admission(root, identity, receipt, advance=True)
            if storage_root is not None
            else None
        )
        _set_current(root, identity, receipt, advance=True, encoded=marker)
        if storage_root is not None:
            _durable_cleanup(root)
        else:
            _prune(root, protected_source=_key(_source_identity(identity)))


def save(
    project_root,
    source_id: str,
    patch_hash: str,
    *,
    pdf: bytes,
    svg: bytes,
    artifact: dict,
    receipt: dict,
    execution_owner: dict | None = None,
    static_sha256: str | None = None,
    remap_fingerprint: str = "",
    activate: bool = False,
    storage_root: Path | None = None,
) -> dict:
    """Persist an already validated same-Figure pair; invalid input raises ValueError.

    ``static_sha256`` is an explicit, caller-validated binding, never inferred
    from a matching filename. The execution artifact still keeps the exact
    lookup source ID, patch, generation and receipt identity. ``storage_root``
    is a project-contained directory validated by the application, never an
    arbitrary request path. Durable additions fail before changing established
    records when their final state would exceed the storage budget.
    ``execution_owner`` is optional, explicit script/run-configuration provenance
    bound to this receipt; it cannot include a launch spec or raw arguments.
    """
    identity = _identity(
        project_root,
        source_id,
        patch_hash,
        static_sha256,
        remap_fingerprint,
        portable=storage_root is not None,
    )
    for data, limit in ((pdf, MAX_PDF_BYTES), (svg, MAX_SVG_BYTES)):
        if not isinstance(data, bytes) or not 0 < len(data) <= limit:
            raise ValueError("empty or oversized Figure snapshot bytes")
    pdf_artifact = _artifact(artifact, identity, "pdf")
    if pdf_artifact["bytes_sha256"] != _digest(pdf) or pdf_artifact["size_bytes"] != len(pdf):
        raise ValueError("Figure PDF bytes do not match its source artifact")
    svg_artifact = {
        **pdf_artifact,
        "kind": "svg",
        "bytes_sha256": _digest(svg),
        "size_bytes": len(svg),
    }
    payload = {
        "version": VERSION,
        "identity": identity,
        "created_at": time.time(),
        "pdf_artifact": pdf_artifact,
        "svg_artifact": svg_artifact,
        "receipt": receipt,
    }
    if execution_owner is not None:
        payload["execution_owner"] = _execution_owner(execution_owner)
    encoded = _json({"payload": payload, "sha256": _digest(_json(payload))})
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError("oversized Figure snapshot metadata")
    # Detach the caller's mutable receipt and validate exactly what will persist.
    payload = _validate_record(json.loads(encoded), expected=identity)
    root = _root(create=True, storage_root=storage_root)
    key = _key(identity)
    with _guard(root):
        target = root / "entries" / (key + ".json")
        if any(
            path.is_symlink()
            for path in (target, _blob_path(root, pdf_artifact), _blob_path(root, svg_artifact))
        ):
            raise ValueError("unsafe Figure snapshot file")
        snapshot_path = None
        if storage_root is not None:
            snapshot_path = root / "snapshots" / (_snapshot_id(payload) + ".json")
            if snapshot_path.exists() or snapshot_path.is_symlink():
                # Existing immutable bytes win, including their original
                # timestamp/provenance. Never rewrite a previously issued ref.
                payload = _read_snapshot(snapshot_path, expected=identity)
                encoded = _json({"payload": payload, "sha256": _digest(_json(payload))})
        marker = (
            _durable_admission(
                root,
                identity,
                payload["receipt"],
                advance=activate,
                payload=payload,
                encoded=encoded,
            )
            if storage_root is not None
            else None
        )
        _set_current(root, identity, payload["receipt"], advance=activate, encoded=marker)
        group = _key(_source_identity(identity))
        if storage_root is None:
            _prune(
                root,
                protected=key,
                protected_source=group,
                reserve=len(pdf) + len(svg) + len(encoded),
            )
        for name, data in (("pdf_artifact", pdf), ("svg_artifact", svg)):
            path = _blob_path(root, payload[name])
            if path.exists() or path.is_symlink():
                if _read_file(path, len(data)) != data:
                    raise ValueError("corrupt immutable Figure snapshot blob")
            else:
                atomicio.write_bytes(path, data, mode=0o600)
        if snapshot_path is not None and not snapshot_path.exists():
            # The archive is independently committed only after both immutable
            # blobs. A later current-entry failure cannot destroy this version.
            atomicio.write_bytes(snapshot_path, encoded, mode=0o600)
        target = root / "entries" / (key + ".json")
        if target.is_symlink():
            raise ValueError("unsafe Figure snapshot metadata")
        atomicio.write_bytes(target, encoded, mode=0o600)
        if storage_root is not None:
            _durable_cleanup(root)
        else:
            _prune(root, protected=key, protected_source=group)
        return _result(
            root,
            payload,
            pdf,
            svg,
            reference=_snapshot_reference(payload) if storage_root is not None else None,
        )


def load(
    project_root,
    source_id: str,
    patch_hash: str,
    *,
    static_sha256: str | None = None,
    remap_fingerprint: str = "",
    storage_root: Path | None = None,
) -> dict | None:
    """Read only the exact admitted snapshot, freezing bytes before eviction can run.

    Missing, malformed, oversized, changed or unsafe records are a miss.
    Expiry applies only to the default local cache, never project-owned assets. No fallback search, source execution or source-file read occurs.
    """
    try:
        identity = _identity(
            project_root,
            source_id,
            patch_hash,
            static_sha256,
            remap_fingerprint,
            portable=storage_root is not None,
        )
        root = _root(create=False, storage_root=storage_root)
        with _guard(root):
            payload = _read_record(root / "entries" / (_key(identity) + ".json"), expected=identity)
            marker = _read_current(_current_path(root, identity))
            if (
                marker["identity"] != _source_identity(identity)
                or marker["receipt"] != _receipt_key(payload["receipt"])
                or (
                    storage_root is None
                    and not -60 <= time.time() - marker["updated_at"] <= MAX_AGE_SECONDS
                )
            ):
                return None
            age = time.time() - payload["created_at"]
            if storage_root is None and not -60 <= age <= MAX_AGE_SECONDS:
                return None
            data = []
            for name, limit in (("pdf_artifact", MAX_PDF_BYTES), ("svg_artifact", MAX_SVG_BYTES)):
                artifact = payload[name]
                blob = _read_file(_blob_path(root, artifact), limit)
                if len(blob) != artifact["size_bytes"] or _digest(blob) != artifact["bytes_sha256"]:
                    return None
                data.append(blob)
            return _result(
                root,
                payload,
                *data,
                reference=_existing_reference(root, payload) if storage_root is not None else None,
            )
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def load_snapshot(
    project_root,
    reference,
    *,
    source_id: str,
    patch_hash: str,
    static_sha256: str | None = None,
    remap_fingerprint: str = "",
    storage_root: Path | None = None,
) -> dict | None:
    """Read an explicit immutable project version, independent of current runs.

    Only durable project stores issue references. No mutable current marker,
    cache TTL, file discovery or execution participates. Reads take no write
    lock: archived metadata and its blobs are immutable and never evicted, so
    bounded checked reads also work on an entirely read-only project copy.
    """
    if storage_root is None:
        return None
    try:
        reference = validate_reference(reference)
        if reference["sourceId"] != source_id or reference["patchHash"] != patch_hash:
            return None
        identity = _identity(
            project_root, source_id, patch_hash, static_sha256, remap_fingerprint, portable=True
        )
        root = _root(create=False, storage_root=storage_root, archived=True)
        payload = _read_snapshot(
            root / "snapshots" / (reference["snapshotId"] + ".json"), expected=identity
        )
        expected_reference = _snapshot_reference(payload)
        if any(value != expected_reference.get(key) for key, value in reference.items()):
            return None
        data = []
        for name, limit in (("pdf_artifact", MAX_PDF_BYTES), ("svg_artifact", MAX_SVG_BYTES)):
            artifact = payload[name]
            blob = _read_file(_blob_path(root, artifact), limit)
            if len(blob) != artifact["size_bytes"] or _digest(blob) != artifact["bytes_sha256"]:
                return None
            data.append(blob)
        return _result(root, payload, *data, reference=expected_reference)
    except (OSError, ValueError, TypeError, RecursionError):
        return None


def source_records_for_owner(
    project_root,
    script: str,
    run_config: str | None,
    *,
    storage_root: Path | None = None,
) -> list[dict]:
    """List explicit historical ownership evidence, without loading any blobs.

    This is not current execution authority. The caller observes a real new run
    and decides which older receipts to revoke. Legacy ownerless records remain
    readable by explicit reference, but are never attributed by parsing source
    IDs or by guessing static filenames. Different run configurations are exact
    separate owners, including when they once wrote the same static source ID.

    ``current`` requires a matching owner in a current entry and its exact
    source marker's full receipt key. Historical same-name files owned by
    another execution are not promoted to current ownership.

    Reads require no writable lock or source-marker/blob directory. A missing
    store is empty; malformed, changed, unsafe or oversized metadata raises, so
    callers cannot mistake an incomplete scan for proof of no previous outputs.
    """
    owner = _execution_owner({"script": script, "run_config": run_config})
    project = _identity(
        project_root,
        "owner-scan",
        "sha256:" + "0" * 64,
        None,
        "",
        portable=storage_root is not None,
    )["project"]
    try:
        root = _root(create=False, storage_root=storage_root, metadata_only=True)
    except FileNotFoundError:
        return []
    paths = []
    for directory, reader in (("entries", _read_record), ("snapshots", _read_snapshot)):
        path = root / directory
        try:
            _directory(path)
        except FileNotFoundError:
            continue
        paths.extend((item, reader) for item in _entries(path) if _METADATA.fullmatch(item.name))
    if len(paths) > MAX_SNAPSHOTS:
        raise ValueError("Figure ownership metadata budget exceeded")
    total = 0
    records = []
    archives = set()
    current_records = set()
    for path, reader in paths:
        record = reader(path)
        records.append((path, record))
        total += path.lstat().st_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Figure ownership metadata budget exceeded")
        if reader is _read_snapshot:
            archives.add(_snapshot_id(record))
        elif record.get("execution_owner") is not None:
            current_records.add(_snapshot_id(record))
    if storage_root is not None and not current_records.issubset(archives):
        raise ValueError("Figure execution ownership archive is missing")
    markers = {}
    found = {}
    now = time.time()
    for path, record in records:
        if record["identity"]["project"] != project or record.get("execution_owner") != owner:
            continue
        result = {"source_id": record["identity"]["source_id"], **_receipt_key(record["receipt"])}
        key = _key(result)
        result["current"] = False
        if _snapshot_id(record) in current_records:
            marker_path = _current_path(root, record["identity"])
            if marker_path not in markers:
                if len(markers) >= MAX_SOURCES:
                    raise ValueError("Figure ownership source budget exceeded")
                try:
                    marker = _read_current(marker_path)
                except FileNotFoundError:
                    marker = None
                if marker is not None:
                    total += marker_path.lstat().st_size
                    if total > MAX_TOTAL_BYTES:
                        raise ValueError("Figure ownership metadata budget exceeded")
                markers[marker_path] = marker
            marker = markers[marker_path]
            result["current"] = bool(
                marker is not None
                and marker["identity"] == _source_identity(record["identity"])
                and marker["receipt"] == _receipt_key(record["receipt"])
                and (
                    storage_root is not None
                    or (
                        -60 <= now - record["created_at"] <= MAX_AGE_SECONDS
                        and -60 <= now - marker["updated_at"] <= MAX_AGE_SECONDS
                    )
                )
            )
        if key not in found or result["current"]:
            found[key] = result
    return sorted(
        found.values(),
        key=lambda item: (
            item["source_id"],
            item["receipt_id"],
            item["receipt_identity"],
            item["generation"],
        ),
    )
