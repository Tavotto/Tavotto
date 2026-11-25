"""Common color-scale manifest fields and the shipped palette vocabulary."""

from __future__ import annotations

import json
from pathlib import Path

PALETTE_CATALOG_PATH = Path(__file__).resolve().parents[1] / "resources" / "color_palettes.json"
with PALETTE_CATALOG_PATH.open(encoding="utf-8") as source:
    CMAPS = list(json.load(source)["palettes"])


def color_scale_fields(vmin, vmax, cmap_field, *, precision=4):
    """Project and inspector properties for a continuous data-to-color scale."""
    span = abs(float(vmax) - float(vmin)) if vmin is not None and vmax is not None else 1.0
    step = max(span / 100.0, 1e-6)

    def number(value):
        if value is None:
            return None
        value = float(value)
        return value if precision is None else round(value, precision)

    return [
        cmap_field,
        *(
            {
                "prop": prop,
                "type": "number",
                "value": number(value),
                "step": number(step),
                "group": "颜色映射",
            }
            for prop, value in (("vmin", vmin), ("vmax", vmax))
        ),
    ]
