"""纯标准库 TIFF 编码器 —— 导出管线里「位图 → .tiff」的唯一出处（ADR 0046）。

### 为什么自己写，而不是 Pillow

Flask 父进程的依赖边界是 **flask + pymupdf**（`pyproject.toml`，`src/tavotto/AGENTS.md`
「进程与依赖边界」），Pillow 只在 worker 解释器里（matplotlib 的依赖）与 CI 的
`ci` extra 里有。画布合成与「按原图」导出都在父进程的 PyMuPDF 里出位图，而
`Pixmap.save()` 不认 TIFF、`Pixmap.pil_save()` 要 Pillow。为一个容器格式把图像库
拖进父进程，等于把那条边界松掉一格；而 Baseline TIFF + Deflate 用 `struct` + `zlib`
一百多行就能写对，且**产物可以被任何 TIFF 读取端独立解码**（测试里用 Pillow 与
PyMuPDF 做对拍，两侧与本模块无一行共享代码）。

### 写出来的是什么

* Baseline TIFF 6.0，小端（`II`），单页，RGB 或 RGBA（`ExtraSamples = 2`，
  非预乘 alpha——与 PyMuPDF 的像素语义一致，PNG 那条路写出去的也是它）；
* 压缩 **8 = Adobe Deflate**（zlib），无损。不选 LZW 的理由是纯 Python 的 LZW
  逐字节编码一张 600 ppi 的整页要跑几十秒，Deflate 由 zlib 的 C 实现完成；
  libtiff / Pillow / Photoshop / ImageMagick / macOS 预览 / Windows 照片都认 8；
* 条带（strip）约 1 MiB 一条，`RowsPerStrip` 按行字节数算，最后一条可以短；
* 分辨率：给了 `dpi` 就写 `XResolution` / `YResolution`（RATIONAL）+
  `ResolutionUnit = 2`（英寸）；**没给就写 `ResolutionUnit = 1`**（TIFF 6.0 §8
  "No absolute unit of measurement"）+ 1/1——这是 TIFF 自己表达「没有物理尺寸」
  的写法，Pillow 读到它时给 `info["resolution"]` 而**不给** `info["dpi"]`。整组
  不写反而更坏：读取端会按默认值把它当 1 dpi 或 72 dpi 报出来。比编一个 72 或
  96 诚实（同 `engine/originalspec` 的「没测量的维度一律 None」）。

不做的事：多页、调色板、CMYK、预测器（predictor 2 纯 Python 逐字节差分同样慢，
且对科研图的白底大面积收益有限）、BigTIFF（4 GiB 以上；PPI 上限 1200 下一张
A3 也只有 ~600 MB 原始像素）。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from .engine import brand

#: TIFF 头：小端 + 魔数 42。读取端认的第一样东西。
MAGIC_LE = b"II*\x00"

COMPRESSION_DEFLATE = 8
PHOTOMETRIC_RGB = 2
RESUNIT_NONE = 1  # 没有绝对单位（TIFF 6.0 §8）
RESUNIT_INCH = 2
EXTRASAMPLE_UNASSOCIATED_ALPHA = 2

#: 一条 strip 的目标字节数（压缩前）。TIFF 6.0 建议 8 KiB，那是 1992 年的内存
#: 口径；今天 1 MiB 让一张 4000×3000 的图只有十几条，IFD 短、读取端一次 seek。
STRIP_TARGET_BYTES = 1 << 20

#: zlib 级别。6 是默认档：再往上 CPU 翻倍、体积只小百分之几。
DEFLATE_LEVEL = 6

# TIFF 字段类型
_SHORT, _LONG, _RATIONAL, _ASCII = 3, 4, 5, 2

# 标签号
_IMAGE_WIDTH = 256
_IMAGE_LENGTH = 257
_BITS_PER_SAMPLE = 258
_COMPRESSION = 259
_PHOTOMETRIC = 262
_STRIP_OFFSETS = 273
_SAMPLES_PER_PIXEL = 277
_ROWS_PER_STRIP = 278
_STRIP_BYTE_COUNTS = 279
_X_RESOLUTION = 282
_Y_RESOLUTION = 283
_PLANAR_CONFIG = 284
_RESOLUTION_UNIT = 296
_SOFTWARE = 305
_EXTRA_SAMPLES = 338


class TiffWriteError(ValueError):
    """输入不成形（尺寸 / 通道数 / 缓冲区长度对不上）。"""


def _rational(value: float) -> bytes:
    """RATIONAL = 两个 LONG（分子 / 分母）。整数 dpi 原样进；非整数保留三位小数。"""
    if float(value).is_integer():
        return struct.pack("<II", int(value), 1)
    return struct.pack("<II", int(round(float(value) * 1000)), 1000)


def _rows(samples: bytes, width: int, height: int, channels: int, stride: int) -> bytes:
    """把可能带行填充的缓冲区整理成紧凑的 `height × (width × channels)`。"""
    row_bytes = width * channels
    if stride == row_bytes:
        return samples[: row_bytes * height]
    out = bytearray(row_bytes * height)
    for y in range(height):
        start = y * stride
        out[y * row_bytes : (y + 1) * row_bytes] = samples[start : start + row_bytes]
    return bytes(out)


def write_tiff(
    path: Path | str,
    width: int,
    height: int,
    samples: bytes,
    channels: int,
    *,
    stride: int | None = None,
    dpi: float | None = None,
) -> dict:
    """把 8 bit RGB / RGBA 像素写成一个 Deflate 压缩的 TIFF 文件。

    * `samples`：逐行、逐像素、逐通道的 8 bit 值（PyMuPDF `Pixmap.samples` 的布局）；
    * `channels`：3（RGB）或 4（RGBA，非预乘）；
    * `stride`：一行占多少字节（缺省 `width × channels`，PyMuPDF 给 `Pixmap.stride`）；
    * `dpi`：物理分辨率；`None` = 不知道，写成「没有绝对单位」而不是编一个数。

    回 `{px_w, px_h, channels, strips, compression, dpi}`——都是写进文件的事实。
    """
    width, height, channels = int(width), int(height), int(channels)
    if width <= 0 or height <= 0:
        raise TiffWriteError(f"尺寸必须为正：{width}×{height}")
    if channels not in (3, 4):
        raise TiffWriteError(f"只写 RGB / RGBA（3 或 4 通道），收到 {channels}")
    row_bytes = width * channels
    stride = row_bytes if stride is None else int(stride)
    if stride < row_bytes:
        raise TiffWriteError(f"stride {stride} 小于一行的 {row_bytes} 字节")
    if len(samples) < stride * (height - 1) + row_bytes:
        raise TiffWriteError(
            f"像素缓冲区只有 {len(samples)} 字节，装不下 {width}×{height}×{channels}"
        )

    pixels = _rows(samples, width, height, channels, stride)
    rows_per_strip = max(1, STRIP_TARGET_BYTES // row_bytes)
    strips: list[bytes] = []
    for y0 in range(0, height, rows_per_strip):
        y1 = min(height, y0 + rows_per_strip)
        strips.append(zlib.compress(pixels[y0 * row_bytes : y1 * row_bytes], DEFLATE_LEVEL))
    n_strips = len(strips)

    software = (brand.PRODUCT_NAME + "\x00").encode("ascii")

    # ---- IFD 条目（tag, type, count, payload）。payload 不到 4 字节的内联 ----
    entries: list[tuple[int, int, int, bytes]] = [
        (_IMAGE_WIDTH, _LONG, 1, struct.pack("<I", width)),
        (_IMAGE_LENGTH, _LONG, 1, struct.pack("<I", height)),
        (_BITS_PER_SAMPLE, _SHORT, channels, struct.pack(f"<{channels}H", *([8] * channels))),
        (_COMPRESSION, _SHORT, 1, struct.pack("<H", COMPRESSION_DEFLATE)),
        (_PHOTOMETRIC, _SHORT, 1, struct.pack("<H", PHOTOMETRIC_RGB)),
        (_STRIP_OFFSETS, _LONG, n_strips, b""),  # 稍后回填
        (_SAMPLES_PER_PIXEL, _SHORT, 1, struct.pack("<H", channels)),
        (_ROWS_PER_STRIP, _LONG, 1, struct.pack("<I", rows_per_strip)),
        (_STRIP_BYTE_COUNTS, _LONG, n_strips, struct.pack(f"<{n_strips}I", *map(len, strips))),
    ]
    known_dpi = dpi is not None and float(dpi) > 0
    entries += [
        (_X_RESOLUTION, _RATIONAL, 1, _rational(dpi if known_dpi else 1)),
        (_Y_RESOLUTION, _RATIONAL, 1, _rational(dpi if known_dpi else 1)),
        (_PLANAR_CONFIG, _SHORT, 1, struct.pack("<H", 1)),
        (
            _RESOLUTION_UNIT,
            _SHORT,
            1,
            struct.pack("<H", RESUNIT_INCH if known_dpi else RESUNIT_NONE),
        ),
        (_SOFTWARE, _ASCII, len(software), software),
    ]
    if channels == 4:
        entries.append(
            (_EXTRA_SAMPLES, _SHORT, 1, struct.pack("<H", EXTRASAMPLE_UNASSOCIATED_ALPHA))
        )
    entries.sort(key=lambda e: e[0])  # TIFF 要求标签升序

    # ---- 布局：头(8) → IFD → 内联放不下的值 → strip 数据 ----
    # 溢出值的**大小**与它们的取值无关（StripOffsets 恒是 4 × n_strips 字节），
    # 所以先按大小排版、算出 strip 的落点，再回过头填 StripOffsets 的真实值。
    ifd_offset = 8
    ifd_size = 2 + 12 * len(entries) + 4
    cursor = ifd_offset + ifd_size
    slot_of: dict[int, int] = {}  # 溢出值各自的偏移
    for tag, _typ, _count, payload in entries:
        size = 4 * n_strips if tag == _STRIP_OFFSETS else len(payload)
        if size > 4:
            slot_of[tag] = cursor
            cursor += size + (size % 2)  # 字偏移对齐
    data_offset = cursor
    strip_offsets: list[int] = []
    pos = data_offset
    for s in strips:
        strip_offsets.append(pos)
        pos += len(s) + (len(s) % 2)
    entries = [
        (tag, typ, count, struct.pack(f"<{n_strips}I", *strip_offsets))
        if tag == _STRIP_OFFSETS
        else (tag, typ, count, payload)
        for tag, typ, count, payload in entries
    ]

    out = bytearray()
    out += MAGIC_LE + struct.pack("<I", ifd_offset)
    out += struct.pack("<H", len(entries))
    for tag, typ, count, payload in entries:
        out += struct.pack("<HHI", tag, typ, count)
        out += struct.pack("<I", slot_of[tag]) if tag in slot_of else payload.ljust(4, b"\x00")
    out += struct.pack("<I", 0)  # 没有下一个 IFD
    for tag, _typ, _count, payload in entries:
        if tag in slot_of:
            assert len(out) == slot_of[tag], (tag, len(out), slot_of[tag])
            out += payload + (b"\x00" if len(payload) % 2 else b"")
    assert len(out) == data_offset, (len(out), data_offset)
    for s in strips:
        out += s + (b"\x00" if len(s) % 2 else b"")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return {
        "px_w": width,
        "px_h": height,
        "channels": channels,
        "strips": n_strips,
        "compression": "deflate",
        "dpi": float(dpi) if known_dpi else None,
    }
