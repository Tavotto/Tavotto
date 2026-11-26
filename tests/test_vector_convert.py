"""「转为路径」的 Python 版（`engine/vectorgeom.to_path_object`）与前端 `lib/convertToPath.toPathObject` 逐数对拍（ADR 0212）。

样本由前端生成（`web/src/lib/convertToPath.golden.test.ts`，`UPDATE_GOLDEN=1` 重写），写在
`tests/golden/convert_to_path_vectors.json`：那边核「现在算出来的 = 金样」，这里核「Python 算出来的 = 金样」。
后端的比例坐标与包围盒保留 6 位小数，前端不取整，所以数值按 1e-6 比、键集合与非数值逐字比。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.engine import vectorgeom

GOLDEN = Path(__file__).parent / "golden" / "convert_to_path_vectors.json"
CASES = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def _close(a: object, b: object, where: str = "") -> None:
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or a is None:
        assert a == b, where
    elif isinstance(a, (int, float)):
        assert isinstance(b, (int, float)) and math.isclose(a, b, abs_tol=1e-6), (where, a, b)
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), where
        for k, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{where}[{k}]")
    elif isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (where, sorted(set(a) ^ set(b)))
        for k in a:
            _close(a[k], b[k], f"{where}.{k}")
    else:  # pragma: no cover
        raise AssertionError((where, a, b))


def test_golden_covers_every_kind():
    kinds = {c["input"].get("shape") or c["input"]["type"] for c in CASES}
    assert kinds >= {
        "rect",
        "ellipse",
        "triangle",
        "diamond",
        "polygon",
        "star",
        "brace",
        "arc",
        "line",
        "arrow",
    }


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_to_path_object_matches_frontend(case):
    got = vectorgeom.to_path_object(case["input"])
    assert got is not None
    _close(json.loads(json.dumps(got)), case["output"], case["name"])


def test_input_not_mutated():
    src = json.loads(json.dumps(CASES[0]["input"]))
    vectorgeom.to_path_object(src)
    assert src == CASES[0]["input"]


def test_only_shapes_and_arrows_convert():
    assert (
        vectorgeom.to_path_object({"id": "t", "type": "text", "x": 0, "y": 0, "w": 1, "h": 1})
        is None
    )
    path = next(c["output"] for c in CASES if c["name"] == "rect-plain")
    assert vectorgeom.as_path(path) is path


def test_pathfinder_operands():
    rect = next(c["input"] for c in CASES if c["name"] == "rect-plain")
    assert vectorgeom.is_pathfinder_operand(rect)
    assert not vectorgeom.is_pathfinder_operand({**rect, "locked": True})
    assert not vectorgeom.is_pathfinder_operand({**rect, "shape": "line"})
    arrow = next(c["input"] for c in CASES if c["name"] == "arrow-heads")
    assert not vectorgeom.is_pathfinder_operand(arrow)
