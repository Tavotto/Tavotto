"""An actual finish seals the original bytes, never current-path replacements."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import executionfact, inputdeclaration as inputs


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "中文 source"
    root.mkdir()
    (root / "plot.py").write_bytes(b"# actual compiled source\n")
    (root / "data.csv").write_bytes(b'"A\tB",2\r\n"C""D\nE",3\n\0\xff')
    prefix = root / "selected-prefix"
    (prefix / "empty" / "nested").mkdir(parents=True)
    # A duplicate input must keep its logical role but share its physical blob.
    (prefix / "same.csv").write_bytes((root / "data.csv").read_bytes())
    (prefix / "helper.py").write_bytes(b"VALUE = 7\n")
    candidate = {
        "version": 1,
        "scope": {"script": "plot.py", "cwd_mode": "project_root", "run_config": None},
        "script_path": str(root / "plot.py"),
        "files": [{"id": "数据.csv", "path": str(root / "data.csv")}],
        "prefixes": [{"id": "remap:opaque-set", "path": str(prefix)}],
        "mappings": [{"kind": "prefix", "from": "../requested", "id": "remap:opaque-set"}],
        "rules": [{"kind": "prefix", "from": "../requested", "to": str(prefix)}],
        "limits": {"max_files": 100, "max_bytes": 1024 * 1024},
    }
    return candidate, tmp_path / inputs.ARCHIVE_DIRNAME


def begin(candidate):
    capture = inputs.begin(
        candidate,
        script=candidate["scope"]["script"],
        script_path=candidate["script_path"],
        cwd_mode=candidate["scope"]["cwd_mode"],
        run_config=candidate["scope"]["run_config"],
        remap_rules=candidate["rules"],
        actual_cwd=str(Path(candidate["script_path"]).parent),
        project_root=str(Path(candidate["script_path"]).parent),
    )
    assert capture is not None
    capture.entered(executionfact.new_id())
    return capture


def finish(capture, candidate, archive_root=None):
    source_sha = hashlib.sha256(Path(candidate["script_path"]).read_bytes()).hexdigest()
    if archive_root is None:
        return capture.finish(source_sha)
    return capture.finish(source_sha, archive_root=archive_root)


def frozen(world):
    candidate, root = world
    capture = begin(candidate)
    fact = finish(capture, candidate, root)
    descriptor = capture.archive_descriptor()
    assert fact is not None and descriptor is not None
    archive = inputs.verify_archive(descriptor, fact, archive_root=root, limits=candidate["limits"])
    assert archive is not None
    return capture, fact, descriptor, archive


def test_finish_seals_exact_source_file_prefix_bytes_and_empty_directories(world):
    candidate, root = world
    _, fact, descriptor, archive = frozen(world)
    assert inputs.validate_archive_descriptor(descriptor, fact=fact) == descriptor
    assert set(descriptor) == {"version", "key", "revision"}
    assert len(descriptor["key"]) == 48 and descriptor["revision"] == fact["revision"]
    assert inputs.validate_graph(fact["declaration"], expected_revision=fact["revision"])
    assert json.loads(archive.graph_bytes) == fact["declaration"]
    assert b"\\u6570" in archive.graph_bytes  # The original revision escapes non-ASCII.
    assert "empty/nested" in json.loads(archive.graph_bytes)["prefixes"][0]["directories"]
    expected = {
        Path(candidate["script_path"]).read_bytes(),
        Path(candidate["files"][0]["path"]).read_bytes(),
        (Path(candidate["prefixes"][0]["path"]) / "helper.py").read_bytes(),
    }
    assert {resource.path.read_bytes() for resource in archive.resources} == expected
    assert len(archive.resources) == 3
    assert all(resource.path.parent == root / descriptor["key"] for resource in archive.resources)
    with pytest.raises(dataclasses.FrozenInstanceError):
        archive.graph_bytes = b"changed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        archive.resources[0].size_bytes = 0
    if os.name != "nt":
        assert stat.S_IMODE((root / descriptor["key"]).stat().st_mode) == 0o700
        assert all(stat.S_IMODE(row.path.stat().st_mode) == 0o600 for row in archive.resources)


def test_default_finish_keeps_fact_and_source_io_without_archive_io(world, monkeypatch):
    candidate, root = world
    capture = begin(candidate)
    expected, _ = inputs._snapshot(candidate)

    def forbidden(*_args, **_kwargs):
        pytest.fail("No archive was requested")

    monkeypatch.setattr(inputs, "_ArchiveWriter", forbidden)
    monkeypatch.setattr(inputs, "verify_archive", forbidden)
    fact = finish(capture, candidate)
    assert fact["declaration"] == expected
    assert set(fact) == {"version", "execution_id", "revision", "declaration"}
    assert capture.archive_descriptor() is None
    assert not root.exists()


def test_source_reads_for_finish_are_the_same_stream_that_is_sealed(world, monkeypatch):
    candidate, root = world
    capture = begin(candidate)
    original_open = inputs._OPEN
    opened = []

    def observe(path, flags, *args):
        if flags & (os.O_WRONLY | os.O_RDWR) == 0:
            path = Path(path)
            if path.is_relative_to(Path(candidate["script_path"]).parent):
                opened.append(path)
        return original_open(path, flags, *args)

    monkeypatch.setattr(inputs, "_OPEN", observe)
    fact = finish(capture, candidate, root)
    assert fact is not None
    assert len(opened) == 4  # script, selected file, two prefix members; no late source copy.
    assert len(set(opened)) == 4


def test_cold_process_needs_only_fact_descriptor_and_private_archive(world, tmp_path):
    candidate, root = world
    _, fact, descriptor, archive = frozen(world)
    expected = {row.sha256: row.path.read_bytes().hex() for row in archive.resources}
    for path in Path(candidate["script_path"]).parent.rglob("*"):
        if path.is_file():
            path.unlink()
    exchange = tmp_path / "fact.json"
    exchange.write_text(json.dumps({"fact": fact, "descriptor": descriptor}))
    code = """
import json, sys
from pathlib import Path
from tavotto.engine import inputdeclaration as inputs
data = json.loads(Path(sys.argv[2]).read_text())
archive = inputs.verify_archive(data['descriptor'], data['fact'], archive_root=sys.argv[1], limits={'max_files':100,'max_bytes':1048576})
assert archive is not None
assert not any(name in sys.modules for name in ('matplotlib','numpy','tavotto.engine.pool','tavotto.engine.declaredinputs'))
print(json.dumps({row.sha256:row.path.read_bytes().hex() for row in archive.resources}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(root), str(exchange)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


def test_changed_original_bytes_never_replace_the_sealed_copy(world):
    candidate, root = world
    capture, fact, descriptor, archive = frozen(world)
    original = {row.sha256: row.path.read_bytes() for row in archive.resources}
    Path(candidate["files"][0]["path"]).write_bytes(b"changed after execution")
    checked = inputs.verify_archive(descriptor, fact, archive_root=root, limits=candidate["limits"])
    assert {row.sha256: row.path.read_bytes() for row in checked.resources} == original
    assert finish(capture, candidate, root) is None
    assert capture.archive_descriptor() is None
    assert (root / descriptor["key"]).is_dir()


def test_continuous_finishes_and_resume_share_one_lineage_cache(world, monkeypatch):
    candidate, root = world
    capture, fact, descriptor, _ = frozen(world)
    before = {path.name: path.read_bytes() for path in (root / descriptor["key"]).iterdir()}

    def forbidden(*_args, **_kwargs):
        pytest.fail("An unchanged lineage must not materialize another copy")

    monkeypatch.setattr(inputs, "_ArchiveWriter", forbidden)
    assert finish(capture, candidate, root) == fact
    assert capture.archive_descriptor() == descriptor
    assert finish(capture, candidate) == fact
    assert capture.archive_descriptor() == descriptor
    assert capture.resume(executionfact.new_id(), fact["declaration"]["script"]["sha256"])
    assert capture.archive_descriptor() is None
    next_fact = finish(capture, candidate, root)
    assert next_fact["execution_id"] != fact["execution_id"]
    assert capture.archive_descriptor() == descriptor
    assert list(root.iterdir()) == [root / descriptor["key"]]
    assert {path.name: path.read_bytes() for path in (root / descriptor["key"]).iterdir()} == before


def test_different_archive_root_cannot_borrow_a_key(world, tmp_path):
    candidate, root = world
    capture, fact, first, _ = frozen(world)
    other = tmp_path / "other-cache"
    assert finish(capture, candidate, other) == fact
    second = capture.archive_descriptor()
    assert second["key"] != first["key"]
    assert (
        inputs.verify_archive(second, fact, archive_root=root, limits=candidate["limits"]) is None
    )
    assert (
        inputs.verify_archive(first, fact, archive_root=other, limits=candidate["limits"]) is None
    )


def test_missing_cache_is_rebuilt_only_after_the_original_baseline_matches(world):
    candidate, root = world
    capture, fact, first, archive = frozen(world)
    archive.resources[0].path.unlink()
    assert finish(capture, candidate, root) == fact
    second = capture.archive_descriptor()
    assert second["key"] != first["key"]
    assert inputs.verify_archive(second, fact, archive_root=root, limits=candidate["limits"])
    assert (root / first["key"]).is_dir()


@pytest.mark.parametrize("failure", ["write", "fsync", "graph", "truncated"])
def test_materialization_failure_cleans_only_the_new_attempt(world, monkeypatch, failure):
    candidate, root = world
    _, fact, old, archive = frozen(world)
    old_bytes = {row.path: row.path.read_bytes() for row in archive.resources}
    capture = begin(candidate)
    if failure == "write":
        monkeypatch.setattr(
            inputs.os, "write", lambda *_args: (_ for _ in ()).throw(OSError("full"))
        )
    elif failure == "fsync":
        monkeypatch.setattr(
            inputs.os, "fsync", lambda *_args: (_ for _ in ()).throw(OSError("sync"))
        )
    elif failure == "graph":
        monkeypatch.setattr(
            inputs._ArchiveWriter,
            "finish",
            lambda *_args: (_ for _ in ()).throw(OSError("metadata")),
        )
    else:
        original = inputs._ArchiveSink.write
        monkeypatch.setattr(
            inputs._ArchiveSink, "write", lambda self, chunk: original(self, chunk[:-1])
        )
    assert finish(capture, candidate, root) is None
    assert capture.archive_descriptor() is None
    assert list(root.iterdir()) == [root / old["key"]]
    assert {path: path.read_bytes() for path in old_bytes} == old_bytes
    assert inputs.validate_fact(fact) is not None


def test_existing_nonce_is_never_overwritten(world, monkeypatch):
    candidate, root = world
    _, _, descriptor, _ = frozen(world)
    before = {path: path.read_bytes() for path in (root / descriptor["key"]).iterdir()}
    monkeypatch.setattr(inputs.secrets, "token_hex", lambda _size: descriptor["key"])
    capture = begin(candidate)
    assert finish(capture, candidate, root) is None
    assert {path: path.read_bytes() for path in before} == before


def test_archive_root_file_is_not_replaced_and_fact_is_not_promoted(world):
    candidate, root = world
    root.write_bytes(b"owned existing file")
    capture = begin(candidate)
    assert finish(capture, candidate, root) is None
    assert root.read_bytes() == b"owned existing file"
    assert capture.archive_descriptor() is None


@pytest.mark.parametrize("mutation", ["extra", "missing", "bytes", "graph", "directory"])
def test_archive_requires_exact_members_and_actual_bytes(world, mutation):
    candidate, root = world
    _, fact, descriptor, archive = frozen(world)
    directory = root / descriptor["key"]
    if mutation == "extra":
        (directory / "extra").write_bytes(b"unclaimed")
    elif mutation == "missing":
        archive.resources[0].path.unlink()
    elif mutation == "bytes":
        path = archive.resources[0].path
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    elif mutation == "graph":
        (directory / "graph.json").write_bytes(archive.graph_bytes + b" ")
    else:
        (directory / "extra-directory").mkdir()
    assert (
        inputs.verify_archive(descriptor, fact, archive_root=root, limits=candidate["limits"])
        is None
    )


@pytest.mark.parametrize("limit", ["bytes", "files", "directories", "bool", "extra"])
def test_verification_budget_counts_logical_inputs_and_empty_directories(world, limit):
    candidate, root = world
    _, fact, descriptor, _ = frozen(world)
    graph = fact["declaration"]
    contents = [graph["script"], *graph["files"], *graph["prefixes"][0]["members"]]
    limits = dict(candidate["limits"])
    if limit == "bytes":
        limits["max_bytes"] = sum(row["size_bytes"] for row in contents) - 1
    elif limit == "files":
        limits["max_files"] = len(contents) - 1
    elif limit == "directories":
        limits["max_files"] = len(contents)  # Physical blobs fit; the empty dirs do not.
    elif limit == "bool":
        limits["max_files"] = True
    else:
        limits["extra"] = 1
    assert inputs.verify_archive(descriptor, fact, archive_root=root, limits=limits) is None


@pytest.mark.parametrize("mutation", ["path", "key", "version", "revision", "fact"])
def test_descriptor_is_closed_and_bound_to_the_actual_fact_without_io(world, monkeypatch, mutation):
    _, _, descriptor, _ = frozen(world)
    _, fact, _, _ = frozen(world)
    bad = dict(descriptor)
    if mutation == "path":
        bad["path"] = "/outside"
    elif mutation == "key":
        bad["key"] = "../" + "0" * 48
    elif mutation == "version":
        bad["version"] = True
    elif mutation == "revision":
        bad["revision"] = "sha256:" + "0" * 64
    else:
        fact["declaration"]["script"]["size_bytes"] += 1
    monkeypatch.setattr(inputs, "_regular", lambda *_a, **_k: pytest.fail("metadata did IO"))
    assert inputs.validate_archive_descriptor(bad, fact=fact) is None


def test_graph_validator_has_no_execution_or_resource_io_and_is_detached(world, monkeypatch):
    candidate, _ = world
    capture = begin(candidate)
    fact = finish(capture, candidate)
    graph = fact["declaration"]
    monkeypatch.setattr(inputs, "_regular", lambda *_a, **_k: pytest.fail("graph did resource IO"))
    monkeypatch.setattr(
        inputs.executionfact, "valid_id", lambda *_a: pytest.fail("graph invented execution")
    )
    checked = inputs.validate_graph(graph, expected_revision=fact["revision"])
    assert checked == graph
    assert hashlib.sha256(inputs.canonical_graph_bytes(graph)).hexdigest() == fact["revision"][7:]
    checked["scope"]["script"] = "other.py"
    assert checked != graph
    invalid = copy.deepcopy(graph)
    invalid["private_path"] = "/outside"
    assert inputs.validate_graph(invalid) is None
    with pytest.raises(ValueError, match="Invalid captured"):
        inputs.canonical_graph_bytes(invalid)


def test_symlink_or_reparse_archive_members_are_rejected(world, monkeypatch):
    candidate, root = world
    _, fact, descriptor, archive = frozen(world)
    original = inputs.os.lstat
    bad_path = archive.resources[0].path

    def reparse(path):
        value = original(path)
        if Path(path) == bad_path:

            class Reparse:
                st_file_attributes = 0x400
                st_mode = value.st_mode

            return Reparse()
        return value

    monkeypatch.setattr(inputs.os, "lstat", reparse)
    assert (
        inputs.verify_archive(descriptor, fact, archive_root=root, limits=candidate["limits"])
        is None
    )
