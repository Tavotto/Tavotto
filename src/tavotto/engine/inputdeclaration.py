"""Freeze explicitly selected file evidence around one actual execution.

The parent owns path approval and portable names. This standard-library leaf
only reads approved paths; it never selects an environment, writes resources,
or claims that Python's unobserved input channels were covered.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path

try:
    from . import executionfact
except ImportError:  # worker bootstrap first loads the shared leaf as a flat module
    import executionfact

VERSION = 1
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FILE_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_OPEN = os.open
_READ = os.read
_CLOSE = os.close
_FSTAT = os.fstat
_CHUNK_BYTES = 64 * 1024


def _fields(raw, keys):
    if not isinstance(raw, dict) or set(raw) != set(keys):
        raise ValueError("Invalid declared input fields")
    return raw


def _text(raw):
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise ValueError("Invalid declared input identity")
    return raw


def _scope(raw):
    _fields(raw, ("script", "cwd_mode", "run_config"))
    _text(raw["script"])
    if raw["cwd_mode"] not in ("sandbox", "project", "project_root"):
        raise ValueError("Unsupported declared input working directory")
    if raw["run_config"] is not None:
        _text(raw["run_config"])
    return raw


def _rows(raw):
    if not isinstance(raw, list):
        raise ValueError("Declared input rows must be arrays")
    return raw


def _rule(raw):
    _fields(raw, ("kind", "from", "to"))
    if raw["kind"] not in ("file", "prefix"):
        raise ValueError("Invalid declared input mapping kind")
    _requested(raw["from"], raw["kind"])
    if not os.path.isabs(_text(raw["to"])):
        raise ValueError("Declared input targets must be absolute")
    return raw


def _requested(value, kind):
    # The existing remapper permits an empty prefix for all relative requests.
    if not isinstance(value, str) or "\0" in value or (not value and kind != "prefix"):
        raise ValueError("Invalid declared input request")
    return value


def validate_candidate(raw) -> dict:
    """Check the private IPC shape without reading any selected resource.

    IDs are opaque here. The parent uses the original portable member validator
    before producing them; this module never joins an ID to a filesystem path.
    """
    _fields(
        raw, ("version", "scope", "script_path", "files", "prefixes", "mappings", "rules", "limits")
    )
    if type(raw["version"]) is not int or raw["version"] != VERSION:
        raise ValueError("Unsupported declared input version")
    _scope(raw["scope"])
    if not os.path.isabs(_text(raw["script_path"])):
        raise ValueError("Declared script path must be absolute")
    limits = _fields(raw["limits"], ("max_files", "max_bytes"))
    if any(type(n) is not int or n < 1 for n in limits.values()):
        raise ValueError("Invalid declared input budget")
    identifiers = {}
    for kind, field in (("file", "files"), ("prefix", "prefixes")):
        rows = _rows(raw[field])
        if len(rows) > limits["max_files"]:
            raise ValueError("Declared input member budget exceeded")
        for row in rows:
            _fields(row, ("id", "path"))
            identifier = _text(row["id"])
            if identifier in identifiers or not os.path.isabs(_text(row["path"])):
                raise ValueError("Ambiguous declared input resource")
            identifiers[identifier] = kind
    rules = _rows(raw["rules"])
    for rule in rules:
        _rule(rule)
    mappings, seen = _rows(raw["mappings"]), set()
    for row in mappings:
        _fields(row, ("kind", "from", "id"))
        if row["kind"] not in ("file", "prefix"):
            raise ValueError("Invalid declared input mapping kind")
        key = (row["kind"], _requested(row["from"], row["kind"]))
        if key in seen or identifiers.get(_text(row["id"])) != row["kind"]:
            raise ValueError("Ambiguous declared input mapping")
        seen.add(key)
        if not any(rule["kind"] == row["kind"] and rule["from"] == row["from"] for rule in rules):
            raise ValueError("Declared mapping is not an approved execution rule")
    return copy.deepcopy(raw)


def _same_path(left, right):
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _stamp(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _regular(path, *, directory=False):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Declared input links are not a closed resource set")
    if not _same_path(os.path.realpath(path), path):
        raise ValueError("Declared input path changed")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Declared input is not a regular resource")
    return info


class _Inventory:
    def __init__(self, limits):
        self.limits, self.count, self.size, self.stamps = limits, 0, 0, {}

    def file(self, path):
        before = _regular(path)
        self.count += 1
        if (
            self.count > self.limits["max_files"]
            or before.st_size > self.limits["max_bytes"] - self.size
        ):
            raise ValueError("Declared input budget exceeded")
        digest, size = hashlib.sha256(), 0
        fd = _OPEN(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = _FSTAT(fd)
            # Windows path stat and descriptor fstat can expose different ctime
            # meanings. Compare common identity/size/mtime across APIs, then
            # compare each API's complete stamp against its own baseline.
            if _stamp(opened)[:4] != _stamp(before)[:4]:
                raise ValueError("Declared input changed while opening")
            while True:
                chunk = _READ(
                    fd, min(_CHUNK_BYTES, self.limits["max_bytes"] - self.size - size + 1)
                )
                if not chunk:
                    break
                size += len(chunk)
                if self.size + size > self.limits["max_bytes"]:
                    raise ValueError("Declared input byte budget exceeded")
                digest.update(chunk)
            if size != before.st_size or _stamp(_FSTAT(fd)) != _stamp(opened):
                raise ValueError("Declared input changed while reading")
        finally:
            _CLOSE(fd)
        after = _regular(path)
        if _stamp(after) != _stamp(before):
            raise ValueError("Declared input changed while reading")
        self.size += size
        self.stamps[path] = _stamp(after)
        return {"sha256": digest.hexdigest(), "size_bytes": size}

    def prefix(self, path):
        before = _regular(path, directory=True)
        members, pending, names = [], [(path, "")], []
        directories = {}
        while pending:
            current, suffix = pending.pop()
            info = _regular(current, directory=True)
            directories[current] = _stamp(info)
            names.append(suffix.rstrip("/"))
            # Directories count too: a tree of empty directories must be bounded.
            self.count += 1
            if self.count > self.limits["max_files"]:
                raise ValueError("Declared input member budget exceeded")
            with os.scandir(current) as entries:
                for entry in entries:
                    relative = suffix + entry.name
                    if (
                        entry.is_symlink()
                        or getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                        & 0x400
                    ):
                        raise ValueError("Declared input prefix contains a link")
                    if entry.is_dir(follow_symlinks=False):
                        if self.count + len(pending) + 1 > self.limits["max_files"]:
                            raise ValueError("Declared input member budget exceeded")
                        pending.append((entry.path, relative + "/"))
                    else:
                        members.append({"suffix": relative, **self.file(entry.path)})
        for directory, stamp in directories.items():
            if _stamp(_regular(directory, directory=True)) != stamp:
                raise ValueError("Declared input prefix changed")
            self.stamps[directory] = stamp
        if _stamp(_regular(path, directory=True)) != _stamp(before):
            raise ValueError("Declared input prefix changed")
        return {
            "members": sorted(members, key=lambda item: item["suffix"]),
            "directories": sorted(names),
        }


def _snapshot(candidate):
    inventory = _Inventory(candidate["limits"])
    graph = {
        "version": VERSION,
        "scope": copy.deepcopy(candidate["scope"]),
        "script": inventory.file(candidate["script_path"]),
        "files": sorted(
            ({"id": row["id"], **inventory.file(row["path"])} for row in candidate["files"]),
            key=lambda row: row["id"],
        ),
        "prefixes": sorted(
            ({"id": row["id"], **inventory.prefix(row["path"])} for row in candidate["prefixes"]),
            key=lambda row: row["id"],
        ),
        "mappings": sorted(
            copy.deepcopy(candidate["mappings"]),
            key=lambda row: (row["kind"], row["from"], row["id"]),
        ),
    }
    return graph, inventory.stamps


def _revision(graph):
    data = json.dumps(
        graph, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


class Capture:
    """One attempt; cached builds never create or replace this object."""

    def __init__(self, candidate):
        self._candidate = candidate
        self._graph, self._stamps = _snapshot(candidate)
        self._execution_id = None
        self._invalid = False

    def entered(self, execution_id):
        if self._execution_id is not None or not executionfact.valid_id(execution_id):
            self._invalid = True
        else:
            self._execution_id = execution_id

    def finish(self, source_sha256):
        if (
            self._invalid
            or self._execution_id is None
            or source_sha256 != self._graph["script"]["sha256"]
        ):
            return None
        try:
            graph, stamps = _snapshot(self._candidate)
        except (OSError, ValueError):
            self._invalid = True
            return None
        if graph != self._graph or stamps != self._stamps:
            self._invalid = True
            return None
        return {
            "version": VERSION,
            "execution_id": self._execution_id,
            "revision": _revision(graph),
            "declaration": copy.deepcopy(graph),
        }


def begin(
    candidate,
    *,
    script,
    script_path,
    cwd_mode,
    run_config,
    remap_rules=(),
    actual_cwd=None,
    project_root=None,
    sandbox=None,
):
    """Begin before user code; unavailable evidence leaves ordinary execution intact."""
    if candidate is None:
        return None
    try:
        candidate = validate_candidate(candidate)
        if candidate["scope"] != {
            "script": script,
            "cwd_mode": cwd_mode,
            "run_config": run_config or None,
        }:
            return None
        if not _same_path(candidate["script_path"], script_path):
            return None
        # The child has effective cwd, not the parent enum. Check the approved
        # enum against the actual directory without guessing from equal paths.
        expected = {
            "sandbox": sandbox,
            "project": str((Path(project_root) / script).parent) if project_root else None,
            "project_root": project_root,
        }.get(cwd_mode)
        if expected is None or actual_cwd is None or not _same_path(expected, actual_cwd):
            return None
        actual_rules = [{key: rule[key] for key in ("kind", "from", "to")} for rule in remap_rules]
        if candidate["rules"] != actual_rules:
            return None
        return Capture(candidate)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _content(row):
    if not isinstance(row, dict) or not _FILE_DIGEST.fullmatch(str(row.get("sha256", ""))):
        raise ValueError("Invalid captured content digest")
    if type(row.get("size_bytes")) is not int or row["size_bytes"] < 0:
        raise ValueError("Invalid captured content size")


def validate_fact(raw, *, execution_id=None):
    """Validate detached evidence; never read paths supplied by a response."""
    try:
        _fields(raw, ("version", "execution_id", "revision", "declaration"))
        if (
            type(raw["version"]) is not int
            or raw["version"] != VERSION
            or not executionfact.valid_id(raw["execution_id"])
        ):
            return None
        if execution_id is not None and raw["execution_id"] != execution_id:
            return None
        graph = _fields(
            raw["declaration"], ("version", "scope", "script", "files", "prefixes", "mappings")
        )
        if type(graph["version"]) is not int or graph["version"] != VERSION:
            return None
        _scope(graph["scope"])
        _fields(graph["script"], ("sha256", "size_bytes"))
        _content(graph["script"])
        ids = {}
        for row in _rows(graph["files"]):
            _fields(row, ("id", "sha256", "size_bytes"))
            identifier = _text(row["id"])
            if identifier in ids:
                return None
            ids[identifier] = "file"
            _content(row)
        for row in _rows(graph["prefixes"]):
            _fields(row, ("id", "members", "directories"))
            identifier = _text(row["id"])
            if identifier in ids:
                return None
            ids[identifier] = "prefix"
            directories = _rows(row["directories"])
            if any(not isinstance(name, str) or "\0" in name for name in directories) or len(
                set(directories)
            ) != len(directories):
                return None
            suffixes = set()
            for member in _rows(row["members"]):
                _fields(member, ("suffix", "sha256", "size_bytes"))
                suffix = _text(member["suffix"])
                if suffix in suffixes:
                    return None
                suffixes.add(suffix)
                _content(member)
        seen = set()
        for row in _rows(graph["mappings"]):
            _fields(row, ("kind", "from", "id"))
            key = (row["kind"], _requested(row["from"], row["kind"]))
            if key in seen or ids.get(_text(row["id"])) != row["kind"]:
                return None
            seen.add(key)
        if (
            not isinstance(raw["revision"], str)
            or not _DIGEST.fullmatch(raw["revision"])
            or raw["revision"] != _revision(graph)
        ):
            return None
        return copy.deepcopy(raw)
    except (ValueError, TypeError, KeyError):
        return None


def public_fact(raw):
    checked = validate_fact(raw)
    return {"version": VERSION, "revision": checked["revision"]} if checked is not None else None


def validate_public_fact(raw):
    """The only public projection: no paths, parameters, or resource names."""
    if (
        not isinstance(raw, dict)
        or set(raw) != {"version", "revision"}
        or type(raw["version"]) is not int
        or raw["version"] != VERSION
        or not isinstance(raw["revision"], str)
        or not _DIGEST.fullmatch(raw["revision"])
    ):
        return None
    return dict(raw)
