"""ZIP 资源选择/预算与原子发布；不以安装桩或科学计算替身证明 HTTP 恢复。"""

from __future__ import annotations

import errno
import io
import json
import stat
import subprocess
import sys
import tempfile
import threading
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from pathlib import Path

import pytest

from tavotto.engine import atomicio, brand, documentpackage as packages, documents


@pytest.fixture
def budget():
    return packages.PackageBudget(
        max_archive_bytes=1024 * 1024,
        max_members=32,
        max_directory_bytes=64 * 1024,
        max_total_bytes=256 * 1024,
        max_layout_bytes=64 * 1024,
        max_manifest_bytes=8 * 1024,
    )


def _members():
    return {
        "layout.json": json.dumps(
            {"schema": 4, "canvases": [{"id": "c1", "objects": []}]}
        ).encode(),
        "package_manifest.json": json.dumps(
            {"kind": brand.PACKAGE_KIND, "version": 1, "document_format": "v4"}
        ).encode(),
    }


def _zip(members=None, *, extra=(), compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w", compression) as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, data in [*(members or _members()).items(), *extra]:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_reader_returns_exact_requested_bytes_without_owning_the_upload_stream(tmp_path, budget):
    payload = b'outer SVG\r\n"quoted data"\t\x00'
    source = io.BytesIO(_zip(extra=[("assets/tavottofile/chart_sources/a.svg", payload)]))
    before = list(tmp_path.iterdir())
    with packages.open_package(source, budget=budget) as package:
        assert package.document["schema"] == 4
        assert package.manifest["kind"] == brand.PACKAGE_KIND
        assert dict(package.read_required({"assets/tavottofile/chart_sources/a.svg": 100})) == {
            "assets/tavottofile/chart_sources/a.svg": payload
        }
    assert not source.closed and list(tmp_path.iterdir()) == before


@pytest.mark.parametrize("field", [field.name for field in fields(packages.PackageBudget)])
@pytest.mark.parametrize("bad", [0, True])
def test_every_budget_is_an_explicit_positive_integer(budget, field, bad):
    with pytest.raises(packages.PackageError, match="positive integers"):
        replace(budget, **{field: bad})


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "other"},
        {"version": 2},
        {"version": True},
        {"version": None},
        {"document_format": "legacy"},
        {"extra": 1},
        {"assets": {}},
        {"scripts": None},
        {"figure_snapshots": True},
        {"figure_snapshots": -1},
        {"svg_snapshot_warning": "yes"},
        {"assets": [None]},
        {"scripts": [4]},
        {"runtime_assets": [True]},
        {"svg_snapshot_warnings": [{}]},
        {"page": []},
    ],
)
def test_strict_v4_envelope_rejects_unknown_or_malformed_fields(budget, change):
    members = _members()
    manifest = json.loads(members["package_manifest.json"])
    manifest.update(change)
    members["package_manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(packages.PackageError):
        with packages.open_package(_zip(members), budget=budget):
            pytest.fail("invalid envelope was admitted")


@pytest.mark.parametrize("missing", ["kind", "version", "document_format"])
def test_envelope_cannot_omit_a_required_field(budget, missing):
    members = _members()
    manifest = json.loads(members["package_manifest.json"])
    del manifest[missing]
    members["package_manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(packages.PackageError):
        with packages.open_package(_zip(members), budget=budget):
            pytest.fail("incomplete envelope was admitted")


@pytest.mark.parametrize(
    "name,payload",
    [
        ("layout.json", b'{"schema":4,"schema":3,"canvases":[{}]}'),
        (
            "package_manifest.json",
            b'{"kind":"wrong","kind":"tavotto-package","version":1,"document_format":"v4"}',
        ),
        ("layout.json", b'{"schema":4,"canvases":[{}],"bad":NaN}'),
        ("layout.json", b'{"schema":4,"canvases":[{}],"bad":1e400}'),
    ],
)
def test_json_ambiguity_and_nonfinite_data_are_rejected(budget, name, payload):
    members = _members()
    members[name] = payload
    with pytest.raises(packages.PackageError):
        with packages.open_package(_zip(members), budget=budget):
            pytest.fail("ambiguous JSON was admitted")


def test_layout_validation_delegates_to_documents_and_preserves_caller_errors(budget, monkeypatch):
    calls = []
    original = documents.validate_chart_document

    def validate(raw, **kwargs):
        calls.append(raw)
        return original(raw, **kwargs)

    monkeypatch.setattr(documents, "validate_chart_document", validate)
    with packages.open_package(_zip(), budget=budget) as package:
        assert calls == [package.document]
        error = documents.DocumentError("chart_source_mismatch", "caller resource check")
    # with 内的消费者异常不允许被 contextmanager 改包成 ZIP 错误。
    with pytest.raises(documents.DocumentError) as caught:
        with packages.open_package(_zip(), budget=budget):
            raise error
    assert caught.value is error


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "C:/absolute",
        "assets\\other",
        "assets//a",
        "assets/./a",
        "assets/../a",
        "assets/file.",
        "assets/file ",
        "assets/x:ads",
        "assets/CON.txt",
        "unknown.txt",
    ],
)
def test_paths_are_rejected_before_any_project_write(tmp_path, monkeypatch, budget, name):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "keep"
    marker.write_bytes(b"unchanged")
    raw = _zip(extra=[(name, b"untrusted")])
    if "\\" in name:
        # Windows ZipInfo 写出时先规范化 os.sep；反例必须落进 ZIP 的真实名字。
        encoded = name.replace("\\", "/").encode()
        assert raw.count(encoded) == 2
        raw = raw.replace(encoded, name.encode())
    with pytest.raises(packages.PackageError):
        with packages.open_package(raw, budget=budget):
            pytest.fail("unsafe path was admitted")
    assert list(tmp_path.iterdir()) == [marker] and marker.read_bytes() == b"unchanged"


@pytest.mark.parametrize(
    "pair",
    [
        ("layout.json", "layout.json"),
        ("assets/A", "assets/a"),
        ("assets/caf\u00e9", "assets/cafe\u0301"),
    ],
)
def test_duplicate_and_cross_platform_alias_members_are_rejected(budget, pair):
    extras = [(name, b"x") for name in pair]
    if pair[0] == "layout.json":
        valid = json.loads(_members()["layout.json"])
        extras = [
            (name, json.dumps({**valid, "name": f"variant-{i}"}).encode())
            for i, name in enumerate(pair)
        ]
    with pytest.raises(packages.PackageError) as caught:
        with packages.open_package(_zip(extra=extras), budget=budget):
            pytest.fail("duplicate archive members were admitted")
    assert caught.value.reason == "duplicate_member"


def test_embedded_nul_cannot_be_hidden_by_zipinfo_filename_truncation(budget):
    raw = _zip(extra=[("assets/xQy", b"value")])
    assert raw.count(b"assets/xQy") == 2
    raw = raw.replace(b"assets/xQy", b"assets/x\x00y")
    with pytest.raises(packages.PackageError) as caught:
        with packages.open_package(raw, budget=budget):
            pytest.fail("NUL name was admitted")
    assert caught.value.reason == "unsafe_member"


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFDIR])
def test_zip_links_and_special_files_are_never_admitted(budget, kind):
    entry = zipfile.ZipInfo("assets/special")
    entry.create_system = 3
    entry.external_attr = (kind | 0o600) << 16
    with pytest.raises(packages.PackageError) as caught:
        with packages.open_package(_zip(extra=[(entry, b"target")]), budget=budget):
            pytest.fail("special member was admitted")
    assert caught.value.reason == "special_member"


def test_unsupported_compression_and_encryption_are_rejected(budget):
    raw = bytearray(_zip())
    central = raw.index(b"PK\x01\x02")
    raw[central + 8] |= 1
    for archive in (bytes(raw), _zip(compression=zipfile.ZIP_BZIP2)):
        with pytest.raises(packages.PackageError) as caught:
            with packages.open_package(archive, budget=budget):
                pytest.fail("unsupported ZIP encoding was admitted")
        assert caught.value.reason == "unsupported_member"


def test_safe_directory_entries_work_but_file_directory_collisions_do_not(budget):
    with packages.open_package(
        _zip(extra=[("assets/", b""), ("assets/a", b"a")]), budget=budget
    ) as package:
        assert dict(package.read_required({"assets/a": 1})) == {"assets/a": b"a"}
    with pytest.raises(packages.PackageError) as caught:
        with packages.open_package(
            _zip(extra=[("assets/a", b"a"), ("assets/a/b", b"b")]), budget=budget
        ):
            pytest.fail("file/directory collision was admitted")
    assert caught.value.reason == "ambiguous_member"


def test_unrequested_resources_are_not_automatically_read_or_installed(budget):
    raw = _zip(
        extra=[("assets/a", b"a"), ("scripts/not-run.py", b"raise RuntimeError('never execute')")]
    )
    with packages.open_package(raw, budget=budget) as package:
        with pytest.raises(packages.PackageError) as caught:
            list(package.read_required({"assets/a": 1}))
        assert caught.value.reason == "unexpected_member"
        assert dict(package.read_required({"assets/a": 1}, exact=False)) == {"assets/a": b"a"}
        with pytest.raises(packages.PackageError):
            list(package.read_required({"assets/missing": 1}, exact=False))


def test_all_archive_metadata_and_expansion_limits_are_checked(budget, monkeypatch):
    members = {**_members(), "assets/a": b"a" * 200}
    raw = _zip(members)
    for limited, reason in (
        (replace(budget, max_archive_bytes=len(raw) - 1), "archive_budget"),
        (replace(budget, max_members=2), "member_budget"),
        (replace(budget, max_total_bytes=sum(map(len, members.values())) - 1), "expanded_budget"),
        (replace(budget, max_layout_bytes=len(members["layout.json"]) - 1), "member_byte_budget"),
        (
            replace(budget, max_manifest_bytes=len(members["package_manifest.json"]) - 1),
            "member_byte_budget",
        ),
    ):
        with pytest.raises(packages.PackageError) as caught:
            with packages.open_package(raw, budget=limited):
                pytest.fail("budget was ignored")
        assert caught.value.reason == reason

    def too_late(_self):
        pytest.fail("central directory allocation already happened before the read cap")

    monkeypatch.setattr(zipfile.ZipFile, "infolist", too_late)
    with pytest.raises(packages.PackageError) as caught:
        with packages.open_package(raw, budget=replace(budget, max_directory_bytes=64)):
            pytest.fail("directory cap was ignored")
    assert caught.value.reason == "directory_budget"


def test_actual_repeated_decompression_consumes_the_total_budget_and_reads_in_chunks(
    budget, monkeypatch
):
    members = {**_members(), "assets/a": b"a" * 100_000}
    raw = _zip(members)
    calls = []
    original = zipfile.ZipExtFile.read

    def read(stream, n=-1):
        calls.append(n)
        return original(stream, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    limited = replace(budget, max_total_bytes=sum(map(len, members.values())) + 1)
    with packages.open_package(raw, budget=limited) as package:
        with pytest.raises(packages.PackageError):
            list(package.read_required({"assets/a": 99_999}))
        assert dict(package.read_required({"assets/a": 100_000}))["assets/a"] == members["assets/a"]
        with pytest.raises(packages.PackageError) as caught:
            list(package.read_required({"assets/a": 100_000}))
        assert caught.value.reason == "expanded_budget"
    assert calls and min(calls) >= 1 and max(calls) <= 64 * 1024


def test_bad_crc_and_broken_zip_leave_the_project_unchanged(tmp_path, monkeypatch, budget):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "old.json"
    marker.write_bytes(b"old document")
    raw = _zip(extra=[("assets/a", b"unique-PAYLOAD-value")], compression=zipfile.ZIP_STORED)
    assert raw.count(b"unique-PAYLOAD-value") == 1
    corrupt = raw.replace(b"unique-PAYLOAD-value", b"unique-CHANGED-value")
    with packages.open_package(corrupt, budget=budget) as package:
        with pytest.raises(packages.PackageError) as caught:
            list(package.read_required({"assets/a": 100}))
        assert caught.value.reason == "invalid_member"
    with pytest.raises(packages.PackageError):
        with packages.open_package(b"not a ZIP", budget=budget):
            pytest.fail("invalid ZIP was admitted")
    assert list(tmp_path.iterdir()) == [marker] and marker.read_bytes() == b"old document"


def test_writer_preserves_legacy_metadata_bytes_and_regular_source_contents(tmp_path, budget):
    members = _members()
    members["layout.json"] = b'{"schema":2,"objects":[]}\r\n'
    members["package_manifest.json"] = b'{"kind":"tavotto-package","version":1}\n'
    source = tmp_path / "input.svg"
    source.write_bytes(b"exact input bytes\r\n")
    members["assets/input.svg"] = source
    target = tmp_path / "result.tavotto"
    packages.write_package(target, members, budget=budget)
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == list(members)
        for name, value in members.items():
            assert archive.read(name) == (value.read_bytes() if isinstance(value, Path) else value)
    assert {p.name for p in tmp_path.iterdir()} == {"result.tavotto", "input.svg"}


def test_new_writer_manifest_enospc_keeps_the_preexisting_valid_archive(
    tmp_path, budget, monkeypatch
):
    target = tmp_path / "same-second.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    original = zipfile.ZipFile.open
    intercepted = []

    def fail_manifest(archive, name, mode="r", *args, **kwargs):
        if name == "package_manifest.json" and mode == "w":
            intercepted.append(True)
            raise OSError(errno.ENOSPC, "full disk while writing manifest")
        return original(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fail_manifest)
    with pytest.raises(OSError) as caught:
        packages.write_package(target, _members(), budget=budget)
    assert caught.value.errno == errno.ENOSPC and intercepted == [True]
    assert target.read_bytes() == before
    assert list(tmp_path.iterdir()) == [target]


def test_writer_reopens_the_sealed_zip_and_refuses_corrupted_contents(
    tmp_path, budget, monkeypatch
):
    target = tmp_path / "keep.tavotto"
    target.write_bytes(b"old package bytes")
    original = zipfile.ZipFile.close
    intercepted = []

    def corrupt_after_seal(archive):
        writing = archive.mode == "w" and archive.fp is not None
        original(archive)
        if writing:
            (temporary,) = tmp_path.glob("*.tmp")
            data = bytearray(temporary.read_bytes())
            assert data.startswith(b"PK\x03\x04")
            offset = (
                30 + int.from_bytes(data[26:28], "little") + int.from_bytes(data[28:30], "little")
            )
            data[offset] ^= 0x40
            temporary.write_bytes(data)
            intercepted.append(True)

    monkeypatch.setattr(zipfile.ZipFile, "close", corrupt_after_seal)
    with pytest.raises(packages.PackageError):
        packages.write_package(target, _members(), budget=budget)
    assert intercepted == [True]
    assert target.read_bytes() == b"old package bytes" and list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("fault", ["output_budget", "source_directory", "publish"])
def test_writer_failure_before_commit_preserves_target_and_cleans_temp(
    tmp_path, budget, monkeypatch, fault
):
    target = tmp_path / "package.tavotto"
    target.write_bytes(b"old package")
    members = _members()
    if fault == "output_budget":
        budget = replace(budget, max_archive_bytes=50)
    elif fault == "source_directory":
        members["assets/invalid"] = tmp_path
    else:

        def fail_publish(_src, _dest):
            raise atomicio.AtomicWriteError("replace_failed", "test fault", target)

        monkeypatch.setattr(atomicio, "publish_file", fail_publish)
    with pytest.raises((packages.PackageError, atomicio.AtomicWriteError)):
        packages.write_package(target, members, budget=budget)
    assert target.read_bytes() == b"old package" and list(tmp_path.iterdir()) == [target]


def test_writer_explicit_no_clobber_never_replaces_existing_archive(tmp_path, budget):
    target = tmp_path / "package.tavotto"
    packages.write_package(target, _members(), budget=budget, overwrite=False)
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        packages.write_package(
            target, {**_members(), "assets/a": b"new"}, budget=budget, overwrite=False
        )
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_staged_package_consumes_short_lived_helper_files_and_preserves_legacy_bytes(
    tmp_path, budget, monkeypatch
):
    target = tmp_path / "same-second.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    layout = '{"schema":2,"title":"中文引号\\"与换行\\n"}\r\n'
    manifest = b'{"kind":"tavotto-package","version":1}\r\n'
    payload = b"exact helper SVG\x00\r\n"
    with packages.staged_package(target, budget=budget) as archive:
        with tempfile.TemporaryDirectory(dir=tmp_path) as helper_directory:
            source = Path(helper_directory) / "temporary.svg"
            source.write_bytes(payload)
            with monkeypatch.context() as patch:
                patch.setattr(Path, "read_bytes", lambda _path: pytest.fail("whole-file read"))
                archive.write(str(source), "assets/figure_snapshots/temporary.svg")
        assert not source.exists()
        assert target.read_bytes() == before
        archive.writestr("layout.json", layout)
        archive.writestr("package_manifest.json", manifest)
    with zipfile.ZipFile(target) as archive:
        assert archive.read("assets/figure_snapshots/temporary.svg") == payload
        assert archive.read("layout.json") == layout.encode("utf-8")
        assert archive.read("package_manifest.json") == manifest
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize(
    "first,second,reason",
    [
        ("assets/A.svg", "assets/a.svg", "duplicate_member"),
        ("assets/e\u0301.svg", "assets/é.svg", "duplicate_member"),
        ("assets/a", "assets/a/b", "ambiguous_member"),
        ("assets/a/b", "assets/a", "ambiguous_member"),
        ("assets/a", "assets/../a", "unsafe_member"),
    ],
)
def test_staged_members_are_checked_incrementally_and_swallowed_error_still_aborts(
    tmp_path, budget, monkeypatch, first, second, reason
):
    target = tmp_path / "keep.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    copied = []
    original = packages._copy_member

    def observe(archive, name, *args):
        copied.append(name)
        return original(archive, name, *args)

    monkeypatch.setattr(packages, "_copy_member", observe)
    with pytest.raises(packages.PackageError) as outer:
        with packages.staged_package(target, budget=budget) as archive:
            for name, data in _members().items():
                archive.writestr(name, data)
            archive.writestr(first, b"one")
            try:
                archive.writestr(second, b"two")
            except packages.PackageError as caught:
                assert caught.reason == reason
            else:
                pytest.fail("bad incremental member was admitted")
    assert outer.value.reason == reason
    assert copied == [*_members(), first]
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_staged_member_count_is_checked_before_opening_the_next_source(tmp_path, budget):
    target = tmp_path / "keep.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    with pytest.raises(packages.PackageError) as caught:
        with packages.staged_package(target, budget=replace(budget, max_members=2)) as archive:
            for name, data in _members().items():
                archive.writestr(name, data)
            archive.write(tmp_path / "does-not-exist", "assets/never-read")
    assert caught.value.reason == "member_budget"
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_staged_actual_source_reads_stop_at_remaining_budget_not_after_crc(
    tmp_path, budget, monkeypatch
):
    target = tmp_path / "keep.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    source = tmp_path / "large.svg"
    source.write_bytes(b"x" * (256 * 1024))
    metadata_bytes = sum(map(len, _members().values()))
    limited = replace(budget, max_total_bytes=metadata_bytes + 17)
    reads = []
    original = packages.os.fdopen

    class Observed:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size):
            value = self.stream.read(size)
            reads.append((size, len(value)))
            return value

    monkeypatch.setattr(packages.os, "fdopen", lambda *a, **k: Observed(original(*a, **k)))
    with pytest.raises(packages.PackageError) as caught:
        with packages.staged_package(target, budget=limited) as archive:
            for name, data in _members().items():
                archive.writestr(name, data)
            archive.write(source, "assets/large.svg")
    assert caught.value.reason == "expanded_budget"
    assert reads == [(18, 18)]
    assert target.read_bytes() == before
    assert set(tmp_path.iterdir()) == {target, source}


def test_staged_utf8_metadata_limit_counts_bytes(tmp_path, budget):
    target = tmp_path / "keep.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    with pytest.raises(packages.PackageError) as caught:
        with packages.staged_package(
            target, budget=replace(budget, max_layout_bytes=10)
        ) as archive:
            archive.writestr("layout.json", "中文测试")
    assert caught.value.reason == "expanded_budget"
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_staged_helper_swallowed_enospc_never_publishes_partial_zip(tmp_path, budget, monkeypatch):
    target = tmp_path / "same-second.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    original = zipfile.ZipFile.open

    def fail(archive, name, mode="r", *args, **kwargs):
        if name == "assets/unwritten.svg" and mode == "w":
            raise OSError(errno.ENOSPC, "source helper output full")
        return original(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fail)
    with pytest.raises(OSError) as caught:
        with packages.staged_package(target, budget=budget) as archive:
            for name, data in _members().items():
                archive.writestr(name, data)
            try:
                archive.writestr("assets/unwritten.svg", b"needed source")
            except OSError as swallowed:
                assert swallowed.errno == errno.ENOSPC
    assert caught.value.errno == errno.ENOSPC
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_staged_body_exception_preserves_complete_target(tmp_path, budget):
    target = tmp_path / "keep.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="caller failed"):
        with packages.staged_package(target, budget=budget) as archive:
            for name, data in _members().items():
                archive.writestr(name, data)
            raise RuntimeError("caller failed before manifest completion")
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_staged_output_without_both_metadata_members_never_publishes(tmp_path, budget):
    target = tmp_path / "keep.tavotto"
    packages.write_package(target, _members(), budget=budget)
    before = target.read_bytes()
    with pytest.raises(packages.PackageError) as caught:
        with packages.staged_package(target, budget=budget) as archive:
            archive.writestr("layout.json", b"layout")
    assert caught.value.reason == "missing_metadata"
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_staged_no_clobber_concurrent_helpers_have_one_complete_winner(tmp_path, budget):
    target = tmp_path / "winner.tavotto"
    barrier = threading.Barrier(4)

    def write(index):
        try:
            with packages.staged_package(target, budget=budget, overwrite=False) as archive:
                for name, data in _members().items():
                    archive.writestr(name, data)
                archive.writestr("assets/winner", str(index))
                barrier.wait(timeout=10)
            return index
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        winners = [winner for winner in executor.map(write, range(4)) if winner is not None]
    assert len(winners) == 1
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert archive.read("assets/winner") == str(winners[0]).encode()
    assert list(tmp_path.iterdir()) == [target]


def test_staged_closed_context_rejects_additions_without_mutating_published_zip(tmp_path, budget):
    target = tmp_path / "complete.tavotto"
    with packages.staged_package(target, budget=budget) as archive:
        for name, data in _members().items():
            archive.writestr(name, data)
    before = target.read_bytes()
    with pytest.raises(packages.PackageError) as caught:
        archive.writestr("assets/late", b"late")
    assert caught.value.reason == "closed_output"
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_publish_file_if_absent_orders_fsync_and_atomic_publication(tmp_path, monkeypatch):
    source, target = tmp_path / "staged.tmp", tmp_path / "final.bin"
    source.write_bytes(b"complete staged bytes")
    calls = []
    original_file, original_publish, original_dir = (
        atomicio.fsync_file,
        atomicio._publish_if_absent,
        atomicio.fsync_dir,
    )

    def sync_file(path):
        calls.append("file")
        original_file(path)

    def publish(src, dest):
        assert calls == ["file"]
        calls.append("publish")
        original_publish(src, dest)

    def sync_dir(path):
        calls.append("directory")
        original_dir(path)

    monkeypatch.setattr(atomicio, "fsync_file", sync_file)
    monkeypatch.setattr(atomicio, "_publish_if_absent", publish)
    monkeypatch.setattr(atomicio, "fsync_dir", sync_dir)
    atomicio.publish_file_if_absent(source, target)
    assert calls == ["file", "publish", "directory"]
    assert target.read_bytes() == b"complete staged bytes" and not source.exists()


@pytest.mark.parametrize("fault", ["file_fsync", "native_publish", "directory_fsync", "collision"])
def test_publish_file_if_absent_failure_contract_and_cleanup(tmp_path, monkeypatch, fault):
    source, target = tmp_path / "staged.tmp", tmp_path / "final.bin"
    source.write_bytes(b"new complete bytes")
    if fault == "collision":
        target.write_bytes(b"old immutable bytes")
        expected = FileExistsError
    else:
        expected = atomicio.AtomicWriteError

        def fail(*_args):
            raise OSError(errno.EIO, "injected I/O failure")

        name = {
            "file_fsync": "fsync_file",
            "native_publish": "_publish_if_absent",
            "directory_fsync": "fsync_dir",
        }[fault]
        monkeypatch.setattr(atomicio, name, fail)
    with pytest.raises(expected) as caught:
        atomicio.publish_file_if_absent(source, target)
    assert not source.exists()
    if fault == "collision":
        assert target.read_bytes() == b"old immutable bytes"
    elif fault == "directory_fsync":
        assert (
            target.read_bytes() == b"new complete bytes" and caught.value.code == "dir_fsync_failed"
        )
    else:
        assert not target.exists()
        assert caught.value.code == ("write_failed" if fault == "file_fsync" else "replace_failed")


def test_publish_file_if_absent_cannot_delete_its_own_destination(tmp_path):
    path = tmp_path / "keep.bin"
    path.write_bytes(b"keep")
    with pytest.raises(ValueError):
        atomicio.publish_file_if_absent(path, path)
    assert path.read_bytes() == b"keep"


def test_concurrent_complete_staged_files_have_one_real_no_clobber_winner(tmp_path):
    target = tmp_path / "winner.bin"
    barrier = threading.Barrier(8)
    payloads = [bytes([i]) * (1000 + i) for i in range(8)]

    def publish(index):
        staged = tmp_path / f"input-{index}.tmp"
        staged.write_bytes(payloads[index])
        barrier.wait(timeout=10)
        try:
            atomicio.publish_file_if_absent(staged, target)
            return index
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        winners = [winner for winner in executor.map(publish, range(8)) if winner is not None]
    assert len(winners) == 1 and target.read_bytes() == payloads[winners[0]]
    assert list(tmp_path.iterdir()) == [target]


def test_concurrent_package_writers_publish_one_complete_zip(tmp_path, budget):
    target = tmp_path / "winner.tavotto"
    barrier = threading.Barrier(4)

    def write(index):
        barrier.wait(timeout=10)
        try:
            packages.write_package(
                target,
                {**_members(), "assets/winner": str(index).encode()},
                budget=budget,
                overwrite=False,
            )
            return index
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        winners = [winner for winner in executor.map(write, range(4)) if winner is not None]
    assert len(winners) == 1
    with packages.open_package(target.read_bytes(), budget=budget) as package:
        assert (
            dict(package.read_required({"assets/winner": 1}))["assets/winner"]
            == str(winners[0]).encode()
        )
    assert list(tmp_path.iterdir()) == [target]


def test_two_processes_publish_one_complete_staged_file(tmp_path):
    root = Path(__file__).resolve().parents[1]
    target, marker = tmp_path / "winner.bin", tmp_path / "go"
    code = """
import pathlib,sys,time
sys.path.insert(0,sys.argv[1])
from tavotto.engine import atomicio
deadline=time.monotonic()+10
while not pathlib.Path(sys.argv[4]).exists():
    assert time.monotonic()<deadline
    time.sleep(.01)
try:
    atomicio.publish_file_if_absent(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]))
except FileExistsError:
    raise SystemExit(17)
"""
    processes = []
    payloads = [b"first writer complete bytes", b"second writer complete bytes"]
    try:
        for i, payload in enumerate(payloads):
            staged = tmp_path / f"staged-{i}.tmp"
            staged.write_bytes(payload)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        code,
                        str(root / "src"),
                        str(staged),
                        str(target),
                        str(marker),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        marker.write_bytes(b"start")
        codes = []
        for process in processes:
            _output, errors = process.communicate(timeout=20)
            assert process.returncode in (0, 17), errors
            codes.append(process.returncode)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    assert sorted(codes) == [0, 17] and target.read_bytes() == payloads[codes.index(0)]
    assert {p.name for p in tmp_path.iterdir()} == {"winner.bin", "go"}


def test_import_is_pure_standard_library(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = "import sys;sys.path.insert(0,sys.argv[1]);from tavotto.engine import documentpackage;assert not any(x in sys.modules for x in ('matplotlib','numpy','PIL','pikepdf','pypdfium2'))"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root / "src")],
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
