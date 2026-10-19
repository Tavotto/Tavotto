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
    with Path(path).open("rb") as stream:
        data = stream.read(_MAX_BYTES + 1)
    # Remove only the exact inert stock declaration before parsing. Any custom
    # DTD, entity, subset or second declaration still fails the strict parser.
    data = strip_matplotlib_doctype(data)
    try:
        root = parse_svg(data)
    except SVGError as exc:
        raise UnsupportedSVG(exc.code) from exc
    w, h = _length(root.get("width")), _length(root.get("height"))
    view = root.get("viewBox")
    if view is not None:
        try:
            box = [float(v) for v in re.split(r"[\s,]+", view.strip())]
            if len(box) != 4 or not all(math.isfinite(v) for v in box) or min(box[2:]) <= 0:
                raise ValueError
        except ValueError as exc:
            raise UnsupportedSVG("svg_dimensions") from exc
        if w is None and h is None:
            w, h = box[2] * 0.75, box[3] * 0.75
        elif w is None:
            w = h * box[2] / box[3]
        elif h is None:
            h = w * box[3] / box[2]
    if w is None or h is None or not all(math.isfinite(v) and 0 < v <= 144000 for v in (w, h)):
        raise UnsupportedSVG("svg_dimensions")
    return data, {"kind": "svg", "w_pt": w, "h_pt": h}
