"""重录 `tests/golden/star_arc_vectors.json`（星形 / 弧线几何的共享向量，ADR 0185 M1）。

期望值由 `rendercore.geometry.star_points` / `arc_segments` 出数；前端 `web/src/lib/shapeGeometry.ts` 的
`starPoints` / `arcPoints` 是独立实现、对拍这份向量（`web/src/lib/starArc.golden.test.ts`；后端一侧
`tests/test_star_arc.py`）。改公式两侧一起改，再重录：

    python scripts/dev/gen_star_arc_vectors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tavotto.rendercore import geometry  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

STARS = [
    (5, 40.0, 40.0, 0.5, 0.382),
    (5, 60.0, 30.0, 0.0, 0.5),
    (8, 30.0, 30.0, 1.0, 0.7),
    (2, 20.0, 20.0, 0.0, 0.01),  # 夹到 3 角、内径比 0.05
    (20, 20.0, 20.0, 0.0, 1.5),  # 夹到 12 角、内径比 0.95
]
ARCS = [(40.0, 30.0, 0.5), (10.0, 50.0, 0.0), (0.0, 0.0, 0.0)]


def main(out: Path) -> None:
    data = {
        "_doc": "星形 / 弧线几何的共享向量（ADR 0185 M1）：前端 starPoints / arcPoints 与后端 star_points / arc_segments 各跑一遍。"
        "框空间：原点框左上、y 向下。由 scripts/dev/gen_star_arc_vectors.py 重录。",
        "star": [
            {
                "points": n,
                "w": w,
                "h": h,
                "inset": inset,
                "ratio": r,
                "out": [list(p) for p in geometry.star_points(n, w, h, inset, r)],
            }
            for n, w, h, inset, r in STARS
        ],
        "arc": [
            {
                "w": w,
                "h": h,
                "inset": inset,
                "segments": [list(s) for s in geometry.arc_segments(w, h, inset)],
            }
            for w, h, inset in ARCS
        ],
    }
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main(
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else ROOT / "tests" / "golden" / "star_arc_vectors.json"
    )
