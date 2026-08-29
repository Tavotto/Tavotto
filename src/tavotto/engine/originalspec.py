"""「原图规格」的**事实层**：这张素材文件自己说它有多大。

`docs/adr/0028-original-output-spec.md` 是判据与来源优先级的权威，这里只放
读代码时够用的那一层。

改造前，「这张图有多大」这个问题在导出那一刻才现算，而且**位图那一档是猜
出来的**：`app.py` 里写着 `ppi = 600 if ext == ".png" else 300`，一句注释
（"matplotlib 输出 PNG 为 600ppi；照片等按 300ppi 给个初始物理尺寸"）就是
全部依据。猜得对不对没人知道，界面上也一个字都不说——用户拿到一张物理尺寸
错一倍的图，看不出是哪一步错的。

本模块把它拆成两件事：

* **量得到的**（`dpi_source="metadata"`）：PNG 的 `pHYs`、JPEG 的 JFIF 密度
  或 Exif 分辨率。这是文件自己写下的物理密度，不是我们的推测。
* **量不到的**（`dpi_source="assumed"`）：文件没写。这时才用 `ASSUMED_DPI`
  ——取值与改造前**逐位相同**（PNG 600 / 其余 300），所以老项目里已经摆好的
  面板尺寸一个都不变；区别只是现在它**说出来**自己是假定的，界面据此提示。

「不知道」是独立一档，不许合并进相邻取值：`dpi=None` 与 `dpi=96.0` 是两个
不同的答案。MuPDF 的 `Pixmap.xres` 在这一点上不可用——**没有 pHYs 的 PNG 和
真的写着 96 dpi 的 PNG，它一律回 96**（实测，PyMuPDF 1.28.2）。所以密度由
本模块自己按格式解析，只用标准库。

纯标准库 + `pdfbackend` 的 probe 结果：Flask 父进程 import 它，不碰 worker。
"""

from __future__ import annotations

import struct
from pathlib import Path

#: pt → mm。与 `app.py` 的 `MM_PER_PT` 同值（那一份是 HTTP 层的常量，
#: 这里不 import 它——engine 不依赖 app）。
MM_PER_PT = 25.4 / 72.0

#: 文件没写物理密度时的**明确假定**，按扩展名。取值与改造前 `app.py` 里那
#: 两个字面量逐位相同：PNG 主要来自 matplotlib（`savefig(dpi=600)` 是本产品
#: 的默认出图密度），其余按印刷常规 300。**它是 fallback，不是事实**——
#: 每一条走这里的 spec 都带 `dpi_source="assumed"`，界面必须说出来。
ASSUMED_DPI: dict[str, float] = {".png": 600.0}
ASSUMED_DPI_DEFAULT = 300.0

#: `dpi_source` 的闭集（后端只发前两个；`derived` / `unknown` 由前端在
#: 「源已不可用、只剩文档里那份」时使用，见 `web/src/lib/originalSpec.ts`）。
DPI_SOURCES = ("metadata", "assumed", "derived", "unknown")

#: 素材形态的闭集。`figure`（可编辑 Figure 的真实图幅）不由本模块产出——
#: 那一档的权威是渲染回来的 manifest，只存在于前端（`web/AGENTS.md`：
#: 「图幅不是派生字段」）。
SOURCE_KINDS = ("vector", "raster", "figure", "unknown")


def assumed_dpi(suffix: str) -> float:
    return ASSUMED_DPI.get(suffix.lower(), ASSUMED_DPI_DEFAULT)


# ---------------------------------------------------------------------------
# 位图的物理密度：文件自己写的那一份
# ---------------------------------------------------------------------------
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: 每米像素 → 每英寸像素
_M_PER_INCH = 0.0254


def _unquantize(dpi: float) -> float:
    """pHYs 存的是**每米整数像素**，300 dpi 落盘后读回来是 299.9994。

    量化误差的上界是 `0.0254/2 = 0.0127` dpi，所以「离最近的整数不到 0.02」
    这件事只可能由一个整数 dpi 产生——把它还原回去不是四舍五入的方便，是
    把编码损失去掉。超出这个半径的值原样保留（真的有非整数密度的素材）。
    """
    nearest = round(dpi)
    return float(nearest) if abs(dpi - nearest) <= 0.02 else round(dpi, 3)


def _png_dpi(data: bytes) -> tuple[float, float] | None:
    """PNG `pHYs` 块 → (x_dpi, y_dpi)。没有该块、或单位是"未指定"时回 None。

    单位字节为 0 的 pHYs 只声明**像素长宽比**，不声明物理尺寸（PNG 规范
    11.3.5.3）。那种文件在物理密度这件事上与"没有 pHYs"完全一样，不能把
    比例数字当 dpi 用。
    """
    pos = len(_PNG_MAGIC)
    n = len(data)
    while pos + 8 <= n:
        (length,) = struct.unpack_from(">I", data, pos)
        ctype = data[pos + 4 : pos + 8]
        body = pos + 8
        if ctype == b"pHYs":
            if length < 9 or body + 9 > n:
                return None
            ppu_x, ppu_y, unit = struct.unpack_from(">IIB", data, body)
            if unit != 1 or ppu_x <= 0 or ppu_y <= 0:
                return None  # 单位未指定 = 只有长宽比，没有物理密度
            return (_unquantize(ppu_x * _M_PER_INCH), _unquantize(ppu_y * _M_PER_INCH))
        # pHYs 规范上必须在 IDAT 之前；扫到像素数据就可以停了
        if ctype == b"IDAT" or ctype == b"IEND":
            return None
        pos = body + length + 4  # 数据 + CRC
    return None


def _exif_dpi(seg: bytes) -> tuple[float, float] | None:
    """APP1 里的 Exif：IFD0 的 XResolution / YResolution / ResolutionUnit。"""
    if not seg.startswith(b"Exif\x00\x00"):
        return None
    tiff = seg[6:]
    if len(tiff) < 8:
        return None
    order = tiff[:2]
    if order == b"II":
        end = "<"
    elif order == b"MM":
        end = ">"
    else:
        return None
    (offset,) = struct.unpack_from(end + "I", tiff, 4)
    if offset + 2 > len(tiff):
        return None
    (count,) = struct.unpack_from(end + "H", tiff, offset)
    vals: dict[int, float] = {}
    unit: int | None = None
    for i in range(count):
        e = offset + 2 + i * 12
        if e + 12 > len(tiff):
            break
        tag, typ, cnt = struct.unpack_from(end + "HHI", tiff, e)
        if tag in (0x011A, 0x011B) and typ == 5 and cnt == 1:
            (voff,) = struct.unpack_from(end + "I", tiff, e + 8)
            if voff + 8 <= len(tiff):
                num, den = struct.unpack_from(end + "II", tiff, voff)
                if den:
                    vals[tag] = num / den
        elif tag == 0x0128 and typ == 3:
            (unit,) = struct.unpack_from(end + "H", tiff, e + 8)
    x, y = vals.get(0x011A), vals.get(0x011B)
    if not x or not y:
        return None
    # 1 = 无单位（只有长宽比）；2 = 英寸；3 = 厘米
    if unit == 3:
        return (x * 2.54, y * 2.54)
    if unit in (None, 2):
        return (x, y)
    return None


def _jpeg_dpi(data: bytes) -> tuple[float, float] | None:
    """JPEG：先认 JFIF APP0 的密度，没有再认 Exif APP1 的分辨率。"""
    pos = 2
    n = len(data)
    exif: tuple[float, float] | None = None
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            return exif
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA or marker == 0xD9:  # 进入压缩数据 / 文件结束
            return exif
        (seg_len,) = struct.unpack_from(">H", data, pos + 2)
        seg = data[pos + 4 : pos + 2 + seg_len]
        if marker == 0xE0 and seg.startswith(b"JFIF\x00") and len(seg) >= 12:
            unit = seg[7]
            x, y = struct.unpack_from(">HH", seg, 8)
            if unit == 1 and x and y:
                return (float(x), float(y))
            if unit == 2 and x and y:  # 每厘米
                return (x * 2.54, y * 2.54)
            # unit == 0：只有长宽比，继续看 Exif
        elif marker == 0xE1 and exif is None:
            exif = _exif_dpi(seg)
        pos += 2 + seg_len
    return exif


def raster_dpi(path: Path) -> tuple[float, float] | None:
    """位图文件自己写下的物理密度 (x, y)，没写就回 None。

    **读不动不等于没有**：本函数把 I/O 失败一并回成 None，调用方因此会退到
    `assumed`——那是能给出的最诚实的降级（我们确实不知道），并且它在 spec
    里说得出自己是假定的。
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
            if head.startswith(_PNG_MAGIC):
                return _png_dpi(head)
            if head[:2] == b"\xff\xd8":
                # Exif 缩略图能把 APP1 顶到几十 KB，头 4 KB 不一定装得下
                return _jpeg_dpi(head + fh.read(262144))
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# 素材 → spec
# ---------------------------------------------------------------------------
def asset_spec(path: Path, kind: str, probe: dict) -> dict:
    """一份素材的原图规格。`probe` 是 `pdfbackend.probe_asset()` 的结果。

    回的是**文件说了什么**，不掺任何画布信息：这里没有 x/y/w/h、没有画布
    缩放、没有页面裁切。「按原图导出」忽略哪些 layout 变换是前端那一份服务
    的事（`web/src/lib/originalSpec.ts`），本模块连文档都看不到。
    """
    if kind == "pdf":
        w_pt = float(probe["w_pt"])
        h_pt = float(probe["h_pt"])
        return {
            "source_kind": "vector",
            "logical_w_mm": round(w_pt * MM_PER_PT, 3),
            "logical_h_mm": round(h_pt * MM_PER_PT, 3),
            "px_w": None,
            "px_h": None,
            "dpi": None,
            "dpi_source": "unknown",
            "viewport_pt": [round(w_pt, 3), round(h_pt, 3)],
            # 矢量页面没有 alpha 通道这一说；页面画不画底色要解析内容流才知道，
            # 本模块不猜——`None` 就是「这一维我们没测量」。
            "transparent": None,
        }
    px_w = int(probe["px_w"])
    px_h = int(probe["px_h"])
    measured = raster_dpi(path)
    if measured is not None:
        dpi_x, dpi_y = measured
        dpi_source = "metadata"
    else:
        dpi_x = dpi_y = assumed_dpi(path.suffix)
        dpi_source = "assumed"
    alpha = probe.get("alpha")
    return {
        "source_kind": "raster",
        "logical_w_mm": round(px_w / dpi_x * 25.4, 3),
        "logical_h_mm": round(px_h / dpi_y * 25.4, 3),
        "px_w": px_w,
        "px_h": px_h,
        # 非正方像素（x≠y）的素材极少见，但真出现时报 x 会撒谎；两轴不等就
        # 不给单一 dpi——逻辑尺寸上面已经按各自的轴算过了。
        "dpi": round(dpi_x, 3) if abs(dpi_x - dpi_y) < 1e-6 else None,
        "dpi_source": dpi_source,
        "viewport_pt": None,
        "transparent": bool(alpha) if alpha is not None else None,
    }
