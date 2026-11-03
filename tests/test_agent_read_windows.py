"""Large approved resources remain readable without unbounded reads or write grants."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from tavotto.engine.agent_runner import tools


@pytest.fixture(params=["figure_edit", "convert_to_editable"])
def toolbox(tmp_path, request):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "figure.py").write_text("color = 'red'\n", encoding="utf-8")
    env = tools.RunEnvelope(
        task_type=request.param,
        cwd=root,
        target="figure.py",
        output="figure_matplotlib.py",
        instruction="preserve the figure",
    )
    return root, {tool.name: tool for tool in tools.TOOLSETS[request.param].tools(env)}


def test_large_utf8_resource_can_be_reassembled_without_skipping_characters(toolbox):
    root, ts = toolbox
    source = "<svg>\r\n" + "<text>汉🙂\u0301</text>\r\n" * 12_000 + "</svg>"
    raw = source.encode("utf-8")
    assert len(raw) > tools.READ_LIMIT_BYTES
    path = root / "native.svg"
    path.write_bytes(raw)
    read = ts["read_file"].handler
    assert read({"path": "native.svg"}).code == "too_large"
    offset, pieces = 0, []
    while offset < len(raw):
        result = read({"path": "native.svg", "byte_offset": offset, "byte_limit": 1021})
        assert not result.is_error, result.content
        header, text = result.content.split("\n", 1)
        meta = json.loads(header)
        assert meta["byte_offset"] == offset and meta["file_size"] == len(raw)
        assert 0 < meta["next_byte_offset"] - offset <= 1021
        assert text.encode("utf-8") == raw[offset : meta["next_byte_offset"]]
        offset = meta["next_byte_offset"]
        assert meta["eof"] == (offset == len(raw))
        pieces.append(text)
    assert "".join(pieces) == source
    assert path.read_bytes() == raw
    eof = read({"path": "native.svg", "byte_offset": offset})
    assert json.loads(eof.content.split("\n", 1)[0])["eof"] is True


@pytest.mark.parametrize(
    "args",
    [
        {"byte_offset": -1},
        {"byte_offset": True},
        {"byte_offset": "0"},
        {"byte_offset": 999},
        {"byte_limit": 0},
        {"byte_limit": 3},
        {"byte_limit": 65537},
        {"byte_limit": False},
    ],
)
def test_window_arguments_are_bounded_at_execution(toolbox, args):
    _root, ts = toolbox
    result = ts["read_file"].handler({"path": "figure.py", **args})
    assert (result.is_error, result.code) == (True, "bad_arguments")


def test_window_schema_and_path_boundary(toolbox):
    root, ts = toolbox
    schema = ts["read_file"].schema["properties"]
    assert schema["byte_limit"]["maximum"] == tools.READ_WINDOW_BYTES
    outside = root.parent / "private.txt"
    outside.write_text("OUTSIDE_SECRET", encoding="utf-8")
    read = ts["read_file"].handler
    result = read({"path": "../private.txt", "byte_offset": 0})
    assert result.is_error and "OUTSIDE_SECRET" not in result.content
    try:
        (root / "link").symlink_to(outside)
    except OSError:
        return  # The direct traversal assertion above still ran on this host.
    result = read({"path": "link", "byte_offset": 0})
    assert result.is_error and "OUTSIDE_SECRET" not in result.content


def test_window_never_authorizes_editing_a_partially_read_target(toolbox):
    root, ts = toolbox
    before = (root / "figure.py").read_bytes()
    assert not ts["read_file"].handler({"path": "figure.py", "byte_limit": 4}).is_error
    result = ts["edit_file"].handler({"path": "figure.py", "old": "red", "new": "blue"})
    assert result.is_error
    assert (root / "figure.py").read_bytes() == before


def test_window_refuses_binary_or_split_start(toolbox):
    root, ts = toolbox
    read = ts["read_file"].handler
    (root / "binary").write_bytes(b"\xff\x00")
    assert read({"path": "binary", "byte_offset": 0}).code == "not_text"
    (root / "unicode").write_text("汉字", encoding="utf-8")
    assert read({"path": "unicode", "byte_offset": 1}).code == "not_text"


def test_window_discards_bytes_if_open_file_changes(toolbox, monkeypatch):
    _root, ts = toolbox
    fstat, count = os.fstat, 0

    def changed(fd):
        nonlocal count
        count += 1
        st = fstat(fd)
        if count == 2:
            return SimpleNamespace(
                **{
                    key: getattr(st, key) + (1 if key == "st_mtime_ns" else 0)
                    for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                }
            )
        return st

    monkeypatch.setattr(tools.os, "fstat", changed)
    result = ts["read_file"].handler({"path": "figure.py", "byte_offset": 0})
    assert (result.is_error, result.code) == (True, "file_changed")
    assert "color" not in result.content
