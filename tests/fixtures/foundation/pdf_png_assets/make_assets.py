"""U00 合成 fixture ⑥ 的生成脚本：一页 PDF（文字 + 图形 + 透明 + 非对称页盒）与
带元数据的原 PNG。**纯标准库**（不用 PyMuPDF、Pillow、matplotlib）——夹具的真值
不能来自被测产品用的那套库。

产物进 git（几 KB）；改本脚本后重跑 `python make_assets.py` 并把 `truth.json`
的数字对照过再提交。生成是确定性的：同一脚本两次跑出的字节逐位相同（不写时间戳）。

PDF：MediaBox 300×200 pt，CropBox [15 10 285 170]——四边内缩 15 / 10 / 15 / 30 pt，
**刻意不对称**（读页盒的实现如果只看 MediaBox、或把 CropBox 当成居中裁切，尺寸就会
错）。内容：Helvetica 12pt 一行文字、实心蓝矩形、一条折线、一块 α=0.5 的红矩形
盖在蓝矩形上（ExtGState /ca /CA）。

PNG：64×48 RGBA，左半不透明蓝、右半 alpha 渐变；`pHYs` 300 dpi（11811 px/m）；
`tEXt` Software / Comment 两条。另出一张没有 `pHYs` 的同图 `original_nophys.png`
——「密度没写」与「密度写着 300」是两个必须分得开的答案。
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

MEDIA_BOX = (0, 0, 300, 200)
CROP_BOX = (15, 10, 285, 170)
TEXT = "U00 fixture: y = 3x + 1"
FONT = "Helvetica"
ALPHA = 0.5

PNG_W, PNG_H = 64, 48
PNG_DPI = 300
PNG_PPM = round(PNG_DPI / 0.0254)  # 11811
PNG_TEXT = {"Software": "tavotto-foundation-fixture", "Comment": "U00 synthetic original PNG"}


def _pdf_content() -> bytes:
    lines = [
        "BT",
        f"/F1 12 Tf 30 150 Td ({TEXT}) Tj",
        "ET",
        "0 0 1 rg 40 40 100 60 re f",  # 实心蓝矩形
        "0 g 1 w 30 30 m 120 120 l 200 60 l S",  # 折线
        "q /GS0 gs 1 0 0 rg 90 60 100 60 re f Q",  # 半透明红矩形，盖住蓝矩形一角
    ]
    return ("\n".join(lines) + "\n").encode("latin-1")


def build_pdf() -> bytes:
    content = _pdf_content()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R"
            + f" /MediaBox [{' '.join(map(str, MEDIA_BOX))}]".encode()
            + f" /CropBox [{' '.join(map(str, CROP_BOX))}]".encode()
            + b" /Resources << /Font << /F1 4 0 R >> /ExtGState << /GS0 6 0 R >> >>"
            + b" /Contents 5 0 R >>"
        ),
        f"<< /Type /Font /Subtype /Type1 /BaseFont /{FONT} /Encoding /WinAnsiEncoding >>".encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        f"<< /Type /ExtGState /ca {ALPHA} /CA {ALPHA} >>".encode(),
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def _png_rows() -> bytes:
    rows = bytearray()
    for y in range(PNG_H):
        rows.append(0)  # filter: none
        for x in range(PNG_W):
            if x < PNG_W // 2:
                rows += bytes((30, 60, 200, 255))  # 左半：不透明蓝
            else:
                alpha = int(255 * (PNG_W - 1 - x) / (PNG_W // 2 - 1))
                rows += bytes((200, 40, 40, max(0, min(255, alpha))))  # 右半：alpha 渐变红
    return bytes(rows)


def build_png(*, with_phys: bool) -> bytes:
    ihdr = struct.pack(">IIBBBBB", PNG_W, PNG_H, 8, 6, 0, 0, 0)  # 8-bit RGBA
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += _chunk(b"IHDR", ihdr)
    if with_phys:
        out += _chunk(b"pHYs", struct.pack(">IIB", PNG_PPM, PNG_PPM, 1))
    for key, value in PNG_TEXT.items():
        out += _chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value.encode("latin-1"))
    out += _chunk(b"IDAT", zlib.compress(_png_rows(), 9))
    out += _chunk(b"IEND", b"")
    return bytes(out)


def truth() -> dict:
    return {
        "fixture": "pdf_png_assets",
        "pdf": {
            "file": "page.pdf",
            "pages": 1,
            "media_box": list(MEDIA_BOX),
            "crop_box": list(CROP_BOX),
            "crop_insets_pt": {
                "left": CROP_BOX[0] - MEDIA_BOX[0],
                "bottom": CROP_BOX[1] - MEDIA_BOX[1],
                "right": MEDIA_BOX[2] - CROP_BOX[2],
                "top": MEDIA_BOX[3] - CROP_BOX[3],
            },
            "visible_size_pt": [CROP_BOX[2] - CROP_BOX[0], CROP_BOX[3] - CROP_BOX[1]],
            "text": TEXT,
            "fonts": [FONT],
            "font_embedded": False,
            "transparency_alpha": ALPHA,
            "vector": True,
        },
        "png": {
            "file": "original.png",
            "width": PNG_W,
            "height": PNG_H,
            "bit_depth": 8,
            "color_type": 6,
            "has_alpha": True,
            "phys_pixels_per_metre": PNG_PPM,
            "dpi_declared": PNG_DPI,
            "text": PNG_TEXT,
        },
        "png_nophys": {
            "file": "original_nophys.png",
            "width": PNG_W,
            "height": PNG_H,
            "phys": None,
        },
    }


def main() -> None:
    (HERE / "page.pdf").write_bytes(build_pdf())
    (HERE / "original.png").write_bytes(build_png(with_phys=True))
    (HERE / "original_nophys.png").write_bytes(build_png(with_phys=False))
    (HERE / "truth.json").write_text(
        json.dumps(truth(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
