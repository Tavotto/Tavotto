"""重录 `tests/golden/appearance_vectors.json`（填充与描边补齐的共享向量，ADR 0185 第二批）。

后端 `rendercore.geometry` 的 `valid_dash_pattern` / `miter_limit` / `shape_inset` / `_stroke` 出数；前端
`web/src/lib/shapeGeometry.ts` 的 `validDashPattern` / `miterLimitOf` / `shapeInset` / `svgStroke` 是独立实现、对拍这份向量
（`web/src/lib/appearance.golden.test.ts`；后端 `tests/test_appearance.py`）。改判据两侧一起改，再重录：

    python scripts/dev/gen_appearance_vectors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tavotto.rendercore import geometry  # noqa: E402

DASH = [
    [4, 2],
    [4, 2, 1, 2],
    [0, 3],
    [3],
    [],
    [0, 0],
    [-1, 2],
    [1] * 13,
    ["4", 2],
    [True, 2],
    None,
    "4 2",
]
MITER = [None, 10, 4, 1, 0.5, 100, -3, True, "4"]
INSET = [
    {"shape": "rect"},
    {"shape": "rect", "stroke_align": "center"},
    {"shape": "rect", "stroke_align": "outside"},
    {"shape": "rect", "stroke": False, "stroke_align": "outside"},
    {"shape": "ellipse", "stroke_align": "outside"},
    {"shape": "line", "stroke_align": "outside"},
    {"shape": "brace", "stroke_align": "center"},
    {"shape": "arc", "stroke_align": "outside"},
    {"shape": "star", "stroke_align": "center"},
    {"shape": "rect", "stroke_align": "bogus"},
]
STROKES = [
    {"dash": "dashed", "stroke_pt": 2.0},
    {"dash": "dotted", "dash_pattern": [3, 1], "line_cap": "butt", "stroke_pt": 2.0},
    {"dash_pattern": [5, 2.5, 1, 2.5], "stroke_pt": 0.5},
    {"dash": "dotted", "line_cap": "butt", "stroke_pt": 1.0},
    {"miter_limit": 3, "line_join": "miter", "stroke_pt": 1.0},
]


def main(out: Path) -> None:
    sw = 2.0
    data = {
        "_doc": "填充与描边补齐的共享向量（ADR 0185 第二批）。dash：自定义虚线的判据；miter：斜接上限的读法；inset：形状描边内缩量"
        "（线宽 sw = 2，描边对齐 内侧 / 居中 / 外侧）；stroke：一条描边实际生效的线型（pt）/ 线端 / 斜接上限。"
        "由 scripts/dev/gen_appearance_vectors.py 重录。",
        "dash": [
            {"in": v, "out": (list(r) if (r := geometry.valid_dash_pattern(v)) else None)}
            for v in DASH
        ],
        "miter": [{"in": v, "out": geometry.miter_limit(v)} for v in MITER],
        "inset": [{"in": o, "sw": sw, "out": geometry.shape_inset(o, sw)} for o in INSET],
        "stroke": [],
    }
    for o in STROKES:
        s = geometry._stroke({"color": "#000000", **o}, float(o["stroke_pt"]), join="round")
        data["stroke"].append(
            {"in": o, "dash": list(s.dash), "cap": s.cap, "miter_limit": s.miter_limit}
        )
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main(
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else ROOT / "tests" / "golden" / "appearance_vectors.json"
    )
