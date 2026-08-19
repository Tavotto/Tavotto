#!/usr/bin/env python3
"""生成 macOS 安装 .dmg 的窗口背景图（assets/brand/dmg-background.png）。

设计遵循 Tavotto Brand System：纸色 #f2f2ef 底、左上角 compact 标志 + 字标、
中部「App → Applications」的极简引导箭头、下方一行说明文字。图标落点必须
与 scripts/make_dmg.sh 里 Finder 的 icon position 保持一致（app 165,190 /
Applications 495,190，窗口内容 660×400）。

用 .venv 里现成的 PyMuPDF 直接绘制（不引 SVG 光栅化依赖）；输出 1320×800、
144 dpi 的 PNG——Finder 按 dpi 折算成 660×400 的点尺寸，Retina 下即 @2x。
产物提交进仓库，CI 直接取用；改设计只改本文件后重跑：

    .venv/bin/python scripts/build_dmg_background.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_brand_assets import GEOMETRY, PALETTES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "brand" / "dmg-background.png"

W, H = 660.0, 400.0
# 色板 token（与 web/src/index.css 同源；独立资产里色值固定是正常需求）
PAPER = (0xF2 / 255, 0xF2 / 255, 0xEF / 255)
INK = (0x1B / 255, 0x1B / 255, 0x18 / 255)
INK2 = (0x5C / 255, 0x5C / 255, 0x55 / 255)
INK3 = (0x6B / 255, 0x6B / 255, 0x64 / 255)
FAINT = (0xA3 / 255, 0xA3 / 255, 0x9A / 255)

# Finder icon position 是图标中心；与 make_dmg.sh 严格同源
APP_POS = (165.0, 190.0)
FOLDER_POS = (495.0, 190.0)


def hex_rgb(s: str) -> tuple[float, float, float]:
    return tuple(int(s[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def draw_mark(page: pymupdf.Page, x: float, y: float, size: float) -> None:
    """compact 标志：几何与 build_brand_assets 同一张表（viewBox 1024）。"""
    k = size / 1024.0
    palette = PALETTES["paper"]
    for role, g in GEOMETRY["compact"]:
        rect = pymupdf.Rect(x + g["x"] * k, y + g["y"] * k,
                            x + (g["x"] + g["w"]) * k, y + (g["y"] + g["h"]) * k)
        if role == "ink-stroke":
            page.draw_rect(rect, color=hex_rgb(palette["ink"]),
                           width=g["sw"] * k, fill=None)
        else:
            page.draw_rect(rect, color=None, fill=hex_rgb(palette[role]))


def centered(page: pymupdf.Page, font: pymupdf.Font, text: str, cx: float,
             baseline: float, size: float, color) -> None:
    w = font.text_length(text, fontsize=size)
    tw = pymupdf.TextWriter(page.rect, color=color)
    tw.append((cx - w / 2, baseline), text, font=font, fontsize=size)
    tw.write_text(page)


def main() -> int:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.draw_rect(page.rect, color=None, fill=PAPER)

    # 左上角品牌：compact 标志 + 字标
    draw_mark(page, 28, 26, 26)
    helv_bold = pymupdf.Font("helvetica-bold")
    tw = pymupdf.TextWriter(page.rect, color=INK)
    tw.append((64, 26 + 20), "Tavotto", font=helv_bold, fontsize=15)
    tw.write_text(page)

    # 中部引导箭头：App 图标 → Applications（落点为图标中心连线）
    y = APP_POS[1]
    x0, x1 = APP_POS[0] + 92, FOLDER_POS[0] - 92
    page.draw_line((x0, y), (x1, y), color=INK3, width=2.2)
    for dy in (-7.5, 7.5):
        page.draw_line((x1 - 11, y + dy), (x1, y), color=INK3, width=2.2)

    # 说明文字：主句中文，副句英文（图标标签在 y≈270，说明再往下留足空档）
    cjk = pymupdf.Font("china-s")
    helv = pymupdf.Font("helvetica")
    centered(page, cjk, "把 Tavotto 拖进 Applications 完成安装", W / 2, 320, 13, INK2)
    centered(page, helv, "Drag Tavotto to Applications to install", W / 2, 340, 10, FAINT)

    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    pix.set_dpi(144, 144)  # Finder 按 dpi 折算：1320×800 px = 660×400 pt（Retina @2x）
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pix.save(OUT)
    print(f"✓ {OUT.relative_to(ROOT)}  {pix.width}×{pix.height} @144dpi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
