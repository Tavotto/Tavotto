"""Read-only, bounded static SVG assets. Browser display never embeds inline markup."""

from __future__ import annotations

import math
import re
from pathlib import Path

from .rendercore.svgcheck import SVGBudget, SVGError, parse_svg, strip_matplotlib_doctype

_MAX_BYTES = SVGBudget().max_bytes
_UNITS = {
    "": 0.75,
    "px": 0.75,
    "pt": 1.0,
    "pc": 12.0,
    "mm": 72 / 25.4,
    "cm": 72 / 2.54,
    "in": 72.0,
    "q": 72 / 101.6,
}


class UnsupportedSVG(ValueError):
    code = "svg_asset_unsupported"

    def __init__(self, reason: str):
        super().__init__(reason)
        self.params = {"reason": reason}


def _length(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z]*)\s*", value)
    if match is None or match[2] not in _UNITS:
        raise UnsupportedSVG("svg_dimensions")
    result = float(match[1]) * _UNITS[match[2]]
    if not math.isfinite(result) or result <= 0 or result > 144000:
        raise UnsupportedSVG("svg_dimensions")
    return result


def read(path: Path) -> tuple[bytes, dict]:
    """Validate the exact bytes being served, including when a previously valid file changes."""
    _, data, facts = snapshot(path)
    return data, facts


def snapshot(path: Path) -> tuple[bytes, bytes, dict]:
    """One bounded read: raw identity, validated representation, physical facts."""
    with Path(path).open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    data, facts = inspect_bytes(raw)
    return raw, data, facts


def resolve_size(
    width: float | None, height: float | None, view_box: list[float] | None
) -> tuple[float, float]:
    """Resolve physical pt size from explicit lengths and a viewBox.

    Missing dimensions follow the static asset rule: viewBox user units are CSS
    pixels (0.75 pt), and one explicit dimension scales the other by the viewBox
    aspect ratio. This is the single size authority for every SVG consumer.
    """
    if view_box is not None:
        # Extent validation belongs here, before any aspect-ratio division: a zero
        # or non-finite viewBox must be a structured svg_dimensions refusal for
        # every consumer, not a ZeroDivisionError from one of them.
        if (
            len(view_box) != 4
            or not all(math.isfinite(v) for v in view_box)
            or min(view_box[2:]) <= 0
        ):
            raise UnsupportedSVG("svg_dimensions")
        if width is None and height is None:
            width, height = view_box[2] * 0.75, view_box[3] * 0.75
        elif width is None:
            width = height * view_box[2] / view_box[3]
        elif height is None:
            height = width * view_box[3] / view_box[2]
    if (
        width is None
        or height is None
        or not all(math.isfinite(v) and 0 < v <= 144000 for v in (width, height))
    ):
        raise UnsupportedSVG("svg_dimensions")
    return width, height


def inspect_bytes(data: bytes) -> tuple[bytes, dict]:
    """Validate frozen bytes and reuse the static asset physical-size authority."""
    # Remove only the exact inert stock declaration before parsing. Any custom
    # DTD, entity, subset or second declaration still fails the strict parser.
    data = strip_matplotlib_doctype(data)
    try:
        root = parse_svg(data)
    except SVGError as exc:
        raise UnsupportedSVG(exc.code) from exc
    w, h = _length(root.get("width")), _length(root.get("height"))
    view = root.get("viewBox")
    box = None
    if view is not None:
        try:
            box = [float(v) for v in re.split(r"[\s,]+", view.strip())]
        except ValueError as exc:
            raise UnsupportedSVG("svg_dimensions") from exc
    w, h = resolve_size(w, h, box)
    return data, {"kind": "svg", "w_pt": w, "h_pt": h}
