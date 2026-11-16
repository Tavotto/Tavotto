"""Directory publication tests; this is not a package installation transaction."""

from __future__ import annotations

import errno
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import atomicio


def _stage(parent, name="stage", data=b"complete bytes"):
    root = parent / name
    (root / "nested" / "empty").mkdir(parents=True)
    (root / "nested" / "value.bin").write_bytes(data)
    return root


def _link(target, path):
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Creating a symlink is not permitted on this host: {exc}")


def test_real_directory_is_published_complete_without_removing_its_empty_children(tmp_path):
    stage = _stage(tmp_path)
    target = tmp_path / "中文 新项目"
    atomicio.fsync_file(stage / "nested" / "value.bin")
    atomicio.fsync_dir(stage / "nested" / "empty")
    atomicio.fsync_dir(stage / "nested")
    atomicio.publish_directory_if_absent(stage, target)
    assert not stage.exists()
    assert (target / "nested" / "value.bin").read_bytes() == b"complete bytes"
    assert (target / "nested" / "empty").is_dir()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "broken_symlink"])
def test_existing_target_is_never_overwritten_and_source_remains_owned(tmp_path, kind):
    stage = _stage(tmp_path)
    target = tmp_path / "existing"
    referent = tmp_path / "referent"
    if kind == "file":
        target.write_bytes(b"keep target")
    elif kind == "directory":
        target.mkdir()
        (target / "sentinel").write_bytes(b"keep target")
    else:
        if kind == "symlink":
            referent.mkdir()
            (referent / "sentinel").write_bytes(b"keep target")
        _link(referent, target)
    with pytest.raises(FileExistsError):
        atomicio.publish_directory_if_absent(stage, target)
    assert (stage / "nested" / "value.bin").read_bytes() == b"complete bytes"
    if kind == "file":
        assert target.read_bytes() == b"keep target"
    elif kind == "directory":
        assert (target / "sentinel").read_bytes() == b"keep target"
    else:
        assert target.is_symlink() and target.readlink() == referent
        if kind == "symlink":
            assert (referent / "sentinel").read_bytes() == b"keep target"
        else:
            assert not referent.exists()


def test_two_concurrent_directories_have_exactly_one_complete_winner(tmp_path):
    stages = [_stage(tmp_path, f"stage-{index}", bytes([index]) * 10001) for index in range(2)]
    target = tmp_path / "target"
    gate = threading.Barrier(2)

    def publish(index):
        gate.wait(timeout=10)
        try:
            atomicio.publish_directory_if_absent(stages[index], target)
        except FileExistsError:
            return "collision", index
        return "published", index

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, range(2)))
    assert sorted(state for state, _ in results) == ["collision", "published"]
    winner = next(index for state, index in results if state == "published")
    loser = 1 - winner
    assert not stages[winner].exists()
    assert (target / "nested" / "value.bin").read_bytes() == bytes([winner]) * 10001
    assert (stages[loser] / "nested" / "value.bin").read_bytes() == bytes([loser]) * 10001


def test_sync_order_is_stage_then_exclusive_publish_then_parent(tmp_path, monkeypatch):
    stage = _stage(tmp_path)
    target = tmp_path / "target"
    events = []
    original = atomicio._publish_if_absent

    def publish(src, dest, *, directory=False):
        assert directory is True
        assert events == [("sync", stage)]
        assert src == stage and dest == target
        events.append(("publish", target))
        return original(src, dest, directory=directory)

    monkeypatch.setattr(atomicio, "fsync_dir", lambda path: events.append(("sync", path)))
    monkeypatch.setattr(atomicio, "_publish_if_absent", publish)
    atomicio.publish_directory_if_absent(stage, target)
    assert events == [("sync", stage), ("publish", target), ("sync", tmp_path)]


@pytest.mark.parametrize("failure", ["stage_sync", "publish", "parent_sync"])
def test_real_errors_preserve_stage_before_commit_and_target_after_commit(
    tmp_path, monkeypatch, failure
):
    stage = _stage(tmp_path)
    target = tmp_path / "target"

    def sync(path):
        if path == (stage if failure == "stage_sync" else tmp_path):
            raise OSError(errno.EIO, "injected directory sync failure")

    def fail_publish(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "injected rename failure")

    if failure == "publish":
        monkeypatch.setattr(atomicio, "_publish_if_absent", fail_publish)
    else:
        monkeypatch.setattr(atomicio, "fsync_dir", sync)
    with pytest.raises(atomicio.AtomicWriteError) as raised:
        atomicio.publish_directory_if_absent(stage, target)
    assert raised.value.code == ("replace_failed" if failure == "publish" else "dir_fsync_failed")
    if failure == "parent_sync":
        assert not stage.exists()
        assert (target / "nested" / "value.bin").read_bytes() == b"complete bytes"
    else:
        assert not target.exists()
        assert (stage / "nested" / "value.bin").read_bytes() == b"complete bytes"


def test_collision_does_not_get_hidden_by_an_unnecessary_parent_sync(tmp_path, monkeypatch):
    stage = _stage(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    calls = []

    def sync(path):
        assert path == stage, "A rejected publication must retain FileExistsError"
        calls.append(path)

    monkeypatch.setattr(atomicio, "fsync_dir", sync)
    with pytest.raises(FileExistsError):
        atomicio.publish_directory_if_absent(stage, target)
    assert calls == [stage]


@pytest.mark.parametrize("relation", ["same", "different_parent"])
def test_invalid_path_relationship_never_modifies_either_directory(tmp_path, relation):
    stage = _stage(tmp_path)
    if relation == "same":
        target = stage
    else:
        parent = tmp_path / "other"
        parent.mkdir()
        target = parent / "target"
    with pytest.raises(ValueError):
        atomicio.publish_directory_if_absent(stage, target)
    assert (stage / "nested" / "value.bin").read_bytes() == b"complete bytes"
    if target != stage:
        assert not target.exists()


def test_same_directory_with_different_case_is_rejected_on_windows(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows path identity test")
    stage = _stage(tmp_path, "CaseStage")
    with pytest.raises(ValueError):
        atomicio.publish_directory_if_absent(stage, tmp_path / "casestage")
    assert stage.is_dir()


@pytest.mark.parametrize("kind", ["file", "symlink", "reparse"])
def test_source_must_be_a_real_directory_without_reparse_attributes(tmp_path, monkeypatch, kind):
    source = tmp_path / "stage"
    target = tmp_path / "target"
    if kind == "file":
        source.write_bytes(b"not a directory")
    elif kind == "symlink":
        actual = _stage(tmp_path, "actual")
        _link(actual, source)
    else:
        source = _stage(tmp_path)
        info = source.lstat()
        original = Path.lstat

        def lstat(path):
            if path == source:
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_dev=info.st_dev,
                    st_ino=info.st_ino,
                    st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                )
            return original(path)

        monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError):
        atomicio.publish_directory_if_absent(source, target)
    assert not target.exists()
    assert source.exists()


def test_missing_source_is_a_structured_io_failure(tmp_path):
    with pytest.raises(atomicio.AtomicWriteError) as raised:
        atomicio.publish_directory_if_absent(tmp_path / "missing", tmp_path / "target")
    assert raised.value.code == "write_failed"
    assert not (tmp_path / "target").exists()


def test_source_identity_is_rechecked_after_sync_without_deleting_either_version(
    tmp_path, monkeypatch
):
    stage = _stage(tmp_path)
    original = tmp_path / "original"
    target = tmp_path / "target"

    def replace_during_sync(path):
        assert path == stage
        stage.rename(original)
        stage.mkdir()
        (stage / "intruder").write_bytes(b"different directory")

    monkeypatch.setattr(atomicio, "fsync_dir", replace_during_sync)
    with pytest.raises(atomicio.AtomicWriteError) as raised:
        atomicio.publish_directory_if_absent(stage, target)
    assert raised.value.code == "replace_failed"
    assert (original / "nested" / "value.bin").read_bytes() == b"complete bytes"
    assert (stage / "intruder").read_bytes() == b"different directory"
    assert not target.exists()


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_posix_directory_never_tries_a_hard_link_and_uses_existing_exclusive_rename(
    tmp_path, monkeypatch, platform
):
    source, target = tmp_path / "stage", tmp_path / "target"
    calls = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(
        atomicio.os, "link", lambda *_: pytest.fail("Directory hard link attempted")
    )
    monkeypatch.setattr(atomicio, "_rename_exclusive", lambda *paths: calls.append(paths))
    atomicio._publish_if_absent(source, target, directory=True)
    assert calls == [(source, target)]


def test_missing_exclusive_directory_primitive_fails_without_destructive_fallback(
    tmp_path, monkeypatch
):
    stage = _stage(tmp_path)
    target = tmp_path / "target"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        atomicio.os, "link", lambda *_: pytest.fail("Directory hard link attempted")
    )
    monkeypatch.setattr(
        atomicio.os, "rename", lambda *_: pytest.fail("Unsafe POSIX rename attempted")
    )

    def unsupported(*_):
        raise OSError(errno.ENOTSUP, "No exclusive rename available")

    monkeypatch.setattr(atomicio, "_rename_exclusive", unsupported)
    with pytest.raises(atomicio.AtomicWriteError) as raised:
        atomicio.publish_directory_if_absent(stage, target)
    assert raised.value.code == "replace_failed"
    assert stage.is_dir() and not target.exists()
