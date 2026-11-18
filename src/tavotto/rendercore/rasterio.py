"""位图源解码：PNG / JPEG / TIFF 字节 → `raster.RasterBuffer`（统一实施包 U07，ADR 0065 / 0066）。

native 适配层：**Pillow 只在这里 import，且在函数里按需 import**。它是 pikepdf 的硬依赖，本来就在
闭包里；U07 起它有了一个我们**直接调用**的用途（把用户的位图素材解成像素好写进 PDF 的 Image XObject），
所以 pyproject 把它列成正式依赖（ADR 0066 §依赖；U10 起在 `dependencies` 里，ADR 0072），不再是
「传递依赖、我们不碰」。`tiffwrite.py` 仍是纯标准库编码器——Pillow 在这里只**解码**。

## 合同

* 输出恒是 8 bit sRGB 的 RGB / RGBA（`RasterBuffer`）：灰度 / 调色板 / 16 bit / CMYK 都在这里归一
  （16 bit 取高 8 位；调色板的 tRNS 变成 alpha；CMYK 交给 Pillow 转 RGB）。alpha straight。
* 像素网格不变：不重采样、不按 DPI 缩放（RC-055 那句「native pixel grid」在画布放置上同样成立：
  缩放发生在 `cm` 矩阵里，不在像素上）。唯一的例外是 `preview()`：它只喂 `/api/render` 的预览缓存，
  从不进 PDF。
* 密度只从文件自己声明过的信息取（PNG pHYs / JPEG JFIF / TIFF 分辨率标签，Pillow 的 `info["dpi"]`），
  没有就是 `None`——不编一个数（与 `engine/originalspec` 同一口径）。
* **JPEG 直通**（`jpeg_passthrough()`）：8 bit RGB / 灰度的 JPEG 可以不解码、原字节以 `/DCTDecode`
  进 PDF（与旧后端 `insert_image` 保持 JPEG 不重编码同一取舍）；CMYK / 12 bit 等一律走解码路。
* **方向标记（EXIF / TIFF 的 Orientation，标签 0x0112）一律照办**：手机照片常是「横着存像素 + 标记显示时转
  90°」。画布上 JPEG 由浏览器 `<img>` 直接显示（`web/src/lib/api.ts` 的 `panelSrc`），浏览器默认按标记摆正；这里
  若不照办，导出 / 预览就是横着的原始像素被塞进画布上那个竖框——转 90° 还变形（TVM-22EE / TVD-1KN2）。所以
  `header_size()` / `header_info()` 报**摆正后**的宽高，`decode()` / `preview()` 回**摆正后**的像素；JPEG 直通
  不改字节，`jpeg_passthrough()` 把方向值交给调用方（PDF 写入器把它折进绘制矩阵，SVG 写入器改走解码）。只认
  JPEG 与 TIFF：PNG 的 eXIf 浏览器并不一致地照办，画布上不转的这里也不转。
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


#: EXIF / TIFF 的方向标签（Orientation）
_ORIENTATION_TAG = 0x0112
#: 方向值 → 把**存储的**像素摆正要做的 Pillow 转置（与 `PIL.ImageOps.exif_transpose` 同一张表；1 = 不动）
_TRANSPOSE = {2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM", 5: "TRANSPOSE",
              6: "ROTATE_270", 7: "TRANSVERSE", 8: "ROTATE_90"}  # fmt: skip
#: 方向值 → PDF 里把存储的位图（单位正方形）画成摆正后的样子的矩阵 `[a b c d e f]`，单位正方形映到自身。
#: 推导：存储像素归一化坐标 (s, t)（t 向下）在 PDF 单位正方形里是 (s, 1 − t)；摆正后它落在 (u, v)，画出来应是
#: (u, 1 − v)。例：6 = 顺时针转 90°，(u, v) = (1 − t, s) → X = y、Y = 1 − x → [0 −1 1 0 0 1]
ORIENTATION_MATRIX = {
    1: (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
    2: (-1.0, 0.0, 0.0, 1.0, 1.0, 0.0),
    3: (-1.0, 0.0, 0.0, -1.0, 1.0, 1.0),
    4: (1.0, 0.0, 0.0, -1.0, 0.0, 1.0),
    5: (0.0, -1.0, -1.0, 0.0, 1.0, 1.0),
    6: (0.0, -1.0, 1.0, 0.0, 0.0, 1.0),
    7: (0.0, 1.0, 1.0, 0.0, 0.0, 0.0),
    8: (0.0, 1.0, -1.0, 0.0, 1.0, 0.0),
}


def swaps_axes(orientation: int) -> bool:
    """5–8 摆正后宽高互换。"""
    return orientation in (5, 6, 7, 8)


def _valid_orientation(value) -> int:
    try:
        o = int(value)
    except (TypeError, ValueError):
        return 1
    return o if o in ORIENTATION_MATRIX else 1


def kind_of(path) -> str:
    """素材类型 = 扩展名（小写，jpeg 归 jpg）。预览缓存的副本没有扩展名，类型一律从**源路径**取。"""
    from pathlib import Path

    kind = Path(path).suffix.lstrip(".").lower()
    return "jpg" if kind == "jpeg" else kind


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
    except Image.DecompressionBombError as exc:
        # Pillow 自己的炸弹闸（默认 2 × 89M 像素）先响：同样是「太大」，不是「读不出」
        raise RasterDecodeError("raster_too_large", f"{kind}: {exc}") from exc
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


def header_size(data: bytes, kind: str) -> tuple[int, int]:
    """只读文件头里的像素尺寸，**不解码**：JPEG 先读 SOF（纯标准库，一个像素都不碰），其它经 Pillow 懒打开；
    认不出 / 不符同 `decode()` 的错误码。写入器用它在任何解码之前记两级预算的账。宽高是按方向标记**摆正后**的。"""
    sof = _jpeg_sof(data, kind)
    if sof is not None:
        w, h = int(sof["width"]), int(sof["height"])
        return (h, w) if swaps_axes(_jpeg_orientation(data)) else (w, h)
    im = _open(data, kind)
    return _upright_size(im, kind)


def _has_alpha(im) -> bool:
    # 预乘的 RGBa / La 也带透明通道（`decode` 会先反预乘；`header_info` 不解码、按模式名判）
    return im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in im.info


def header_info(data: bytes, kind: str) -> dict:
    """`{width, height, alpha}`，**不解码**（Pillow 懒打开只读头；JPEG 永远没有 alpha）。facade 的
    `probe_asset(kind="raster")` 用它——`alpha` 是「这张位图带不带透明通道」（调色板的 tRNS 也算），
    与旧后端 `Pixmap.alpha` 同一问题。密度不从这里取（`engine/originalspec` 是唯一出处）。"""
    sof = _jpeg_sof(data, kind)
    if sof is not None:
        w, h = int(sof["width"]), int(sof["height"])
        if swaps_axes(_jpeg_orientation(data)):
            w, h = h, w
        return {"width": w, "height": h, "alpha": False}
    im = _open(data, kind)
    w, h = _upright_size(im, kind)
    return {"width": w, "height": h, "alpha": _has_alpha(im)}


def decode(data: bytes, kind: str, *, max_pixels: int | None = None) -> RasterBuffer:
    """字节 → RasterBuffer（RGB 或 RGBA，紧凑 stride，alpha straight），像素按方向标记摆正。"""
    im = _open(data, kind)
    if max_pixels is not None and im.width * im.height > max_pixels:
        raise RasterDecodeError(
            "raster_too_large", f"{im.width}×{im.height} > 像素预算 {max_pixels}"
        )
    dpi = _dpi(im)
    try:
        orientation = _pending_orientation(im, data, kind)
        if im.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I"):
            # 16 bit 灰度：取高 8 位（Pillow 的 convert("L") 会把 >255 的值截断成 255）
            im = im.point(lambda v: v * (1.0 / 256.0)).convert("L")
        # 预乘（associated）alpha 的 `RGBa` / `La` 先反预乘成 straight（Pillow 只认 RGBa→RGBA、La→LA 这两步）：
        # 漏掉它们就会走 `convert("RGB")`，alpha 静默丢掉、颜色带着预乘的暗（Codex #463 第八轮 P2）。
        # Pillow 12.3 的 TIFF 读取器对 ExtraSamples=1 已经在加载时反预乘、报 RGBA，这里是兜底
        if im.mode == "RGBa":
            im = im.convert("RGBA")
        elif im.mode == "La":
            im = im.convert("LA")
        rgb = im.convert("RGBA" if _has_alpha(im) else "RGB")
        if orientation != 1:
            from PIL import Image

            rgb = rgb.transpose(getattr(Image.Transpose, _TRANSPOSE[orientation]))
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


def preview(
    data: bytes,
    kind: str,
    width_px: int,
    *,
    transparent: bool = False,
    max_pixels: int | None = None,
) -> RasterBuffer:
    """位图素材的预览：解码（同 `decode()` 的预算与模式处理）→ 等比缩放到 `width_px` 宽 → 白底 RGB
    （`transparent=True` 时保留 alpha）。

    与 PDF 预览（render child 里 PDFium 画的白底页）同一个出口形状：`/api/render` 对 PNG / JPEG / TIFF
    素材也要回一张给定宽度的白底 PNG。旧后端（PyMuPDF）能直接打开位图，候选后端以前把它们也交给
    PDFium，于是素材库里所有位图的缩略图都是 500（2026-09-23 beta 实测，`PDFium: Data format error`）。
    """
    from PIL import Image

    buf = decode(data, kind, max_pixels=max_pixels)
    mode = "RGBA" if buf.channels == 4 else "RGB"
    im = Image.frombytes(mode, (buf.width, buf.height), buf.samples, "raw", mode, buf.stride)
    w = max(1, int(width_px))
    h = max(1, round(buf.height * w / buf.width))
    if (w, h) != im.size:
        im = im.resize((w, h), Image.Resampling.LANCZOS)
    if mode == "RGBA" and transparent:
        return RasterBuffer(
            width=im.width, height=im.height, channels=4, samples=im.tobytes(), stride=im.width * 4
        )
    if mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    return RasterBuffer(
        width=im.width, height=im.height, channels=3, samples=im.tobytes(), stride=im.width * 3
    )


def jpeg_passthrough(data: bytes, kind: str, *, max_pixels: int | None = None) -> dict | None:
    """8 bit RGB / 灰度 JPEG：回 `{width, height, components, bit_depth, orientation}`，字节可原样进 `/DCTDecode`；
    其它（CMYK、12 bit、不是 JPEG）回 None，调用方改走 `decode()`。

    尺寸 / 分量数从 SOF 段自己读（Pillow 的 `mode` 只说 RGB / L / CMYK，不说 bit 深）；Adobe 的 CMYK
    JPEG 还带反相约定，避开它比写 /Decode 数组更稳。**直通之前整张真解一遍**（`verified_jpeg`）：只看
    SOI + SOF 就直通的话，一个 12 字节的假头也会被嵌进 PDF 报成功（Codex #463 P2）——解不开、尺寸 /
    模式与 SOF 说的不一致，就回 None 让调用方走 `decode()`，坏文件在那里变成 `raster_unreadable`。
    """
    header = _jpeg_sof(data, kind)
    if header is None:
        return None
    if max_pixels is not None and header["width"] * header["height"] > max_pixels:
        # 预算按 SOF 里的尺寸判，在真解之前——超预算的 JPEG 一个字节都不解
        raise RasterDecodeError(
            "raster_too_large", f"{header['width']}×{header['height']} > 像素预算 {max_pixels}"
        )
    if not verified_jpeg(data, header):
        return None
    # 字节不动、`width` / `height` 仍是 SOF 里**存储的**尺寸（`/Width` `/Height` 要的就是它）；摆正交给调用方
    return {**header, "orientation": _jpeg_orientation(data)}


def verified_jpeg(data: bytes, header: dict) -> bool:
    """Pillow 把整张 JPEG 解到底（`load()`），且 format / 尺寸 / 模式与 SOF 一致才算「读取器解得开」。"""
    require()
    from PIL import Image, UnidentifiedImageError

    try:
        im = Image.open(io.BytesIO(data))
        if im.format != "JPEG":
            return False
        im.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return False
    want_mode = "RGB" if header["components"] == 3 else "L"
    return im.size == (header["width"], header["height"]) and im.mode == want_mode


def orientation(data: bytes, kind: str) -> int:
    """文件里的方向标记（1–8；没有 / 读不出 / 非 JPEG、TIFF 一律 1）。不解码像素。"""
    if kind in ("jpg", "jpeg"):
        return _jpeg_orientation(data)
    if kind in ("tif", "tiff"):
        return _tiff_tag_orientation(_open(data, kind))
    return 1


def _tiff_tag_orientation(im) -> int:
    try:
        return _valid_orientation(im.tag_v2.get(_ORIENTATION_TAG))
    except Exception:  # noqa: BLE001 —— 标签读不出 = 没有方向标记，不是读图失败
        return 1


def _upright_size(im, kind: str) -> tuple[int, int]:
    """懒打开（不解码）的非 JPEG 图摆正后的宽高。TIFF 从标签算（存储宽高 + 方向）：Pillow 12 打开时已经按方向
    报尺寸，更老的不一定——不依赖它的口径。"""
    if kind in ("tif", "tiff"):
        try:
            w, h = int(im.tag_v2[256]), int(im.tag_v2[257])
        except Exception:  # noqa: BLE001 —— 标签不全就信 Pillow 报的
            return int(im.width), int(im.height)
        return (h, w) if swaps_axes(_tiff_tag_orientation(im)) else (w, h)
    return int(im.width), int(im.height)


def _pending_orientation(im, data: bytes, kind: str) -> int:
    """解码时**还要我们来做**的摆正。JPEG：Pillow 不动，按 APP1 里的标记来。TIFF：先 `load()`——Pillow 12 加载时
    自己摆正并删掉方向标签（`TiffImageFile.load_end`），更老的不做；加载后标签还在才轮到我们，免得转两次。"""
    if kind in ("jpg", "jpeg"):
        return _jpeg_orientation(data)
    if kind in ("tif", "tiff"):
        im.load()
        return _tiff_tag_orientation(im)
    return 1


def _jpeg_orientation(data: bytes) -> int:
    """JPEG 第一段 Exif APP1 里 IFD0 的 Orientation（纯标准库，走到扫描段为止，一个像素都不碰）。"""
    if data[:2] != b"\xff\xd8":
        return 1
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return 1
        marker = data[i + 1]
        if marker == 0xFF:  # 填充字节
            i += 1
            continue
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if marker == 0xD9 or marker == 0xDA:
            return 1
        (seg_len,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker == 0xE1 and data[i + 4 : i + 10] == b"Exif\x00\x00":
            return _tiff_orientation(data[i + 10 : i + 2 + seg_len])
        i += 2 + seg_len
    return 1


def _tiff_orientation(tiff: bytes) -> int:
    """TIFF 结构（Exif 载荷）IFD0 里的 Orientation：SHORT、值 1–8，其余一律当没有。"""
    if len(tiff) < 8:
        return 1
    endian = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if endian is None or struct.unpack(endian + "H", tiff[2:4])[0] != 42:
        return 1
    (offset,) = struct.unpack(endian + "I", tiff[4:8])
    if offset + 2 > len(tiff):
        return 1
    (count,) = struct.unpack(endian + "H", tiff[offset : offset + 2])
    for k in range(count):
        p = offset + 2 + 12 * k
        if p + 12 > len(tiff):
            break
        tag, typ, cnt = struct.unpack(endian + "HHI", tiff[p : p + 8])
        if tag == _ORIENTATION_TAG:
            if typ != 3 or cnt < 1:
                return 1
            return _valid_orientation(struct.unpack(endian + "H", tiff[p + 8 : p + 10])[0])
    return 1


def _jpeg_sof(data: bytes, kind: str) -> dict | None:
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


__all__ = [
    "ORIENTATION_MATRIX",
    "RASTER_DECODE_CODES",
    "RasterDecodeError",
    "decode",
    "header_info",
    "header_size",
    "jpeg_passthrough",
    "orientation",
    "require",
    "swaps_axes",
    "verified_jpeg",
]
