"""External edits during replay must survive both original-file write routes.

The existing writeback fixture owns the fake rendering boundary. Its staging
outputs here are valid PDF/PNG files, while the real HTTP transaction, backup,
pixel gate, persistence and cleanup all run unchanged.
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
