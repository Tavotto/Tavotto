"""Source identity checks for both original-file write routes.

External edits during replay must survive publication and rollback. The same
fingerprint also refuses a prepare baseline that cannot be read, instead of
silently skipping the check. The existing writeback fixture owns the fake
rendering boundary; its staging outputs here are valid PDF/PNG files, while the
real HTTP transaction, backup, pixel gate, persistence and cleanup all run
unchanged.
"""

from __future__ import annotations

import copy
import os
import struct
import zlib
from pathlib import Path

import pytest

import test_write_back as writeback
from tavotto import app as m

client = writeback.client


def _png(rgb):
    def chunk(kind, data):
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(rgb), level=0))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("endpoint", ("update_source", "history/restore"))
@pytest.mark.parametrize("target", ("requested_pdf", "same_stem_png"))
@pytest.mark.parametrize("stage", ("verify", "after_backup"))
def test_external_edit_with_same_size_and_nanosecond_mtime_survives_writeback(
    client, tmp_path, monkeypatch, endpoint, target, stage
):
    root = writeback._figs(tmp_path)
    source = root / "Fig1.pdf"
    png = root / "Fig1.png"
    source.write_bytes(source.read_bytes() + b"\n% revision=A\n")
    png.write_bytes(_png((10, 20, 30)))
    originals = {path: path.read_bytes() for path in (source, png)}
    changed = source if target == "requested_pdf" else png
    external = (
        originals[changed].replace(b"revision=A", b"revision=B")
        if changed == source
        else _png((20, 30, 40))
    )
    stamp = changed.stat()
    assert len(external) == stamp.st_size and external != originals[changed]
    hot, fresh = writeback._pair(root, tmp_path)
    discarded = writeback._use(monkeypatch, hot, fresh)
    export = fresh.export

    def valid_staging(stem, patches, path, fmt="pdf", dpi=600):
        result = export(stem, patches, path, fmt, dpi)
        Path(path).write_bytes(
            originals[source] + b"\n% verified output\n" if fmt == "pdf" else _png((40, 50, 60))
        )
        return result

    monkeypatch.setattr(fresh, "export", valid_staging)
    observed = []

    def external_write():
        changed.write_bytes(external)
        os.utime(changed, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        observed.append(changed.stat())

    if stage == "verify":
        override = fresh.override

        def during_replay(*args, **kwargs):
            response = override(*args, **kwargs)
            external_write()
            return response

        monkeypatch.setattr(fresh, "override", during_replay)
    else:
        backup = m._backup_targets

        def after_backup(*args, **kwargs):
            result = backup(*args, **kwargs)
            external_write()
            return result

        monkeypatch.setattr(m, "_backup_targets", after_backup)

    replace = Path.replace
    replacements = []

    def record_replace(path, destination):
        if Path(destination) in originals:
            replacements.append(Path(destination))
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", record_replace)
    history = copy.deepcopy(m.load_baked())
    body = {"id": "Fig1.pdf", "expected_mtime": int(source.stat().st_mtime)}
    body.update({"patches": []} if endpoint == "update_source" else {"n": -1})
    response = client.post("/api/engine/" + endpoint, json=body)

    # The injected write really lies in the intended window. Same ns/length
    # excludes an mtime-only predicate which accidentally passes this test.
    assert len(observed) == 1
    assert observed[0].st_size == stamp.st_size
    assert observed[0].st_mtime_ns == stamp.st_mtime_ns
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "source_changed", response.get_json()
    assert replacements == []
    assert changed.read_bytes() == external
    assert all(path.read_bytes() == before for path, before in originals.items() if path != changed)
    assert writeback._leftovers(root) == []
    assert m.load_baked() == history
    assert discarded == [fresh] and fresh.shutdowns == 1


def test_an_unreadable_prepare_baseline_is_an_error_not_a_skip(tmp_path):
    """prepare 时读不到原件：没有可比的基准也不能跳过核验。"""
    source = tmp_path / "Fig1.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(m.WriteBackPersistError) as excinfo:
        m._assert_sources_unchanged({source: None}, [source])
    assert "无法核验写回原件" in str(excinfo.value)
    assert source.name in str(excinfo.value)


def test_a_source_that_cannot_be_hashed_at_prepare_is_a_409_before_any_backup(
    client, tmp_path, monkeypatch
):
    """prepare 时原件读不到：409，原件零改动，不产生备份或历史。"""
    root = writeback._figs(tmp_path)
    source = root / "Fig1.pdf"
    png = root / "Fig1.png"
    originals = {path: path.read_bytes() for path in (source, png)}
    hot, fresh = writeback._pair(root, tmp_path)
    discarded = writeback._use(monkeypatch, hot, fresh)
    real_sha1 = m._sha1_of
    failures = []

    def sha1(path):
        if Path(path) == source and not failures:
            failures.append(Path(path))
            raise PermissionError("source is locked")
        return real_sha1(path)

    monkeypatch.setattr(m, "_sha1_of", sha1)
    history = copy.deepcopy(m.load_baked())
    response = client.post(
        "/api/engine/update_source",
        json={"id": "Fig1.pdf", "patches": [], "expected_mtime": int(source.stat().st_mtime)},
    )

    # The failure really came from the prepare baseline, not from a later read.
    assert failures == [source], "prepare 没有读到原件"
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["code"] == "write_back_persist_failed", body
    assert source.name in body["params"]["reason"], body
    assert all(path.read_bytes() == before for path, before in originals.items())
    assert writeback._leftovers(root) == []
    assert writeback._backup_files() == []
    assert m.load_baked() == history
    assert discarded == [fresh] and fresh.shutdowns == 1


def test_the_baseline_binds_content_to_the_observed_stat(tmp_path):
    """同长度、同纳秒 mtime 的内容变化也必须换掉指纹：核验读的是全文。"""
    source = tmp_path / "Fig1.pdf"
    source.write_bytes(b"one")
    stamp = source.stat()
    first = m._source_fingerprint([source])[source]
    source.write_bytes(b"two")
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = m._source_fingerprint([source])[source]

    assert first is not None and second is not None
    assert first[:2] == second[:2] == (stamp.st_mtime_ns, stamp.st_size)
    assert first[2] != second[2]
