"""Computed SVG output frame; source inputs remain in the original pt basis.

Pure stdlib. The /2 policy is union(source viewport, actual same-draw ink bbox)
plus one physical point, rounded outwards to whole points. Not savefig provenance.
"""

from __future__ import annotations

import copy
import math

KEYS = {"version", "source", "source_viewport_pt", "rect_pt", "edit_basis"}
MARKER = "data-tavotto-native-render-frame-pt"


def _numbers(value, length):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != length
        or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
            for x in value
        )
    ):
        raise ValueError("invalid computed frame numbers")
    return list(value)


def validate_frame(raw):
    if (
        not isinstance(raw, dict)
        or set(raw) != KEYS
        or type(raw.get("version")) is not int
        or raw["version"] != 1
        or raw.get("source") != "computed-svg"
        or raw.get("edit_basis") != "source-viewport-pt"
    ):
        raise ValueError("invalid computed frame")
    sw, sh = _numbers(raw["source_viewport_pt"], 2)
    left, top, width, height = _numbers(raw["rect_pt"], 4)
    if (
        min(sw, sh, width, height) <= 0
        or left > 0
        or top > 0
        or left + width < sw
        or top + height < sh
    ):
        raise ValueError("computed frame does not contain source viewport")
    return copy.deepcopy(raw)


def from_ink(source_size, ink):
    sw, sh = _numbers(source_size, 2)
    x0, y0, x1, y1 = _numbers(ink, 4)
    if min(sw, sh) <= 0 or x1 <= x0 or y1 <= y0:
        raise ValueError("actual ink extent unavailable")
    left, top = math.floor(min(0, x0 * sw) - 1), math.floor(min(0, y0 * sh) - 1)
    right, bottom = math.ceil(max(sw, x1 * sw) + 1), math.ceil(max(sh, y1 * sh) + 1)
    return validate_frame(
        {
            "version": 1,
            "source": "computed-svg",
            "source_viewport_pt": [sw, sh],
            "rect_pt": [left, top, right - left, bottom - top],
            "edit_basis": "source-viewport-pt",
        }
    )


def source_fraction_rect(raw, rect):
    """Source fractions -> render-frame fractions, including true ancestor clips."""
    frame = validate_frame(raw)
    sw, sh = frame["source_viewport_pt"]
    left, top, width, height = frame["rect_pt"]
    x, y, w, h = _numbers(rect, 4)
    return [(x * sw - left) / width, (y * sh - top) / height, w * sw / width, h * sh / height]
