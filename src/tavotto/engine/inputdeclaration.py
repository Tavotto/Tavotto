"""Freeze explicitly selected file evidence around one actual execution.

The parent owns path approval and portable names. This standard-library leaf
only reads approved paths and optionally seals their bytes in a caller-owned
cache; it never selects an environment or claims that Python's unobserved
input channels were covered.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

try:
    from . import executionfact
except ImportError:  # worker bootstrap first loads the shared leaf as a flat module
    import executionfact

VERSION = 1
ARCHIVE_DIRNAME = "captured-inputs"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_FILE_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ARCHIVE_KEY = re.compile(r"[0-9a-f]{48}\Z")
_OPEN = os.open
_READ = os.read
_CLOSE = os.close
_FSTAT = os.fstat
_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class InputResource:
    sha256: str
    size_bytes: int
    path: Path


@dataclass(frozen=True)
class InputArchive:
    graph_bytes: bytes
    resources: tuple[InputResource, ...]


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


def _limits(raw):
    checked = _fields(raw, ("max_files", "max_bytes"))
    if any(type(n) is not int or n < 1 for n in checked.values()):
        raise ValueError("Invalid declared input budget")
    return checked


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
    limits = _limits(raw["limits"])
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
    def __init__(self, limits, *, archive=None):
        self.limits, self.count, self.size, self.stamps = limits, 0, 0, {}
        self.archive = archive

    def file(self, path):
        before = _regular(path)
        self.count += 1
        if (
            self.count > self.limits["max_files"]
            or before.st_size > self.limits["max_bytes"] - self.size
        ):
            raise ValueError("Declared input budget exceeded")
        digest, size = hashlib.sha256(), 0
        sink = self.archive.start_file() if self.archive is not None else None
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
                if sink is not None:
                    sink.write(chunk)
            if size != before.st_size or _stamp(_FSTAT(fd)) != _stamp(opened):
                raise ValueError("Declared input changed while reading")
        finally:
            _CLOSE(fd)
        after = _regular(path)
        if _stamp(after) != _stamp(before):
            raise ValueError("Declared input changed while reading")
        self.size += size
        self.stamps[path] = _stamp(after)
        content = {"sha256": digest.hexdigest(), "size_bytes": size}
        if sink is not None:
            sink.finish(content)
        return content

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


def _snapshot(candidate, *, archive=None):
    inventory = _Inventory(candidate["limits"], archive=archive)
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


def _graph_bytes(graph):
    return json.dumps(
        graph, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _revision(graph):
    return "sha256:" + hashlib.sha256(_graph_bytes(graph)).hexdigest()


class Capture:
    """One attempt; cached builds never create or replace this object."""

    def __init__(self, candidate):
        self._candidate = candidate
        self._graph, self._stamps = _snapshot(candidate)
        self._execution_id = None
        self._invalid = False
        self._finished = False
        self._archive = None
        self._saved_archive = None

    def entered(self, execution_id):
        if self._execution_id is not None or not executionfact.valid_id(execution_id):
            self._invalid = True
        else:
            self._execution_id = execution_id

    def finish(self, source_sha256, *, archive_root=None):
        self._archive = None
        if (
            self._invalid
            or self._execution_id is None
            or source_sha256 != self._graph["script"]["sha256"]
        ):
            return None
        archive = None
        try:
            saved = self._saved_archive
            reuse = (
                archive_root is not None
                and saved is not None
                and _same_path(archive_root, saved[0])
            )
            if archive_root is None or reuse:
                graph, stamps = _snapshot(self._candidate)
            else:
                archive = _ArchiveWriter(archive_root)
                graph, stamps = _snapshot(self._candidate, archive=archive)
            if graph != self._graph or stamps != self._stamps:
                raise ValueError("Declared input changed during execution")
            fact = {
                "version": VERSION,
                "execution_id": self._execution_id,
                "revision": _revision(graph),
                "declaration": copy.deepcopy(graph),
            }
            if reuse:
                if (
                    verify_archive(
                        saved[1],
                        fact,
                        archive_root=archive_root,
                        limits=self._candidate["limits"],
                    )
                    is not None
                ):
                    self._archive = dict(saved[1])
                else:
                    # Rebuild a missing cache only from this original lineage.
                    archive = _ArchiveWriter(archive_root)
                    graph, stamps = _snapshot(self._candidate, archive=archive)
                    if graph != self._graph or stamps != self._stamps:
                        raise ValueError("Declared input changed during execution")
            if archive is not None:
                archive.finish(graph)
                descriptor = {"version": VERSION, "key": archive.key, "revision": fact["revision"]}
                if (
                    verify_archive(
                        descriptor,
                        fact,
                        archive_root=archive_root,
                        limits=self._candidate["limits"],
                    )
                    is None
                ):
                    raise ValueError("Captured input archive could not be verified")
                self._archive = descriptor
                self._saved_archive = (archive.root, dict(descriptor))
            elif archive_root is None and saved is not None:
                # resume checks the old baseline without discarding its bytes.
                self._archive = dict(saved[1])
        except (OSError, ValueError, TypeError):
            self._invalid = True
            if archive is not None:
                archive.discard()
            return None
        self._finished = True
        return fact

    def archive_descriptor(self):
        """Private cache reference for this completed finish, never a response path."""
        return (
            dict(self._archive)
            if self._archive is not None and self._finished and not self._invalid
            else None
        )

    def resume(self, execution_id, source_sha256):
        """Continue this same run lineage; never establish a newer baseline.

        A barrier can be released only after a successful finish. Changes while
        it was paused invalidate the whole lineage, even if restored later.
        """
        if (
            self._invalid
            or not self._finished
            or execution_id == self._execution_id
            or not executionfact.valid_id(execution_id)
            or self.finish(source_sha256) is None
        ):
            self._invalid = True
            return False
        self._execution_id = execution_id
        self._finished = False
        return True


def current_public_fact(candidate):
    """Current approved bytes for comparison, without inventing execution facts."""
    try:
        graph, _ = _snapshot(validate_candidate(candidate))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return {"version": VERSION, "revision": _revision(graph)}


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


def validate_graph(raw, *, expected_revision=None):
    """Validate detached graph metadata, without inventing an execution or doing IO."""
    try:
        graph = _fields(raw, ("version", "scope", "script", "files", "prefixes", "mappings"))
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
        if expected_revision is not None and (
            not isinstance(expected_revision, str)
            or not _DIGEST.fullmatch(expected_revision)
            or expected_revision != _revision(graph)
        ):
            return None
        return copy.deepcopy(graph)
    except (ValueError, TypeError, KeyError, RecursionError):
        return None


def canonical_graph_bytes(graph):
    """The same bytes used by capture revisions, including ASCII escaping."""
    checked = validate_graph(graph)
    if checked is None:
        raise ValueError("Invalid captured input graph")
    try:
        return _graph_bytes(checked)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid captured input graph") from exc


def validate_fact(raw, *, execution_id=None):
    """Validate detached evidence; never read paths supplied by a response."""
    try:
        _fields(raw, ("version", "execution_id", "revision", "declaration"))
        if (
            type(raw["version"]) is not int
            or raw["version"] != VERSION
            or not executionfact.valid_id(raw["execution_id"])
            or (execution_id is not None and raw["execution_id"] != execution_id)
            or not isinstance(raw["revision"], str)
            or not _DIGEST.fullmatch(raw["revision"])
            or validate_graph(raw["declaration"], expected_revision=raw["revision"]) is None
        ):
            return None
        return copy.deepcopy(raw)
    except (ValueError, TypeError, KeyError, RecursionError):
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


def validate_archive_descriptor(raw, *, fact):
    """Check a private opaque key against the actual fact; never follow a wire path."""
    checked = validate_fact(fact)
    if (
        checked is None
        or not isinstance(raw, dict)
        or set(raw) != {"version", "key", "revision"}
        or type(raw["version"]) is not int
        or raw["version"] != VERSION
        or not isinstance(raw["key"], str)
        or not _ARCHIVE_KEY.fullmatch(raw["key"])
        or raw["revision"] != checked["revision"]
    ):
        return None
    return dict(raw)


def _archive_root(path, *, create=False):
    root = Path(path).absolute()
    if create and not root.exists() and not root.is_symlink():
        # The controller supplies an existing owned parent, not an arbitrary
        # nested request path. Validate before mkdir could follow an alias.
        _regular(root.parent, directory=True)
        root.mkdir(mode=0o700)
    _regular(root, directory=True)
    return root


def _graph_resources(graph, limits):
    limits = _limits(limits)
    contents = [graph["script"], *graph["files"]]
    directories = 0
    for prefix in graph["prefixes"]:
        contents.extend(prefix["members"])
        directories += len(prefix["directories"])
    if (
        len(contents) + directories > limits["max_files"]
        or sum(row["size_bytes"] for row in contents) > limits["max_bytes"]
    ):
        raise ValueError("Captured input archive exceeds its budget")
    resources = {}
    for row in contents:
        previous = resources.setdefault(row["sha256"], row["size_bytes"])
        if previous != row["size_bytes"]:
            raise ValueError("Captured input digest has inconsistent sizes")
    return resources


def _archive_members(directory, limit):
    members = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(members) >= limit:
                raise ValueError("Captured input archive has too many members")
            _regular(entry.path)
            members.add(entry.name)
    return members


def _read_archive_file(path, size, digest, *, collect=False):
    before = _regular(path)
    if before.st_size != size:
        raise ValueError("Captured input archive size mismatch")
    fd = _OPEN(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    result, count, hashed = [], 0, hashlib.sha256()
    try:
        opened = _FSTAT(fd)
        if _stamp(opened)[:4] != _stamp(before)[:4]:
            raise ValueError("Captured input archive changed while opening")
        while chunk := _READ(fd, min(_CHUNK_BYTES, size - count + 1)):
            count += len(chunk)
            if count > size:
                raise ValueError("Captured input archive exceeds its size")
            hashed.update(chunk)
            if collect:
                result.append(chunk)
        if _stamp(_FSTAT(fd)) != _stamp(opened):
            raise ValueError("Captured input archive changed while reading")
    finally:
        _CLOSE(fd)
    if _stamp(_regular(path)) != _stamp(before):
        raise ValueError("Captured input archive changed while reading")
    if count != size or hashed.hexdigest() != digest:
        raise ValueError("Captured input archive digest mismatch")
    return b"".join(result) if collect else None


def verify_archive(raw, fact, *, archive_root, limits):
    """Freeze verified private cache facts without reading any original source.

    Resource paths are conveniences into this caller-owned nonce directory, not
    leases. Consumers must verify the bytes again when crossing a new boundary.
    """
    try:
        checked = validate_fact(fact)
        descriptor = validate_archive_descriptor(raw, fact=checked)
        if descriptor is None:
            return None
        graph = checked["declaration"]
        contents = _graph_resources(graph, limits)
        graph_bytes = canonical_graph_bytes(graph)
        root = _archive_root(archive_root)
        directory = root / descriptor["key"]
        before = _regular(directory, directory=True)
        expected = {"graph.json", *contents}
        if _archive_members(directory, limits["max_files"] + 1) != expected:
            return None
        stored_graph = _read_archive_file(
            directory / "graph.json",
            len(graph_bytes),
            descriptor["revision"][7:],
            collect=True,
        )
        if stored_graph != graph_bytes:
            return None
        resources = []
        for digest, size in sorted(contents.items()):
            path = directory / digest
            _read_archive_file(path, size, digest)
            resources.append(InputResource(digest, size, path))
        if (
            _stamp(_regular(directory, directory=True)) != _stamp(before)
            or _archive_members(directory, limits["max_files"] + 1) != expected
        ):
            return None
        return InputArchive(stored_graph, tuple(resources))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


class _ArchiveSink:
    def __init__(self, owner, name, fd):
        self.owner, self.name, self.fd = owner, name, fd

    def write(self, data):
        while data:
            count = os.write(self.fd, data)
            if count <= 0:
                raise OSError("Captured input archive write failed")
            data = data[count:]

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            _CLOSE(fd)

    def finish(self, content):
        if _FSTAT(self.fd).st_size != content["size_bytes"]:
            raise ValueError("Captured input archive write was truncated")
        os.fsync(self.fd)
        self.close()
        self.owner.seal(self.name, content["sha256"])


class _ArchiveWriter:
    """Only this attempt's exclusive flat directory can be changed or cleaned."""

    def __init__(self, archive_root):
        self.root = _archive_root(archive_root, create=True)
        self.key = secrets.token_hex(24)
        if not _ARCHIVE_KEY.fullmatch(self.key):
            raise ValueError("Invalid captured input cache key")
        self.directory = self.root / self.key
        self.directory.mkdir(mode=0o700)  # An existing nonce is never overwritten.
        self.info = _regular(self.directory, directory=True)
        self.created, self.sinks = {}, []

    def _owned(self):
        if not os.path.samestat(self.info, _regular(self.directory, directory=True)):
            raise ValueError("Captured input cache directory changed")

    def start_file(self):
        self._owned()
        name = f".{len(self.sinks)}.tmp"
        fd = _OPEN(
            self.directory / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        self.created[name] = _FSTAT(fd)
        sink = _ArchiveSink(self, name, fd)
        self.sinks.append(sink)
        return sink

    def seal(self, name, digest):
        self._owned()
        source, target = self.directory / name, self.directory / digest
        if not os.path.samestat(self.created[name], _regular(source)):
            raise ValueError("Captured input cache file changed")
        if digest not in self.created:
            # link is an atomic no-replace publication, unlike POSIX rename.
            os.link(source, target, follow_symlinks=False)
            self.created[digest] = _regular(target)
        elif not os.path.samestat(self.created[digest], _regular(target)):
            raise ValueError("Captured input cache file changed")
        source.unlink()
        del self.created[name]

    def finish(self, graph):
        sink = self.start_file()
        encoded = canonical_graph_bytes(graph)
        for offset in range(0, len(encoded), _CHUNK_BYTES):
            sink.write(encoded[offset : offset + _CHUNK_BYTES])
        sink.finish({"sha256": "graph.json", "size_bytes": len(encoded)})

    def discard(self):
        for sink in self.sinks:
            try:
                sink.close()
            except OSError:
                pass
        try:
            self._owned()
        except (OSError, ValueError):
            return  # Never traverse a replacement directory or a link.
        for name, info in self.created.items():
            try:
                path = self.directory / name
                if os.path.samestat(info, path.lstat()):
                    path.unlink()
            except OSError:
                pass
        try:
            self.directory.rmdir()  # Unknown members are not ours to delete.
        except OSError:
            pass
