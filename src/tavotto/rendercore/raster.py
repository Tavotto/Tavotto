"""RasterBuffer —— RenderCore 里一块像素的**唯一形状**（统一实施包 U07，ADR 0066）。

它出现在两处：位图源解码之后（`rasterio.decode()` → 写进 PDF 的 Image XObject）、Canonical PDF
栅格化之后（render child 的 PDFium 位图 → PNG / TIFF，`encode_png` / `write_tiff` 就在本模块）。两条路共用
同一个定义，PNG 与 TIFF 从**同一个 RasterBuffer** 编码——"同参数像素逐个相同"不是记得要一致，是同一份字节
（RC-052 / 053）。

## 合同（RC-051：通道、stride、alpha 语义只有这一份说法）

* `channels ∈ {3, 4}`：RGB 或 RGBA，**通道顺序 R, G, B(, A)**，每通道 8 bit（`bit_depth = 8`）。
  PDFium 的 BGRA 在 child 里就换成 RGBA 再交出来（`rev_byteorder=True`），本类不认识 BGR。
* `stride`：一行占多少字节，**≥ `width × channels`**；多出来的是行尾填充，`rows()` 把它剥掉。
  持有带填充的缓冲不复制；需要紧凑布局的编码器各自按行取。
* **alpha 一律 straight（非预乘）**：与 PNG（`IHDR` 色型 6）、TIFF（`ExtraSamples = 2`）、
  PDF `/SMask` 同一语义；PDFium 的 `FPDFBitmap_BGRA` 也是非预乘（`BGRA_Premul` 才是预乘，
  我们不请求它）。`premultiplied` 字段恒 False，留着是让「把 premultiplied 当 straight」这条
  must_fail 有一个能断言的主语。
* `colorspace = "srgb"`：8 bit sRGB / DeviceRGB，不做色彩管理（源 PNG 的 gAMA / iCCP 不解释，
  与旧后端相同）。
* `dpi`：物理密度；`None` = 不知道（TIFF 写「没有绝对单位」，PNG 不写 pHYs）——不编一个数。
* **所有权**：`samples` 是本对象自己的 `bytes`（不可变），构造时从 PDFium / Pillow 的缓冲**复制**
  出来；native 对象在复制完成后就可以释放，本对象的生命周期与它无关（RC-050 must_fail：
  共享已关闭的 native handle）。

纯标准库。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Iterator

from .. import deflate

#: 一块像素的预算（约 8000×8000）：位图**源**解码前按头里的尺寸判（`rasterio.decode(max_pixels=…)`），
#: render child 栅格前父子两侧各判一次（ADR 0066）。超过的不是「解慢一点」，是结构化拒绝——
#: 一张压缩得很小的高分辨率 PNG 解开是几百 MB，导出进程会被它挤死而不是报错（Codex #463 P2）。
SOURCE_MAX_PIXELS = 64_000_000
#: 一份文档里**全部**位图源解码后的像素总预算（去重后按资源算）。单张 64M 之下、张数不限的话，几张
#: 8000×8000 的 RGBA 各自 256 MB 留在 pikepdf 对象里直到 save()，导出进程照样被挤死（Codex #463 第二轮
#: P2）——预算是写入器一级的，不是每张各算一次。
DOCUMENT_MAX_PIXELS = 160_000_000


class RasterError(ValueError):
    """缓冲区不成形（尺寸 / 通道 / stride / 字节数对不上）。"""


@dataclass(frozen=True)
class RasterBuffer:
    width: int
    height: int
    channels: int
    samples: bytes
    stride: int
    dpi: float | None = None
    bit_depth: int = 8
    colorspace: str = "srgb"
    premultiplied: bool = False

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise RasterError(f"尺寸必须为正：{self.width}×{self.height}")
        if self.channels not in (3, 4):
            raise RasterError(f"只有 RGB / RGBA（3 或 4 通道），收到 {self.channels}")
        if self.bit_depth != 8:
            raise RasterError(f"只有 8 bit，收到 {self.bit_depth}")
        if self.premultiplied:
            raise RasterError("RasterBuffer 的 alpha 一律 straight，不接受预乘缓冲")
        if self.colorspace != "srgb":
            raise RasterError(f"只有 srgb，收到 {self.colorspace!r}")
        if self.stride < self.row_bytes:
            raise RasterError(f"stride {self.stride} 小于一行的 {self.row_bytes} 字节")
        need = self.stride * (self.height - 1) + self.row_bytes
        if not isinstance(self.samples, bytes) or len(self.samples) < need:
            raise RasterError(
                f"缓冲区 {len(self.samples)} 字节装不下 {self.width}×{self.height}×{self.channels}"
                f"（stride {self.stride} 需要 {need}）"
            )

    @property
    def row_bytes(self) -> int:
        return self.width * self.channels

    @property
    def has_alpha(self) -> bool:
        return self.channels == 4

    def rows(self) -> Iterator[bytes]:
        """逐行产出**紧凑**的像素字节（行尾填充剥掉）。"""
        rb, st = self.row_bytes, self.stride
        for y in range(self.height):
            start = y * st
            yield self.samples[start : start + rb]

    def packed(self) -> bytes:
        """整幅紧凑像素（`height × width × channels`）。stride 已紧凑时不复制。"""
        if self.stride == self.row_bytes:
            return self.samples[: self.row_bytes * self.height]
        return b"".join(self.rows())

    def split_alpha(self) -> tuple[bytes, bytes | None]:
        """(紧凑 RGB, 紧凑 alpha 或 None)。PDF 的 Image XObject 要把颜色与 /SMask 分开写。"""
        if self.channels == 3:
            return self.packed(), None
        packed = self.packed()
        rgb = bytearray(self.width * self.height * 3)
        alpha = bytearray(self.width * self.height)
        rgb[0::3] = packed[0::4]
        rgb[1::3] = packed[1::4]
        rgb[2::3] = packed[2::4]
        alpha[:] = packed[3::4]
        return bytes(rgb), bytes(alpha)

    def pixel(self, x: int, y: int) -> tuple[int, ...]:
        """(x, y) 处的通道值（y 从顶行起算）。测试与采样用。"""
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError((x, y))
        i = y * self.stride + x * self.channels
        return tuple(self.samples[i : i + self.channels])


# ---------------------------------------------------------------------------
# 编码：同一个 RasterBuffer → PNG / TIFF（RC-052 / RC-053：同一份字节，两个容器）
# ---------------------------------------------------------------------------
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: zlib 级别：与 `tiffwrite.DEFLATE_LEVEL` 同一档（6：再往上 CPU 翻倍、体积只小百分之几）。
PNG_DEFLATE_LEVEL = 6


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _png_rows_per_block(row_bytes: int) -> int:
    """扫描线凑块的唯一规则（整幅编码与条带编码共用）：块边界只由行宽定，输出因此确定。"""
    return max(1, deflate.BLOCK // (row_bytes + 1))


def _png_head(width: int, height: int, channels: int, dpi: float | None) -> bytes:
    color_type = 6 if channels == 4 else 2
    head = PNG_SIGNATURE + _png_chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    )
    if dpi is not None and dpi > 0:
        ppm = int(round(dpi / 0.0254))
        head += _png_chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    return head


def _png_scanline_blocks(buf: RasterBuffer) -> Iterator[bytes]:
    """PNG 的滤波后扫描线（每行前一个滤波字节 0 + 紧凑的行），按 `_png_rows_per_block` 凑块；任何时刻只有在飞的
    那几块被复制，整幅扫描线从不整份拼出来（旧做法多一份整图）。"""
    rb, st = buf.row_bytes, buf.stride
    rows = _png_rows_per_block(rb)
    view = memoryview(buf.samples)
    zero = b"\x00"
    for y0 in range(0, buf.height, rows):
        y1 = min(buf.height, y0 + rows)
        yield b"".join(part for y in range(y0, y1) for part in (zero, view[y * st : y * st + rb]))


def encode_png(buf: RasterBuffer) -> bytes:
    """RasterBuffer → PNG 字节：8 bit、色型 2（RGB）或 6（RGBA，straight alpha）、每行滤波 0、非交错；
    `dpi` 已知时写 `pHYs`（像素 / 米，单位 1），未知不写——不编一个数。行尾填充在这里剥掉。

    IDAT 是**一条** zlib 流，由 `deflate.zlib_stream` 按扫描线块并行压（ADR 0077 P1）：解出来与逐行串行压的
    是同一份扫描线（像素逐个相同）；压缩流的字节与单线程 `zlib.compress` 不同（块边界处 `Z_SYNC_FLUSH`），但同一输入
    永远同一份输出、与线程数无关。一块以内（小图）逐字节等于从前。"""
    idat = deflate.zlib_stream(_png_scanline_blocks(buf), PNG_DEFLATE_LEVEL)
    return b"".join(
        (
            _png_head(buf.width, buf.height, buf.channels, buf.dpi),
            _png_chunk(b"IDAT", idat),
            _png_chunk(b"IEND", b""),
        )
    )


class PngStreamWriter:
    """条带栅格（ADR 0077 P2）的 PNG 写入器：按行带逐次 `feed(RasterBuffer)`，扫描线按 `_png_rows_per_block` 凑块
    （与 `encode_png` 同一条规则），压好的片段**边收边写**成连续的 IDAT 块——整幅扫描线与整条压缩流都不在内存里。
    PNG 允许 IDAT 分成多块（解码器按顺序拼接）；把各块 IDAT 的数据拼起来，就是 `encode_png(同一幅像素)` 那条
    zlib 流，逐字节相同。`close()` 核行数；出错用 `abort()` 收线程、关文件（半截文件由调用方的临时目录收）。"""

    def __init__(self, path, width: int, height: int, channels: int, dpi: float | None) -> None:
        if channels not in (3, 4) or width <= 0 or height <= 0:
            raise RasterError(f"PNG 写入器不收 {width}×{height}×{channels}")
        self.width, self.height, self.channels = int(width), int(height), int(channels)
        self._rb = self.width * self.channels
        self._per_block = _png_rows_per_block(self._rb)
        self._pending: list = []
        self._pending_rows = 0
        self._rows = 0
        self._z = deflate.ZlibStreamer(PNG_DEFLATE_LEVEL)
        self._fh = open(path, "wb")  # noqa: SIM115 —— close()/abort() 关
        self._fh.write(_png_head(self.width, self.height, self.channels, dpi))

    def _idat(self, data: bytes) -> None:
        if data:
            self._fh.write(_png_chunk(b"IDAT", data))

    def _flush(self) -> None:
        block = b"".join(self._pending)
        self._pending, self._pending_rows = [], 0
        self._idat(self._z.feed(block))

    def feed(self, band: RasterBuffer) -> None:
        if (band.width, band.channels) != (self.width, self.channels):
            raise RasterError(
                f"行带 {band.width}×{band.channels} 与 PNG {self.width}×{self.channels} 不符"
            )
        if self._rows + band.height > self.height:
            raise RasterError(f"行数超出：{self._rows} + {band.height} > {self.height}")
        view, st, rb = memoryview(band.samples), band.stride, self._rb
        for y in range(band.height):
            self._pending += (b"\x00", view[y * st : y * st + rb])
            self._pending_rows += 1
            if self._pending_rows == self._per_block:
                self._flush()
        self._rows += band.height

    def close(self) -> dict:
        if self._rows != self.height:
            self.abort()
            raise RasterError(f"PNG 只收到 {self._rows} / {self.height} 行")
        if self._pending:
            self._flush()
        self._idat(self._z.close())
        self._fh.write(_png_chunk(b"IEND", b""))
        self._fh.close()
        return {"px_w": self.width, "px_h": self.height, "channels": self.channels}

    def abort(self) -> None:
        self._z.abort()
        self._fh.close()


def write_tiff(buf: RasterBuffer, path) -> dict:
    """RasterBuffer → TIFF 文件，编码器是纯标准库的 `tavotto.tiffwrite`（ADR 0046：Deflate 无损、RGBA 写
    `ExtraSamples = 2` 非预乘——与本类的 straight alpha 同义；`dpi=None` 写「没有绝对单位」）。像素直接
    交 `samples` + `stride`，编码器自己剥行尾填充——与 `encode_png` 吃的是**同一份** `samples`。"""
    from ..tiffwrite import write_tiff as _write_tiff

    return _write_tiff(
        path, buf.width, buf.height, buf.samples, buf.channels, stride=buf.stride, dpi=buf.dpi
    )


__all__ = [
    "DOCUMENT_MAX_PIXELS",
    "SOURCE_MAX_PIXELS",
    "RasterBuffer",
    "RasterError",
    "encode_png",
    "write_tiff",
]
