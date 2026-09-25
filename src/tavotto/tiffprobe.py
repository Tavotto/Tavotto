"""TIFF 素材的**头部事实与支持范围** —— 「这张 TIFF 能不能当素材用」的唯一判据（issue #534）。

纯标准库，只读文件头与第一个 IFD，一个像素都不解。Flask 父进程
import 它，渲染后端（RenderCore）**不**另判一遍：素材清单（`app.scan_panels`）与每一次按面板 id
取文件（`app.safe_resolve`：缩略图、原文件、原图导出、重渲染）都经这里，画布合成的静态源解析器
（不经 `safe_resolve`）由 `app._export_produce_rendercore` 补同一道闸。

### 支持范围（定死，改它就是改产品能力）

范围是在 U10 之前对两个后端（PyMuPDF 1.28.2 / RenderCore + Pillow 12.3）逐格实测、两边画出同一张图
才定的；今天只剩 RenderCore，`tests/test_tiff_assets.py` 逐格看护它：

* 容器：经典 TIFF（`II` / `MM` 两种字节序、条带或瓦片、样本交错存放）。**只取第一页**——多页 TIFF 与
  多页 PDF 同一条规则：一张「图」在本产品里恒等于一页，画布上看到的、导出的都是首页。
* 颜色：灰度（`BlackIsZero` / `WhiteIsZero`，可带 alpha）、RGB（可带 alpha）、8 位调色板、
  JPEG 压缩的 YCbCr。
* 位深：8 位、16 位无符号整数（16 位**取高 8 位**，不做自动对比度拉伸——只用了低 12 位的相机数据
  会显得很暗，那是文件本来的样子）；1 位只在灰度上（线稿 / 传真）。
* 压缩：无、LZW、Deflate（8 / 32946）、PackBits、JPEG（7）、CCITT G3 / G4。

**范围之外一律拒绝，不静默出一张错图**（右列是 U10 前实测的错法；BigTIFF 与平面存放 RenderCore 其实解得开，
放开它们要本模块先会读 BigTIFF 头、再补逐格用例，是后续一步，不在 #534 里顺手放）：

| code | 什么样的文件 | 不拒绝会怎样 |
| --- | --- | --- |
| `tiff_sample_format` | 浮点 / 有符号整数像素 | 两个后端各出一张不同的错图（PyMuPDF 灰一片、RenderCore 全黑） |
| `tiff_color_space` | CMYK、Lab 等 | 两个后端各按自己的公式转 RGB，颜色不一致、都不做色彩管理 |
| `tiff_bit_depth` | 32 位整数等 | PyMuPDF 出乱码色块，RenderCore 读不开 |
| `tiff_compression` | ZSTD / WebP / JPEG 2000 等 | PyMuPDF 读不开，RenderCore 解码失败 |
| `tiff_bigtiff` | BigTIFF（魔数 43） | PyMuPDF 读不开，RenderCore 读得开——两个后端答案不同 |
| `tiff_planar` | 多通道按平面分开存（`PlanarConfiguration = 2`） | 同上 |
| `tiff_unreadable` | 头部不成形 / 截断 | —— |

### 资源上界（坏头只换来 `tiff_unreadable`，换不来一次巨大的分配）

文件里读出来的每个数在驱动分配 / 读长度 / 循环 / seek 之前都有上界（Codex #561，逐个看护在
`tests/test_tiff_assets.py` 的「坏头」一组）：

* IFD 偏移：seek 本身不分配，越过文件尾时读到的不足 2 字节 → `tiff_unreadable`；
* IFD 条目数：u16 天然 ≤ 65535，且 `2 + 12 × 条目数` 必须整段落在文件里，0 条目算坏头；
* 每个条目声明的值区（`类型字节数 × 个数`，放不进 4 字节时）必须整段落在文件里——条带 / 瓦片的偏移与
  字节数表、ColorMap 本模块都不读，但个数写成 0xffffffff 的就在这里拦下；
* 读得到的标签值最多取 16 个（`values()`）；
* `SamplesPerPixel`：必须是整数类型、1–16（`_MAX_SAMPLES`），核过之后才按它建默认元组；
* 不沿「下一个 IFD」链往后走（只看首页），没有页循环。

这些 code 同时是用户可见的后端错误码（`ERROR_CODES`，`tests/test_error_codes.py` 看护两种语言的文案）。
"""

from __future__ import annotations

import dataclasses
import struct
from pathlib import Path

ERROR_CODES = (
    "tiff_unreadable",
    "tiff_bigtiff",
    "tiff_compression",
    "tiff_color_space",
    "tiff_bit_depth",
    "tiff_sample_format",
    "tiff_planar",
)

#: 两个后端都解得开、且解出同一张图的压缩方式（TIFF 6.0 编号 + 两个事实标准）。
SUPPORTED_COMPRESSION = frozenset({1, 3, 4, 5, 7, 8, 32773, 32946})

#: 颜色模型 → 允许的每像素样本数（第二个值是「带一个 alpha」）。YCbCr 只在 JPEG 压缩下出现。
_SAMPLES_OF_PHOTOMETRIC = {
    0: (1, 2),  # WhiteIsZero
    1: (1, 2),  # BlackIsZero
    2: (3, 4),  # RGB
    3: (1,),  # Palette
    6: (3,),  # YCbCr（仅 JPEG-in-TIFF）
}
_PHOTOMETRIC_YCBCR = 6
_COMPRESSION_JPEG = 7

# 字段类型 → 单个值的字节数
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}

_WIDTH, _HEIGHT, _BITS, _COMPRESSION, _PHOTOMETRIC = 256, 257, 258, 259, 262
_SAMPLES, _XRES, _YRES, _PLANAR, _RESUNIT, _SAMPLE_FORMAT = 277, 282, 283, 284, 296, 339
_WANTED = (_WIDTH, _HEIGHT, _BITS, _COMPRESSION, _PHOTOMETRIC, _SAMPLES, _XRES, _YRES, _RESUNIT)
_WANTED += (_PLANAR, _SAMPLE_FORMAT)

#: 每像素样本数的解析上限。支持范围最多 4（RGBA），5–16 还能判成 `tiff_color_space`；再往上不是
#: 一张图，是一个坏头——按它去建默认元组会分配几十亿个元素（Codex #561：`SamplesPerPixel` 写成
#: LONG 0xffffffff，扫一遍项目就能把服务 OOM 掉）。
_MAX_SAMPLES = 16


class UnsupportedTiff(ValueError):
    """这张 TIFF 不在支持范围里（或读不出头）。`code` ∈ `ERROR_CODES`，`params = {"file": 文件名}`。"""

    def __init__(self, code: str, message: str, file: str = "") -> None:
        assert code in ERROR_CODES, code
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = {"file": file}


@dataclasses.dataclass(frozen=True)
class TiffHeader:
    width: int
    height: int
    bits: tuple[int, ...]
    samples: int
    photometric: int | None
    compression: int
    sample_format: tuple[int, ...]
    #: 1 = 各样本交错（chunky），2 = 按平面分开存
    planar: int
    #: `(x, y, 单位)`；单位 1 = 没有绝对单位、2 = 英寸、3 = 厘米。没写分辨率标签就是 None。
    resolution: tuple[float, float, int] | None


def is_tiff(head: bytes) -> bool:
    """文件签名（前 4 字节）是不是经典 TIFF 或 BigTIFF。"""
    return head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def read_header(path: Path) -> TiffHeader:
    """读第一个 IFD。读不出 → `tiff_unreadable`；BigTIFF → `tiff_bigtiff`（不往下解析）。"""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            return _read(fh, path.name)
    except OSError as exc:
        raise UnsupportedTiff("tiff_unreadable", f"{path.name}: 读不出文件（{exc}）", path.name)
    except (struct.error, ValueError) as exc:
        if isinstance(exc, UnsupportedTiff):
            raise
        raise UnsupportedTiff("tiff_unreadable", f"{path.name}: TIFF 头不成形（{exc}）", path.name)


def _read(fh, name: str) -> TiffHeader:
    head = fh.read(8)
    if head[:4] in (b"II+\x00", b"MM\x00+"):
        raise UnsupportedTiff("tiff_bigtiff", f"{name}: BigTIFF 不在支持范围内", name)
    if not is_tiff(head) or len(head) < 8:
        raise UnsupportedTiff("tiff_unreadable", f"{name}: 不是 TIFF 文件", name)
    end = "<" if head[:2] == b"II" else ">"
    (ifd,) = struct.unpack(end + "I", head[4:8])
    # 文件里读出来的每一个数（偏移 / 个数）在驱动 seek、读长度、循环或分配之前，都先对文件大小
    # 核一遍：坏头只能得到 `tiff_unreadable`，不能换来一次巨大的分配（Codex #561）
    size_of_file = fh.seek(0, 2)

    def entries(offset: int) -> dict[int, tuple[int, int, int, bytes]]:
        fh.seek(offset)  # 越过文件尾时下一行读到的不足 2 字节，struct 报错 → tiff_unreadable
        (count,) = struct.unpack(end + "H", fh.read(2))
        # 条目数是 u16（≤ 65535，读长度 ≤ 768 KiB），另外必须整段落在文件里
        if count == 0 or offset + 2 + count * 12 > size_of_file:
            raise ValueError(f"IFD 条目数 {count} 与文件大小不符")
        body = fh.read(count * 12)
        out = {}
        for i in range(count):
            tag, typ, n = struct.unpack_from(end + "HHI", body, i * 12)
            inline = body[i * 12 + 8 : i * 12 + 12]
            size = _TYPE_SIZE.get(typ)
            if size is not None and size * n > 4:
                # 值放在别处的标签（条带 / 瓦片偏移与字节数、ColorMap……）：本模块多半不读它们，
                # 但声明的值区必须整段落在文件里——个数写成 0xffffffff 的条带表就在这里拦下，
                # 不留给解码器去分配
                (voff,) = struct.unpack(end + "I", inline)
                if voff + size * n > size_of_file:
                    raise ValueError(f"标签 {tag} 的值区（{n} 个）越过文件尾")
            out[tag] = (typ, n, i, inline)
        return out

    def values(entry: tuple[int, int, int, bytes]) -> list:
        typ, n, _i, inline = entry
        size = _TYPE_SIZE.get(typ)
        if size is None or n == 0:
            return []
        n = min(n, 16)  # 这里用到的标签都是每样本一个值，样本数不会超过十几个
        if size * n <= 4:
            data = inline
        else:
            (off,) = struct.unpack(end + "I", inline)
            fh.seek(off)
            data = fh.read(size * n)
            if len(data) != size * n:
                raise ValueError("标签值越过文件尾")
        if typ == 3:
            return list(struct.unpack_from(f"{end}{n}H", data))
        if typ == 4:
            return list(struct.unpack_from(f"{end}{n}I", data))
        if typ == 1:
            return list(data[:n])
        if typ == 5:
            nums = struct.unpack_from(f"{end}{2 * n}I", data)
            return [nums[k] / nums[k + 1] if nums[k + 1] else 0.0 for k in range(0, 2 * n, 2)]
        return []

    first = entries(ifd)  # 只看第一页：后面的页不影响判据，也不读
    got = {tag: values(first[tag]) for tag in _WANTED if tag in first}

    def one(tag: int, default: int | None) -> int | None:
        v = got.get(tag)
        return int(v[0]) if v else default

    width, height = one(_WIDTH, None), one(_HEIGHT, None)
    if not width or not height:
        raise ValueError("缺宽高")
    if _SAMPLES in first and first[_SAMPLES][0] not in (3, 4):
        raise ValueError("SamplesPerPixel 的类型不是整数")
    samples = one(_SAMPLES, 1)
    if samples is None or not 1 <= samples <= _MAX_SAMPLES:
        # 在任何按样本数分配（下面的默认元组）之前拦下
        raise ValueError(f"SamplesPerPixel = {samples}")
    bits = tuple(int(b) for b in got.get(_BITS) or [1] * samples)
    fmt = tuple(int(f) for f in got.get(_SAMPLE_FORMAT) or [1] * samples)
    resolution = None
    if _XRES in got and _YRES in got and got[_XRES] and got[_YRES]:
        resolution = (float(got[_XRES][0]), float(got[_YRES][0]), one(_RESUNIT, 2) or 2)

    return TiffHeader(
        width=int(width),
        height=int(height),
        bits=bits,
        samples=int(samples),
        photometric=one(_PHOTOMETRIC, None),
        compression=one(_COMPRESSION, 1) or 1,
        sample_format=fmt,
        planar=one(_PLANAR, 1) or 1,
        resolution=resolution,
    )


def unsupported_reason(h: TiffHeader) -> str | None:
    """头部事实 → 不支持的原因 code；在支持范围内回 None。"""
    if h.compression not in SUPPORTED_COMPRESSION:
        return "tiff_compression"
    allowed = _SAMPLES_OF_PHOTOMETRIC.get(h.photometric) if h.photometric is not None else None
    if allowed is None or h.samples not in allowed:
        return "tiff_color_space"
    if h.photometric == _PHOTOMETRIC_YCBCR and h.compression != _COMPRESSION_JPEG:
        return "tiff_color_space"
    if h.samples > 1 and h.planar != 1:
        return "tiff_planar"
    if any(f != 1 for f in h.sample_format):
        return "tiff_sample_format"
    if len(set(h.bits)) != 1:
        return "tiff_bit_depth"
    bits = h.bits[0]
    if h.photometric == 3:
        ok = bits == 8
    elif bits == 1:
        ok = h.photometric in (0, 1) and h.samples == 1
    else:
        ok = bits in (8, 16)
    return None if ok else "tiff_bit_depth"


_REASON_TEXT = {
    "tiff_compression": "压缩方式不在支持范围内",
    "tiff_color_space": "颜色模型不在支持范围内（只支持灰度 / RGB / 调色板）",
    "tiff_sample_format": "像素是浮点数或有符号整数（只支持无符号整数）",
    "tiff_bit_depth": "位深不在支持范围内（只支持 8 / 16 位，1 位仅限灰度）",
    "tiff_planar": "各通道按平面分开存放（只支持交错存放）",
}


def check(path: Path) -> TiffHeader:
    """读头 + 判范围：在范围内回头部事实，否则抛 `UnsupportedTiff`。"""
    h = read_header(path)
    reason = unsupported_reason(h)
    if reason is not None:
        name = Path(path).name
        raise UnsupportedTiff(reason, f"{name}: {_REASON_TEXT[reason]}", name)
    return h


def density(h: TiffHeader) -> tuple[float, float] | None:
    """文件**自己声明**的物理密度（每英寸），没写 / 只有纵横比（单位 1）/ 非正数时回 None。"""
    if h.resolution is None:
        return None
    x, y, unit = h.resolution
    if x <= 0 or y <= 0:
        return None
    if unit == 2:
        return (x, y)
    if unit == 3:
        return (x * 2.54, y * 2.54)
    return None
