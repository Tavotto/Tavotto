"""Harness Lab 基准跑分（ADR 0138 Phase 6b，`scripts/dev/taf_bench.py`）：回放跑通真链路、指标口径、holdout 防泄漏。

跑分本身走真 app + 真桥 + 真 safe worker（只有「理解指令」换成回放），所以这里既是基准工具的自证，也是改图任务端到端
在 FigureBench 上的一次回归：参考解必须 completed + 目标 / 保持全 verified，反例必须被抓住。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "taf_bench", ROOT / "scripts" / "dev" / "taf_bench.py"
)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_replay_runs_the_real_chain_and_negatives_are_caught(tmp_path):
    dev = {s["id"]: s for s in bench.load_scenarios("dev") if bench.benchable(s)}
    picked = [dev["FB-S02"], dev["FB-S03"]]
    report = bench.run(picked, tmp_path / "projects", negatives=True)
    rows = {(r["id"], r["variant"]): r for r in report["rows"]}
    for sid in ("FB-S02", "FB-S03"):
        ref = rows[(sid, "ref")]
        assert ref["outcome"] == "completed" and ref["figure_verdict"] == "verified", ref
        assert ref["goal"] == "verified" and ref["preserve"] == "verified", ref
        assert ref["unintended"] is False and ref["rounds"] == 1, ref
    assert rows[("FB-S02", "n.only_one")]["goal"] == "failed"
    assert rows[("FB-S03", "n.collateral")]["preserve"] == "failed"
    m = report["metrics"]
    assert m["scenarios_run"] == 4 and m["not_run"] == []
    assert m["first_attempt_verified_rate"] == 1.0 and m["negatives_caught_rate"] == 1.0
    assert m["unintended_modification_rate"] == 0.0
    env = report["environment"]
    assert env["pack"] == {"id": "figure_edit", "version": 1} and env["executor"] == "replay"
    json.dumps(report)  # 报告可序列化


def test_metrics_keep_not_run_out_of_the_denominator():
    row = dict.fromkeys(bench.ROW_FIELDS)
    rows = [
        {
            **row,
            "id": "A",
            "variant": "ref",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 1,
            "wall_s": 1.0,
        },
        {
            **row,
            "id": "B",
            "variant": "ref",
            "outcome": "failed",
            "figure_verdict": "unknown",
            "rounds": 1,
            "goal": "unknown",
            "preserve": "unknown",
            "unintended": True,
            "wall_s": 3.0,
        },
        {**row, "id": "C", "variant": "ref", "outcome": None, "unintended": False},  # 没跑起来
        {  # 修正一轮后才成功：不计首发，计最终
            **row,
            "id": "E",
            "variant": "ref",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 2,
            "wall_s": 2.0,
        },
        {
            **row,
            "id": "A",
            "variant": "n.x",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 1,
            "wall_s": 2.0,
        },
    ]
    m = bench._metrics(rows)
    assert m["scenarios_run"] == 4 and m["not_run"] == ["C/ref"]
    # 参考解行 A(首发成功) / B(失败) / E(两轮成功)
    assert m["first_attempt_verified_rate"] == round(1 / 3, 4), "rounds=2 的成功不计首发"
    assert m["final_verified_completion_rate"] == round(2 / 3, 4), "但计最终通过"
    assert m["scientific_preservation_rate"] == round(2 / 3, 4), "unknown 不算保持"
    assert m["unintended_modification_rate"] == round(1 / 3, 4)
    kept_failed = {**rows[0], "id": "F", "preserve": "failed", "unintended": False}
    assert bench._metrics([kept_failed])["unintended_modification_rate"] == 1.0, (
        "保持断言 failed 也算非预期修改（§三口径）"
    )
    assert m["negatives_caught_rate"] == 0.0, "反例没被抓住就是 0，不是跳过"
    assert m["taf_overclaim_rate"] is None, "没有被基准抓住的反例：说多了的比例无从谈起"
    goalless = {**rows[0], "id": "D", "goal": "not_applicable"}
    assert bench._metrics([goalless])["first_attempt_verified_rate"] == 1.0, (
        "没有目标断言 = 目标满足"
    )
    assert bench._metrics([goalless])["final_verified_completion_rate"] == 1.0
    caught = {**rows[-1], "goal": "failed"}
    assert bench._metrics([caught])["taf_overclaim_rate"] == 1.0, (
        "TAF 说 completed、基准判反例违约 = 说多了"
    )


def test_holdout_aggregate_has_no_per_scenario_ids():
    row = dict.fromkeys(bench.ROW_FIELDS)
    rows = [
        {**row, "id": "FB-H99-SECRET", "variant": "ref", "outcome": None, "unintended": False},
        {
            **row,
            "id": "FB-H98-SECRET",
            "variant": "ref",
            "outcome": "completed",
            "figure_verdict": "verified",
            "goal": "verified",
            "preserve": "verified",
            "unintended": False,
            "rounds": 1,
            "wall_s": 1.0,
        },
    ]
    m = bench._metrics(rows, aggregate_only=True)
    text = json.dumps(m, ensure_ascii=False)
    assert "FB-H99-SECRET" not in text and "not_run" not in m
    assert m["not_run_count"] == 1


@pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器")
def test_same_work_dir_can_be_rerun(tmp_path):
    s02 = next(s for s in bench.load_scenarios("dev") if s["id"] == "FB-S02")
    work = tmp_path / "projects"
    for _ in range(2):
        report = bench.run([s02], work)
        assert report["metrics"]["not_run"] == [], report["rows"]
        assert report["rows"][0]["outcome"] == "completed", report["rows"]


def test_only_replayable_dev_scenarios_are_benched_by_default():
    dev = bench.load_scenarios("dev")
    picked = [s for s in dev if bench.benchable(s)]
    assert picked and all(s["split"] == "dev" for s in picked)
    assert not any(s.get("needs_real_model") or s["task_type"] != "figure_edit" for s in picked)
    holdout_ids = {s["id"] for s in bench.load_scenarios("holdout")}
    assert not holdout_ids & {s["id"] for s in picked}


def test_leak_guard_flags_holdout_content_and_passes_clean_text():
    holdout = bench.load_scenarios("holdout")
    some = holdout[0]
    with pytest.raises(bench.LeakError):
        bench.assert_no_leak(["一份候选 Pack", f"提到了 {some['id']}"], holdout)
    with pytest.raises(bench.LeakError):
        bench.assert_no_leak([f"……{some['instruction']}……"], holdout)
    bench.assert_no_leak(
        [
            json.dumps(
                __import__("tavotto.engine.ai_prompt_pack").engine.ai_prompt_pack.V1,
                ensure_ascii=False,
            )
        ],
        holdout,
    )
    markers = bench.holdout_markers(holdout)
    assert all(len(m) >= 4 for m in markers) and some["id"] in markers
    # 每一类都要单独在场：指令原文、断言 id（光靠「碰巧也命中了别的标记」不算守住）
    assert all(s["instruction"] in markers for s in holdout)
    assert all(
        a["id"] in markers
        for s in holdout
        for a in [*s.get("goal", []), *s.get("preserve", [])]
        if len(a["id"]) >= 4
    )
