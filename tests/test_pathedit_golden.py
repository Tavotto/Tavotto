"""路径锚点编辑与前端的逐数对拍（ADR 0212 第 4 期）。

`tests/golden/path_edit_vectors.json` 由前端真的 store 动作录出（`web/src/lib/pathEdit.golden.test.ts`，`UPDATE_GOLDEN=1` 重录）：
每条样本 = 输入对象表（文档形态）+ 一条补丁（Python 写法）+ 前端改后的对象表。这里把同一条补丁交给 `vectoredit.apply_ops`，
逐对象逐键比：键集合严格相等，数值容差 1e-6（Python 6 位小数取整、前端不取整），其余相等。新长出来的 id 按在数组里出现的先后归一成 NEW1…。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.engine import vectoredit as ve

GOLDEN = Path(__file__).parent / "golden" / "path_edit_vectors.json"
CASES = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
TOL = 1e-6


def _same(a, b, where: str) -> None:
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        assert a == b, where
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        assert math.isclose(a, b, abs_tol=TOL), f"{where}: {a} != {b}"
    elif isinstance(a, dict) and isinstance(b, dict):
        assert sorted(a) == sorted(b), f"{where}: keys {sorted(a)} != {sorted(b)}"
        for k in a:
            _same(a[k], b[k], f"{where}.{k}")
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        assert len(a) == len(b), f"{where}: len {len(a)} != {len(b)}"
        for n, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{where}[{n}]")
    else:
        assert a == b, f"{where}: {a!r} != {b!r}"


def _normalize(before: list[dict], after: list[dict]) -> list[dict]:
    known = {o["id"] for o in before}
    names: dict[str, str] = {}
    out = []
    for o in after:
        if o["id"] in known:
            out.append(o)
            continue
        names.setdefault(o["id"], f"NEW{len(names) + 1}")
        out.append({**o, "id": names[o["id"]]})
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_matches_the_frontend(case):
    got = ve.apply_ops(case["objects"], [case["op"]])["objects"]
    got = json.loads(json.dumps(_normalize(case["objects"], got)))
    _same(got, case["expect"], case["name"])


def test_the_golden_covers_every_anchor_op():
    ops = {c["op"]["op"] for c in CASES}
    assert ops == set(ve._ANCHOR_OPS)  # noqa: SLF001 — 新加一个锚点操作必须配上前端样本
