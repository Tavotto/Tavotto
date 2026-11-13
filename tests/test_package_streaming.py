"""真实 ZIP 资源流式暂存；大普通成员不经过整项 bytes API 或无界文件 read。"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import struct
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from tavotto.engine import (
    atomicio,
    brand,
    chartpackage,
    config,
    documentpackage as packages,
    packagebudget,
)

CHUNK = packages.ARCHIVE_CHUNK_BYTES


def metadata(rows=()):
    return {
        packages.LAYOUT_MEMBER: atomicio.dumps_json({"schema": 4, "canvases": [{"objects": []}]}),
        packages.MANIFEST_MEMBER: atomicio.dumps_json(
            {
                "kind": brand.PACKAGE_KIND,
                "version": 1,
                "document_format": "v4",
                "assets": list(rows),
            }
        ),
    }


def archive(data=b"streamed\x00payload", *, compression=zipfile.ZIP_DEFLATED):
    name = "assets/data.bin"
    row = {
        "kind": "file",
        "id": "data.bin",
        "path": name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    members = {**metadata([row]), name: data}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as output:
        for member, value in members.items():
            output.writestr(member, value)
    budget = packagebudget.output_budget({member: len(value) for member, value in members.items()})
    return buffer.getvalue(), budget, name


@pytest.mark.parametrize(
    "data",
    [b"", b"quotes\r\n\x00" + "中文".encode(), b"a" * (CHUNK * 3 + 7)],
    ids=["empty", "unicode-nul", "multiple-chunks"],
)
def test_copy_and_existing_bytes_api_deliver_identical_exact_payload_and_close_targets(data):
    raw, budget, name = archive(data)
    events, received = [], {}

    @contextmanager
    def target(member):
        events.append(("open", member))
        try:
            with io.BytesIO() as stream:
                yield stream
                received[member] = stream.getvalue()
        finally:
            events.append(("close", member))

    upload = io.BytesIO(raw)
    with packages.open_package(upload, budget=budget) as ready:
        assert ready.copy_required({name: max(1, len(data))}, targets=target) is None
    assert not upload.closed
    assert received == {name: data}
    assert events == [("open", name), ("close", name)]
    with packages.open_package(raw, budget=budget) as ready:
        assert dict(ready.read_required({name: max(1, len(data))})) == received


@pytest.mark.parametrize("fault", ["metadata", "missing", "zero", "unknown", "not_mapping"])
def test_copy_checks_the_complete_required_selection_before_opening_any_sink(fault):
    raw, budget, name = archive()
    calls = []

    @contextmanager
    def target(member):
        calls.append(member)
        yield io.BytesIO()

    needed = {
        "metadata": {name: 100, packages.LAYOUT_MEMBER: 100},
        "missing": {name: 100, "assets/not-carried": 100},
        "zero": {name: 0},
        "unknown": {},
        "not_mapping": [],
    }[fault]
    with packages.open_package(raw, budget=budget) as ready:
        with pytest.raises(packages.PackageError):
            ready.copy_required(needed, targets=target)
    assert calls == []


@pytest.mark.parametrize("returned", [0, 1, None, True, -1, CHUNK + 1])
def test_partial_or_invalid_sink_writes_are_never_reported_as_success(returned):
    raw, budget, name = archive(b"a" * (CHUNK + 17))
    events = []

    class Sink:
        def write(self, data):
            assert len(data) <= CHUNK
            events.append("write")
            return returned

    @contextmanager
    def target(_name):
        try:
            yield Sink()
        finally:
            events.append("closed")

    with packages.open_package(raw, budget=budget) as ready:
        with pytest.raises(packages.PackageError) as error:
            ready.copy_required({name: CHUNK + 17}, targets=target)
    assert error.value.reason == "invalid_output"
    assert events == ["write", "closed"]


def test_local_sink_disk_error_keeps_its_identity_and_closes_the_real_zip_reader():
    raw, budget, name = archive()
    problem = OSError(errno.ENOSPC, "test staging volume full")
    closed = []

    class Sink:
        def write(self, _data):
            raise problem

    @contextmanager
    def target(_name):
        try:
            yield Sink()
        finally:
            closed.append(True)

    upload = io.BytesIO(raw)
    with packages.open_package(upload, budget=budget) as ready:
        with pytest.raises(OSError) as error:
            ready.copy_required({name: 100}, targets=target)
        assert error.value is problem
    assert closed == [True] and not upload.closed


def test_copy_uses_existing_member_and_actual_cumulative_expansion_budgets():
    data = b"a" * (CHUNK + 17)
    raw, budget, name = archive(data)

    @contextmanager
    def target(_name):
        with io.BytesIO() as stream:
            yield stream

    with packages.open_package(raw, budget=budget) as ready:
        with pytest.raises(packages.PackageError) as error:
            ready.copy_required({name: len(data) - 1}, targets=target)
        assert error.value.reason == "member_byte_budget"
        ready.copy_required({name: len(data)}, targets=target)
        with pytest.raises(packages.PackageError) as error:
            ready.copy_required({name: len(data)}, targets=target)
        assert error.value.reason == "expanded_budget"


def damaged_member(raw, name):
    with zipfile.ZipFile(io.BytesIO(raw)) as opened:
        entry = opened.getinfo(name)
        filename, extra = struct.unpack_from("<HH", raw, entry.header_offset + 26)
        offset = entry.header_offset + zipfile.sizeFileHeader + filename + extra
    changed = bytearray(raw)
    changed[offset + CHUNK + 2] ^= 1
    return bytes(changed)


@pytest.mark.parametrize("fault", ["crc", "truncated"])
def test_actual_corrupt_or_truncated_zip_never_yields_prepared_resources(
    tmp_path, monkeypatch, fault
):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "runtime"))
    raw, budget, name = archive(b"a" * (CHUNK * 3 + 7), compression=zipfile.ZIP_STORED)
    raw = damaged_member(raw, name) if fault == "crc" else raw[:-20]
    with pytest.raises(packages.PackageError):
        with chartpackage.inspect_chart_package(raw, budget=budget):
            pytest.fail("damaged ZIP became a prepared package")
    assert not list(config.data_dir().glob("chart-package-*"))


@pytest.mark.parametrize("kind", ["file", "script", "figure_snapshot"])
def test_large_real_member_and_prepared_rechecks_never_collect_or_read_the_whole_resource(
    tmp_path, monkeypatch, kind
):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "runtime"))
    source = tmp_path / "large-source.bin"
    pattern = bytes(range(256)) * (CHUNK // 256)
    digest = hashlib.sha256()
    with source.open("wb") as stream:
        for _ in range(320):
            stream.write(pattern)
            digest.update(pattern)
    size = source.stat().st_size
    name = "scripts/large.py" if kind == "script" else "assets/large.bin"
    row = {"kind": kind, "path": name, "sha256": digest.hexdigest(), "bytes": size}
    if kind in ("file", "script"):
        row["id"] = name.partition("/")[2]
    members = metadata([row])
    path = tmp_path / "large.tavotto"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        for member, data in members.items():
            output.writestr(member, data)
        output.write(source, name)
    budget = packagebudget.output_budget(
        {**{member: len(data) for member, data in members.items()}, name: size}
    )
    zip_reads, staged_reads = [], []
    original_read, original_fdopen, original_collect = (
        zipfile.ZipExtFile.read,
        os.fdopen,
        packages._Archive.read,
    )

    def read_zip(stream, n=-1):
        assert 0 < n <= CHUNK, "whole ZIP member read bypassed the chunk budget"
        zip_reads.append(n)
        return original_read(stream, n)

    def collect(archive, member, limit, *, collect=True):
        assert member != name or not collect, "large resource went through the full-bytes API"
        return original_collect(archive, member, limit, collect=collect)

    class CheckedFile:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, n=-1):
            assert 0 < n <= CHUNK, "whole staged resource read bypassed the chunk budget"
            staged_reads.append(n)
            return self.stream.read(n)

    def fdopen(fd, mode="r", *args, **kwargs):
        stream = original_fdopen(fd, mode, *args, **kwargs)
        return CheckedFile(stream) if mode == "rb" else stream

    monkeypatch.setattr(zipfile.ZipExtFile, "read", read_zip)
    monkeypatch.setattr(os, "fdopen", fdopen)
    monkeypatch.setattr(packages._Archive, "read", collect)
    with (
        path.open("rb") as upload,
        chartpackage.inspect_chart_package(upload, budget=budget) as ready,
    ):
        resource = ready.resources[0]
        assert resource.source.name.isdecimal() and resource.source.stat().st_size == size
        assert resource.record["sha256"] == digest.hexdigest()
        plan = chartpackage.plan_project_resources(ready)
        assert plan[0][0] == name.partition("/")[2]
    assert zip_reads and staged_reads
    assert max(zip_reads) <= CHUNK and max(staged_reads) <= CHUNK
    assert not list(config.data_dir().glob("chart-package-*"))


@pytest.mark.parametrize("fault", ["bytes", "truncate", "replace"])
def test_existing_prepared_source_integrity_guards_still_reject_after_stage_tampering(
    tmp_path, monkeypatch, fault
):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "runtime"))
    data = b"a" * (CHUNK + 19)
    raw, budget, _name = archive(data)
    with chartpackage.inspect_chart_package(raw, budget=budget) as ready:
        path = ready.resources[0].source
        if fault == "replace":
            replacement = path.with_suffix(".changed")
            replacement.write_bytes(b"b" * len(data))
            os.replace(replacement, path)
        else:
            path.write_bytes(b"b" * len(data) if fault == "bytes" else b"short")
        with pytest.raises(packages.PackageError) as error:
            chartpackage.plan_project_resources(ready)
        assert error.value.reason == "resource_mismatch"
    assert not list(config.data_dir().glob("chart-package-*"))


def test_staging_publish_failure_preserves_project_and_cleans_its_private_files(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "runtime"))
    untouched = tmp_path / "project"
    untouched.mkdir()
    (untouched / "kept").write_bytes(b"old user data")
    raw, budget, _name = archive(b"a" * (CHUNK + 17))
    problem = OSError(errno.ENOSPC, "staging disk full")
    calls = []

    def fail(source, destination):
        calls.append((Path(source), Path(destination)))
        raise problem

    monkeypatch.setattr(atomicio, "publish_file_if_absent", fail)
    with pytest.raises(OSError) as error:
        with chartpackage.inspect_chart_package(raw, budget=budget):
            pytest.fail("failed staging publication became prepared")
    assert error.value is problem and len(calls) == 1
    assert (untouched / "kept").read_bytes() == b"old user data"
    assert not list(config.data_dir().glob("chart-package-*"))


def test_streaming_staging_still_rejects_declared_digest_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "runtime"))
    raw, _budget, name = archive()
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        members = {member: source.read(member) for member in source.namelist()}
    manifest = json.loads(members[packages.MANIFEST_MEMBER])
    manifest["assets"][0]["sha256"] = "0" * 64
    members[packages.MANIFEST_MEMBER] = atomicio.dumps_json(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for member, data in members.items():
            target.writestr(member, data)
    budget = packagebudget.output_budget({member: len(data) for member, data in members.items()})
    with pytest.raises(packages.PackageError) as error:
        with chartpackage.inspect_chart_package(output.getvalue(), budget=budget):
            pytest.fail("declared resource hash was trusted without reading its bytes")
    assert error.value.reason == "resource_mismatch"
    assert name.startswith("assets/")
    assert not list(config.data_dir().glob("chart-package-*"))


def test_copy_preserves_crc_check_even_when_its_target_accepts_every_write():
    raw, budget, name = archive(b"a" * (CHUNK * 3 + 7), compression=zipfile.ZIP_STORED)
    raw = damaged_member(raw, name)
    written = []

    class Sink:
        def write(self, data):
            written.append(len(data))
            return len(data)

    @contextmanager
    def target(_name):
        yield Sink()

    with packages.open_package(raw, budget=budget) as ready:
        with pytest.raises(packages.PackageError) as error:
            ready.copy_required({name: CHUNK * 3 + 7}, targets=target)
    assert error.value.reason == "invalid_member" and written
