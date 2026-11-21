"""评测题库 × 失败提示（ADR 0197 第 3 层·离线）：未修前还失败的每道题，都要拿到一条点名正确工具的提示。

不叫模型、不起 sidecar。量的是**实测**结局：`results/` 里最近一轮 `baseline.jsonl` 的 `before`（真 sidecar 跑出来的
未修前结局，`harness.py run baseline` / `probe` 产出）。题库 `hint: {code, trace?, tool}` 必须与之对上：
实测还失败的题都要写，`code` 就是实测的结局码；实测已经出图（产品自己修好了、不用 AI）的题不写。
产品换了结局码，下一轮 baseline 一跑就和题库对不上；提示表漏了某个形状，这里直接红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tavotto.engine.fixtools import REGISTRY, hints

EVAL = Path(__file__).resolve().parents[1] / "scripts" / "eval" / "fix_compat"
CORPUS = json.loads((EVAL / "corpus.json").read_text(encoding="utf-8"))
CASES = {cid: case["hint"] for cid, case in CORPUS.items() if "hint" in case}


def _latest_baseline() -> tuple[str, dict[str, dict]]:
    """最近一轮（目录名以日期开头，按名排序取最后）的 baseline：题 → 未修前的实测摘要。"""
    runs = sorted(p for p in EVAL.glob("results/20*/baseline.jsonl"))
    assert runs, "results/ 下没有带日期的 baseline.jsonl"
    rows = [json.loads(line) for line in runs[-1].read_text(encoding="utf-8").splitlines() if line]
    return runs[-1].parent.name, {r["id"]: r.get("before") or {} for r in rows}


def test_expectations_match_the_latest_measured_outcomes():
    run, before = _latest_baseline()
    assert set(before) == set(CORPUS), f"{run}：baseline 的题与题库不一致"
    still_failing = {cid for cid, b in before.items() if b.get("phase") != "completed"}
    assert set(CASES) == still_failing, (
        f"{run}：实测还失败的题 {sorted(still_failing)}，题库写了期望的题 {sorted(CASES)}"
    )
    for cid in CASES:
        assert CASES[cid]["code"] == before[cid].get("outcome_code"), (cid, run, before[cid])


@pytest.mark.parametrize("cid", sorted(CASES))
def test_the_failure_before_the_fix_gets_a_hint_naming_the_fixing_tool(cid):
    spec = CASES[cid]
    assert spec["tool"] in REGISTRY, spec["tool"]
    shape = hints.shape_of(spec["code"], spec.get("trace", ""))
    got = hints.hint_for(shape, lambda _name: True)
    assert got is not None, f"{cid}: 形状 {shape!r} 没有提示"
    assert re.search(rf"\b{re.escape(spec['tool'])}\b", got[1]), (cid, got)
