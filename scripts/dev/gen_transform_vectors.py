"""生成 tests/golden/transform_vectors.json：变换面板（ADR 0185 M4 第二批）的参考实现。

独立于前端写的一份：镜像 / 倾斜 / 缩放之后的路径对象（框 + 比例坐标锚点 + 旋转角）。前端 `lib/transform.golden.test.ts`
对拍它；后端 `tests/test_transform_vectors.py` 把同一份结果经 `geometry.path_segments` 换到页面，核「倾斜 = 页面上的斜切」
「镜像 = 页面上的翻转」逐点成立——结果只用旧字段，导出端不需要新东西。

    PYTHONPATH=$PWD/src python scripts/dev/gen_transform_vectors.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def node(p, i=None, o=None):
    out = {"p": p}
    if i is not None:
        out["in"] = i
    if o is not None:
        out["out"] = o
    return out


BASE = {
    "id": "p",
    "type": "path",
    "strokePt": 1,
    "color": "#000000",
    "fill": "#88ccee",
}

CASES = [
    {
        "name": "旋转 30° 的曲线路径，水平镜像（参考点中心）",
        "obj": {
            **BASE,
            "x": 10,
            "y": 20,
            "w": 40,
            "h": 30,
            "rotationDeg": 30,
            "subpaths": [
                {
                    "closed": True,
                    "nodes": [
                        node([0, 0], None, [0.4, -0.2]),
                        node([1, 0.3]),
                        node([0.2, 1], [0.8, 1.1]),
                    ],
                }
            ],
        },
        "op": {"kind": "reflect", "axis": "h"},
        "ref": [30, 35],
    },
    {
        "name": "开放折线，垂直镜像（参考点在框外）",
        "obj": {
            **BASE,
            "x": 0,
            "y": 0,
            "w": 20,
            "h": 10,
            "subpaths": [
                {"closed": False, "nodes": [node([0, 0]), node([0.5, 1]), node([1, 0.2])]}
            ],
        },
        "op": {"kind": "reflect", "axis": "v"},
        "ref": [5, 40],
    },
    {
        "name": "旋转 −45° 的曲线路径，倾斜 30°",
        "obj": {
            **BASE,
            "x": 30,
            "y": 10,
            "w": 50,
            "h": 20,
            "rotationDeg": -45,
            "subpaths": [
                {
                    "closed": True,
                    "nodes": [
                        node([0, 0.5], [0, 0.9], [0, 0.1]),
                        node([1, 0.5], [1, 0.1], [1, 0.9]),
                    ],
                }
            ],
        },
        "op": {"kind": "skew", "deg": 30},
        "ref": [55, 20],
    },
    {
        "name": "矩形路径，倾斜 −20°（参考点在底边）",
        "obj": {
            **BASE,
            "x": 0,
            "y": 0,
            "w": 30,
            "h": 20,
            "subpaths": [
                {"closed": True, "nodes": [node([0, 0]), node([1, 0]), node([1, 1]), node([0, 1])]}
            ],
        },
        "op": {"kind": "skew", "deg": -20},
        "ref": [15, 20],
    },
]


def rot(p, c, deg):
    if not deg:
        return p
    a = math.radians(deg)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return [c[0] + dx * math.cos(a) - dy * math.sin(a), c[1] + dx * math.sin(a) + dy * math.cos(a)]


def page_nodes(o):
    c = [o["x"] + o["w"] / 2, o["y"] + o["h"] / 2]
    deg = o.get("rotationDeg", 0)

    def at(q):
        return rot([o["x"] + q[0] * o["w"], o["y"] + q[1] * o["h"]], c, deg)

    out = []
    for sp in o["subpaths"]:
        out.append(
            {
                "closed": sp["closed"],
                "nodes": [
                    {
                        "p": at(n["p"]),
                        "in": at(n.get("in", n["p"])),
                        "out": at(n.get("out", n["p"])),
                    }
                    for n in sp["nodes"]
                ],
            }
        )
    return out


def cubic_extent(p0, p1, p2, p3):
    """三次贝塞尔一维的极值（导数的根）。"""
    vals = [p0, p3]
    a = -p0 + 3 * p1 - 3 * p2 + p3
    b = 2 * (p0 - 2 * p1 + p2)
    c = p1 - p0
    ts = []
    if abs(a) < 1e-12:
        if abs(b) > 1e-12:
            ts.append(-c / b)
    else:
        disc = b * b - 4 * a * c
        if disc >= 0:
            r = math.sqrt(disc)
            ts += [(-b + r) / (2 * a), (-b - r) / (2 * a)]
    for t in ts:
        if 0 < t < 1:
            mt = 1 - t
            vals.append(mt**3 * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t**3 * p3)
    return min(vals), max(vals)


def fit(subs):
    xs, ys = [], []
    for sp in subs:
        ns = sp["nodes"]
        pairs = list(zip(ns, ns[1:])) + ([(ns[-1], ns[0])] if sp["closed"] else [])
        for a, b in pairs:
            for k, acc in ((0, xs), (1, ys)):
                lo, hi = cubic_extent(a["p"][k], a["out"][k], b["in"][k], b["p"][k])
                acc += [lo, hi]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0

    def frac(q):
        return [(q[0] - x0) / w, (q[1] - y0) / h]

    return {
        "x": x0,
        "y": y0,
        "w": w,
        "h": h,
        "subpaths": [
            {
                "closed": sp["closed"],
                "nodes": [{k: frac(n[k]) for k in ("p", "in", "out")} for n in sp["nodes"]],
            }
            for sp in subs
        ],
    }


def expect(case):
    o, op, ref = case["obj"], case["op"], case["ref"]
    if op["kind"] == "skew":
        t = math.tan(math.radians(op["deg"]))
        subs = [
            {
                "closed": sp["closed"],
                "nodes": [
                    {k: [n[k][0] + t * (ref[1] - n[k][1]), n[k][1]] for k in ("p", "in", "out")}
                    for n in sp["nodes"]
                ],
            }
            for sp in page_nodes(o)
        ]
        return {**fit(subs), "rotationDeg": 0}
    # 镜像：页面上翻转全部锚点，再按「反向的旋转角」放回一个框里——等价写法：框关于 ref 翻，角度取反，框内比例坐标翻
    axis = op["axis"]

    def flip(q):
        return [1 - q[0], q[1]] if axis == "h" else [q[0], 1 - q[1]]

    x, y = o["x"], o["y"]
    if axis == "h":
        x = 2 * ref[0] - (o["x"] + o["w"])
    else:
        y = 2 * ref[1] - (o["y"] + o["h"])
    return {
        "x": x,
        "y": y,
        "w": o["w"],
        "h": o["h"],
        "rotationDeg": -o.get("rotationDeg", 0) or 0,
        "subpaths": [
            {
                "closed": sp["closed"],
                "nodes": [
                    {k: flip(n.get(k, n["p"])) for k in ("p", "in", "out")} for n in sp["nodes"]
                ],
            }
            for sp in o["subpaths"]
        ],
    }


def main() -> None:
    out = [{**c, "expect": expect(c)} for c in CASES]
    dest = Path(__file__).resolve().parents[2] / "tests" / "golden" / "transform_vectors.json"
    dest.write_text(
        json.dumps({"cases": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {dest} ({len(out)} cases)")


if __name__ == "__main__":
    main()
