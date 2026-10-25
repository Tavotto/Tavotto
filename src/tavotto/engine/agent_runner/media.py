"""Bounded image evidence for model requests, never for event/diagnostic logs.

Pure stdlib: the application supplies captured PNGs; this module does not render
or execute files. File reads use the task's existing candidate path boundary.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_CHECK_IMAGES = 16
MAX_CHECK_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Image:
    mime_type: str
    data: str
    label: str = ""
    estimated_tokens: int = 65536

    @property
    def url(self) -> str:
        return f"data:{self.mime_type};base64,{self.data}"

    def as_dict(self) -> dict:
        return {"mime_type": self.mime_type, "data": self.data, "label": self.label}


def _pixels(raw: bytes, mime: str) -> int | None:
    if mime == "image/png" and len(raw) >= 24 and raw[12:16] == b"IHDR":
        return int.from_bytes(raw[16:20], "big") * int.from_bytes(raw[20:24], "big")
    if mime == "image/gif" and len(raw) >= 10:
        return int.from_bytes(raw[6:8], "little") * int.from_bytes(raw[8:10], "little")
    if mime == "image/jpeg":
        offset = 2
        while offset + 4 <= len(raw) and raw[offset] == 255:
            marker = raw[offset + 1]
            if marker == 255:
                offset += 1
                continue
            if marker in (0xD9, 0xDA):
                break
            length = int.from_bytes(raw[offset + 2 : offset + 4], "big")
            if length < 2 or offset + length + 2 > len(raw):
                break
            if (
                marker
                in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF)
                and length >= 7
            ):
                return int.from_bytes(raw[offset + 5 : offset + 7], "big") * int.from_bytes(
                    raw[offset + 7 : offset + 9], "big"
                )
            offset += length + 2
    return None


def from_bytes(raw: bytes, label: str = "") -> Image | None:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif raw.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        return None
    pixels = _pixels(raw, mime)
    # Image tokenization is spatial, not base64 text tokenization. Keep a
    # deliberately generous fallback; reported endpoint usage remains authority.
    estimate = max(4096, 1024 + (pixels + 255) // 256) if pixels else 65536
    return Image(mime, base64.b64encode(raw).decode("ascii"), label[:200], estimate)


def from_wire(items: object) -> list[Image]:
    """Validate IPC evidence too; a bad item is omitted, never sent as a URL."""
    images: list[Image] = []
    total = 0
    if not isinstance(items, list):
        return images
    for item in items[:MAX_CHECK_IMAGES]:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        label = item.get("label", "")
        if not isinstance(data, str) or not isinstance(label, str):
            continue
        if len(data) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            continue
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            continue
        img = from_bytes(raw, label)
        if img is None or img.mime_type != item.get("mime_type"):
            continue
        if total + len(raw) > MAX_CHECK_BYTES:
            break
        total += len(raw)
        images.append(img)
    return images


@dataclass
class CheckResult:
    ok: bool
    report: str
    images: list[Image] = field(default_factory=list)
