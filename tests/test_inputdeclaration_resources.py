"""Cold consumers validate one graph closure and stream only checked resources."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import inputdeclaration as inputs

LIMITS = {"max_files": 100, "max_bytes": 1024 * 1024}


@pytest.fixture
def archive(tmp_path):
    contents = [b"# frozen source\n", bytes(range(256)) * 770 + b"tail\0\xff", b""]
    resources = []
    for index, content in enumerate(contents):
        path = tmp_path / str(index)
        path.write_bytes(content)
        resources.append(
            inputs.InputResource(hashlib.sha256(content).hexdigest(), len(content), path)
        )

    def content(resource):
        return {"sha256": resource.sha256, "size_bytes": resource.size_bytes}

    graph = {
        "version": 1,
        "scope": {"script": "plot.py", "cwd_mode": "project_root", "run_config": None},
        "script": content(resources[0]),
        "files": [
            {"id": "数据.csv", **content(resources[1])},
            {"id": "opaque-empty", **content(resources[2])},
        ],
        "prefixes": [
            {
                "id": "remap:opaque",
                "members": [{"suffix": "same.csv", **content(resources[1])}],
                "directories": ["", "empty", "empty/nested"],
            }
        ],
        "mappings": [{"kind": "prefix", "from": "../requested", "id": "remap:opaque"}],
    }
    return inputs.InputArchive(inputs.canonical_graph_bytes(graph), tuple(resources))


def test_metadata_validation_is_complete_detached_and_never_reads_paths(archive, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Metadata validation must not open or stat a resource")

    monkeypatch.setattr(inputs, "_regular", forbidden)
    monkeypatch.setattr(inputs, "_OPEN", forbidden)
    monkeypatch.setattr(inputs, "_READ", forbidden)
    graph = inputs.validate_input_archive(archive, limits=LIMITS)
    assert graph == json.loads(archive.graph_bytes)
    assert inputs.graph_resources(graph, limits=LIMITS) == {
        item.sha256: item.size_bytes for item in archive.resources
    }
    assert graph["files"][1]["size_bytes"] == 0
    graph["scope"]["script"] = "changed.py"
    assert graph != json.loads(archive.graph_bytes)


@pytest.mark.parametrize(
    "damage",
    [
        "object",
        "text",
        "mutable_bytes",
        "list",
        "duplicate",
        "missing",
        "extra",
        "size",
        "sha",
        "row_object",
        "bool_size",
        "negative",
        "string_path",
        "noncanonical",
        "invalid_graph",
        "invalid_json",
    ],
)
def test_archive_requires_exact_typed_unique_resource_closure_without_io(
    archive, monkeypatch, damage
):
    resources = archive.resources
    if damage == "object":
        bad = {"graph_bytes": archive.graph_bytes, "resources": resources}
    elif damage == "text":
        bad = dataclasses.replace(archive, graph_bytes=archive.graph_bytes.decode())
    elif damage == "mutable_bytes":
        bad = dataclasses.replace(archive, graph_bytes=bytearray(archive.graph_bytes))
    elif damage == "list":
        bad = dataclasses.replace(archive, resources=list(resources))
    elif damage == "duplicate":
        bad = dataclasses.replace(archive, resources=(*resources, resources[0]))
    elif damage == "missing":
        bad = dataclasses.replace(archive, resources=resources[:-1])
    elif damage == "extra":
        bad = dataclasses.replace(
            archive, resources=(*resources, inputs.InputResource("a" * 64, 0, Path("unused")))
        )
    elif damage in {"size", "sha", "bool_size", "negative", "string_path"}:
        changes = {
            "size": {"size_bytes": resources[0].size_bytes + 1},
            "sha": {"sha256": "A" * 64},
            "bool_size": {"size_bytes": True},
            "negative": {"size_bytes": -1},
            "string_path": {"path": str(resources[0].path)},
        }[damage]
        bad = dataclasses.replace(
            archive, resources=(dataclasses.replace(resources[0], **changes), *resources[1:])
        )
    elif damage == "row_object":
        bad = dataclasses.replace(
            archive, resources=(SimpleNamespace(**dataclasses.asdict(resources[0])), *resources[1:])
        )
    elif damage == "noncanonical":
        bad = dataclasses.replace(archive, graph_bytes=archive.graph_bytes + b" ")
    elif damage == "invalid_graph":
        graph = json.loads(archive.graph_bytes)
        graph["private_path"] = "/not-authorized"
        bad = dataclasses.replace(archive, graph_bytes=json.dumps(graph).encode())
    else:
        bad = dataclasses.replace(archive, graph_bytes=b"{")
    monkeypatch.setattr(
        inputs, "_regular", lambda *_a, **_k: pytest.fail("Malformed attachment did IO")
    )
    with pytest.raises(ValueError):
        inputs.validate_input_archive(bad, limits=LIMITS)


@pytest.mark.parametrize("target", ["graph", "archive"])
@pytest.mark.parametrize("budget", ["files", "bytes", "invalid", "inconsistent_digest"])
def test_public_metadata_helpers_share_logical_budget_and_digest_consistency(
    archive, target, budget
):
    graph = json.loads(archive.graph_bytes)
    limits = dict(LIMITS)
    if budget == "files":
        limits["max_files"] = 4  # Four logical files fit, but empty directories also count.
    elif budget == "bytes":
        limits["max_bytes"] = sum(item.size_bytes for item in archive.resources)
    elif budget == "invalid":
        limits["max_files"] = True
    else:
        graph["prefixes"][0]["members"][0]["size_bytes"] += 1
        archive = dataclasses.replace(archive, graph_bytes=inputs.canonical_graph_bytes(graph))
    with pytest.raises(ValueError):
        if target == "graph":
            inputs.graph_resources(graph, limits=limits)
        else:
            inputs.validate_input_archive(archive, limits=limits)


def test_public_graph_resources_rejects_a_bad_graph_without_source_io(archive, monkeypatch):
    graph = json.loads(archive.graph_bytes)
    graph["private_path"] = "arbitrary"
    monkeypatch.setattr(
        inputs, "_regular", lambda *_a, **_k: pytest.fail("Graph validation did IO")
    )
    with pytest.raises(ValueError, match="Invalid captured input graph"):
        inputs.graph_resources(graph, limits=LIMITS)


@pytest.mark.parametrize("location", ["script", "file", "prefix"])
@pytest.mark.parametrize("value", [int("1" * 64), 1.0, True])
def test_all_graph_content_digests_require_strings(archive, monkeypatch, location, value):
    graph = json.loads(archive.graph_bytes)
    row = {
        "script": graph["script"],
        "file": graph["files"][0],
        "prefix": graph["prefixes"][0]["members"][0],
    }[location]
    row["sha256"] = value
    encoded = json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    bad = dataclasses.replace(archive, graph_bytes=encoded)
    monkeypatch.setattr(inputs, "_regular", lambda *_a, **_k: pytest.fail("Bad graph did IO"))
    assert inputs.validate_graph(graph) is None
    with pytest.raises(ValueError):
        inputs.graph_resources(graph, limits=LIMITS)
    with pytest.raises(ValueError):
        inputs.validate_input_archive(bad, limits=LIMITS)


def test_copy_and_verify_use_bounded_reads_and_handle_short_binary_writes(archive, monkeypatch):
    resource = archive.resources[1]
    expected = resource.path.read_bytes()
    read = inputs._READ
    requested = []

    def bounded(fd, amount):
        requested.append(amount)
        assert 0 < amount <= 64 * 1024
        return read(fd, amount)

    class ShortSink:
        def __init__(self):
            self.output = bytearray()
            self.calls = 0

        def write(self, chunk):
            self.calls += 1
            amount = min(len(chunk), 31)
            self.output.extend(chunk[:amount])
            return amount

    monkeypatch.setattr(inputs, "_READ", bounded)
    monkeypatch.setattr(Path, "read_bytes", lambda *_a: pytest.fail("Resource was whole-read"))
    sink = ShortSink()
    assert inputs.copy_resource(resource, sink) is None
    assert bytes(sink.output) == expected and sink.calls > 3
    assert inputs.verify_resource(resource) is None
    assert len(requested) >= 8


def test_zero_byte_resource_is_valid_and_does_not_write_or_close_the_sink(archive):
    resource = archive.resources[2]
    sink = io.BytesIO()
    assert inputs.copy_resource(resource, sink) is None
    assert inputs.verify_resource(resource) is None
    assert not sink.closed and sink.getvalue() == b""


@pytest.mark.parametrize("progress", [0, -1, None, True, "1", "too_many"])
def test_copy_refuses_invalid_write_progress_and_always_closes_the_source(
    archive, monkeypatch, progress
):
    resource = archive.resources[0]
    close, closed = inputs._CLOSE, []

    def tracked(fd):
        closed.append(fd)
        close(fd)

    class Sink:
        def write(self, chunk):
            return len(chunk) + 1 if progress == "too_many" else progress

    monkeypatch.setattr(inputs, "_CLOSE", tracked)
    with pytest.raises(OSError, match="progress"):
        inputs.copy_resource(resource, Sink())
    assert len(closed) == 1


@pytest.mark.parametrize("operation", ["copy", "verify"])
def test_same_length_tampering_is_rejected_even_after_validation(archive, operation):
    inputs.validate_input_archive(archive, limits=LIMITS)
    resource = archive.resources[0]
    original = resource.path.read_bytes()
    resource.path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(ValueError, match="digest mismatch"):
        if operation == "copy":
            inputs.copy_resource(resource, io.BytesIO())
        else:
            inputs.verify_resource(resource)


@pytest.mark.parametrize("operation", ["copy", "verify"])
def test_midstream_read_failure_closes_fd_and_never_reports_success(
    archive, monkeypatch, operation
):
    resource = archive.resources[1]
    read, close, calls, closed = inputs._READ, inputs._CLOSE, [], []
    sink = io.BytesIO()

    def broken(fd, amount):
        calls.append(amount)
        if len(calls) > 1:
            raise OSError("simulated partial source failure")
        return read(fd, amount)

    def tracked(fd):
        closed.append(fd)
        close(fd)

    monkeypatch.setattr(inputs, "_READ", broken)
    monkeypatch.setattr(inputs, "_CLOSE", tracked)
    with pytest.raises(OSError, match="partial source failure"):
        if operation == "copy":
            inputs.copy_resource(resource, sink)
        else:
            inputs.verify_resource(resource)
    assert len(closed) == 1
    assert len(sink.getvalue()) == (64 * 1024 if operation == "copy" else 0)
    assert not sink.closed


def test_sink_error_is_propagated_and_temporary_partial_output_is_not_published(archive, tmp_path):
    resource = archive.resources[1]
    target = tmp_path / "previous-published"
    target.write_bytes(b"previous immutable asset")
    temporary = tmp_path / "private-stage"

    class BrokenSink:
        def __init__(self, stream):
            self.stream = stream
            self.calls = 0

        def write(self, chunk):
            self.calls += 1
            if self.calls > 1:
                raise OSError("destination full")
            return self.stream.write(chunk)

    with temporary.open("wb") as stream:
        with pytest.raises(OSError, match="destination full"):
            inputs.copy_resource(resource, BrokenSink(stream))
        assert not stream.closed
    assert temporary.stat().st_size == 64 * 1024
    assert target.read_bytes() == b"previous immutable asset"


def test_source_change_after_its_chunk_was_written_is_still_rejected(archive):
    resource = archive.resources[0]
    original = resource.path.read_bytes()

    class MutatingSink(io.BytesIO):
        def write(self, chunk):
            count = super().write(chunk)
            resource.path.write_bytes(b"X" * len(original))
            return count

    sink = MutatingSink()
    with pytest.raises(ValueError, match="changed while reading"):
        inputs.copy_resource(resource, sink)
    assert sink.getvalue() == original  # Hash alone would pass; final source stamps must not.


@pytest.mark.parametrize("operation", ["copy", "verify"])
def test_invalid_resource_fields_cannot_trigger_io(archive, monkeypatch, operation):
    resource = dataclasses.replace(archive.resources[0], sha256="../unsafe")
    monkeypatch.setattr(
        inputs, "_OPEN", lambda *_a, **_k: pytest.fail("Invalid resource was opened")
    )
    with pytest.raises(ValueError, match="Invalid captured input resource"):
        if operation == "copy":
            inputs.copy_resource(resource, io.BytesIO())
        else:
            inputs.verify_resource(resource)


def test_verify_resource_uses_the_original_regular_file_guard(archive, monkeypatch):
    resource = archive.resources[0]
    regular = inputs._regular

    def reject(path, **kwargs):
        assert path == resource.path
        raise ValueError("simulated link or reparse point")

    monkeypatch.setattr(inputs, "_regular", reject)
    with pytest.raises(ValueError, match="link or reparse"):
        inputs.verify_resource(resource)
    monkeypatch.setattr(inputs, "_regular", regular)
    assert inputs.verify_resource(resource) is None
