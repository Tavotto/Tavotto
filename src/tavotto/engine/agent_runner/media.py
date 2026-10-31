"""Bounded image evidence for model requests, never for event/diagnostic logs.

Pure stdlib: the application supplies captured PNGs; this module does not render
or execute files. File reads use the task's existing candidate path boundary.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field

from ..imageheader import image_dimensions

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
    dims = image_dimensions(raw)
    pixels = dims[0] * dims[1] if dims else None
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
