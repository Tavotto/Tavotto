"""重录 `tests/golden/gradient_vectors.json`（渐变的共享向量，ADR 0186）。

后端 `rendercore.geometry` 的 `read_gradient` / `gradient_paint` 出数；前端 `web/src/lib/gradient.ts` 的
`readGradient` / `gradientGeometry` 是独立实现、对拍这份向量（`web/src/lib/gradient.golden.test.ts`；后端
`tests/test_gradient.py`）。改判据两侧一起改，再重录：

    python scripts/dev/gen_gradient_vectors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tavotto.rendercore import geometry, ir  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

S2 = [{"offset": 0, "color": "#ff0000"}, {"offset": 1, "color": "#0000ff"}]
CASES = [
    {"kind": "linear", "stops": S2, "from": [0, 0.5], "to": [1, 0.5]},
    {"kind": "linear", "stops": S2, "from": [0.2, 0.9], "to": [0.8, 0.1]},
    {
        "kind": "linear",
        "stops": [
            {"offset": 1, "color": "#000"},
            {"offset": 0.3, "color": "#00ff00", "opacity": 0.5},
            {"offset": 0, "color": "#ffffff"},
        ],
        "from": [0, 0],
        "to": [1, 1],
    },
    {"kind": "radial", "stops": S2, "center": [0.5, 0.5], "radius": 0.5},
    {"kind": "radial", "stops": S2, "center": [0.25, 0.75], "radius": 1.2},
    # 钳位与丢弃
    {
        "kind": "linear",
        "stops": [
            {"offset": -0.5, "color": "#ff0000", "opacity": 2},
            {"offset": 1.5, "color": "#0000ff", "opacity": -1},
            {"offset": 0.5, "color": "red"},
            {"offset": "0.5", "color": "#00ff00"},
            {"offset": 0.5, "color": "#00ff00", "opacity": "x"},
            {"offset": 0.5},
            "nope",
        ],
        "from": [0, 0],
        "to": [1, 0],
    },
    # 同位置的两个色标：稳定排序，先来的在前
    {
        "kind": "linear",
        "stops": [
            {"offset": 0.5, "color": "#111111"},
            {"offset": 0.5, "color": "#222222"},
            {"offset": 0, "color": "#333333"},
        ],
        "from": [0, 0],
        "to": [1, 0],
    },
    # 读不成 → null
    {"kind": "linear", "stops": S2[:1], "from": [0, 0], "to": [1, 0]},
    {"kind": "linear", "stops": S2, "from": [0.5, 0.5], "to": [0.5, 0.5]},
    {"kind": "linear", "stops": S2, "from": [0, 0]},
    {"kind": "linear", "stops": S2, "from": [0, "0"], "to": [1, 0]},
    {"kind": "radial", "stops": S2, "center": [0.5, 0.5], "radius": 0},
    {"kind": "radial", "stops": S2, "center": [0.5], "radius": 0.5},
    {"kind": "conic", "stops": S2},
    None,
    "linear",
]
BOXES = [(100.0, 50.0, 1.0), (30.0, 80.0, 0.5)]


def _ir(p) -> dict | None:
    if p is None:
        return None
    stops = [[s.offset, *s.rgb, s.alpha] for s in p.stops]
    if isinstance(p, ir.LinearGradient):
        return {"kind": "linear", "stops": stops, "p0": list(p.p0), "p1": list(p.p1)}
    return {"kind": "radial", "stops": stops, "center": list(p.center), "radius": p.radius}


def main() -> None:
    out = []
    for g in CASES:
        r = geometry.read_gradient(g)
        if r is not None:
            r = {k: (list(v) if isinstance(v, tuple) else v) for k, v in r.items()}
        out.append(
            {
                "in": g,
                "read": r,
                "paint": [
                    {"w": w, "h": h, "alpha": a, "out": _ir(geometry.gradient_paint(g, w, h, a))}
                    for w, h, a in BOXES
                ],
            }
        )
    path = ROOT / "tests" / "golden" / "gradient_vectors.json"
    path.write_text(
        json.dumps({"cases": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"{path}: {len(out)} cases")


if __name__ == "__main__":
    main()
