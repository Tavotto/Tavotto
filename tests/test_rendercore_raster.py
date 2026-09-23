"""`RasterBuffer` 的形状合同（统一实施包 U07，ADR 0066）——纯模型，不需要候选包。

RC-051 的主语：**通道顺序 / stride / alpha 语义**。带行尾填充的缓冲区要被正确剥掉；alpha 只有 straight
一种；预乘 / 非 8 bit / 非 sRGB 在构造时就拒。
"""

from __future__ import annotations

import pytest

from tavotto.rendercore.raster import RasterBuffer, RasterError


def _padded(width: int, height: int, channels: int, pad: int) -> tuple[bytes, int]:
    """每行 width×channels 个递增字节 + pad 个 0xEE 填充；返回 (samples, stride)。"""
    stride = width * channels + pad
    rows = []
    for y in range(height):
        row = bytes((y * 16 + i) % 256 for i in range(width * channels))
        rows.append(row + b"\xee" * pad)
    return b"".join(rows), stride


def test_rows_strip_the_padding_and_packed_is_height_times_row_bytes():
    samples, stride = _padded(3, 2, 4, pad=5)
    buf = RasterBuffer(3, 2, 4, samples, stride)
    rows = list(buf.rows())
    assert len(rows) == 2 and all(len(r) == 12 for r in rows)
    assert b"\xee" not in buf.packed()
    assert buf.packed() == rows[0] + rows[1]
    assert buf.pixel(1, 1) == (16 + 4, 16 + 5, 16 + 6, 16 + 7)


def test_a_compact_buffer_is_not_copied_by_packed():
    samples, stride = _padded(4, 3, 3, pad=0)
    buf = RasterBuffer(4, 3, 3, samples, stride)
    assert buf.packed() is samples


def test_split_alpha_separates_rgb_from_straight_alpha_and_none_for_rgb():
    samples, stride = _padded(2, 1, 4, pad=2)
    buf = RasterBuffer(2, 1, 4, samples, stride)
    rgb, alpha = buf.split_alpha()
    assert rgb == bytes([0, 1, 2, 4, 5, 6]) and alpha == bytes([3, 7])
    rgb3, none = RasterBuffer(2, 1, 3, bytes(range(6)), 6).split_alpha()
    assert rgb3 == bytes(range(6)) and none is None


@pytest.mark.parametrize(
    "kwargs, why",
    [
        (dict(width=0, height=1, channels=3, samples=b"", stride=0), "尺寸"),
        (dict(width=1, height=1, channels=2, samples=b"\0\0", stride=2), "通道"),
        (dict(width=2, height=1, channels=3, samples=b"\0" * 6, stride=5), "stride"),
        (dict(width=2, height=2, channels=3, samples=b"\0" * 11, stride=6), "字节"),
        (
            dict(width=1, height=1, channels=4, samples=b"\0" * 4, stride=4, premultiplied=True),
            "预乘",
        ),
        (dict(width=1, height=1, channels=3, samples=b"\0" * 3, stride=3, bit_depth=16), "bit"),
        (
            dict(width=1, height=1, channels=3, samples=b"\0" * 3, stride=3, colorspace="cmyk"),
            "srgb",
        ),
    ],
)
def test_malformed_buffers_are_rejected_at_construction(kwargs, why):
    with pytest.raises(RasterError):
        RasterBuffer(**kwargs)


def test_alpha_semantics_are_declared_straight_and_immutable():
    buf = RasterBuffer(1, 1, 4, b"\x10\x20\x30\x80", 4)
    assert buf.has_alpha and buf.premultiplied is False and buf.bit_depth == 8
    with pytest.raises(AttributeError):
        buf.premultiplied = True  # type: ignore[misc]


# ---------------------------------------------------------------- 并行 zlib（pigz 式）


def _payload(n: int) -> bytes:
    """像真内容流 / 位图行一样有重复、也有变化（纯常数会让压缩器走捷径，看不出跨块引用）。"""
    import hashlib

    out = bytearray()
    i = 0
    while len(out) < n:
        out += b"q 1 0 0 rg %d %d 80 40 re f Q\n" % (i % 997, i % 13)
        if i % 50 == 0:
            out += hashlib.sha256(str(i).encode()).digest()
        i += 1
    return bytes(out[:n])


def test_zlib_compress_within_one_block_is_byte_identical_to_zlib():
    import zlib

    from tavotto.rendercore import raster

    for data in (b"", b"x", _payload(raster.DEFLATE_BLOCK)):
        assert raster.zlib_compress(data) == zlib.compress(data, 6)


@pytest.mark.parametrize("level", [1, 6, 9])
def test_zlib_compress_of_many_blocks_is_one_valid_stream_independent_of_the_thread_count(
    level, monkeypatch
):
    """主语：拼出来的**整段**是一条合法 zlib 流（`zlib.decompress` 核头的 FCHECK 与尾的 adler32），解出的就是
    原文；块边界只由输入长度定——线程池 1 个线程与 8 个线程给出同一份字节（确定性不依赖调度）。"""
    import zlib

    from tavotto.rendercore import raster

    data = _payload(3 * raster.DEFLATE_BLOCK + 12345)
    many = raster.zlib_compress(data, level)
    assert zlib.decompress(many) == data
    monkeypatch.setattr(raster, "_workers", lambda: 1)
    assert raster.zlib_compress(data, level) == many
    # 压缩率与单线程同档（每块带前一块的 32 KiB 当字典）：不许因为切块明显变大
    assert len(many) <= len(zlib.compress(data, level)) * 1.02 + 64
