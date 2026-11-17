"""HTTP snapshot byte verification without changing v1 cache/path semantics."""

from __future__ import annotations

import hashlib

import pytest

from tavotto.engine import journalremote as jr
from test_journal_remote import SNAP_BYTES, SNAP_SHA

pytest_plugins = ("test_journal_remote",)


def test_download_persists_and_legacy_path_remains_available_offline(env):
    jr.sync(force=True)
    file = f"snapshots/{SNAP_SHA}.webp"
    path = jr.snapshot_path(file)
    assert path == jr.cache_dir() / file and path.read_bytes() == SNAP_BYTES
    n = len(env.calls)
    env.offline = True
    assert jr.snapshot_bytes(file) == SNAP_BYTES
    assert jr.snapshot_path(file) == path
    assert len(env.calls) == n


@pytest.mark.parametrize("kind", ["truncated", "oversized", "mismatched"])
def test_http_rejects_bad_cached_snapshot_without_modifying_it(client, env, kind):
    jr.sync(force=True)
    file = f"snapshots/{SNAP_SHA}.webp"
    path = jr.snapshot_path(file)
    assert path is not None
    bad = {
        "truncated": SNAP_BYTES[:-1],
        "oversized": b"x" * (jr.MAX_SNAPSHOT_BYTES + 1),
        "mismatched": b"other",
    }[kind]
    path.write_bytes(bad)
    # Legacy API deliberately remains unchanged for this limited candidate.
    assert jr.snapshot_path(file) == path
    response = client.get(f"/api/journal-catalog/assets/{file}")
    assert response.status_code == 404
    assert response.get_json()["code"] == "snapshot_unavailable"
    assert path.read_bytes() == bad


def test_http_rejects_symlink_without_touching_target(client, env, tmp_path):
    jr.sync(force=True)
    file = f"snapshots/{SNAP_SHA}.webp"
    path = jr.cache_dir() / file
    path.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "unrelated"
    target.write_bytes(SNAP_BYTES)
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem does not support symlinks")
    response = client.get(f"/api/journal-catalog/assets/{file}")
    assert response.status_code == 404
    assert target.read_bytes() == SNAP_BYTES and path.is_symlink()


def test_http_serves_verified_bytes_when_path_changes_after_read(client, env, monkeypatch):
    jr.sync(force=True)
    file = f"snapshots/{SNAP_SHA}.webp"
    path = jr.snapshot_path(file)
    assert path is not None
    read = jr.snapshot_bytes

    def mutate_after_read(name):
        data = read(name)
        path.write_bytes(b"changed after verification")
        return data

    monkeypatch.setattr(jr, "snapshot_bytes", mutate_after_read)
    response = client.get(f"/api/journal-catalog/assets/{file}")
    assert response.status_code == 200 and response.data == SNAP_BYTES
    assert path.read_bytes() == b"changed after verification"


def test_http_etag_and_range_apply_to_verified_bytes(client, env):
    jr.sync(force=True)
    file = f"snapshots/{SNAP_SHA}.webp"
    url = f"/api/journal-catalog/assets/{file}"
    response = client.get(url)
    assert response.status_code == 200 and response.data == SNAP_BYTES
    assert response.headers["ETag"] == f'"{SNAP_SHA}"'
    assert client.get(url, headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
    partial = client.get(url, headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206 and partial.data == SNAP_BYTES[:4]
    # Even a matching conditional request cannot bless a corrupted cache file.
    (jr.cache_dir() / file).write_bytes(SNAP_BYTES[:-1])
    assert client.get(url, headers={"If-None-Match": response.headers["ETag"]}).status_code == 404


def test_http_verified_read_works_without_posix_nofollow_flag(env, monkeypatch):
    import os

    jr.sync(force=True)
    file = f"snapshots/{SNAP_SHA}.webp"
    path = jr.snapshot_path(file)
    assert path is not None
    # Simulates absence of the optional flag, not the Windows filesystem.
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    assert jr.snapshot_bytes(file) == SNAP_BYTES
    path.write_bytes(b"bad")
    assert jr.snapshot_bytes(file) is None


def test_network_body_with_wrong_hash_is_not_cached(env):
    jr.sync(force=True)
    file = f"snapshots/{hashlib.sha256(b'expected').hexdigest()}.webp"
    env.snapshots[hashlib.sha256(b"expected").hexdigest()] = b"different"
    assert jr.snapshot_bytes(file) is None
    assert not (jr.cache_dir() / file).exists()
