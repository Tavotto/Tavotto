"""位图源解码：PNG / JPEG / TIFF 字节 → `raster.RasterBuffer`（统一实施包 U07，ADR 0065 / 0066）。

native 适配层：**Pillow 只在这里 import，且在函数里按需 import**。它是 pikepdf 的硬依赖，本来就在
`tavotto[rendercore]` 的闭包里；U07 起它有了一个我们**直接调用**的用途（把用户的位图素材解成像素
好写进 PDF 的 Image XObject），所以 pyproject 的 `rendercore` extra 把它列成正式依赖（ADR 0066 §依赖），
不再是「传递依赖、我们不碰」。父进程默认路径（PyMuPDF）一字不变；`tiffwrite.py` 仍是纯标准库
编码器——Pillow 在这里只**解码**。

## 合同

* 输出恒是 8 bit sRGB 的 RGB / RGBA（`RasterBuffer`）：灰度 / 调色板 / 16 bit / CMYK 都在这里归一
  （16 bit 取高 8 位；调色板的 tRNS 变成 alpha；CMYK 交给 Pillow 转 RGB）。alpha straight。
* 像素网格不变：不重采样、不按 DPI 缩放（RC-055 那句「native pixel grid」在画布放置上同样成立：
  缩放发生在 `cm` 矩阵里，不在像素上）。
* 密度只从文件自己声明过的信息取（PNG pHYs / JPEG JFIF / TIFF 分辨率标签，Pillow 的 `info["dpi"]`），
  没有就是 `None`——不编一个数（与 `engine/originalspec` 同一口径）。
* **JPEG 直通**（`jpeg_passthrough()`）：8 bit RGB / 灰度的 JPEG 可以不解码、原字节以 `/DCTDecode`
  进 PDF（与旧后端 `insert_image` 保持 JPEG 不重编码同一取舍）；CMYK / 12 bit 等一律走解码路。
* 认不出 / 解不开的文件抛 `RasterDecodeError(code)`，code 闭集见 `RASTER_DECODE_CODES`；不返回
  一张空图。
"""

from __future__ import annotations

import io
import struct

from .raster import RasterBuffer

RASTER_DECODE_CODES = (
    "raster_unreadable",  # Pillow 认不出 / 解码抛错
    "raster_kind_mismatch",  # 扩展名说 png、字节是别的（资源 kind 与文件签名不符）
    "raster_too_large",  # 像素数超过调用方给的预算
)

_FORMAT_OF_KIND = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "tif": "TIFF",
    "tiff": "TIFF",
}


class RasterDecodeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        assert code in RASTER_DECODE_CODES, code
        super().__init__(message)
        self.code = code


def require() -> None:
    from .hbshaper import require as _require

    _require("PIL")


def _open(data: bytes, kind: str):
    require()
    from PIL import Image, UnidentifiedImageError

    try:
        im = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RasterDecodeError("raster_unreadable", f"{kind}: {exc}") from exc
    want = _FORMAT_OF_KIND.get(kind)
    if want is not None and im.format != want:
        raise RasterDecodeError(
            "raster_kind_mismatch", f"资源说是 {kind}，字节是 {im.format or '未知格式'}"
        )
    return im


def _dpi(im) -> float | None:
    """文件**自己声明过**的密度，否则 None。实测（Pillow 12.3）：PNG 没 pHYs、JPEG 没 JFIF 密度时
    `info` 里根本没有 `dpi` 键；TIFF 没有分辨率标签时 Pillow 报 (1, 1)、JFIF 单位 0 时也是 (1, 1)
    ——那是纵横比不是密度。两种形态都归 None，不编一个数。"""
    dpi = im.info.get("dpi")
    if not dpi:
        return None
    try:
        x, y = float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError, IndexError):
        return None
    if x <= 1.0 or y <= 1.0:
        return None
    return x if abs(x - y) < 1e-6 else max(x, y)


def decode(data: bytes, kind: str, *, max_pixels: int | None = None) -> RasterBuffer:
    """字节 → RasterBuffer（RGB 或 RGBA，紧凑 stride，alpha straight）。"""
    im = _open(data, kind)
    if max_pixels is not None and im.width * im.height > max_pixels:
        raise RasterDecodeError(
            "raster_too_large", f"{im.width}×{im.height} > 像素预算 {max_pixels}"
        )
    dpi = _dpi(im)
    try:
        if im.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
            # 16 bit 灰度：取高 8 位（Pillow 的 convert("L") 会把 >255 的值截断成 255）
            im = im.point(lambda v: v * (1.0 / 256.0)).convert("L")
        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        rgb = im.convert("RGBA" if has_alpha else "RGB")
        samples = rgb.tobytes()
    except (OSError, ValueError) as exc:
        raise RasterDecodeError("raster_unreadable", f"{kind}: 解码失败 {exc}") from exc
    channels = 4 if rgb.mode == "RGBA" else 3
    return RasterBuffer(
        width=rgb.width,
        height=rgb.height,
        channels=channels,
        samples=bytes(samples),
        stride=rgb.width * channels,
        dpi=dpi,
    )


def jpeg_passthrough(data: bytes, kind: str) -> dict | None:
    """8 bit RGB / 灰度 JPEG：回 `{width, height, components, bit_depth}`，字节可原样进 `/DCTDecode`；
    其它（CMYK、12 bit、不是 JPEG）回 None，调用方改走 `decode()`。

    尺寸 / 分量数从 SOF 段自己读（Pillow 的 `mode` 只说 RGB / L / CMYK，不说 bit 深）；Adobe 的 CMYK
    JPEG 还带反相约定，避开它比写 /Decode 数组更稳。
    """
    if kind not in ("jpg", "jpeg") or data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if marker == 0xD9 or marker == 0xDA:
            return None  # 到扫描 / 结尾都没见到 SOF：不直通
        (seg_len,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in (
            0xC0,
            0xC1,
            0xC2,
        ):  # baseline / extended / progressive（PDF 的 DCTDecode 都认）
            if i + 10 > n:
                return None
            precision, height, width, components = struct.unpack(">BHHB", data[i + 4 : i + 10])
            if precision != 8 or components not in (1, 3) or width == 0 or height == 0:
                return None
            return {
                "width": width,
                "height": height,
                "components": components,
                "bit_depth": precision,
            }
        if 0xC3 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return None  # 无损 / 分层 / 算术编码：PDF 读取器未必认，走解码路
        i += 2 + seg_len
    return None


__all__ = ["RASTER_DECODE_CODES", "RasterDecodeError", "decode", "jpeg_passthrough", "require"]
