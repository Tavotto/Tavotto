"""`tavotto/tiffwrite.py`：纯标准库 TIFF 编码器（ADR 0046）。

读取端是 `tests/support/tiffcheck.py`——与写入端**无一行共享代码**；Pillow 在场
（`ci` extra / 本机 .venv）时再用它解一遍当第三把尺。两把尺都是「按 TIFF 6.0
读出来的东西 == 喂进去的东西」，任何一把量出偏差都是写入端的缺陷。
"""

from __future__ import annotations

import struct

import pytest

from tavotto import tiffwrite
from tests.support import tiffcheck


def _gradient(w: int, h: int, channels: int) -> bytes:
    out = bytearray()
    for y in range(h):
        for x in range(w):
            px = [(x * 7) & 255, (y * 13) & 255, (x + y) & 255]
            if channels == 4:
                px.append((x * y) & 255)
            out += bytes(px)
    return bytes(out)


def test_rgb_roundtrips_through_an_independent_reader(tmp_path):
    w, h = 37, 11  # 奇数宽：行字节数是奇数，strip 对齐那条分支才会走到
    samples = _gradient(w, h, 3)
    facts = tiffwrite.write_tiff(tmp_path / "a.tiff", w, h, samples, 3, dpi=300)
    tags, pixels = tiffcheck.decode_samples(tmp_path / "a.tiff")
    assert (tmp_path / "a.tiff").read_bytes()[:4] == b"II*\x00"
    assert tags["width"] == w and tags["height"] == h
    assert tags["bits_per_sample"] == [8, 8, 8]
    assert tags["samples_per_pixel"] == 3
    assert tags["compression"] == 8, "要的是 Adobe Deflate（无损）"
    assert tags["photometric"] == 2
    assert tags["planar_config"] == 1
    assert tags["x_resolution"] == 300 and tags["y_resolution"] == 300
    assert tags["resolution_unit"] == 2, "英寸"
    assert tags["software"] == "Tavotto"
    assert "extra_samples" not in tags
    assert tags["next_ifd"] == 0
    assert pixels == samples, "解出来的像素与喂进去的不是同一份"
    assert facts == {
        "px_w": w,
        "px_h": h,
        "channels": 3,
        "strips": 1,
        "compression": "deflate",
        "dpi": 300.0,
    }


def test_rgba_declares_unassociated_alpha(tmp_path):
    w, h = 8, 5
    samples = _gradient(w, h, 4)
    tiffwrite.write_tiff(tmp_path / "a.tiff", w, h, samples, 4, dpi=600)
    tags, pixels = tiffcheck.decode_samples(tmp_path / "a.tiff")
    assert tags["samples_per_pixel"] == 4
    assert tags["bits_per_sample"] == [8, 8, 8, 8]
    assert tags["extra_samples"] == 2, "非预乘 alpha（与 PyMuPDF / PNG 的像素语义一致）"
    assert pixels == samples


def test_unknown_density_is_written_as_no_absolute_unit_not_a_made_up_number(tmp_path):
    """`dpi=None` 不许变成 72 / 96 / 1 dpi：TIFF 自己有「没有绝对单位」这一档。"""
    tiffwrite.write_tiff(tmp_path / "a.tiff", 4, 4, _gradient(4, 4, 3), 3, dpi=None)
    tags = tiffcheck.read_tags(tmp_path / "a.tiff")
    assert tags["resolution_unit"] == 1
    assert tags["x_resolution"] == 1 and tags["y_resolution"] == 1
    pil = pytest.importorskip("PIL.Image")
    with pil.open(tmp_path / "a.tiff") as im:
        im.load()
        assert "dpi" not in im.info, f"Pillow 不该读出一个 dpi 来：{im.info.get('dpi')}"


def test_padded_rows_are_compacted(tmp_path):
    """`stride` 大于 `width × channels` 时（有行填充的缓冲区）只取有效字节。"""
    w, h = 5, 3
    rows = [_gradient(w, 1, 3) + b"\xee\xee\xee" for _ in range(h)]  # 每行 3 字节垃圾
    samples = b"".join(rows)
    tiffwrite.write_tiff(tmp_path / "a.tiff", w, h, samples, 3, stride=w * 3 + 3, dpi=72)
    _, pixels = tiffcheck.decode_samples(tmp_path / "a.tiff")
    assert pixels == _gradient(w, 1, 3) * h
    assert b"\xee\xee\xee" not in pixels


def test_large_images_are_split_into_strips_that_reassemble_exactly(tmp_path, monkeypatch):
    """多 strip 那条路：偏移、字节数、对齐都得对，拼回来逐字节相同。"""
    monkeypatch.setattr(tiffwrite, "STRIP_TARGET_BYTES", 100)  # 逼出很多条 strip
    w, h = 9, 40  # 一行 27 字节 → 每条 3 行 → 14 条
    samples = _gradient(w, h, 3)
    facts = tiffwrite.write_tiff(tmp_path / "a.tiff", w, h, samples, 3, dpi=150)
    tags, pixels = tiffcheck.decode_samples(tmp_path / "a.tiff")
    assert facts["strips"] == 14
    assert len(tags["strip_offsets"]) == 14 == len(tags["strip_byte_counts"])
    assert tags["rows_per_strip"] == 3
    assert all(o % 2 == 0 for o in tags["strip_offsets"]), "strip 要落在字边界上"
    assert pixels == samples


def test_non_integer_dpi_keeps_three_decimals(tmp_path):
    tiffwrite.write_tiff(tmp_path / "a.tiff", 2, 2, _gradient(2, 2, 3), 3, dpi=299.9994)
    tags = tiffcheck.read_tags(tmp_path / "a.tiff")
    assert tags["x_resolution"] == pytest.approx(299.999, abs=1e-3)


@pytest.mark.parametrize(
    "w, h, channels, stride, nbytes",
    [
        (0, 4, 3, None, 0),  # 宽为 0
        (4, 4, 2, None, 32),  # 灰度不写（调用方先转 RGB）
        (4, 4, 3, 8, 48),  # stride 小于一行
        (4, 4, 3, None, 47),  # 缓冲区差一个字节
    ],
)
def test_malformed_input_is_refused_before_writing(tmp_path, w, h, channels, stride, nbytes):
    with pytest.raises(tiffwrite.TiffWriteError):
        tiffwrite.write_tiff(tmp_path / "a.tiff", w, h, b"\x00" * nbytes, channels, stride=stride)
    assert not (tmp_path / "a.tiff").exists(), "拒绝的输入不该留下半个文件"


def test_pillow_reads_the_same_pixels_when_available(tmp_path):
    """第三把尺：Pillow（libtiff）。与我们的读取端互不相识，两把都过才算。"""
    pil = pytest.importorskip("PIL.Image")
    w, h = 23, 17
    for channels in (3, 4):
        samples = _gradient(w, h, channels)
        tiffwrite.write_tiff(tmp_path / f"{channels}.tiff", w, h, samples, channels, dpi=600)
        with pil.open(tmp_path / f"{channels}.tiff") as im:
            im.load()
            assert im.size == (w, h)
            assert im.mode == ("RGBA" if channels == 4 else "RGB")
            assert im.info["dpi"] == (600.0, 600.0)
            assert im.info.get("compression") == "tiff_adobe_deflate"
            assert im.tobytes() == samples


def test_ifd_entries_are_sorted_ascending(tmp_path):
    """TIFF 6.0 要求标签升序；乱序的文件很多读取端直接拒收（Photoshop 会报损坏）。"""
    tiffwrite.write_tiff(tmp_path / "a.tiff", 3, 3, _gradient(3, 3, 4), 4, dpi=300)
    data = (tmp_path / "a.tiff").read_bytes()
    (ifd,) = struct.unpack("<I", data[4:8])
    (n,) = struct.unpack("<H", data[ifd : ifd + 2])
    tags = [struct.unpack("<H", data[ifd + 2 + 12 * i : ifd + 4 + 12 * i])[0] for i in range(n)]
    assert tags == sorted(tags), tags
    assert len(set(tags)) == len(tags), "同一个标签写了两遍"
