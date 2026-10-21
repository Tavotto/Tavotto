"""`atomicio` 的 Windows 有界重试：`os.replace` 遇共享冲突（winerror 5 / 32）才重试，且次数与时长有上限。

非 Windows 上用 monkeypatch 模拟 `os.name` 与 `PermissionError.winerror`；其它错误、非 Windows 平台一律立即抛。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tavotto.engine import atomicio


def _perm(winerror: int | None) -> PermissionError:
    exc = PermissionError(13, "denied")
    if winerror is not None:
        exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


@pytest.fixture
def sleeps(monkeypatch):
    got: list[float] = []
    monkeypatch.setattr(atomicio.time, "sleep", got.append)
    return got


def _flaky_replace(monkeypatch, fails: int, error: Exception):
    real = os.replace
    calls = {"n": 0}

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] <= fails:
            raise error
        real(src, dst)

    monkeypatch.setattr(atomicio.os, "replace", fake)
    return calls


@pytest.mark.parametrize("winerror", [5, 32])
@pytest.mark.parametrize("writer", ["write_bytes", "publish_file"])
def test_sharing_violation_is_retried_then_succeeds(
    tmp_path, monkeypatch, sleeps, winerror, writer
):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    dest.write_bytes(b"old")
    calls = _flaky_replace(monkeypatch, 3, _perm(winerror))
    if writer == "write_bytes":
        atomicio.write_bytes(dest, b"new")
    else:
        tmp = tmp_path / "a.json.1.1.tmp"
        tmp.write_bytes(b"new")
        atomicio.publish_file(tmp, dest)
    assert dest.read_bytes() == b"new"
    assert calls["n"] == 4
    assert sleeps == list(atomicio._REPLACE_RETRY_DELAYS_S[:3])


def test_retry_is_bounded_and_failure_keeps_old_file_and_cleans_tmp(tmp_path, monkeypatch, sleeps):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    dest.write_bytes(b"old")
    calls = _flaky_replace(monkeypatch, 10**6, _perm(32))
    with pytest.raises(atomicio.AtomicWriteError) as ei:
        atomicio.write_bytes(dest, b"new")
    assert ei.value.code == "replace_failed"
    assert calls["n"] == 1 + len(atomicio._REPLACE_RETRY_DELAYS_S)
    assert sum(sleeps) <= 0.7  # 总等待上限写死，不会无限卡住持锁的调用方
    assert dest.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


@pytest.mark.parametrize(
    "error",
    [_perm(None), _perm(1), PermissionError(13, "x"), OSError(28, "no space")],
    ids=["no-winerror", "other-winerror", "plain", "enospc"],
)
def test_other_errors_raise_immediately(tmp_path, monkeypatch, sleeps, error):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    calls = _flaky_replace(monkeypatch, 10**6, error)
    with pytest.raises(atomicio.AtomicWriteError):
        atomicio.write_bytes(dest, b"new")
    assert calls["n"] == 1 and sleeps == []


def test_non_windows_never_retries_even_with_a_winerror(tmp_path, monkeypatch, sleeps):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: False)
    calls = _flaky_replace(monkeypatch, 10**6, _perm(32))
    with pytest.raises(atomicio.AtomicWriteError):
        atomicio.write_bytes(tmp_path / "a.json", b"new")
    assert calls["n"] == 1 and sleeps == []


def test_happy_path_does_not_sleep(tmp_path, sleeps):
    atomicio.write_bytes(Path(tmp_path) / "a.json", b"x")
    assert sleeps == []


# --- 重试前重新验证目标没变（Codex P1：别用退避盖掉外部进程刚写完的更新）---


def _replace_failing_once_with_sleep_hook(monkeypatch, hook):
    real = os.replace
    calls = {"n": 0}

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _perm(32)
        real(src, dst)

    monkeypatch.setattr(atomicio.os, "replace", fake)
    monkeypatch.setattr(atomicio.time, "sleep", lambda _s: hook())
    return calls


def _assert_no_retry(tmp_path, dest, calls, expected: bytes | None):
    assert calls["n"] == 1
    if expected is None:
        assert not dest.exists()
    else:
        assert dest.read_bytes() == expected
    assert [p.name for p in tmp_path.iterdir()] == ([dest.name] if expected is not None else [])


@pytest.mark.parametrize("external", [b"external-longer-bytes", b"ext"])
def test_target_rewritten_during_backoff_is_not_overwritten(tmp_path, monkeypatch, external):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    dest.write_bytes(b"old")
    calls = _replace_failing_once_with_sleep_hook(monkeypatch, lambda: dest.write_bytes(external))
    with pytest.raises(atomicio.AtomicWriteError) as ei:
        atomicio.write_bytes(dest, b"new")
    assert ei.value.code == "replace_failed"
    _assert_no_retry(tmp_path, dest, calls, external)


def test_target_touched_same_size_during_backoff_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    dest.write_bytes(b"old")
    st = dest.stat()

    def touch():
        dest.write_bytes(b"ext")  # 同样 3 字节，只有 mtime 变
        os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    calls = _replace_failing_once_with_sleep_hook(monkeypatch, touch)
    with pytest.raises(atomicio.AtomicWriteError):
        atomicio.write_bytes(dest, b"new")
    _assert_no_retry(tmp_path, dest, calls, b"ext")


def test_target_deleted_during_backoff_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    dest.write_bytes(b"old")
    calls = _replace_failing_once_with_sleep_hook(monkeypatch, dest.unlink)
    with pytest.raises(atomicio.AtomicWriteError):
        atomicio.write_bytes(dest, b"new")
    _assert_no_retry(tmp_path, dest, calls, None)


def test_target_created_during_backoff_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    calls = _replace_failing_once_with_sleep_hook(monkeypatch, lambda: dest.write_bytes(b"ext"))
    with pytest.raises(atomicio.AtomicWriteError):
        atomicio.write_bytes(dest, b"new")
    _assert_no_retry(tmp_path, dest, calls, b"ext")


def test_unprovable_target_state_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    dest.write_bytes(b"old")
    calls = _replace_failing_once_with_sleep_hook(monkeypatch, lambda: None)
    real_stat = os.stat
    seen = {"n": 0}

    def fake_stat(path, *a, **k):
        if Path(path) == dest:
            seen["n"] += 1
            if seen["n"] >= 2:  # 第一次（基线）正常，退避后那次被共享冲突挡住
                raise _perm(32)
        return real_stat(path, *a, **k)

    monkeypatch.setattr(atomicio.os, "stat", fake_stat)
    with pytest.raises(atomicio.AtomicWriteError):
        atomicio.write_bytes(dest, b"new")
    _assert_no_retry(tmp_path, dest, calls, b"old")


def test_unchanged_target_is_still_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(atomicio, "_is_windows", lambda: True)
    dest = tmp_path / "a.json"
    dest.write_bytes(b"old")
    calls = _replace_failing_once_with_sleep_hook(monkeypatch, lambda: None)
    atomicio.write_bytes(dest, b"new")
    assert calls["n"] == 2 and dest.read_bytes() == b"new"
