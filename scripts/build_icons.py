#!/usr/bin/env python3
"""从 assets/icon/icon.svg 生成 .icns（macOS）与 .ico（Windows）。

产物是二进制且要进版本库——CI 的打包机上没有 SVG 渲染器，让它们现生成会把
构建环境搞复杂。改了 icon.svg 之后在 macOS 上跑一次本脚本并提交产物即可：

    python scripts/build_icons.py

依赖：rsvg-convert（brew install librsvg）、iconutil（macOS 自带）、
magick（brew install imagemagick，只用来打 .ico）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "icon" / "icon.svg"
ICNS = ROOT / "assets" / "icon" / "icon.icns"
ICO = ROOT / "assets" / "icon" / "icon.ico"

# macOS iconset 要求的尺寸组合（@1x/@2x 成对）
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

# Tauri 壳直接引用的 PNG（tauri.conf.json 的 bundle.icon）——
# 和 .icns/.ico 出自同一份 SVG，忘了重出会让 dmg 窗口图标停在旧画面
TAURI_PNGS = [
    (ROOT / "src-tauri" / "icons" / "128x128.png", 128),
    (ROOT / "src-tauri" / "icons" / "icon.png", 512),
]


def need(tool: str) -> str:
    p = shutil.which(tool)
    if p is None:
        print(f"找不到 {tool}", file=sys.stderr)
        raise SystemExit(1)
    return p


def render(rsvg: str, size: int, out: Path) -> None:
    subprocess.run([rsvg, "-w", str(size), "-h", str(size), str(SVG), "-o", str(out)],
                   check=True)


def main() -> int:
    if not SVG.is_file():
        print(f"缺少 {SVG}", file=sys.stderr)
        return 1
    rsvg = need("rsvg-convert")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ---- .icns ----
        if sys.platform == "darwin":
            iconset = tmp / "icon.iconset"
            iconset.mkdir()
            for s in ICNS_SIZES:
                render(rsvg, s, iconset / f"icon_{s}x{s}.png")
                if s * 2 <= 1024:          # @2x：iconutil 靠文件名识别
                    render(rsvg, s * 2, iconset / f"icon_{s}x{s}@2x.png")
            subprocess.run([need("iconutil"), "-c", "icns", str(iconset),
                            "-o", str(ICNS)], check=True)
            print(f"✓ {ICNS.relative_to(ROOT)}  {ICNS.stat().st_size:,} bytes")
        else:
            print("跳过 .icns（只能在 macOS 上用 iconutil 生成）")

        # ---- .ico ----
        pngs = []
        for s in ICO_SIZES:
            p = tmp / f"{s}.png"
            render(rsvg, s, p)
            pngs.append(str(p))
        subprocess.run([need("magick"), *pngs, str(ICO)], check=True)
        print(f"✓ {ICO.relative_to(ROOT)}  {ICO.stat().st_size:,} bytes")

        # ---- Tauri PNGs ----
        for out, size in TAURI_PNGS:
            if out.parent.is_dir():
                render(rsvg, size, out)
                print(f"✓ {out.relative_to(ROOT)}  {out.stat().st_size:,} bytes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
