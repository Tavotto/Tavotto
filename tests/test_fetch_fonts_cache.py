"""Restored CI archives are untrusted bytes, never a substitute for the allowlist.

Synthetic approved sources keep every corruption/transport case offline. Concurrent
jobs own separate destinations; warm jobs may also read the same immutable archive.
The downloader and product runtime are unchanged by the CI cache.
"""

from __future__ import annotations

import copy
import hashlib
import io
import sys
import tarfile
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_fonts as ff  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tar_bytes(members: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if name == symlink:
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                archive.addfile(info)
            else:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture
def approved(monkeypatch):
    face, license_text = b"approved font bytes", b"approved OFL license text"
    archive_url = "https://approved.example/fonts.tar.gz"
    archive = tar_bytes({"release/Face.ttf": face, "release/LICENSE": license_text})
    source = {"kind": "tarball-member", "url": archive_url, "sha256": digest(archive)}
    data = {
        "schema": 1,
        "faces": {
            "face": {
                "file": "family/Face.ttf",
                "sha256": digest(face),
                "source": {**source, "member": "release/Face.ttf"},
            },
        },
        "license_files": {
            "family/LICENSE": {
                **source,
                "member": "release/LICENSE",
                "file_sha256": digest(license_text),
            },
        },
    }
    requests = []

    def open_approved(url, *, timeout):
        assert url == archive_url and timeout == 300
        requests.append(url)
        return io.BytesIO(archive)

    monkeypatch.setattr(ff.urllib.request, "urlopen", open_approved)
    monkeypatch.setattr(ff.time, "sleep", lambda _delay: None)
    return data, archive, requests


def test_cold_cache_downloads_one_archive_and_verifies_fonts_and_license(tmp_path, approved):
    data, archive, requests = approved
    dest, cache = tmp_path / "fonts", tmp_path / "cache"
    out = ff.fetch(dest, cache=cache, allowlist=data)
    assert out == {"face": dest / "family/Face.ttf"}
    assert ff.check(dest, data) == []
    assert len(requests) == 1
    assert (cache / "fonts.tar.gz").read_bytes() == archive
    assert sorted(p.name for p in cache.iterdir()) == ["fonts.tar.gz"]


def test_warm_archive_avoids_get_but_reextracts_and_rechecks_every_file(tmp_path, approved):
    data, archive, requests = approved
    cache = tmp_path / "restored"
    cache.mkdir()
    (cache / "fonts.tar.gz").write_bytes(archive)
    for name in ("fresh", "corrupt-destination"):
        dest = tmp_path / name
        if name == "corrupt-destination":
            (dest / "family").mkdir(parents=True)
            (dest / "family/Face.ttf").write_bytes(b"tampered")
            (dest / "family/LICENSE").write_bytes(b"truncated license")
        ff.fetch(dest, cache=cache, allowlist=data)
        assert ff.check(dest, data) == []
    assert requests == []


@pytest.mark.parametrize("kind", ["corrupt", "truncated", "empty", "stale"])
def test_invalid_restored_archive_is_replaced_from_the_approved_source(tmp_path, approved, kind):
    data, archive, requests = approved
    bad = {
        "corrupt": b"not a tarball",
        "truncated": archive[:20],
        "empty": b"",
        "stale": tar_bytes({"release/Face.ttf": b"older approved release"}),
    }[kind]
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fonts.tar.gz").write_bytes(bad)
    ff.fetch(tmp_path / "fonts", cache=cache, allowlist=data)
    assert ff.check(tmp_path / "fonts", data) == []
    assert requests == [data["faces"]["face"]["source"]["url"]]
    assert (cache / "fonts.tar.gz").read_bytes() == archive


def test_partial_archive_is_never_a_hit(tmp_path, approved):
    data, archive, requests = approved
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fonts.tar.gz.part").write_bytes(archive[:10])
    ff.fetch(tmp_path / "fonts", cache=cache, allowlist=data)
    assert len(requests) == 1
    assert (cache / "fonts.tar.gz").read_bytes() == archive
    assert not (cache / "fonts.tar.gz.part").exists()


def test_invalid_restore_fails_closed_before_tar_open_when_source_is_unavailable(
    tmp_path, approved, monkeypatch
):
    data, _archive, _requests = approved
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fonts.tar.gz").write_bytes(b"malformed untrusted restore")
    calls = []

    def unavailable(url, *, timeout):
        calls.append(url)
        raise urllib.error.URLError("504 from approved source")

    def never_extract(*args, **kwargs):
        pytest.fail("unverified restored archive reached tarfile.open")

    monkeypatch.setattr(ff.urllib.request, "urlopen", unavailable)
    monkeypatch.setattr(ff.tarfile, "open", never_extract)
    with pytest.raises(urllib.error.URLError):
        ff.fetch(tmp_path / "fonts", cache=cache, allowlist=data)
    assert len(calls) == 3
    assert list(cache.iterdir()) == []
    assert not (tmp_path / "fonts/family/Face.ttf").exists()


def test_download_hash_mismatch_is_not_retried_or_published(tmp_path, approved, monkeypatch):
    data, _archive, _requests = approved
    calls = []

    def tampered(url, *, timeout):
        calls.append(url)
        return io.BytesIO(b"wrong downloaded bytes")

    monkeypatch.setattr(ff.urllib.request, "urlopen", tampered)
    with pytest.raises(ff.HashMismatch):
        ff.fetch(tmp_path / "fonts", cache=tmp_path / "cache", allowlist=data)
    assert len(calls) == 1
    assert list((tmp_path / "cache").iterdir()) == []


@pytest.mark.parametrize("member", ["face", "license"])
def test_trusted_archive_hash_cannot_replace_individual_member_hashes(tmp_path, approved, member):
    data, archive, requests = approved
    data = copy.deepcopy(data)
    if member == "face":
        data["faces"]["face"]["sha256"] = digest(b"different approved face")
    else:
        data["license_files"]["family/LICENSE"]["file_sha256"] = digest(
            b"different approved license"
        )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fonts.tar.gz").write_bytes(archive)
    with pytest.raises(ff.HashMismatch):
        ff.fetch(tmp_path / "fonts", cache=cache, allowlist=data)
    assert requests == []
    assert ff.check(tmp_path / "fonts", data)


def test_archive_symlink_member_is_refused_even_with_a_matching_archive_hash(tmp_path, approved):
    data, _archive, _requests = approved
    malicious = tar_bytes({"release/Face.ttf": b""}, symlink="release/Face.ttf")
    data["faces"]["face"]["source"]["sha256"] = digest(malicious)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "fonts.tar.gz").write_bytes(malicious)
    with pytest.raises(ValueError, match="不是普通文件"):
        ff.fetch(tmp_path / "fonts", cache=cache, allowlist=data)
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "fonts/family/Face.ttf").exists()


@pytest.mark.parametrize("warm", [False, True])
def test_concurrent_jobs_keep_destinations_and_partial_writes_separate(tmp_path, approved, warm):
    data, archive, requests = approved
    shared = tmp_path / "immutable-restored-cache"
    if warm:
        shared.mkdir()
        (shared / "fonts.tar.gz").write_bytes(archive)

    def run_job(index):
        job = tmp_path / f"job-{index}"
        # Hosted cold jobs have distinct checkout/cache roots. Restored bytes are
        # immutable; even a shared warm archive is read-only throughout fetch.
        cache = shared if warm else job / "cache"
        ff.fetch(job / "fonts", cache=cache, allowlist=data)
        assert ff.check(job / "fonts", data) == []
        return (job / "fonts/family/LICENSE").read_bytes()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run_job, range(4)))
    assert results == [b"approved OFL license text"] * 4
    assert len(requests) == (0 if warm else 4)
    assert not list(tmp_path.rglob("*.part"))
