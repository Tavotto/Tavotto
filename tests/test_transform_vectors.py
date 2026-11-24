"""变换面板（ADR 0185 M4 第二批）的后端一侧：镜像 / 倾斜的结果只用旧字段（路径的框、比例坐标锚点、旋转角），
导出端照常画。这里把共享向量 `tests/golden/transform_vectors.json` 里的前后两个路径都经后端的同一套几何
（`plan.clip_outline` = `geometry.path_segments` 的填充几何 + 对象变换）换到页面，核：

* 倾斜 = 页面上 x' = x + tan(θ)·(ref.y − y)，逐点；
* 镜像 = 页面上关于过 ref 的竖线 / 横线翻转，逐点。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.rendercore import plan

VECTORS = json.loads(
    (Path(__file__).resolve().parent / "golden" / "transform_vectors.json").read_text(
        encoding="utf-8"
    )
)
MM = 25.4 / 72.0
PAGE_H_MM = 200.0


def _export(o: dict) -> dict:
    return {
        "type": "path",
        "x_mm": o["x"],
        "y_mm": o["y"],
        "w_mm": o["w"],
        "h_mm": o["h"],
        "subpaths": o["subpaths"],
        **({"rotation_deg": o["rotationDeg"]} if o.get("rotationDeg") else {}),
    }


def _page_points(o: dict) -> list[tuple[str, list[tuple[float, float]]]]:
    segs, _ = plan.clip_outline(_export(o), PAGE_H_MM / MM)
    out = []
    for s in segs:
        pts = [(s[i] * MM, PAGE_H_MM - s[i + 1] * MM) for i in range(1, len(s), 2)]
        out.append((s[0], pts))
    return out


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_result_is_the_page_space_transform_of_the_original(case):
    before = _page_points(case["obj"])
    after = _page_points(case["expect"])
    assert [k for k, _ in before] == [k for k, _ in after]
    rx, ry = case["ref"]
    op = case["op"]
    for (_, pa), (_, pb) in zip(before, after):
        for (x, y), (x2, y2) in zip(pa, pb):
            if op["kind"] == "skew":
                want = (x + math.tan(math.radians(op["deg"])) * (ry - y), y)
            elif op["axis"] == "h":
                want = (2 * rx - x, y)
            else:
                want = (x, 2 * ry - y)
            assert x2 == pytest.approx(want[0], abs=1e-6)
            assert y2 == pytest.approx(want[1], abs=1e-6)


def test_vector_set_covers_both_operations_and_rotation():
    kinds = {c["op"]["kind"] for c in VECTORS["cases"]}
    assert kinds == {"skew", "reflect"}
    assert any(c["obj"].get("rotationDeg") for c in VECTORS["cases"] if c["op"]["kind"] == "skew")
    assert any(
        c["obj"].get("rotationDeg") for c in VECTORS["cases"] if c["op"]["kind"] == "reflect"
    )
