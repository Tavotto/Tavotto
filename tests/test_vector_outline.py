"""轮廓化描边的 Python 版（`engine/vectorgeom.outline_stroke`：虚线、端型、内外对齐，ADR 0212）与前端
`pathfinder.outlineStrokes` + `lib/outlineStroke` 逐数对拍。

样本由前端生成（`web/src/lib/outlineStroke.golden.test.ts`，真 WASM 内核），每例的 `doc` 是原对象与结果的文档形态；
后端用 wasmtime 跑**同一份** vecops.wasm，所以内核那一段位级一致，差别只来自 Python 侧取 6 位小数——路径按 1e-5 比；
形状先「转为路径」时那一步已取过一次 6 位小数，再经虚线切段放大到 ~2e-5 mm，按 1e-4 比（0.1 µm，远小于导出精度 0.01 pt）。
形状 / 箭头先经 `as_path`（「转为路径」）再轮廓化，与前端同一条路。新长出来的轮廓 id 前端是随机的，金样里写成 `extra`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import vecops, vectorgeom
from tests.test_vector_convert import _close

pytestmark = pytest.mark.skipif(
    not vecops.available(), reason="vecops 内核不可用（not_run，不是绿）"
)

GOLDEN = Path(__file__).parent / "golden" / "outline_stroke_vectors.json"
CASES = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_every_case_has_a_doc_form():
    assert CASES and all("doc" in c for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_outline_stroke_matches_frontend(case):
    orig = case["doc"]["original"]
    p = vectorgeom.as_path(orig)
    assert p is not None
    res = vectorgeom.outline_stroke(vecops, p, "extra")
    assert res is not None, case["name"]
    replace, extra = res
    got = [replace, *([extra] if extra else [])]
    tol = 1e-5 if orig["type"] == "path" else 1e-4
    _close(json.loads(json.dumps(got)), case["doc"]["outlined"], case["name"], tol=tol)
