"""修改方案「逐组勾选后的 override 列表」的同源对（ADR 0206）：`changeplan.apply_groups` ↔ 前端 `lib/changePlanApply.ts` 的 `applyGroups`。

两侧各读 `tests/golden/change_plan_apply_vectors.json`（前端：`web/src/lib/changePlanApply.golden.test.ts`），不读对方源码。
向量里的 `expected` 由本侧实现生成：改规则时先改后端、用 `apply_groups` 重算每条 `expected` 写回向量文件，再同步前端实现，两侧都过才算数。
"""

import json
from pathlib import Path

from tavotto.engine import changeplan

GOLDEN = Path(__file__).parent / "golden" / "change_plan_apply_vectors.json"


def _cases():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_apply_groups_matches_golden_vectors():
    cases = _cases()
    assert len(cases) >= 10
    for c in cases:
        got = changeplan.apply_groups(c["base"], c["groups"], c["keys"])
        assert got == c["expected"], c["name"]


def test_golden_expected_is_canonical():
    """期望值本身必须是规范形：再规范化一遍不变（前端对拍时不必替它再洗一次）。"""
    for c in _cases():
        assert changeplan.patchspec.canonicalize(c["expected"]) == c["expected"], c["name"]
