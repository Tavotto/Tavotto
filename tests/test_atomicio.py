"""Successful-write revisions describe this call's exact bytes, even after replacement."""

import errno
import json
from pathlib import Path

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
