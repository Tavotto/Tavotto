"""Typed durable input attachments; callers hold svgstore's existing store lock.

This leaf does not select paths from graph identities, grant execution authority,
or manage a second lock. Input resources stream through the capture authority;
only explicitly bounded canonical graph metadata is collected in memory.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import re
import stat
from dataclasses import dataclass, fields
from pathlib import Path

from . import atomicio, inputdeclaration, projectcopy

VERSION = 1
DIRNAME = "inputs"
DEFAULT_GRAPH_BYTES = 64 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_GRAPH = re.compile(r"([0-9a-f]{64})\.json\Z")
_SHARD = re.compile(r"[0-9a-f]{2}\Z")
_TAIL = re.compile(r"[0-9a-f]{62}\Z")


@dataclass(frozen=True)
class InputStoreBudget:
    capture_max_files: int
    capture_max_bytes: int
    max_blob_files: int
    max_blob_bytes: int
    max_graph_bytes: int
    max_total_graph_bytes: int
    max_graphs: int

    def __post_init__(self):
        if any(
            type(getattr(self, f.name)) is not int or getattr(self, f.name) < 0
            for f in fields(self)
        ):
            raise ValueError("Invalid durable input budget")


def default_budget(*, max_graphs: int) -> InputStoreBudget:
    """One explicit cumulative input policy, separate from render-store quotas."""
    return InputStoreBudget(
        projectcopy.MAX_FILES,
        projectcopy.MAX_BYTES,
        projectcopy.MAX_FILES,
        projectcopy.MAX_BYTES,
        DEFAULT_GRAPH_BYTES,
        DEFAULT_GRAPH_BYTES,
        max_graphs,
    )


def _budget(budget):
    if not isinstance(budget, InputStoreBudget):
        raise ValueError("A durable input budget is required")
    return {"max_files": budget.capture_max_files, "max_bytes": budget.capture_max_bytes}


def validate_descriptor(raw) -> dict:
    if (
        not isinstance(raw, dict)
        or set(raw) != {"version", "revision", "graph_size_bytes"}
        or type(raw["version"]) is not int
        or raw["version"] != VERSION
        or not isinstance(raw["revision"], str)
        or not raw["revision"].startswith("sha256:")
        or not _HEX.fullmatch(raw["revision"][7:])
        or type(raw["graph_size_bytes"]) is not int
        or raw["graph_size_bytes"] < 1
    ):
        raise ValueError("Invalid durable input descriptor")
    return dict(raw)


def _context(graph, reference, receipt, execution_owner):
    public = inputdeclaration.validate_public_fact(
        receipt.get("captured_inputs") if isinstance(receipt, dict) else None
    )
    if public is None or public["revision"] != reference["revision"]:
        raise ValueError("Durable input graph does not match its receipt")
    if execution_owner is not None and (
        not isinstance(execution_owner, dict)
        or set(execution_owner) != {"script", "run_config"}
        or any(execution_owner[key] != graph["scope"][key] for key in ("script", "run_config"))
    ):
        raise ValueError("Durable input graph does not match its execution owner")


def _archive(archive, budget):
    limits = _budget(budget)
    if not isinstance(archive, inputdeclaration.InputArchive) or not isinstance(
        archive.graph_bytes, bytes
    ):
        raise ValueError("Invalid durable input archive")
    if not 0 < len(archive.graph_bytes) <= budget.max_graph_bytes:
        raise ValueError("Durable input graph budget exceeded")
    graph = inputdeclaration.validate_input_archive(archive, limits=limits)
    reference = {
        "version": VERSION,
        "revision": "sha256:" + hashlib.sha256(archive.graph_bytes).hexdigest(),
        "graph_size_bytes": len(archive.graph_bytes),
    }
    return reference, graph


def descriptor(archive, *, receipt, execution_owner, budget) -> dict:
    """Check detached metadata/closure only; never open any resource path."""
    reference, graph = _archive(archive, budget)
    _context(graph, reference, receipt, execution_owner)
    return reference


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _stat(path, *, directory=False):
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ) or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Unsafe durable input member")
    return info


def _root(root):
    root = Path(root).absolute()
    if ".." in root.parts:
        raise ValueError("Unsafe durable input root")
    _stat(root, directory=True)
    return root


def _directory_members(path):
    before = _stat(path, directory=True)
    with os.scandir(path) as entries:
        yield from entries
    if _stamp(_stat(path, directory=True)) != _stamp(before):
        raise ValueError("Durable input directory changed during inspection")


def _scan(root, *, budget, allow_pending=False):
    _budget(budget)
    root = _root(root)
    base = root / DIRNAME
    try:
        _stat(base, directory=True)
    except FileNotFoundError:
        return {}, []
    result = {}
    pending = []

    def pending_member(entry, pattern, limit):
        if not allow_pending or not atomicio._TMP_NAME.fullmatch(entry.name):
            return False
        if not pattern.fullmatch(entry.name.rsplit(".", 3)[0]):
            return False
        target = Path(entry.path)
        info = _stat(target)
        # The existing store guard admits only one sequential input writer.
        # A crash can therefore leave one bounded, partially written member.
        if pending or info.st_size > limit:
            raise ValueError("Durable input pending member budget exceeded")
        pending.append((target.relative_to(root).as_posix(), info))
        return True

    graph_count = graph_bytes = blob_count = blob_bytes = 0
    for directory in _directory_members(base):
        if directory.name not in {"graphs", "blobs"}:
            raise ValueError("Unknown durable input member")
        path = Path(directory.path)
        _stat(path, directory=True)
        if directory.name == "graphs":
            for entry in _directory_members(path):
                if not _GRAPH.fullmatch(entry.name):
                    if pending_member(entry, _GRAPH, budget.max_graph_bytes):
                        continue
                    raise ValueError("Unknown durable input graph")
                target = Path(entry.path)
                info = _stat(target)
                graph_count += 1
                graph_bytes += info.st_size
                if (
                    not 0 < info.st_size <= budget.max_graph_bytes
                    or graph_count > budget.max_graphs
                    or graph_bytes > budget.max_total_graph_bytes
                ):
                    raise ValueError("Durable input graph budget exceeded")
                result[target.relative_to(root).as_posix()] = (target, info)
        else:
            for shard in _directory_members(path):
                if not _SHARD.fullmatch(shard.name):
                    raise ValueError("Unknown durable input shard")
                for entry in _directory_members(Path(shard.path)):
                    if not _TAIL.fullmatch(entry.name):
                        if pending_member(entry, _TAIL, budget.capture_max_bytes):
                            continue
                        raise ValueError("Unknown durable input blob")
                    target = Path(entry.path)
                    info = _stat(target)
                    blob_count += 1
                    blob_bytes += info.st_size
                    if (
                        info.st_size > budget.capture_max_bytes
                        or blob_count > budget.max_blob_files
                        or blob_bytes > budget.max_blob_bytes
                    ):
                        raise ValueError("Durable input blob budget exceeded")
                    result[target.relative_to(root).as_posix()] = (target, info)
    return result, pending


def inventory(root, *, budget) -> dict:
    """Census closed typed files; no legacy per-directory 4096 limit is reused."""
    return _scan(root, budget=budget)[0]


def recover(root, *, budget) -> None:
    """Under the caller's store guard, remove only one interrupted owned temp.

    Read-only consumers continue to reject every temporary member. This does
    not collect published orphans: cleanup needs the actual committed payloads.
    """
    root = _root(root)
    _, pending = _scan(root, budget=budget, allow_pending=True)
    for name, before in pending:
        _parents(root, name)
        path = root / name
        if _stamp(_stat(path)) != _stamp(before):
            raise ValueError("Durable input pending member changed during recovery")
        path.unlink()


def _graph_name(reference):
    return f"{DIRNAME}/graphs/{reference['revision'][7:]}.json"


def _blob_name(digest):
    return f"{DIRNAME}/blobs/{digest[:2]}/{digest[2:]}"


def _parents(root, name):
    _stat(root, directory=True)
    for part in Path(name).parts[:-1]:
        root = root / part
        _stat(root, directory=True)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate durable input graph key")
        result[key] = value
    return result


def _read_graph(root, reference, budget):
    reference = validate_descriptor(reference)
    if reference["graph_size_bytes"] > budget.max_graph_bytes:
        raise ValueError("Durable input graph budget exceeded")
    name = _graph_name(reference)
    _parents(root, name)
    resource = inputdeclaration.InputResource(
        reference["revision"][7:], reference["graph_size_bytes"], root / name
    )
    with io.BytesIO() as target:
        inputdeclaration.copy_resource(resource, target)
        encoded = target.getvalue()
    try:
        graph = inputdeclaration.validate_graph(
            json.loads(encoded, object_pairs_hook=_unique), expected_revision=reference["revision"]
        )
        if graph is None or inputdeclaration.canonical_graph_bytes(graph) != encoded:
            raise ValueError("Noncanonical durable input graph")
    except (UnicodeError, TypeError, RecursionError) as exc:
        raise ValueError("Invalid durable input graph") from exc
    contents = inputdeclaration.graph_resources(graph, limits=_budget(budget))
    return encoded, graph, contents


def read(root, descriptor, *, receipt, execution_owner, budget):
    """Read exactly one attachment; unrelated inputs are checked by full inspection."""
    _budget(budget)
    root = _root(root)
    reference = validate_descriptor(descriptor)
    encoded, graph, contents = _read_graph(root, reference, budget)
    _context(graph, reference, receipt, execution_owner)
    _totals(
        {reference["revision"][7:]: {"descriptor": reference}},
        {digest: {"size_bytes": size} for digest, size in contents.items()},
        budget,
    )
    resources = []
    for digest, size in sorted(contents.items()):
        name = _blob_name(digest)
        _parents(root, name)
        resource = inputdeclaration.InputResource(digest, size, root / name)
        inputdeclaration.verify_resource(resource)
        resources.append(resource)
    return inputdeclaration.InputArchive(encoded, tuple(resources))


def _same_inventory(before, after):
    if set(before) != set(after) or any(
        _stamp(info) != _stamp(after[name][1]) for name, (_, info) in before.items()
    ):
        raise ValueError("Durable input store changed during inspection")


def _state(root, payloads, *, incoming=None, budget):
    root = _root(root)
    limits = _budget(budget)
    members = inventory(root, budget=budget)
    graphs, blobs = {}, {}
    for name, (path, info) in members.items():
        if name.startswith(f"{DIRNAME}/graphs/"):
            reference = {
                "version": VERSION,
                "revision": "sha256:" + path.stem,
                "graph_size_bytes": info.st_size,
            }
            _, graph, contents = _read_graph(root, reference, budget)
            graphs[path.stem] = {"descriptor": reference, "graph": graph, "contents": contents}
        else:
            digest = path.parent.name + path.name
            resource = inputdeclaration.InputResource(digest, info.st_size, path)
            inputdeclaration.verify_resource(resource)
            blobs[digest] = {"sha256": digest, "size_bytes": info.st_size}
    # A pre-existing graph must already be complete; incoming cannot repair it.
    for row in graphs.values():
        for digest, size in row["contents"].items():
            if digest not in blobs or blobs[digest]["size_bytes"] != size:
                raise ValueError("Missing or inconsistent durable input blob")
    incoming_reference = incoming_graph = None
    if incoming is not None:
        incoming_reference, incoming_graph = _archive(incoming, budget)
    final_graphs, final_blobs = dict(graphs), dict(blobs)
    if incoming_reference is not None:
        digest = incoming_reference["revision"][7:]
        old = final_graphs.setdefault(
            digest,
            {
                "descriptor": incoming_reference,
                "graph": incoming_graph,
                "contents": inputdeclaration.graph_resources(incoming_graph, limits=limits),
            },
        )
        if old["descriptor"] != incoming_reference:
            raise ValueError("Inconsistent durable input graph size")
        for resource in incoming.resources:
            old = final_blobs.setdefault(
                resource.sha256, {"sha256": resource.sha256, "size_bytes": resource.size_bytes}
            )
            if old["size_bytes"] != resource.size_bytes:
                raise ValueError("Inconsistent durable input blob size")
    _totals(final_graphs, final_blobs, budget)
    for payload in payloads:
        if not isinstance(payload, dict):
            raise ValueError("Invalid Figure payload")
        if "input_archive" not in payload:
            continue
        reference = validate_descriptor(payload["input_archive"])
        row = final_graphs.get(reference["revision"][7:])
        if row is None or row["descriptor"] != reference:
            raise ValueError("Missing durable input graph")
        _context(row["graph"], reference, payload.get("receipt"), payload.get("execution_owner"))
    _same_inventory(members, inventory(root, budget=budget))
    return {"inventory": members, "graphs": graphs, "blobs": blobs}


def _totals(graphs, blobs, budget):
    if (
        len(graphs) > budget.max_graphs
        or sum(row["descriptor"]["graph_size_bytes"] for row in graphs.values())
        > budget.max_total_graph_bytes
        or len(blobs) > budget.max_blob_files
        or sum(row["size_bytes"] for row in blobs.values()) > budget.max_blob_bytes
    ):
        raise ValueError("Durable input store budget exceeded")


def admit(root, payloads, *, incoming=None, budget) -> None:
    """Verify the entire established tree and final deduplicated capacity before writes."""
    _state(root, payloads, incoming=incoming, budget=budget)


def inspect(root, payloads, *, budget) -> dict:
    """Reuse admission's single full hash pass for portable inventory consumers."""
    state = _state(root, payloads, budget=budget)
    state["blob_bytes"] = sum(row["size_bytes"] for row in state["blobs"].values())
    state["graph_bytes"] = sum(
        row["descriptor"]["graph_size_bytes"] for row in state["graphs"].values()
    )
    return state


@dataclass(frozen=True)
class PublishedMember:
    relative: str
    identity: tuple


def _created(root, path):
    return PublishedMember(path.relative_to(root).as_posix(), _stamp(_stat(path)))


def _discard_created(root, created, referenced):
    for token in reversed(created):
        if not isinstance(token, PublishedMember):
            raise ValueError("Invalid durable input publication token")
        if token.relative in referenced:
            continue
        if not (
            _GRAPH.fullmatch(token.relative.removeprefix(f"{DIRNAME}/graphs/"))
            and token.relative.startswith(f"{DIRNAME}/graphs/")
        ) and not (
            token.relative.startswith(f"{DIRNAME}/blobs/")
            and len(Path(token.relative).parts) == 4
            and _SHARD.fullmatch(Path(token.relative).parts[2])
            and _TAIL.fullmatch(Path(token.relative).parts[3])
        ):
            raise ValueError("Invalid durable input publication token")
        path = root / token.relative
        try:
            _parents(root, token.relative)
            if _stamp(_stat(path)) == token.identity:
                path.unlink()
        except FileNotFoundError:
            pass


def _mkdirs(root, relative, directories):
    current = root
    for part in Path(relative).parts[:-1]:
        current = current / part
        try:
            _stat(current, directory=True)
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            directories.append((current, _stamp(_stat(current, directory=True))))


def _publish_one(root, name, resource, encoded, created, directories):
    destination = root / name
    _mkdirs(root, name, directories)
    try:
        _stat(destination)
    except FileNotFoundError:
        pass
    else:
        if resource is not None:
            inputdeclaration.verify_resource(resource)
        wanted = resource or inputdeclaration.InputResource(
            hashlib.sha256(encoded).hexdigest(), len(encoded), destination
        )
        inputdeclaration.verify_resource(
            inputdeclaration.InputResource(wanted.sha256, wanted.size_bytes, destination)
        )
        return
    temporary = atomicio._next_tmp(destination)
    fd = os.open(
        temporary,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(fd, "wb") as output:
            if resource is not None:
                inputdeclaration.copy_resource(resource, output)
            else:
                output.write(encoded)
        before = _stat(temporary)
        try:
            atomicio.publish_file_if_absent(temporary, destination)
        except FileExistsError:
            wanted = resource or inputdeclaration.InputResource(
                hashlib.sha256(encoded).hexdigest(), len(encoded), destination
            )
            inputdeclaration.verify_resource(
                inputdeclaration.InputResource(wanted.sha256, wanted.size_bytes, destination)
            )
        finally:
            try:
                if os.path.samestat(before, _stat(destination)):
                    created.append(_created(root, destination))
            except FileNotFoundError:
                pass
    finally:
        temporary.unlink(missing_ok=True)


def publish(root, archive, *, budget) -> tuple:
    """Stream immutable members; callers have already admitted their final payloads."""
    root = _root(root)
    reference, graph = _archive(archive, budget)
    members = inventory(root, budget=budget)
    graphs, blobs = {}, {}
    for name, (path, info) in members.items():
        if name.startswith(f"{DIRNAME}/graphs/"):
            graphs[path.stem] = {"descriptor": {"graph_size_bytes": info.st_size}}
        else:
            blobs[path.parent.name + path.name] = {"size_bytes": info.st_size}
    previous = graphs.setdefault(reference["revision"][7:], {"descriptor": reference})
    if previous["descriptor"]["graph_size_bytes"] != reference["graph_size_bytes"]:
        raise ValueError("Inconsistent durable input graph size")
    for digest, size in inputdeclaration.graph_resources(graph, limits=_budget(budget)).items():
        previous = blobs.setdefault(digest, {"size_bytes": size})
        if previous["size_bytes"] != size:
            raise ValueError("Inconsistent durable input blob size")
    _totals(graphs, blobs, budget)
    created, directories = [], []
    try:
        for resource in archive.resources:
            _publish_one(root, _blob_name(resource.sha256), resource, None, created, directories)
        _publish_one(root, _graph_name(reference), None, archive.graph_bytes, created, directories)
    except BaseException:
        _discard_created(root, created, set())
        for directory, identity in reversed(directories):
            try:
                if _stamp(_stat(directory, directory=True))[:2] == identity[:2]:
                    directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                # A collision may have published someone else's member in a
                # directory we created. Retain it and the original failure.
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    raise
        raise
    return tuple(created)


def cleanup(root, payloads, *, budget, created=None) -> None:
    """Retain all historical/current references; optionally restrict to this attempt."""
    root = _root(root)
    payloads = tuple(payloads)
    state = _state(root, payloads, budget=budget)
    referenced = set()
    for payload in payloads:
        if "input_archive" not in payload:
            continue
        reference = validate_descriptor(payload["input_archive"])
        referenced.add(_graph_name(reference))
        referenced.update(
            _blob_name(digest) for digest in state["graphs"][reference["revision"][7:]]["contents"]
        )
    if created is not None:
        _discard_created(root, created, referenced)
    else:
        _discard_created(
            root,
            tuple(
                PublishedMember(name, _stamp(info))
                for name, (_, info) in state["inventory"].items()
            ),
            referenced,
        )
