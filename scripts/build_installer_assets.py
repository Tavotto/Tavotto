#!/usr/bin/env python3
"""生成 Windows NSIS 安装器的品牌位图（assets/brand/installer-*.bmp）。

MUI2 的经典尺寸：头图 150×57、欢迎/完成页侧栏 164×314（24 位 BMP）。
设计遵循 Tavotto Brand System：纸色底、compact 标志、字标；几何从
build_brand_assets 导入。PyMuPDF 绘制，BMP 编码内置（bottom-up BGR——
NSIS 吃的是最保守的 BMP3 形态，sips 输出的 top-down DIB 反而有兼容风险）。
产物提交进仓库，Windows 构建直接取用。

    .venv/bin/python scripts/build_installer_assets.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_brand_assets import GEOMETRY, PALETTES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "assets" / "brand"

PAPER = (0xF2 / 255, 0xF2 / 255, 0xEF / 255)
INK = (0x1B / 255, 0x1B / 255, 0x18 / 255)
INK3 = (0x6B / 255, 0x6B / 255, 0x64 / 255)


def hex_rgb(s: str) -> tuple[float, float, float]:
    return tuple(int(s[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def draw_mark(
    page: pymupdf.Page, x: float, y: float, size: float, variant: str = "compact"
) -> None:
    k = size / 1024.0
    palette = PALETTES["paper"]
    for role, g in GEOMETRY[variant]:
        rect = pymupdf.Rect(
            x + g["x"] * k, y + g["y"] * k, x + (g["x"] + g["w"]) * k, y + (g["y"] + g["h"]) * k
        )
        if role == "ink-stroke":
            page.draw_rect(
                rect, color=hex_rgb(palette["ink"]), width=max(g["sw"] * k, 0.6), fill=None
            )
        else:
            page.draw_rect(rect, color=None, fill=hex_rgb(palette[role]))


def save_bmp(page: pymupdf.Page, out: Path) -> None:
    """24 位 bottom-up BMP3：行序自下而上、BGR、行宽补齐到 4 字节。"""
    pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
    w, h, stride = pix.width, pix.height, pix.stride
    samples = pix.samples  # RGB、自上而下
    row_pad = (-w * 3) % 4
    rows = bytearray()
    for y in range(h - 1, -1, -1):
        row = samples[y * stride : y * stride + w * 3]
        for x in range(w):
            r, g, b = row[x * 3 : x * 3 + 3]
            rows += bytes((b, g, r))
        rows += b"\x00" * row_pad
    header = struct.pack("<2sIHHI", b"BM", 54 + len(rows), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(rows), 2835, 2835, 0, 0)
    out.write_bytes(header + info + rows)
    print(f"✓ {out.relative_to(ROOT)}")


def header() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=150, height=57)
    page.draw_rect(page.rect, color=None, fill=PAPER)
    draw_mark(page, 14, 17, 23)
    tw = pymupdf.TextWriter(page.rect, color=INK)
    tw.append((45, 34), "Tavotto", font=pymupdf.Font("helvetica-bold"), fontsize=13)
    tw.write_text(page)
    save_bmp(page, BRAND / "installer-header.bmp")


def sidebar() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=164, height=314)
    page.draw_rect(page.rect, color=None, fill=PAPER)
    draw_mark(page, 50, 92, 64, variant="full")
    helv_bold = pymupdf.Font("helvetica-bold")
    name = "Tavotto"
    w = helv_bold.text_length(name, fontsize=15)
    tw = pymupdf.TextWriter(page.rect, color=INK)
    tw.append(((164 - w) / 2, 192), name, font=helv_bold, fontsize=15)
    tw.write_text(page)
    tag = "Figures, composed."
    helv = pymupdf.Font("helvetica")
    w2 = helv.text_length(tag, fontsize=8.5)
    tw2 = pymupdf.TextWriter(page.rect, color=INK3)
    tw2.append(((164 - w2) / 2, 208), tag, font=helv, fontsize=8.5)
    tw2.write_text(page)
    save_bmp(page, BRAND / "installer-sidebar.bmp")


def main() -> int:
    BRAND.mkdir(parents=True, exist_ok=True)
    header()
    sidebar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
