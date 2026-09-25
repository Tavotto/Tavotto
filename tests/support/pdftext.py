"""PDFium 的字符级文字层读取（独立读取器：与 RenderCore 写入器不同源，U10 起旧后端的
`page.get_text("rawdict")` 由它接替，ADR 0072）。

给排版几何用例用：每个字符的**原点**（pt，顶原点、y 向下——与旧 `rawdict` 的 `origin` 同一口径，
用例里的期望值不用改口径）、字号、字体名（去子集前缀）、包围盒右边界。PDFium 会往文字层里
合成换行 / 空格字符（`FPDFText_IsGenerated`），那些不是页面上落笔的字形，一律剔掉。

只在装了 pypdfium2 的机器上可用（U10 起它是运行时闭包的一部分）；本模块不 import 任何产品代码。
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass
from pathlib import Path

_SUBSET = re.compile(r"^[A-Z]{6}\+")


@dataclass(frozen=True)
class Char:
    x: float  #: 原点 x（pt）
    y: float  #: 原点 y（pt，顶原点、y 向下）
    c: str
    size: float
    font: str  #: 去掉子集前缀的字体名
    x1: float  #: 包围盒右边界（pt）


def chars(pdf: Path | bytes, page_index: int = 0) -> list[Char]:
    """页面上真正落笔的字符，按 (y, x) 排序。"""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    doc = pdfium.PdfDocument(pdf if isinstance(pdf, bytes) else str(pdf))
    try:
        page = doc[page_index]
        height = page.get_height()
        tp = page.get_textpage()
        try:
            out: list[Char] = []
            ox, oy = ctypes.c_double(), ctypes.c_double()
            buf = ctypes.create_string_buffer(256)
            flags = ctypes.c_int()
            for i in range(tp.count_chars()):
                if pdfium_c.FPDFText_IsGenerated(tp, i):
                    continue
                code = pdfium_c.FPDFText_GetUnicode(tp, i)
                pdfium_c.FPDFText_GetCharOrigin(tp, i, ctypes.byref(ox), ctypes.byref(oy))
                size = float(pdfium_c.FPDFText_GetFontSize(tp, i))
                n = pdfium_c.FPDFText_GetFontInfo(tp, i, buf, 256, ctypes.byref(flags))
                font = _SUBSET.sub("", buf.value[: max(0, n - 1)].decode("latin-1"))
                left, bottom, right, top = tp.get_charbox(i)
                out.append(Char(ox.value, height - oy.value, chr(code), size, font, right))
        finally:
            tp.close()
        page.close()
    finally:
        doc.close()
    return sorted(out, key=lambda ch: (round(ch.y, 1), ch.x))


def text(pdf: Path | bytes, page_index: int = 0) -> str:
    """PDFium 按**内容顺序**抽回的整页文字（认 ActualText），剔掉它合成的换行 / 空格
    （上标那一段基线不同，PDFium 会在它前后塞一个合成换行——那不是页面上的字）。"""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    doc = pdfium.PdfDocument(pdf if isinstance(pdf, bytes) else str(pdf))
    try:
        page = doc[page_index]
        tp = page.get_textpage()
        try:
            out = "".join(
                chr(pdfium_c.FPDFText_GetUnicode(tp, i))
                for i in range(tp.count_chars())
                if not pdfium_c.FPDFText_IsGenerated(tp, i)
            )
        finally:
            tp.close()
        page.close()
    finally:
        doc.close()
    return out


def rows(cs: list[Char]) -> list[str]:
    """按基线 y 分组重建的行文本（x 序拼接）。"""
    grouped: dict[float, list[tuple[float, str]]] = {}
    for ch in cs:
        grouped.setdefault(round(ch.y, 1), []).append((ch.x, ch.c))
    return ["".join(c for _, c in sorted(v)) for _, v in sorted(grouped.items())]


def fonts(cs: list[Char]) -> set[str]:
    return {ch.font for ch in cs}


def spans(cs: list[Char]) -> list[tuple[float, float, str]]:
    """按 x 序相邻、同字号同基线的字符并成 span：[(size, origin_y, text)]（上下标靠字号与基线区分；
    `H_{2}O` 是 H / 2 / O 三个 span——按阅读顺序切，不按行分组）。"""
    out: list[tuple[float, float, str]] = []
    for ch in sorted(cs, key=lambda c: (c.x, c.y)):
        key = (round(ch.size, 3), round(ch.y, 2))
        if out and (out[-1][0], out[-1][1]) == key:
            out[-1] = (out[-1][0], out[-1][1], out[-1][2] + ch.c)
        else:
            out.append((key[0], key[1], ch.c))
    return out
