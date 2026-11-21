"""重录 `tests/golden/path_vectors.json`（自由路径几何的共享向量，ADR 0185 §四）。

期望值由 `rendercore.geometry.path_segments` 出数，前端 `web/src/lib/pathGeom.ts` 是独立实现、对拍这份向量
（`web/src/lib/pathGeom.golden.test.ts`；后端一侧 `tests/test_path_object.py`）。改公式时两侧一起改，再重录：

    python scripts/dev/gen_path_vectors.py tests/golden/path_vectors.json

`payload` 是前端 `exportPayload.toExportObject` 对 `doc` 的产物——前端测试另断言两者逐字段相同，所以这里的映射
抄错了会当场红，不会静默喂给后端一份前端发不出来的载荷。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tavotto.rendercore import geometry, plan  # noqa: E402 — 先把仓库的 src 放上 sys.path
from tavotto.rendercore.ir import mm2pt  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PAGE_H_MM = 200.0
K = 0.2238576251  # 0.5 − κ/2：单位框里内切圆的手柄位置


def payload(doc: dict) -> dict:
    """与前端 `exportPayload.toExportObject('path')` 同一映射（缺省值一律不发）。"""
    out: dict = {
        "x_mm": doc["x"],
        "y_mm": doc["y"],
        "w_mm": doc["w"],
        "h_mm": doc["h"],
        "type": "path",
        "subpaths": [
            {
                "closed": sp["closed"],
                "nodes": [{k: n[k] for k in ("p", "in", "out") if k in n} for n in sp["nodes"]],
            }
            for sp in doc["subpaths"]
        ],
    }
    if doc.get("fillRule") == "evenodd":
        out["fill_rule"] = "evenodd"
    out["stroke_pt"] = doc["strokePt"]
    out["color"] = doc["color"]
    out["fill"] = doc["fill"]
    if doc.get("fill") and doc.get("fillOpacity") is not None and doc["fillOpacity"] < 1:
        out["fill_opacity"] = doc["fillOpacity"]
    if doc.get("dash") and doc["dash"] != "solid":
        out["dash"] = doc["dash"]
    if doc.get("stroke") is False:
        out["stroke"] = False
    elif doc.get("strokeOpacity") is not None and doc["strokeOpacity"] < 1:
        out["stroke_opacity"] = doc["strokeOpacity"]
    if doc.get("lineCap") and doc["lineCap"] != "round":
        out["line_cap"] = doc["lineCap"]
    if doc.get("lineJoin") and doc["lineJoin"] != "round":
        out["line_join"] = doc["lineJoin"]
    for a, b in (("headStart", "head_start"), ("headEnd", "head_end")):
        if doc.get(a) and doc[a] != "none":
            out[b] = doc[a]
    for a, b in (("headSizeStart", "head_size_start"), ("headSizeEnd", "head_size_end")):
        if doc.get(a) and doc[a] != "medium":
            out[b] = doc[a]
    if doc.get("rotationDeg"):
        out["rotation_deg"] = doc["rotationDeg"]
    return out


def base(**kw) -> dict:
    d = {
        "id": "p",
        "type": "path",
        "x": 20.0,
        "y": 15.0,
        "w": 40.0,
        "h": 25.0,
        "strokePt": 2.0,
        "color": "#224466",
        "fill": None,
    }
    d.update(kw)
    return d


def closed(*nodes) -> dict:
    return {"closed": True, "nodes": list(nodes)}


def opened(*nodes) -> dict:
    return {"closed": False, "nodes": list(nodes)}


def square(a: float, b: float) -> dict:
    return closed({"p": [a, a]}, {"p": [b, a]}, {"p": [b, b]}, {"p": [a, b]})


CASES = [
    (
        "closed_corner",
        base(
            fill="#ffcc00",
            lineJoin="miter",
            subpaths=[closed({"p": [0.5, 0]}, {"p": [1, 1]}, {"p": [0, 1]})],
        ),
    ),
    (
        "closed_smooth",
        base(
            fill="#88aaee",
            fillOpacity=0.5,
            subpaths=[
                closed(
                    {"p": [1, 0.5], "in": [1, K], "out": [1, 1 - K], "smooth": True},
                    {"p": [0.5, 1], "in": [1 - K, 1], "out": [K, 1], "smooth": True},
                    {"p": [0, 0.5], "in": [0, 1 - K], "out": [0, K], "smooth": True},
                    {"p": [0.5, 0], "in": [K, 0], "out": [1 - K, 0], "smooth": True},
                )
            ],
        ),
    ),
    (
        "open_heads",
        base(
            headStart="triangle",
            headEnd="stealth",
            headSizeEnd="large",
            dash="dashed",
            subpaths=[
                opened(
                    {"p": [0, 1], "out": [0.1, 0.2]},
                    {"p": [0.5, 0.4], "in": [0.3, 0.4], "out": [0.7, 0.4], "smooth": True},
                    {"p": [1, 0], "in": [0.9, 0.6]},
                )
            ],
        ),
    ),
    (
        "open_dot_open_bar",
        base(
            headStart="dot",
            headEnd="open",
            headSizeStart="small",
            strokeOpacity=0.4,
            subpaths=[
                opened({"p": [0, 0]}, {"p": [1, 0.5]}),
                opened(
                    {"p": [0, 1]}, {"p": [0.6, 1], "in": [0.6, 1]}, {"p": [1, 0.8], "in": [1, 1]}
                ),
            ],
        ),
    ),
    (
        "open_bar_diamond_coincident_handles",
        base(
            headStart="bar",
            headEnd="diamond",
            subpaths=[opened({"p": [0, 0.5], "out": [0, 0.5]}, {"p": [1, 0.5], "in": [0.6, 0.1]})],
        ),
    ),
    (
        "multi_evenodd",
        base(
            fill="#336699",
            fillRule="evenodd",
            subpaths=[square(0, 1), square(0.25, 0.75), opened({"p": [0.5, 0.5]})],
        ),
    ),
    (
        "handles_outside_box",
        base(
            fill="#aa3355",
            subpaths=[closed({"p": [0, 1], "out": [-0.4, -0.6]}, {"p": [1, 1], "in": [1.4, -0.6]})],
        ),
    ),
    (
        "no_stroke_drops_heads",
        base(
            fill="#00aa00",
            stroke=False,
            headEnd="triangle",
            subpaths=[opened({"p": [0, 0]}, {"p": [1, 1]})],
        ),
    ),
    (
        "rotated",
        base(
            rotationDeg=35,
            fill="#ccddee",
            subpaths=[
                closed(
                    {"p": [0, 0], "out": [0.3, -0.2]},
                    {"p": [1, 0.2], "in": [0.8, -0.1]},
                    {"p": [0.7, 1]},
                    {"p": [0.1, 0.8], "in": [0.3, 1.1]},
                )
            ],
        ),
    ),
]


def _cubic_extrema(p0: float, p1: float, p2: float, p3: float) -> list[float]:
    a = -p0 + 3 * p1 - 3 * p2 + p3
    b = 2 * (p0 - 2 * p1 + p2)
    c = p1 - p0
    ts: list[float] = []
    if abs(a) < 1e-12:
        if abs(b) > 1e-12:
            ts.append(-c / b)
    else:
        disc = b * b - 4 * a * c
        if disc >= 0:
            sq = math.sqrt(disc)
            ts += [(-b + sq) / (2 * a), (-b - sq) / (2 * a)]
    return [t for t in ts if 0 < t < 1]


def _at(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    u = 1 - t
    return u**3 * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t**3 * p3


def extent(segs) -> list[float]:
    """曲线实际范围（不按控制点）；独立于前端 `segsExtent` 的另一份实现。"""
    xs: list[float] = []
    ys: list[float] = []
    cx = cy = 0.0
    for s in segs:
        if s[0] in ("M", "L"):
            cx, cy = s[1], s[2]
            xs.append(cx)
            ys.append(cy)
        elif s[0] == "C":
            _, ax, ay, bx, by, ex, ey = s
            for t in _cubic_extrema(cx, ax, bx, ex) + _cubic_extrema(cy, ay, by, ey):
                xs.append(_at(cx, ax, bx, ex, t))
                ys.append(_at(cy, ay, by, ey, t))
            cx, cy = ex, ey
            xs.append(cx)
            ys.append(cy)
    return [min(xs), min(ys), max(xs), max(ys)]


def head_json(hd) -> dict:
    if hd[0] == "dot":
        return {"kind": "dot", "cx": hd[1], "cy": hd[2], "r": hd[3]}
    return {"kind": hd[0], "points": [list(p) for p in hd[1]]}


def main(out_path: str) -> None:
    out = {
        "_doc": (
            "自由路径几何的共享向量（ADR 0185 §四）：前端 web/src/lib/pathGeom.golden.test.ts 断言 pathGeometry(doc) 与 "
            "toExportObjects([doc]) 产出它，后端 tests/test_path_object.py 断言 rendercore.geometry.path_segments(payload) "
            "产出同一份。框空间：原点框左上、y 向下、单位 pt；w/h = 框的 pt 尺寸、sw = max(stroke_pt, 0.05)。extent 是曲线"
            "实际范围（不按控制点）；page_points 是锚点在页面上的位置（mm、顶原点，含旋转）。期望值由 "
            "scripts/dev/gen_path_vectors.py 出（后端实现出数），两侧各自独立实现、对拍。改公式两侧一起改并重录。"
        ),
        "cases": [],
    }
    page_h = mm2pt(PAGE_H_MM)
    for name, doc in CASES:
        pl = payload(doc)
        w, h = mm2pt(doc["w"]), mm2pt(doc["h"])
        g = geometry.path_segments(pl, w, h)
        a, b, c, d, e, f = plan._object_transform(pl, page_h)
        page_pts = []
        for sp in geometry.read_path(pl)["subpaths"]:
            for n in sp["nodes"]:
                bx, by = n["p"][0] * w, n["p"][1] * h
                px, py = a * bx + c * by + e, b * bx + d * by + f
                page_pts.append([px * 25.4 / 72, (page_h - py) * 25.4 / 72])
        out["cases"].append(
            {
                "name": name,
                "doc": doc,
                "payload": pl,
                "w": w,
                "h": h,
                "sw": max(float(doc["strokePt"]), 0.05),
                "fill": [list(s) for s in g["fill"]],
                "stroke": [list(s) for s in g["stroke"]],
                "heads": [head_json(hd) for hd in g["heads"]],
                "head_half": g["head_half"],
                "extent": extent(g["fill"]) if g["fill"] else None,
                "page_h_mm": PAGE_H_MM,
                "page_points": page_pts,
            }
        )
    Path(out_path).write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"{len(out['cases'])} cases -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "tests" / "golden" / "path_vectors.json"))
