"""有界、确定性的并行 Deflate（`tavotto.deflate`，ADR 0077）与它的两个消费者：PNG 的 IDAT、TIFF 的条带。

判据的主语：**解码器看到的字节**（`zlib.decompress` 核 zlib 头的 FCHECK 与尾的 adler32；PNG 由测试侧纯标准库的
`pdfread.decode_png_any` 解；TIFF 由本文件自己按 IFD 读条带），以及**输出与线程数无关**（`deflate.workers` 换成 1）。
纯标准库，任何机器都跑。
"""

from __future__ import annotations

import hashlib
import struct
import sys
import zlib
from pathlib import Path

import pytest

from tavotto import deflate, tiffwrite
from tavotto.rendercore import raster

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402


def _payload(n: int) -> bytes:
    """像真内容流 / 位图行一样有重复、也有变化（纯常数会让压缩器走捷径，看不出跨块引用）。"""
    out = bytearray()
    i = 0
    while len(out) < n:
        out += b"q 1 0 0 rg %d %d 80 40 re f Q\n" % (i % 997, i % 13)
        if i % 50 == 0:
            out += hashlib.sha256(str(i).encode()).digest()
        i += 1
    return bytes(out[:n])


def _one_thread(monkeypatch):
    monkeypatch.setattr(deflate, "workers", lambda: 1)


# ---------------------------------------------------------------- deflate 本身


def test_within_one_block_the_output_is_byte_identical_to_zlib():
    for data in (b"", b"x", _payload(deflate.BLOCK)):
        assert deflate.zlib_compress(data) == zlib.compress(data, 6)
        assert deflate.zlib_stream([data]) == zlib.compress(data, 6)


@pytest.mark.parametrize("level", [1, 6, 9])
def test_many_blocks_make_one_valid_stream_independent_of_the_thread_count(level, monkeypatch):
    data = _payload(3 * deflate.BLOCK + 12345)
    many = deflate.zlib_compress(data, level)
    assert zlib.decompress(many) == data
    # 压缩率与单线程同档（每块带前一块的 32 KiB 当字典）：不许因为切块明显变大
    assert len(many) <= len(zlib.compress(data, level)) * 1.02 + 64
    _one_thread(monkeypatch)
    assert deflate.zlib_compress(data, level) == many


def test_a_stream_of_uneven_blocks_decodes_to_their_concatenation():
    blocks = [_payload(10), b"", _payload(deflate.BLOCK + 7), _payload(3)]
    assert zlib.decompress(deflate.zlib_stream(blocks)) == b"".join(blocks)


def test_compress_each_is_serial_zlib_per_item_in_order(monkeypatch):
    items = [_payload(n) for n in (1, 5000, deflate.BLOCK, 17)]
    assert list(deflate.compress_each(items, 6)) == [zlib.compress(x, 6) for x in items]
    _one_thread(monkeypatch)
    assert list(deflate.compress_each(items, 6)) == [zlib.compress(x, 6) for x in items]


def test_ordered_map_keeps_a_bounded_number_of_items_in_flight():
    """内存有界的前提：输入是惰性生成的大块时，产出第一个结果之前最多拉取 `inflight` 件——拉得越多，同时驻留
    的明文块越多（一张 600 ppi 的整页就是一百多块）。"""
    pulled = []

    def items():
        for i in range(100):
            pulled.append(i)
            yield i

    it = deflate.ordered_map(lambda x: x * 2, items(), inflight=3)
    assert next(it) == 0 and len(pulled) == 3
    assert list(it) == [x * 2 for x in range(1, 100)]


# ---------------------------------------------------------------- PNG


def _buffer(width: int, height: int, channels: int, pad: int = 0) -> raster.RasterBuffer:
    row = width * channels
    stride = row + pad
    data = bytearray()
    for y in range(height):
        line = bytes((x * 7 + y * 3 + c * 50) % 256 for x in range(width) for c in range(channels))
        data += line + b"\xee" * pad  # 行尾填充是不该出现在输出里的垃圾
    return raster.RasterBuffer(width, height, channels, bytes(data), stride, dpi=300.0)


def _old_encode_png(buf: raster.RasterBuffer) -> bytes:
    """P1 之前的编码器（整幅扫描线一次 `zlib.compress`）——小图的逐字节对照。"""

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    raw = b"".join(b"\x00" + row for row in buf.rows())
    ppm = int(round(buf.dpi / 0.0254))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(
                ">IIBBBBB", buf.width, buf.height, 8, 6 if buf.channels == 4 else 2, 0, 0, 0
            ),
        )
        + chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("channels,pad", [(3, 0), (3, 3), (4, 0)])
def test_a_multi_block_png_decodes_to_exactly_the_buffer_pixels(channels, pad, monkeypatch):
    """PNG 的像素合同不变：解码后逐个等于缓冲（行尾填充剥掉）；与线程数无关。"""
    monkeypatch.setattr(deflate, "BLOCK", 4096)  # 小图也切成很多块
    buf = _buffer(97, 211, channels, pad)
    png = raster.encode_png(buf)
    w, h, ch, pixels = pdfread.decode_png_any(png)
    assert (w, h, ch) == (97, 211, channels) and pixels == buf.packed()
    _one_thread(monkeypatch)
    assert raster.encode_png(buf) == png


def test_a_png_within_one_block_is_byte_identical_to_the_old_encoder():
    buf = _buffer(64, 40, 3)
    assert raster.encode_png(buf) == _old_encode_png(buf)


# ---------------------------------------------------------------- TIFF


def _tiff_strips(data: bytes) -> list[bytes]:
    """本文件自己的 IFD 读取：StripOffsets（273）/ StripByteCounts（279）→ 每条的已压缩字节。"""
    assert data[:4] == b"II*\x00"
    (ifd,) = struct.unpack_from("<I", data, 4)
    (n,) = struct.unpack_from("<H", data, ifd)
    tags = {}
    for i in range(n):
        tag, typ, count, val = struct.unpack_from("<HHII", data, ifd + 2 + 12 * i)
        tags[tag] = (count, val)

    def longs(tag):
        count, val = tags[tag]
        return [val] if count == 1 else list(struct.unpack_from(f"<{count}I", data, val))

    return [data[o : o + c] for o, c in zip(longs(273), longs(279))]


@pytest.mark.parametrize("channels,pad", [(3, 0), (3, 1), (4, 0)])
def test_tiff_strips_are_exactly_serial_zlib_of_the_compact_rows(
    channels, pad, tmp_path, monkeypatch
):
    """TIFF 的产物**逐字节**不变：每条条带 = 这几行紧凑像素（填充剥掉）的 `zlib.compress(…, 6)`，条带数与行数
    切分同旧；线程数不同文件相同。"""
    monkeypatch.setattr(tiffwrite, "STRIP_TARGET_BYTES", 500)
    buf = _buffer(53, 90, channels, pad)
    info = tiffwrite.write_tiff(
        tmp_path / "a.tiff", 53, 90, buf.samples, channels, stride=buf.stride, dpi=300
    )
    data = (tmp_path / "a.tiff").read_bytes()
    rows = list(buf.rows())
    per = max(1, 500 // (53 * channels))
    expected = [zlib.compress(b"".join(rows[y : y + per]), 6) for y in range(0, 90, per)]
    assert _tiff_strips(data) == expected and info["strips"] == len(expected)
    _one_thread(monkeypatch)
    tiffwrite.write_tiff(
        tmp_path / "b.tiff", 53, 90, buf.samples, channels, stride=buf.stride, dpi=300
    )
    assert (tmp_path / "b.tiff").read_bytes() == data
