"""Canvas metadata used by the reference card and the model context budget."""

import pytest

from tavotto.engine import ai_convert, imageheader
from tavotto.engine.agent_runner import media


def chunk(kind, payload):
    return kind + len(payload).to_bytes(4, "little") + payload + b"\0" * (len(payload) & 1)


def riff(chunks):
    return b"RIFF" + (4 + len(chunks)).to_bytes(4, "little") + b"WEBP" + chunks


def webp(kind, width=600, height=300):
    if kind == b"VP8 ":
        payload = b"\0\0\0\x9d\x01\x2a" + width.to_bytes(2, "little") + height.to_bytes(2, "little")
    elif kind == b"VP8L":
        payload = b"\x2f" + ((width - 1) | ((height - 1) << 14)).to_bytes(4, "little")
    else:
        payload = b"\0" * 4 + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return riff(chunk(kind, payload))


@pytest.mark.parametrize("kind", [b"VP8 ", b"VP8L", b"VP8X"])
def test_webp_reference_and_context_use_the_actual_canvas(kind):
    data = webp(kind)
    assert imageheader.image_dimensions(data) == (600, 300)
    assert ai_convert.image_dimensions(data) == (600, 300)
    img = media.from_bytes(data)
    assert img is not None and img.mime_type == "image/webp"
    assert img.estimated_tokens == media.from_bytes(b"GIF89a\x58\x02\x2c\x01").estimated_tokens
    for length in range(len(data)):
        assert imageheader.image_dimensions(data[:length]) is None


def test_extended_webp_uses_canvas_not_frame_and_skips_padded_unknown_chunks():
    canvas = webp(b"VP8X", 1200, 900)[12:]
    frame = webp(b"VP8L", 100, 70)[12:]
    assert imageheader.image_dimensions(riff(chunk(b"TEST", b"odd") + canvas + frame)) == (
        1200,
        900,
    )


@pytest.mark.parametrize(
    "data",
    [
        riff(chunk(b"VP8 ", b"\0" * 10)),
        riff(chunk(b"VP8L", b"\x2f\0\0\0\xe0")),
        riff(chunk(b"VP8X", b"\0" * 9)),
        riff(b"VP8X" + (200).to_bytes(4, "little") + b"\0" * 10),
        riff(chunk(b"TEST", b"nothing")),
        webp(b"VP8 ", 0, 300),
    ],
)
def test_unknown_or_invalid_headers_keep_the_conservative_budget(data):
    assert imageheader.image_dimensions(data) is None
    assert media.from_bytes(data).estimated_tokens == 65536


def test_large_webp_keeps_a_spatial_budget():
    assert media.from_bytes(webp(b"VP8X", 5000, 5000)).estimated_tokens > 60000
