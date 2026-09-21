"""**纯标准库**的最小合法产物字节（统一实施包 U08：入口用例里替身 worker 写出的文件也要过得了产物检查）。

`blank_pdf(w_pt, h_pt)` 是一份带正确交叉引用表的单页空白 PDF（qpdf `--check` 零告警、PyMuPDF 打得开、
页盒就是给定尺寸）；`solid_png(w, h, dpi=)` 是一张 8 位 RGB 纯色 PNG（逐块 CRC 正确、可选 pHYs）。
它们**不是**产品写入器的替代品——只用于「文件本身没问题、检查器该放行」这一侧的用例；坏文件另造。
本模块不 import 任何产品代码。
"""

from __future__ import annotations

import struct
import zlib


def blank_pdf(w_pt: float, h_pt: float) -> bytes:
    """单页空白 PDF，MediaBox = [0 0 w h]，xref 偏移按字节算。"""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Resources << >> /Contents 4 0 R >>"
            % (_num(w_pt), _num(h_pt))
        ),
        None,  # 内容流，下面拼
    ]
    content = b"q Q\n"
    objs[3] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream"
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def solid_png(w: int, h: int, *, dpi: float | None = None, alpha: bool = False) -> bytes:
    """8 位 RGB(A) 纯色 PNG；`dpi` 给了就写 pHYs（单位：米）。"""

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    color_type = 6 if alpha else 2
    channels = 4 if alpha else 3
    raw = b"".join(b"\x00" + bytes([200, 30, 30, 255][:channels]) * w for _ in range(h))
    out = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    )
    if dpi:
        ppm = int(round(dpi / 0.0254))
        out += chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    out += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return out


def _num(v: float) -> bytes:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return (s or "0").encode("ascii")


__all__ = ["blank_pdf", "solid_png"]
