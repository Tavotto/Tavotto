"""Successful-write revisions describe this call's exact bytes, even after replacement."""

import ctypes
import errno
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import atomicio, discover, registry


@pytest.mark.parametrize("indent", [None, 0, 1, 4])
def test_write_json_returns_revision_of_exact_serialized_bytes(tmp_path, monkeypatch, indent):
    target = tmp_path / "document.json"
    payload = {"图": [1, 2.5, {"label": "café"}], "empty": None}
    expected = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=indent).encode(
        "utf-8"
    )
    real_dumps = atomicio.dumps_json
    serialized = []

    def capture_dumps(obj, **kwargs):
        data = real_dumps(obj, **kwargs)
        serialized.append(data)
        return data

    monkeypatch.setattr(atomicio, "dumps_json", capture_dumps)
    revision = atomicio.write_json(target, payload, indent=indent)

    assert serialized == [expected]
    assert target.read_bytes() == expected
    assert revision == atomicio.revision_of(expected)


def test_write_json_revision_survives_immediate_external_replacement(tmp_path, monkeypatch):
    """The same process simulates an external writer after the successful atomic write."""
    target = tmp_path / "document.json"
    own_bytes = b'{"owner": "ours"}'
    external_bytes = b'{"owner":"external"}\n'
    real_write = atomicio.write_bytes

    def replace_after_write(path, data):
        real_write(path, data)
        Path(path).write_bytes(external_bytes)

    monkeypatch.setattr(atomicio, "write_bytes", replace_after_write)
    revision = atomicio.write_json(target, {"owner": "ours"})

    assert target.read_bytes() == external_bytes
    assert revision == atomicio.revision_of(own_bytes)
    assert atomicio.content_revision(target) == atomicio.revision_of(external_bytes)


def test_write_config_reports_written_revision_and_keeps_path_return(tmp_path):
    target = registry.registry_path(tmp_path)
    payload = {"version": 1, "scripts": {}}
    expected = atomicio.dumps_json(payload, indent=1)
    written = []

    def on_written(revision):
        written.append((revision, target.read_bytes()))

    returned = discover.write_config(tmp_path, payload, on_written=on_written)

    assert returned == target
    assert written == [(atomicio.revision_of(expected), expected)]
    assert discover.write_config(tmp_path, payload) == target


def test_register_reports_own_revision_after_external_replacement(tmp_path, monkeypatch):
    target = registry.registry_path(tmp_path)
    external_bytes = b'{"version": 1, "scripts": {"external.py": {"stems": []}}}'
    real_write = atomicio.write_bytes
    written_bytes = []
    revisions = []

    def replace_after_write(path, data):
        real_write(path, data)
        written_bytes.append(data)
        Path(path).write_bytes(external_bytes)

    monkeypatch.setattr(atomicio, "write_bytes", replace_after_write)
    cfg = discover.register(
        tmp_path, "figure.py", ["图 B", "图 A"], entry="render", on_written=revisions.append
    )

    assert cfg == {
        "version": 1,
        "scripts": {
            "figure.py": {
                "entry": "render",
                "cost": "medium",
                "notes": "",
                "stems": ["图 A", "图 B"],
            }
        },
    }
    assert written_bytes == [atomicio.dumps_json(cfg, indent=1)]
    assert revisions == [atomicio.revision_of(written_bytes[0])]
    assert target.read_bytes() == external_bytes
    assert atomicio.content_revision(target) == atomicio.revision_of(external_bytes)


@pytest.mark.parametrize("failure", ["serialization", "replace", "dir_fsync"])
def test_register_never_reports_revision_on_failed_write(tmp_path, monkeypatch, failure):
    """Even a replace followed by failed directory fsync is not a successful write."""
    target = registry.registry_path(tmp_path)
    old = {"version": 1, "scripts": {}}
    discover.write_config(tmp_path, old)
    old_bytes = target.read_bytes()
    revisions = []
    notes = ""
    expected_code = {
        "serialization": "non_finite_number",
        "replace": "replace_failed",
        "dir_fsync": "dir_fsync_failed",
    }[failure]
    injected_error = None

    if failure == "serialization":
        notes = float("nan")
    elif failure == "replace":

        def fail_replace(_src, _dst):
            raise OSError(errno.EACCES, "injected replace failure")

        monkeypatch.setattr(atomicio.os, "replace", fail_replace)
    else:
        injected_error = atomicio.AtomicWriteError(
            expected_code, "injected directory fsync failure", tmp_path
        )

        def fail_dir_fsync(_directory):
            raise injected_error

        monkeypatch.setattr(atomicio, "_fsync_dir", fail_dir_fsync)

    with pytest.raises(atomicio.AtomicWriteError) as raised:
        discover.register(tmp_path, "figure.py", ["new"], notes=notes, on_written=revisions.append)

    assert raised.value.code == expected_code
    assert revisions == []
    if failure == "dir_fsync":
        assert raised.value is injected_error
        assert json.loads(target.read_bytes())["scripts"]["figure.py"]["stems"] == ["new"]
    else:
        assert target.read_bytes() == old_bytes
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_if_absent_publishes_a_complete_file_and_never_replaces_it(tmp_path):
    target = tmp_path / "immutable.bin"
    first, second = b"original\x00bytes", b"replacement"
    atomicio.write_bytes_if_absent(target, first)
    before = target.stat()
    with pytest.raises(FileExistsError):
        atomicio.write_bytes_if_absent(target, second)
    assert target.read_bytes() == first
    assert os.path.samestat(before, target.stat())
    assert list(tmp_path.iterdir()) == [target]


def test_write_if_absent_concurrent_writers_have_exactly_one_complete_winner(tmp_path):
    target = tmp_path / "immutable.bin"
    payloads = [bytes([i]) * (10000 + i) for i in range(8)]

    def write(data):
        try:
            atomicio.write_bytes_if_absent(target, data)
            return data
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, payloads))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    assert target.read_bytes() == winners[0]
    assert list(tmp_path.iterdir()) == [target]


def test_write_if_absent_cross_process_writers_do_not_clobber_the_winner(tmp_path):
    target = tmp_path / "immutable.bin"
    gate = tmp_path / "start"
    script = """
import sys,time
from pathlib import Path
from tavotto.engine import atomicio
target,gate,ready=map(Path,sys.argv[1:4])
value=int(sys.argv[4])
ready.touch()
while not gate.exists():
    time.sleep(0.01)
try:
    atomicio.write_bytes_if_absent(target,bytes([value])*10001)
except FileExistsError:
    sys.exit(17)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    processes = []
    try:
        for i in range(4):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(target),
                        str(gate),
                        str(tmp_path / f"ready{i}"),
                        str(i),
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        deadline = time.monotonic() + 15
        while len(list(tmp_path.glob("ready*"))) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(list(tmp_path.glob("ready*"))) == 4
        gate.touch()
        results = []
        for process in processes:
            _, stderr = process.communicate(timeout=15)
            assert process.returncode in (0, 17), stderr.decode(errors="replace")
            results.append(process.returncode)
        assert results.count(0) == 1 and results.count(17) == 3
        winner = results.index(0)
        assert target.read_bytes() == bytes([winner]) * 10001
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()


def test_write_if_absent_syncs_complete_staging_bytes_before_publication(tmp_path, monkeypatch):
    target = tmp_path / "immutable.bin"
    real_publish = atomicio._publish_if_absent
    real_fsync = atomicio.os.fsync
    synced = []

    def fsync(fd):
        synced.append(os.fstat(fd).st_size)
        return real_fsync(fd)

    def publish(src, dest):
        assert src.read_bytes() == b"complete bytes"
        assert len(b"complete bytes") in synced
        assert not target.exists()
        return real_publish(src, dest)

    monkeypatch.setattr(atomicio.os, "fsync", fsync)
    monkeypatch.setattr(atomicio, "_publish_if_absent", publish)
    atomicio.write_bytes_if_absent(target, b"complete bytes")


@pytest.mark.parametrize("failure", ["file_fsync", "publish", "dir_fsync"])
def test_write_if_absent_failure_cleans_staging_and_keeps_complete_targets(
    tmp_path, monkeypatch, failure
):
    target = tmp_path / "immutable.bin"

    def fail(*_):
        raise OSError(errno.EIO, "injected I/O failure")

    if failure == "file_fsync":
        monkeypatch.setattr(atomicio.os, "fsync", fail)
    elif failure == "publish":
        monkeypatch.setattr(atomicio, "_publish_if_absent", fail)
    else:
        monkeypatch.setattr(atomicio, "fsync_dir", fail)
    with pytest.raises(atomicio.AtomicWriteError):
        atomicio.write_bytes_if_absent(target, b"complete bytes")
    assert not list(tmp_path.glob("*.tmp"))
    if failure == "dir_fsync":
        assert target.read_bytes() == b"complete bytes"
    else:
        assert not target.exists()


def test_existing_target_still_requires_directory_durability(tmp_path, monkeypatch):
    target = tmp_path / "immutable.bin"
    target.write_bytes(b"keep")

    def fail(_):
        raise OSError(errno.EIO, "directory not durable")

    monkeypatch.setattr(atomicio, "fsync_dir", fail)
    with pytest.raises(atomicio.AtomicWriteError, match="目录项落盘失败"):
        atomicio.write_bytes_if_absent(target, b"replace")
    assert target.read_bytes() == b"keep"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("error", [errno.EACCES, errno.EIO, errno.ENOSPC])
def test_publish_unknown_link_errors_are_not_retried_as_another_primitive(monkeypatch, error):
    monkeypatch.setattr(sys, "platform", "linux")

    def fail(*_):
        raise OSError(error, "injected link error")

    monkeypatch.setattr(atomicio.os, "link", fail)
    monkeypatch.setattr(
        atomicio, "_rename_exclusive", lambda *_: pytest.fail("未知错误不应改走原生 rename")
    )
    with pytest.raises(OSError) as raised:
        atomicio._publish_if_absent(Path("stage"), Path("target"))
    assert raised.value.errno == error


def test_link_unsupported_uses_exclusive_rename_and_preserves_its_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    def no_link(*_):
        raise OSError(errno.ENOTSUP, "volume has no hard links")

    def exclusive(src, dest):
        calls.append((src, dest))
        raise FileExistsError(errno.EEXIST, "already there")

    monkeypatch.setattr(atomicio.os, "link", no_link)
    monkeypatch.setattr(atomicio, "_rename_exclusive", exclusive)
    with pytest.raises(FileExistsError):
        atomicio._publish_if_absent(Path("stage"), Path("target"))
    assert calls == [(Path("stage"), Path("target"))]


def test_windows_publication_uses_rename_without_requiring_hard_links(tmp_path, monkeypatch):
    if sys.platform != "win32":
        # 这项选择器检查也在POSIX运行；真实Windows no-clobber由上方集成用例验证。
        calls = []
        monkeypatch.setattr(atomicio.os, "rename", lambda src, dest: calls.append((src, dest)))
        monkeypatch.setattr(sys, "platform", "win32")
        atomicio._publish_if_absent(tmp_path / "stage", tmp_path / "target")
        assert calls == [(tmp_path / "stage", tmp_path / "target")]
    else:
        stage, target = tmp_path / "stage", tmp_path / "target"
        stage.write_bytes(b"stage")
        target.write_bytes(b"keep")
        monkeypatch.setattr(atomicio.os, "link", lambda *_: pytest.fail("依赖了硬链接"))
        with pytest.raises(FileExistsError):
            atomicio._publish_if_absent(stage, target)
        assert target.read_bytes() == b"keep"
        assert stage.read_bytes() == b"stage"


@pytest.mark.parametrize("platform,symbol", [("darwin", "renamex_np"), ("linux", "renameat2")])
def test_native_exclusive_rename_binds_the_os_abi_and_reports_eexist(
    tmp_path, monkeypatch, platform, symbol
):
    # ABI/errno 的单元测试；不能代替对应系统与文件系统的真机验收。
    calls = []

    class Rename:
        result = 0

        def __call__(self, *args):
            calls.append(args)
            ctypes.set_errno(errno.EEXIST)
            return self.result

    rename = Rename()
    monkeypatch.setattr(
        ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(**{symbol: rename})
    )
    monkeypatch.setattr(sys, "platform", platform)
    src, dest = tmp_path / "stage", tmp_path / "target"
    atomicio._rename_exclusive(src, dest)
    expected = (
        (os.fsencode(src), os.fsencode(dest), 4)
        if platform == "darwin"
        else (-100, os.fsencode(src), -100, os.fsencode(dest), 1)
    )
    assert calls == [expected]
    assert len(rename.argtypes) == len(expected)
    assert rename.restype is ctypes.c_int
    rename.result = -1
    with pytest.raises(FileExistsError):
        atomicio._rename_exclusive(src, dest)


def test_native_exclusive_rename_without_os_support_fails_without_fallback(monkeypatch):
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(OSError) as raised:
        atomicio._rename_exclusive(Path("stage"), Path("target"))
    assert raised.value.errno == errno.ENOTSUP


def test_volume_without_either_publication_primitive_preserves_existing_bytes(
    tmp_path, monkeypatch
):
    target = tmp_path / "immutable.bin"
    target.write_bytes(b"keep")

    def unsupported(*_):
        raise OSError(errno.ENOTSUP, "volume lacks this publication operation")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(atomicio.os, "link", unsupported)
    monkeypatch.setattr(atomicio, "_rename_exclusive", unsupported)
    with pytest.raises(atomicio.AtomicWriteError) as raised:
        atomicio.write_bytes_if_absent(target, b"replace")
    assert raised.value.code == "replace_failed"
    assert target.read_bytes() == b"keep"
    assert list(tmp_path.iterdir()) == [target]
