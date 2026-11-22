"""实时圆角（ADR 0185 M3 第二批）烤出来的路径在导出里真的是圆角：共享向量 `tests/golden/live_corner_vectors.json`
（参考实现 `scripts/dev/gen_live_corner_vectors.py`，前端 `lib/liveCorners.roundCorners` 对拍同一份）交给
`geometry.path_segments`，核每段圆弧：两端是切点、采样点到圆心的距离 = r（三次贝塞尔逼近圆的误差 < 0.03%）、
切线与相邻直线边共线。后端不需要认识「圆角」——它只是普通的 path。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.rendercore import geometry

VECTORS = json.loads(
    (Path(__file__).resolve().parent / "golden" / "live_corner_vectors.json").read_text(
        encoding="utf-8"
    )
)


def _bez(p0, c1, c2, p3, t):
    u = 1 - t
    return [
        u**3 * p0[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t**3 * p3[k] for k in (0, 1)
    ]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_rounded_corners_export_as_true_circular_arcs(case):
    nodes = case["out"]
    o = {
        "stroke_pt": 1,
        "color": "#000000",
        "fill": "#cccccc",
        "subpaths": [
            {
                "closed": case["closed"],
                "nodes": [{k: n[k] for k in ("p", "in", "out")} for n in nodes],
            }
        ],
    }
    g = geometry.path_segments(o, 1.0, 1.0, 1.0)
    segs = g["fill"]
    # 段序：M 起点，之后每段对应相邻两个锚点；圆弧段的两端都带 center / r
    pts = [n["p"] for n in nodes]
    arcs = 0
    cursor = None
    for seg in segs:
        if seg[0] == "M":
            cursor = list(seg[1:3])
            continue
        if seg[0] == "C":
            c1, c2, end = list(seg[1:3]), list(seg[3:5]), list(seg[5:7])
            a = next(
                n
                for n in nodes
                if math.isclose(n["p"][0], cursor[0], abs_tol=1e-9)
                and math.isclose(n["p"][1], cursor[1], abs_tol=1e-9)
            )
            if "center" in a:
                arcs += 1
                cx, cy = a["center"]
                for t in (0.1, 0.25, 0.5, 0.75, 0.9):
                    x, y = _bez(cursor, c1, c2, end, t)
                    assert math.hypot(x - cx, y - cy) == pytest.approx(a["r"], rel=3e-4)
                # 两端的切线垂直于半径（与边相切）
                for p, h in ((cursor, c1), (end, c2)):
                    rad = (p[0] - cx, p[1] - cy)
                    tan = (h[0] - p[0], h[1] - p[1])
                    assert abs(rad[0] * tan[0] + rad[1] * tan[1]) < 1e-9 * max(1.0, a["r"] ** 2)
            cursor = end
        elif seg[0] == "L":
            cursor = list(seg[1:3])
    # 每个圆角至少一段弧（转过的角超过 90° 的切成几段）
    assert arcs >= len(case["corners"]), (arcs, case["corners"])
    assert len(pts) > len(case["points"])
