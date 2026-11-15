"""重录 `tests/golden/live_corner_vectors.json`（实时圆角的共享向量，ADR 0185 M3 第二批）。

这里是圆角几何的**独立参考实现**（不 import 前端）：两条直线边夹一个角、半径 r 的圆弧换成两个切点 + 一段三次贝塞尔
（切点离顶点 r / tan(θ/2)，手柄长 4/3·tan(φ/4)·r，φ = π − θ；r 夹到相邻边长一半 × tan(θ/2)）。前端
`web/src/lib/liveCorners.roundCorners` 对拍这份向量（`web/src/lib/liveCorners.golden.test.ts`）；后端 `tests/test_live_corners.py`
把同一份结果交给 `geometry.path_segments` 导出，核圆弧落在圆上、与两条边相切。改几何两侧一起改，再重录：

    python scripts/dev/gen_live_corner_vectors.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _round(points: list[list[float]], closed: bool, corners: list[int], r: float) -> list[dict]:
    n = len(points)
    out = []
    for i, p in enumerate(points):
        if i not in corners or (not closed and i in (0, n - 1)):
            out.append({"p": p, "in": p, "out": p})
            continue
        a, b = points[(i - 1) % n], points[(i + 1) % n]
        v1 = [a[0] - p[0], a[1] - p[1]]
        v2 = [b[0] - p[0], b[1] - p[1]]
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        u1 = [v1[0] / l1, v1[1] / l1]
        u2 = [v2[0] / l2, v2[1] / l2]
        th = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        rr = min(r, min(l1, l2) / 2 * math.tan(th / 2))
        d = rr / math.tan(th / 2)
        phi = math.pi - th
        # 转过的角超过 90° 就切成 ceil(phi / 90°) 段：一段三次贝塞尔只在 90° 以内足够像圆
        nseg = max(1, math.ceil(phi / (math.pi / 2) - 1e-9))
        k = 4 / 3 * math.tan(phi / (4 * nseg)) * rr
        bl = math.hypot(u1[0] + u2[0], u1[1] + u2[1])
        bis = [(u1[0] + u2[0]) / bl, (u1[1] + u2[1]) / bl]
        ctr = [p[0] + bis[0] * rr / math.sin(th / 2), p[1] + bis[1] * rr / math.sin(th / 2)]
        p1 = [p[0] + u1[0] * d, p[1] + u1[1] * d]
        p2 = [p[0] + u2[0] * d, p[1] + u2[1] * d]
        # 从切点 1 往切点 2 转：方向取使切点 1 处的切线朝着顶点的那一边（与前端同一判据）
        a0 = math.atan2(p1[1] - ctr[1], p1[0] - ctr[0])
        sgn = 1 if (-math.sin(a0)) * -u1[0] + math.cos(a0) * -u1[1] >= 0 else -1
        for j in range(nseg + 1):
            a = a0 + sgn * phi * j / nseg
            q = (
                p1
                if j == 0
                else p2
                if j == nseg
                else [ctr[0] + rr * math.cos(a), ctr[1] + rr * math.sin(a)]
            )
            tg = [-math.sin(a) * sgn, math.cos(a) * sgn]
            out.append(
                {
                    "p": q,
                    "in": q if j == 0 else [q[0] - tg[0] * k, q[1] - tg[1] * k],
                    "out": q if j == nseg else [q[0] + tg[0] * k, q[1] + tg[1] * k],
                    "center": ctr,
                    "r": rr,
                }
            )
    return out


CASES = [
    {
        "name": "方形一个角 r=5",
        "points": [[0, 0], [20, 0], [20, 20], [0, 20]],
        "closed": True,
        "corners": [1],
        "r": 5,
    },
    {
        "name": "方形四个角 r 过大夹到 10",
        "points": [[0, 0], [20, 0], [20, 20], [0, 20]],
        "closed": True,
        "corners": [0, 1, 2, 3],
        "r": 99,
    },
    {
        "name": "直角三角形的锐角 r=1",
        "points": [[0, 0], [20, 0], [0, 10]],
        "closed": True,
        "corners": [1],
        "r": 1,
    },
    {
        "name": "开放折线的中间角 r=3",
        "points": [[0, 0], [10, 0], [10, 10]],
        "closed": False,
        "corners": [1],
        "r": 3,
    },
    {
        "name": "钝角 r=4",
        "points": [[0, 0], [10, 0], [25, 8], [0, 10]],
        "closed": True,
        "corners": [1],
        "r": 4,
    },
]


def main() -> None:
    out = [{**c, "out": _round(c["points"], c["closed"], c["corners"], c["r"])} for c in CASES]
    path = ROOT / "tests" / "golden" / "live_corner_vectors.json"
    path.write_text(
        json.dumps({"cases": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"{path}: {len(out)} cases")


if __name__ == "__main__":
    main()
