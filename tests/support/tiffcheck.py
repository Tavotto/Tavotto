"""测试用的**独立** TIFF 读取端（纯标准库）。

与 `src/tavotto/tiffwrite.py` 没有一行共享代码：对拍要两侧独立——写入端与读取端
同源等于自己验自己（`crosscheck-needs-independent-sides`）。这里按 TIFF 6.0 的
字节布局自己解 IFD、自己按 `Compression = 8` 用 zlib 解 strip，Pillow / PyMuPDF
在场时再多一把尺。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

TAGS = {
    256: "width",
    257: "height",
    258: "bits_per_sample",
    259: "compression",
    262: "photometric",
    273: "strip_offsets",
    277: "samples_per_pixel",
    278: "rows_per_strip",
    279: "strip_byte_counts",
    282: "x_resolution",
    283: "y_resolution",
    284: "planar_config",
    296: "resolution_unit",
    305: "software",
    317: "predictor",
    322: "tile_width",
    338: "extra_samples",
}

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def read_tags(path: Path | str) -> dict:
    """第一个 IFD 的全部已知标签，键是人话名，RATIONAL 已除成 float。"""
    data = Path(path).read_bytes()
    if data[:2] == b"II":
        e = "<"
    elif data[:2] == b"MM":
        e = ">"
    else:
        raise ValueError(f"不是 TIFF：{data[:4]!r}")
    magic, ifd = struct.unpack(e + "HI", data[2:8])
    if magic != 42:
        raise ValueError(f"TIFF 魔数不对：{magic}")
    (n,) = struct.unpack(e + "H", data[ifd : ifd + 2])
    out: dict = {"byte_order": e}
    for i in range(n):
        base = ifd + 2 + 12 * i
        tag, typ, count = struct.unpack(e + "HHI", data[base : base + 8])
        size = _TYPE_SIZE[typ] * count
        if size <= 4:
            raw = data[base + 8 : base + 8 + size]
        else:
            (off,) = struct.unpack(e + "I", data[base + 8 : base + 12])
            raw = data[off : off + size]
        if typ == 2:
            val: object = raw.split(b"\x00", 1)[0].decode("ascii", "replace")
        elif typ == 3:
            val = list(struct.unpack(e + f"{count}H", raw))
        elif typ == 4:
            val = list(struct.unpack(e + f"{count}I", raw))
        elif typ == 5:
            pairs = struct.unpack(e + f"{2 * count}I", raw)
            val = [
                pairs[k] / pairs[k + 1] if pairs[k + 1] else None for k in range(0, len(pairs), 2)
            ]
        else:
            val = raw
        if isinstance(val, list) and len(val) == 1 and tag not in (273, 279):
            val = val[0]
        out[TAGS.get(tag, tag)] = val
    (out["next_ifd"],) = struct.unpack(e + "I", data[ifd + 2 + 12 * n : ifd + 6 + 12 * n])
    return out


def decode_samples(path: Path | str) -> tuple[dict, bytes]:
    """解出紧凑的像素缓冲区（逐行、逐像素、逐通道）。只支持 strip + 无压缩 /
    Deflate(8 / 32946)，且无预测器——这正是我们自己写的、以及 matplotlib 经
    Pillow 写的那两种。"""
    tags = read_tags(path)
    data = Path(path).read_bytes()
    comp = tags.get("compression", 1)
    if tags.get("predictor", 1) != 1:
        raise ValueError(f"带预测器 {tags['predictor']}，这个读取端不认")
    if "tile_width" in tags:
        raise ValueError("分块 TIFF，这个读取端不认")
    out = bytearray()
    for off, cnt in zip(tags["strip_offsets"], tags["strip_byte_counts"]):
        chunk = data[off : off + cnt]
        if comp == 1:
            out += chunk
        elif comp in (8, 32946):
            out += zlib.decompress(chunk)
        else:
            raise ValueError(f"压缩方式 {comp}，这个读取端不认")
    return tags, bytes(out)
