"""纯标准库 TIFF 编码器 —— 导出管线里「位图 → .tiff」的唯一出处（ADR 0046）。

### 为什么自己写，而不是 Pillow

写它的时候（ADR 0046）Flask 父进程的依赖边界是 flask + pymupdf，Pillow 不在父进程里；
旧后端的 `Pixmap.save()` 不认 TIFF、`pil_save()` 要 Pillow。为一个容器格式把图像库拖进
父进程等于把那条边界松掉一格，而 Baseline TIFF + Deflate 用 `struct` + `zlib` 一百多行就能
写对，且**产物可以被任何 TIFF 读取端独立解码**（测试里用 Pillow 与独立读取器做对拍，两侧
与本模块无一行共享代码）。U10 之后 Pillow 进了运行时闭包（`rendercore/rasterio.py` 解码
位图素材，ADR 0066 / 0072），本模块**仍是唯一的 TIFF 写入器**——RenderCore 的 `raster.write_tiff`
复用它（PNG 与 TIFF 从同一个 `RasterBuffer` 编码，RC-052 / RC-053）。

### 写出来的是什么

* Baseline TIFF 6.0，小端（`II`），单页，RGB 或 RGBA（`ExtraSamples = 2`，
  非预乘 alpha——与 `RasterBuffer` 的像素语义一致（straight alpha），PNG 那条路写出去的也是它）；
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

from . import deflate
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


def _strips(samples, height: int, row_bytes: int, stride: int, rows_per_strip: int):
    """逐条产出**紧凑**的条带明文（行尾填充剥掉）。只复制当前这一条（~1 MiB）——整幅紧凑像素从不整份拼出来
    （PDFium 的 BGR 行按 4 字节对齐，宽不是 4 的倍数时 stride 就有填充，旧做法那时多一份整图）。"""
    view = memoryview(samples)
    for y0 in range(0, height, rows_per_strip):
        y1 = min(height, y0 + rows_per_strip)
        if stride == row_bytes:
            yield bytes(view[y0 * row_bytes : y1 * row_bytes])
        else:
            yield b"".join(view[y * stride : y * stride + row_bytes] for y in range(y0, y1))


def _entries(
    width: int, height: int, channels: int, rows_per_strip: int, byte_counts: list[int], dpi
) -> list[tuple[int, int, int, bytes]]:
    """IFD 条目（tag, type, count, payload），标签升序；StripOffsets 的 payload 留空，排版时填。"""
    n_strips = len(byte_counts)
    software = (brand.PRODUCT_NAME + "\x00").encode("ascii")
    entries: list[tuple[int, int, int, bytes]] = [
        (_IMAGE_WIDTH, _LONG, 1, struct.pack("<I", width)),
        (_IMAGE_LENGTH, _LONG, 1, struct.pack("<I", height)),
        (_BITS_PER_SAMPLE, _SHORT, channels, struct.pack(f"<{channels}H", *([8] * channels))),
        (_COMPRESSION, _SHORT, 1, struct.pack("<H", COMPRESSION_DEFLATE)),
        (_PHOTOMETRIC, _SHORT, 1, struct.pack("<H", PHOTOMETRIC_RGB)),
        (_STRIP_OFFSETS, _LONG, n_strips, b""),  # 排版时回填
        (_SAMPLES_PER_PIXEL, _SHORT, 1, struct.pack("<H", channels)),
        (_ROWS_PER_STRIP, _LONG, 1, struct.pack("<I", rows_per_strip)),
        (_STRIP_BYTE_COUNTS, _LONG, n_strips, struct.pack(f"<{n_strips}I", *byte_counts)),
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
    return entries


def _payload_size(tag: int, payload: bytes, n_strips: int) -> int:
    return 4 * n_strips if tag == _STRIP_OFFSETS else len(payload)


def _ifd_size(entries, n_strips: int) -> int:
    """IFD 本体 + 紧随其后的溢出值（>4 字节的 payload，按字对齐）的总字节数——与取值无关。"""
    size = 2 + 12 * len(entries) + 4
    for tag, _typ, _count, payload in entries:
        n = _payload_size(tag, payload, n_strips)
        if n > 4:
            size += n + (n % 2)
    return size


def _ifd_bytes(entries, ifd_offset: int, strip_offsets: list[int]) -> bytes:
    """IFD 落在 `ifd_offset`、溢出值紧随其后；StripOffsets 用 `strip_offsets` 填。IFD 在前（整幅）与在后（流式）
    两种布局共用这一份。"""
    n_strips = len(strip_offsets)
    entries = [
        (tag, typ, count, struct.pack(f"<{n_strips}I", *strip_offsets))
        if tag == _STRIP_OFFSETS
        else (tag, typ, count, payload)
        for tag, typ, count, payload in entries
    ]
    cursor = ifd_offset + 2 + 12 * len(entries) + 4
    slot_of: dict[int, int] = {}
    for tag, _typ, _count, payload in entries:
        if len(payload) > 4:
            slot_of[tag] = cursor
            cursor += len(payload) + (len(payload) % 2)
    out = bytearray(struct.pack("<H", len(entries)))
    for tag, typ, count, payload in entries:
        out += struct.pack("<HHI", tag, typ, count)
        out += struct.pack("<I", slot_of[tag]) if tag in slot_of else payload.ljust(4, b"\x00")
    out += struct.pack("<I", 0)  # 没有下一个 IFD
    for tag, _typ, _count, payload in entries:
        if tag in slot_of:
            assert ifd_offset + len(out) == slot_of[tag], (tag, len(out), slot_of[tag])
            out += payload + (b"\x00" if len(payload) % 2 else b"")
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

    rows_per_strip = max(1, STRIP_TARGET_BYTES // row_bytes)
    # 条带各自独立 `zlib.compress`：线程池并行、按顺序收，逐字节等于串行（ADR 0077 P1）；在飞的条带数有上限
    strips: list[bytes] = list(
        deflate.compress_each(
            _strips(samples, height, row_bytes, stride, rows_per_strip), DEFLATE_LEVEL
        )
    )
    n_strips = len(strips)
    entries = _entries(width, height, channels, rows_per_strip, [len(x) for x in strips], dpi)
    known_dpi = dpi is not None and float(dpi) > 0

    # ---- 布局：头(8) → IFD → 内联放不下的值 → strip 数据 ----
    # 溢出值的**大小**与它们的取值无关（StripOffsets 恒是 4 × n_strips 字节），
    # 所以先按大小排版、算出 strip 的落点，再回过头填 StripOffsets 的真实值。
    ifd_offset = 8
    data_offset = ifd_offset + _ifd_size(entries, n_strips)
    strip_offsets: list[int] = []
    pos = data_offset
    for x in strips:
        strip_offsets.append(pos)
        pos += len(x) + (len(x) % 2)

    out = bytearray()
    out += MAGIC_LE + struct.pack("<I", ifd_offset)
    out += _ifd_bytes(entries, ifd_offset, strip_offsets)
    assert len(out) == data_offset, (len(out), data_offset)
    for x in strips:
        out += x + (b"\x00" if len(x) % 2 else b"")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return {
        "px_w": width,
        "px_h": height,
        "channels": channels,
        "strips": n_strips,
        "compression": "deflate",
        "dpi": float(dpi) if known_dpi else None,
    }


class TiffStreamWriter:
    """条带栅格（ADR 0077 P2）的 TIFF 写入器：按行带逐次 `feed(samples, rows, stride)`，凑满一条 strip 就交给
    `deflate.OrderedPool` 并行压、按顺序**边收边写**进文件——整幅像素与整份压缩结果都不在内存里。布局与
    `write_tiff` 不同的只有一处：strip 数据在前、IFD 在**末尾**（写完才知道各条大小；TIFF 6.0 允许 IFD 在任何
    字对齐的位置，头里的偏移最后回填）。每条 strip 仍是这几行紧凑像素的 `zlib.compress(…, DEFLATE_LEVEL)`，
    行数切分与 `write_tiff` 同一条规则。经典 TIFF 的偏移是 32 位：写过 4 GiB 就结构化拒绝，不写坏文件。"""

    _LIMIT = 0xFFFF_FFFF

    def __init__(self, path, width: int, height: int, channels: int, *, dpi=None) -> None:
        width, height, channels = int(width), int(height), int(channels)
        if width <= 0 or height <= 0:
            raise TiffWriteError(f"尺寸必须为正：{width}×{height}")
        if channels not in (3, 4):
            raise TiffWriteError(f"只写 RGB / RGBA（3 或 4 通道），收到 {channels}")
        self.width, self.height, self.channels, self.dpi = width, height, channels, dpi
        self._rb = width * channels
        self._rows_per_strip = max(1, STRIP_TARGET_BYTES // self._rb)
        self._pending: list = []
        self._pending_rows = 0
        self._rows = 0
        self._offsets: list[int] = []
        self._counts: list[int] = []
        self._pool = deflate.OrderedPool(lambda c: zlib.compress(c, DEFLATE_LEVEL))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "w+b")  # noqa: SIM115 —— close()/abort() 关
        self._fh.write(MAGIC_LE + struct.pack("<I", 0))  # IFD 偏移最后回填

    def _write_strips(self, compressed: list[bytes]) -> None:
        for x in compressed:
            pos = self._fh.tell()
            if pos + len(x) + 1 > self._LIMIT:
                raise TiffWriteError("超过经典 TIFF 的 4 GiB 偏移上限")
            self._offsets.append(pos)
            self._counts.append(len(x))
            self._fh.write(x + (b"\x00" if len(x) % 2 else b""))

    def _flush(self) -> None:
        strip = b"".join(self._pending)
        self._pending, self._pending_rows = [], 0
        self._write_strips(self._pool.put(strip))

    def feed(self, samples, rows: int, stride: int | None = None) -> None:
        stride = self._rb if stride is None else int(stride)
        if stride < self._rb or len(samples) < stride * (rows - 1) + self._rb:
            raise TiffWriteError(
                f"行带 {rows} 行 × stride {stride} 装不下 {self.width}×{self.channels}"
            )
        if self._rows + rows > self.height:
            raise TiffWriteError(f"行数超出：{self._rows} + {rows} > {self.height}")
        view, rb = memoryview(samples), self._rb
        for y in range(rows):
            self._pending.append(view[y * stride : y * stride + rb])
            self._pending_rows += 1
            if self._pending_rows == self._rows_per_strip:
                self._flush()
        self._rows += rows

    def close(self) -> dict:
        try:
            if self._rows != self.height:
                raise TiffWriteError(f"TIFF 只收到 {self._rows} / {self.height} 行")
            if self._pending:
                self._flush()
            self._write_strips(self._pool.finish())
            entries = _entries(
                self.width, self.height, self.channels, self._rows_per_strip, self._counts, self.dpi
            )
            ifd_offset = self._fh.tell()  # strip 都按字对齐写，这里一定是偶数
            self._fh.write(_ifd_bytes(entries, ifd_offset, self._offsets))
            self._fh.seek(4)
            self._fh.write(struct.pack("<I", ifd_offset))
        except BaseException:
            self.abort()
            raise
        self._fh.close()
        known_dpi = self.dpi is not None and float(self.dpi) > 0
        return {
            "px_w": self.width,
            "px_h": self.height,
            "channels": self.channels,
            "strips": len(self._offsets),
            "compression": "deflate",
            "dpi": float(self.dpi) if known_dpi else None,
        }

    def abort(self) -> None:
        self._pool.abort()
        self._fh.close()
