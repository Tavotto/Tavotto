"""阵列（ADR 0185 M6）的后端一侧：结果只用旧字段（框、旋转角），导出端照常画。这里在共享向量 `tests/golden/array_vectors.json` 上核
几何不变量——网格各份等距、环形各份（单个对象时）的中心在同一个圆上、相邻两份的角度差等于 360°/份数。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

VECTORS = json.loads(
    (Path(__file__).resolve().parent / "golden" / "array_vectors.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_array_invariants(case):
    spec, objs = case["spec"], case["objs"]
    o = objs[0]
    centres = [(o["x"] + o["w"] / 2, o["y"] + o["h"] / 2)] + [
        (c[0]["x"] + o["w"] / 2, c[0]["y"] + o["h"] / 2) for c in case["expect"]
    ]
    if spec["kind"] == "grid":
        xs = sorted({round(c[0], 9) for c in centres})
        ys = sorted({round(c[1], 9) for c in centres})
        assert len(xs) == spec["cols"] and len(ys) == spec["rows"]
        assert len({round(b - a, 9) for a, b in zip(xs, xs[1:])}) <= 1
        return
    if len(objs) > 1:
        assert len(case["expect"]) == spec["count"] - 1
        return
    centre = (centres[0][0], centres[0][1] + spec["radius"])
    for c in centres:
        assert math.dist(c, centre) == pytest.approx(spec["radius"], abs=1e-9)
    if spec["rotate"]:
        degs = [0.0] + [c[0]["rotationDeg"] for c in case["expect"]]
        step = 360 / spec["count"]
        for a, b in zip(degs, degs[1:]):
            assert ((b - a) % 360) == pytest.approx(step, abs=1e-9)
