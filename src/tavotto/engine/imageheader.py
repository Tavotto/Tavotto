"""Read image canvas dimensions without importing a renderer or decoding pixels.

Shared by conversion comparison and model image budgeting. Unknown/truncated
headers return None; a recognized header is not validation of the whole image.
"""

from __future__ import annotations


def _webp(data: bytes) -> tuple[int, int] | None:
    # RIFF chunk sizes exclude headers and odd-byte padding. In extended WebP,
    # VP8X describes the canvas, not an individual animation frame.
    # https://developers.google.com/speed/webp/docs/riff_container
    end = int.from_bytes(data[4:8], "little") + 8
    if end > len(data) or end < 20:
        return None
    offset = 12
    while offset + 8 <= end:
        kind = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        start = offset + 8
        stop = start + size
        if stop + (size & 1) > end:
            return None
        if kind == b"VP8X":
            if size < 10:
                return None
            return (
                int.from_bytes(data[start + 4 : start + 7], "little") + 1,
                int.from_bytes(data[start + 7 : start + 10], "little") + 1,
            )
        if kind == b"VP8L":
            if size < 5 or data[start] != 0x2F:
                return None
            bits = int.from_bytes(data[start + 1 : start + 5], "little")
            if bits >> 29:  # unsupported lossless version
                return None
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if kind == b"VP8 ":
            if size < 10 or data[start] & 1 or data[start + 3 : start + 6] != b"\x9d\x01\x2a":
                return None
            return (
                int.from_bytes(data[start + 6 : start + 8], "little") & 0x3FFF,
                int.from_bytes(data[start + 8 : start + 10], "little") & 0x3FFF,
            )
        offset = stop + (size & 1)
    return None


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """PNG, JPEG, GIF or WebP canvas width/height; no file access or execution."""
    dims = None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        dims = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    elif data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        dims = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        dims = _webp(data)
    elif data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data) and data[offset] == 255:
            marker = data[offset + 1]
            if marker == 255:
                offset += 1
                continue
            if marker in (0xD9, 0xDA):
                break
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                offset += 2
                continue
            size = int.from_bytes(data[offset + 2 : offset + 4], "big")
            if size < 2 or offset + 2 + size > len(data):
                break
            if (
                marker
                in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
                and size >= 7
            ):
                dims = (
                    int.from_bytes(data[offset + 7 : offset + 9], "big"),
                    int.from_bytes(data[offset + 5 : offset + 7], "big"),
                )
                break
            offset += size + 2
    return dims if dims and dims[0] > 0 and dims[1] > 0 else None
