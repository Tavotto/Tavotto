#!/usr/bin/env python3
"""生成期刊出处卡的**测试夹具快照图**（ADR 0155 §6）。

真实快照由采集流水线渲染（原网页 / PDF 页，把那一段用黄色荧光笔标出并裁成短图）。这里只画一张
假的「原网页」：几行 FIXTURE 文字，其中一行用黄色荧光笔底标出，存成内容寻址的 webp
（`tests/fixtures/journal_snapshots/snapshots/<sha256>.webp`）。仅供单测 / e2e / 截图，不是任何真实期刊页。

    python scripts/gen_journal_snapshot_fixture.py          # 写文件并打印 sha256 / 宽 / 高
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "journal_snapshots" / "snapshots"
W, H = 520, 150
HIGHLIGHT = (255, 235, 59)  # 黄色荧光笔


def render() -> bytes:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=17)
    d.text((16, 14), "FIXTURE PAGE - Artwork sizing", fill=(60, 60, 60), font=font)
    d.text((16, 44), "Figures should be supplied at a width of", fill=(60, 60, 60), font=font)
    line = "FIXTURE beta double 18 cm"
    box = d.textbbox((16, 74), line, font=font)
    d.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill=HIGHLIGHT)
    d.text((16, 74), line, fill=(20, 20, 20), font=font)
    d.text((16, 108), "when printed in two columns.", fill=(60, 60, 60), font=font)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True)
    return buf.getvalue()


def main() -> int:
    data = render()
    sha = hashlib.sha256(data).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{sha}.webp").write_bytes(data)
    print(f"snapshots/{sha}.webp {W} {H}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
