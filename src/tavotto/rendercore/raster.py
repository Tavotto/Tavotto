"""RasterBuffer —— RenderCore 里一块像素的**唯一形状**（统一实施包 U07，ADR 0066）。

它出现在两处：位图源解码之后（`rasterio.decode()` → 写进 PDF 的 Image XObject）、Canonical PDF
栅格化之后（render child 的 PDFium 位图 → PNG / TIFF）。两条路共用同一个定义，PNG 与 TIFF 从
**同一个 RasterBuffer** 编码——"同参数像素逐个相同"不是记得要一致，是同一份字节（RC-052 / 053）。

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

from dataclasses import dataclass
from typing import Iterator

#: 一块像素的预算（约 8000×8000）：位图**源**解码前按头里的尺寸判（`rasterio.decode(max_pixels=…)`），
#: render child 栅格前父子两侧各判一次（ADR 0066）。超过的不是「解慢一点」，是结构化拒绝——
#: 一张压缩得很小的高分辨率 PNG 解开是几百 MB，导出进程会被它挤死而不是报错（Codex #463 P2）。
SOURCE_MAX_PIXELS = 64_000_000


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


__all__ = ["SOURCE_MAX_PIXELS", "RasterBuffer", "RasterError"]
