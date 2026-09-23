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


# ---------------------------------------------------------------------------
# 并行 zlib（pigz 的做法）：Canonical PDF 的大流、PNG 的 IDAT 共用这一份
# ---------------------------------------------------------------------------
#: 一块的未压缩字节数。块边界只由它与输入长度决定、与线程数无关——同一输入永远同一份输出。
DEFLATE_BLOCK = 1 << 20
#: 每块用前一块末尾这么多字节当预置字典（deflate 的窗口就是 32 KiB）：跨块的回溯引用照样合法（解码器
#: 的窗口里本来就是前一块的明文），压缩率与单线程几乎相同。
_DEFLATE_WINDOW = 32 * 1024


def _workers() -> int:
    """并行压缩的线程数（每次调用临时起、用完 join：不留常驻线程）。"""
    import os

    return max(1, min(8, os.cpu_count() or 1))


def _zlib_header(level: int) -> bytes:
    flevel = 0 if level < 2 else 1 if level < 6 else 2 if level == 6 else 3
    flg = flevel << 6
    flg += (31 - (0x78 * 256 + flg) % 31) % 31
    return bytes((0x78, flg))


def _deflate_block(data, zdict, last: bool, level: int) -> bytes:
    c = (
        zlib.compressobj(level, zlib.DEFLATED, -15, zdict=zdict)
        if zdict
        else zlib.compressobj(level, zlib.DEFLATED, -15)
    )
    return c.compress(data) + c.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)


def zlib_compress(data, level: int = 6) -> bytes:
    """与 `zlib.compress(data, level)` 同一种**合法 zlib 流**（任何解码器解出同一份明文），大输入按
    `DEFLATE_BLOCK` 切块在线程池里并行压（zlib 在 C 里放 GIL）；非末块 `Z_SYNC_FLUSH` 收在字节边界上，
    拼接后补 zlib 头与整段的 adler32。一块以内**逐字节等于** `zlib.compress`。"""
    view = memoryview(data).cast("B")
    n = len(view)
    if n <= DEFLATE_BLOCK:
        return zlib.compress(view, level)
    from concurrent.futures import ThreadPoolExecutor

    starts = range(0, n, DEFLATE_BLOCK)
    last = starts[-1]
    with ThreadPoolExecutor(
        max_workers=min(_workers(), len(starts)), thread_name_prefix="deflate"
    ) as pool:
        futures = [
            pool.submit(
                _deflate_block,
                view[s : s + DEFLATE_BLOCK],
                bytes(view[max(0, s - _DEFLATE_WINDOW) : s]) if s else None,
                s == last,
                level,
            )
            for s in starts
        ]
        adler = zlib.adler32(view)
        body = b"".join(f.result() for f in futures)
    return _zlib_header(level) + body + struct.pack(">I", adler & 0xFFFFFFFF)


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


def encode_png(buf: RasterBuffer) -> bytes:
    """RasterBuffer → PNG 字节：8 bit、色型 2（RGB）或 6（RGBA，straight alpha）、每行滤波 0、非交错；
    `dpi` 已知时写 `pHYs`（像素 / 米，单位 1），未知不写——不编一个数。行尾填充在这里剥掉。"""
    color_type = 6 if buf.channels == 4 else 2
    raw = b"".join(b"\x00" + row for row in buf.rows())
    out = bytearray(PNG_SIGNATURE)
    out += _png_chunk(
        b"IHDR", struct.pack(">IIBBBBB", buf.width, buf.height, 8, color_type, 0, 0, 0)
    )
    if buf.dpi is not None and buf.dpi > 0:
        ppm = int(round(buf.dpi / 0.0254))
        out += _png_chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    out += _png_chunk(b"IDAT", zlib.compress(raw, PNG_DEFLATE_LEVEL))
    out += _png_chunk(b"IEND", b"")
    return bytes(out)


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
