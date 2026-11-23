"""生成 tests/golden/array_vectors.json：阵列（ADR 0185 M6）的参考实现。

独立于前端写的一份：网格 / 环形阵列每一份对象的框与旋转角。前端 `lib/array.golden.test.ts` 对拍；后端 `tests/test_array_vectors.py`
在同一份结果上核几何不变量（网格等距、环形各份中心在圆上、角度等分）。结果只用旧字段，导出端照常画。

    PYTHONPATH=$PWD/src python scripts/dev/gen_array_vectors.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

OBJ = {"id": "r", "type": "shape", "shape": "rect", "x": 10, "y": 20, "w": 8, "h": 4, "strokePt": 1,
       "color": "#000000", "fill": "#ff0000"}  # fmt: skip

CASES = [
    {"name": "网格 2×3", "objs": [OBJ], "spec": {"kind": "grid", "rows": 2, "cols": 3, "gapX": 2, "gapY": 1.5}},
    {"name": "环形 6 份跟着转", "objs": [OBJ], "spec": {"kind": "radial", "count": 6, "radius": 15, "rotate": True}},
    {"name": "环形 4 份不转", "objs": [OBJ], "spec": {"kind": "radial", "count": 4, "radius": 10, "rotate": False}},
    {
        "name": "两个对象当一个整体、环形 3 份跟着转",
        "objs": [OBJ, {**OBJ, "id": "s", "x": 20, "y": 22, "w": 4, "h": 6, "rotationDeg": 10}],
        "spec": {"kind": "radial", "count": 3, "radius": 20, "rotate": True},
    },
]  # fmt: skip


def vbox(o):
    a = math.radians(o.get("rotationDeg", 0))
    c, s = abs(math.cos(a)), abs(math.sin(a))
    w = o["w"] * c + o["h"] * s
    h = o["w"] * s + o["h"] * c
    return (o["x"] + o["w"] / 2 - w / 2, o["y"] + o["h"] / 2 - h / 2, w, h)


def rot(p, c, deg):
    a = math.radians(deg)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return (c[0] + dx * math.cos(a) - dy * math.sin(a), c[1] + dx * math.sin(a) + dy * math.cos(a))


def norm(d):
    r = (d % 360 + 540) % 360 - 180
    return 0.0 if abs(r) < 1e-9 else r


def expect(case):
    objs, spec = case["objs"], case["spec"]
    boxes = [vbox(o) for o in objs]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    w, h = x1 - x0, y1 - y0
    centre = (x0 + w / 2, y0 + h / 2)
    out = []
    if spec["kind"] == "grid":
        for r in range(spec["rows"]):
            for c in range(spec["cols"]):
                if not r and not c:
                    continue
                dx, dy = c * (w + spec["gapX"]), r * (h + spec["gapY"])
                out.append(
                    [
                        {"x": o["x"] + dx, "y": o["y"] + dy, "rotationDeg": o.get("rotationDeg", 0)}
                        for o in objs
                    ]
                )
        return out
    n, R = spec["count"], spec["radius"]
    for i in range(1, n):
        a = 2 * math.pi * i / n
        deg = 360 * i / n
        dx, dy = R * math.sin(a), R - R * math.cos(a)
        copies = []
        for o in objs:
            cx, cy = o["x"] + o["w"] / 2, o["y"] + o["h"] / 2
            rd = o.get("rotationDeg", 0)
            if spec["rotate"]:
                cx, cy = rot((cx, cy), centre, deg)
                rd = norm(rd + deg)
            copies.append({"x": cx - o["w"] / 2 + dx, "y": cy - o["h"] / 2 + dy, "rotationDeg": rd})
        out.append(copies)
    return out


def main() -> None:
    out = [{**c, "expect": expect(c)} for c in CASES]
    dest = Path(__file__).resolve().parents[2] / "tests" / "golden" / "array_vectors.json"
    dest.write_text(
        json.dumps({"cases": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {dest} ({len(out)} cases)")


if __name__ == "__main__":
    main()
