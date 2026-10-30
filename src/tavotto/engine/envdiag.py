"""Bounded, project-scoped observations of managed environment jobs.

Only validated distribution names and versions, closed status values, times,
and opaque environment generations are stored. Paths and pip output have no
place in the persisted record. These observations never control a job.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import atomicio, config, diagnostictext, managedenv

_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?")
_VERSION = re.compile(r"[vV]?[0-9][A-Za-z0-9.+!_-]{0,79}")
_GENERATION = re.compile(r"[0-9a-f]{16}")
_SOURCES = {"pypi", "mirror", "custom", "unknown"}
_OUTCOMES = {"done", "failed", "cancelled", "unknown"}
_lock = threading.RLock()
_write_lock = threading.Lock()
_cache: OrderedDict[str, list[dict]] = OrderedDict()
_loading: dict[str, list[tuple[str, str, float]]] = {}
_MAX_JOBS = 16
_MAX_PACKAGES = 200
_MAX_PROJECTS = 64
_RETENTION_S = 7 * 24 * 3600
_MAX_FILE_BYTES = 2 * 1024 * 1024


def _name(value: object) -> str | None:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        return None
    # An API token is not a distribution name even when it matches the name grammar.
    if re.match(r"(?i)^(?:sk|token|password|secret)[-_]", value) or (
        diagnostictext._SECRET_VALUE.search(value)
    ):
        return None
    return value


def _packages(value: object, *, versions: bool) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value[:_MAX_PACKAGES]:
        if versions and isinstance(item, dict):
            name, version = _name(item.get("name")), item.get("version")
            if (
                name
                and isinstance(version, str)
                and _VERSION.fullmatch(version)
                and not diagnostictext._SECRET_VALUE.search(version)
            ):
                out.append({"name": name, "version": version})
        elif not versions:
            name = _name(item)
            if name:
                out.append(name)
    return out


def _number(value: object) -> float | None:
    if isinstance(value, (float, int)) and not isinstance(value, bool) and 0 < value < 1e12:
        return round(float(value), 3)
    return None


def _generation(value: object) -> str | None:
    return value if isinstance(value, str) and _GENERATION.fullmatch(value) else None


def _project(record: dict) -> dict:
    """Re-project even locally loaded records; never copy unrecognised fields."""
    source, outcome = record.get("source"), record.get("outcome")
    return {
        "requested": _packages(record.get("requested"), versions=False)
        if isinstance(record.get("requested"), list)
        else None,
        "installed": (
            _packages(record.get("installed"), versions=True)
            if isinstance(record.get("installed"), list)
            else None
        ),
        "failed": _packages(record.get("failed"), versions=False)
        if isinstance(record.get("failed"), list)
        else None,
        "outcome": outcome if isinstance(outcome, str) and outcome in _OUTCOMES else "unknown",
        "source": source if isinstance(source, str) and source in _SOURCES else "unknown",
        "started_at": _number(record.get("started_at")),
        "finished_at": _number(record.get("finished_at")),
        "activated_at": _number(record.get("activated_at")),
        "adopted_at": _number(record.get("adopted_at")),
        "committed": record.get("committed") is True,
        "generation": _generation(record.get("generation")),
        "error_code": record.get("error_code")
        if isinstance(record.get("error_code"), str) and record.get("error_code") in _ERROR_CODES
        else None,
        "pip_error_type": record.get("pip_error_type")
        if isinstance(record.get("pip_error_type"), str)
        and record.get("pip_error_type") in _PIP_ERRORS
        else None,
        "packages_truncated": record.get("packages_truncated") is True
        or any(
            isinstance(record.get(field), list) and len(record[field]) > _MAX_PACKAGES
            for field in ("requested", "installed", "failed")
        ),
        "render": _render(record.get("render")),
    }


_ERROR_CODES = {
    "dependency_network_unavailable",
    "dependency_not_found",
    "dependency_conflict",
    "dependency_install_failed",
    "dependency_install_cancelled",
    "dependency_install_timeout",
    "dependency_hash_mismatch",
    "dependency_consistency_failed",
    "managed_env_create_failed",
    "managed_env_unavailable",
    "managed_env_broken",
    "managed_env_write_failed",
    "dependency_requires_build",
    "dependency_import_still_failed",
    "dependency_worker_selftest_failed",
    "dependency_install_busy",
    "pip_unavailable",
}
_PIP_ERRORS = {
    "dependency_network_unavailable",
    "dependency_not_found",
    "dependency_conflict",
    "dependency_install_failed",
    "dependency_install_timeout",
    "dependency_hash_mismatch",
    "dependency_requires_build",
}


def _render(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    return {
        "at": _number(value.get("at")),
        "source": source
        if isinstance(source, str)
        and source
        in {
            "managed_project_env",
            "bundled",
            "project_venv",
            "configured",
            "system",
            "current_process",
            "managed_venv",
            "env_override",
        }
        else "unknown",
        "generation": _generation(value.get("generation")),
    }


def _key(project: str | Path) -> str:
    return managedenv.project_fingerprint(project)


def _path(key: str) -> Path:
    return config.data_path("cache", "environment-diagnostics", key + ".json")


def _load(key: str, path: Path | None = None) -> list[dict]:
    if key not in _cache:
        try:
            path = path or _path(key)
            stat = path.stat()
            if (
                path.is_symlink()
                or stat.st_size > _MAX_FILE_BYTES
                or time.time() - stat.st_mtime > _RETENTION_S
            ):
                raise ValueError("expired or invalid diagnostic cache")
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            rows = []
        _cache[key] = [_project(r) for r in rows[-_MAX_JOBS:] if isinstance(r, dict)]
        while len(_cache) > _MAX_PROJECTS:
            _cache.popitem(last=False)
    _cache.move_to_end(key)
    return _cache[key]


def _save(key: str, path: Path) -> None:
    try:
        # Serialise saves and snapshot only after acquiring the writer lock, so an
        # older asynchronous save can never overwrite a newer observation.
        with _write_lock:
            with _lock:
                if key not in _cache:
                    return
                data = json.dumps(_cache[key], ensure_ascii=True).encode()
            atomicio.write_bytes(path, data, mode=0o600)
            _prune(path.parent, keep=key)
    except Exception:  # noqa: BLE001 - observations must not change a job's outcome
        pass


def _prune(directory: Path, *, keep: str) -> None:
    entries = []
    for path in directory.glob("*.json"):
        if not _GENERATION.fullmatch(path.stem) or path.is_symlink():
            continue
        try:
            entries.append((path.stat().st_mtime, path))
        except OSError:
            continue
    entries.sort(key=lambda entry: entry[0], reverse=True)
    now = time.time()
    for index, (modified, path) in enumerate(entries):
        if path.stem != keep and (index >= _MAX_PROJECTS or now - modified > _RETENTION_S):
            try:
                path.unlink()
            except OSError:
                pass


def record_job(project: str | Path, record: dict) -> None:
    try:
        key = _key(project)
        path = _path(key)
        with _lock:
            rows = _load(key, path)
            rows.append(_project(record))
            del rows[:-_MAX_JOBS]
        threading.Thread(target=_save, args=(key, path), daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def record_render(project: str | Path, source: str, generation: str) -> None:
    """Attach the first subsequent actual worker observation to the latest job."""
    try:
        key = _key(project)
        path = _path(key)
        at = time.time()
        with _lock:
            if key in _loading:
                # Preserve ordering while a previous process's records load.
                # The first subsequent observation is the one that matters.
                if len(_loading[key]) < 16:
                    _loading[key].append((source, generation, at))
                return
            if key not in _cache:
                _loading[key] = [(source, generation, at)]
                threading.Thread(target=_record_cold_render, args=(key, path), daemon=True).start()
                return
            _observe_render(key, path, source, generation, at)
    except Exception:  # noqa: BLE001
        pass


def _observe_render(key: str, path: Path, source: str, generation: str, at: float) -> None:
    with _lock:
        rows = _load(key, path)
        pending = [
            row
            for row in rows
            if row.get("render") is None
            and row.get("finished_at") is not None
            and row["finished_at"] <= at
        ]
        if not pending:
            return
        observed = _render({"at": at, "source": source, "generation": generation})
        for row in pending:
            row["render"] = observed
    threading.Thread(target=_save, args=(key, path), daemon=True).start()


def _record_cold_render(key: str, path: Path) -> None:
    try:
        with _lock:
            _load(key, path)
            for source, generation, at in _loading.get(key, []):
                _observe_render(key, path, source, generation, at)
    finally:
        with _lock:
            _loading.pop(key, None)


def recent(project: str | Path) -> list[dict]:
    try:
        with _lock:
            return [_project(row) for row in reversed(_load(_key(project)))]
    except Exception:  # noqa: BLE001
        return []
