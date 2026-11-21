"""评测题库 × 失败提示（ADR 0197 第 3 层·离线）：题库里每道要 AI 动手的题，未修前的失败都要拿到一条点名正确工具的提示。

不叫模型、不起 sidecar：每题的 `hint` 写着那道题未修前的结局码（`harness.py probe` 实测，必要时带 traceback 片段）
和修好它要用的工具（评测里修好它的轨迹实际用的那一个）。产品换了结局码、或者提示表漏了某个形状，这里先红——
不用等下一轮花额度的评测才发现弱模型在这道题上没人指路。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tavotto.engine.fixtools import REGISTRY, hints

CORPUS = json.loads(
    (
        Path(__file__).resolve().parents[1] / "scripts" / "eval" / "fix_compat" / "corpus.json"
    ).read_text(encoding="utf-8")
)
CASES = {cid: case["hint"] for cid, case in CORPUS.items() if "hint" in case}


def test_every_case_that_needs_the_ai_declares_its_hint():
    """读回家族（未修前就出图、不叫模型）以外的题都写了期望：新加的题不会悄悄没人看护。"""
    no_ai = {cid for cid, case in CORPUS.items() if case.get("family") == "native-readback"}
    assert set(CORPUS) - no_ai == set(CASES)


@pytest.mark.parametrize("cid", sorted(CASES))
def test_the_failure_before_the_fix_gets_a_hint_naming_the_fixing_tool(cid):
    spec = CASES[cid]
    assert spec["tool"] in REGISTRY, spec["tool"]
    shape = hints.shape_of(spec["code"], spec.get("trace", ""))
    got = hints.hint_for(shape, lambda _name: True)
    assert got is not None, f"{cid}: 形状 {shape!r} 没有提示"
    assert re.search(rf"\b{re.escape(spec['tool'])}\b", got[1]), (cid, got)
