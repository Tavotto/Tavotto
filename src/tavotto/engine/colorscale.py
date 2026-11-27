"""Common color-scale manifest fields and the shipped palette vocabulary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ENGINE_DIR = Path(__file__).resolve().parent
# The browser unpacks a flat engine and its resources in one directory.
_RESOURCE_ROOT = _ENGINE_DIR if sys.platform == "emscripten" else _ENGINE_DIR.parent
PALETTE_CATALOG_PATH = _RESOURCE_ROOT / "resources" / "color_palettes.json"
with PALETTE_CATALOG_PATH.open(encoding="utf-8") as source:
    CMAPS = list(json.load(source)["palettes"])


def colorbar_layout_fields(orientation, extend):
    """Guide layout is independent of the data-to-color mapping."""
    fields = []
    for prop, value, options in (
        ("orientation", orientation, ["vertical", "horizontal"]),
        ("extend", extend, ["neither", "both", "min", "max"]),
    ):
        if value is not None:
            fields.append({"prop": prop, "type": "enum", "value": value, "options": options})
    return fields


def colorbar_outline_fields(visible, width):
    return [
        {"prop": "outline_visible", "type": "bool", "value": bool(visible)},
        {
            "prop": "outline_width",
            "type": "number",
            "value": round(float(width), 2),
            "min": 0,
            "max": 10,
            "step": 0.1,
            "unit": "pt",
        },
    ]


def color_scale_fields(vmin, vmax, cmap_field, *, precision=4):
    """Project and inspector properties for a continuous data-to-color scale."""
    span = abs(float(vmax) - float(vmin)) if vmin is not None and vmax is not None else 1.0
    step = max(span / 100.0, 1e-6)

    def number(value):
        if value is None:
            return None
        value = float(value)
        return value if precision is None else round(value, precision)

    step = number(step)
    return [
        cmap_field,
        {
            "prop": "vmin",
            "type": "number",
            "value": number(vmin),
            "step": step,
            "group": "颜色映射",
        },
        {
            "prop": "vmax",
            "type": "number",
            "value": number(vmax),
            "step": step,
            "group": "颜色映射",
        },
    ]
