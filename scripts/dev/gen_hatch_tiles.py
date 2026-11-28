#!/usr/bin/env python3
"""Generate native hatch geometry from the same Matplotlib patterns as its editor."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

from matplotlib.path import Path as ArtistPath
from matplotlib.transforms import Affine2D

from tavotto.engine.manifest_fields import HATCH_LINEWIDTH_PT

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "src/tavotto/resources/native/hatch_tiles.json"


def _presets() -> list[str]:
    source = ROOT / "src/tavotto/engine/overrides.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    definition = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "HATCHES" for target in node.targets)
    )
    return [
        value
        for item in definition.elts
        for value in (
            ast.literal_eval(item.value)
            if isinstance(item, ast.Starred)
            else [ast.literal_eval(item)]
        )
    ]


def _tile(symbol: str) -> dict[str, list]:
    path = ArtistPath.hatch(symbol)
    tile: dict[str, list] = {"open": [], "closed": []}
    current = []
    # Segment curves at a larger scale so the converted path stays close to
    # the original cubic after rescaling into the renderer's unit tile.
    scale = 256
    for points, code in path.iter_segments(
        transform=Affine2D().scale(scale), simplify=False, snap=False, curves=False
    ):
        if code == ArtistPath.CLOSEPOLY:
            if current[-1] == current[0]:
                current.pop()
            tile["closed"].append(current)
            current = []
            continue
        if code == ArtistPath.MOVETO and current:
            tile["open"].append(current)
            current = []
        if code not in (ArtistPath.MOVETO, ArtistPath.LINETO):
            raise ValueError(f"Unexpected hatch segment in {symbol!r}")
        current.append(tuple(round(float(coordinate) / scale, 6) for coordinate in points))
    if current:
        tile["open"].append(current)
    if any(len(line) != 2 for line in tile["open"]):
        raise ValueError(f"Expected independent line segments in {symbol!r}")
    return tile


def build() -> str:
    patterns = {symbol: _tile(symbol) for symbol in _presets() if symbol}
    payload = {"tile_pt": 72, "linewidth_pt": HATCH_LINEWIDTH_PT, "patterns": patterns}
    return json.dumps(payload, separators=(",", ":")) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    args = parser.parse_args()
    data = build()
    if args.write:
        OUTPUT.write_text(data, encoding="utf-8")
    elif OUTPUT.read_text(encoding="utf-8") != data:
        print("Native hatch geometry differs from Matplotlib. Regenerate the catalog.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
