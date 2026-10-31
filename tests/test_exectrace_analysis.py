"""Read-only Execution Trace analysis (scripts/ci/exectrace_analysis.py, ADR 0151 PR4 4.4-4.5).

Traces are real `ExecTrace` records written by a real `TraceStore`; the judged subject is the analysis output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import exectrace_analysis as ea  # noqa: E402
from tavotto.engine import exectrace as xt  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.w, self.m = 1_800_000_000.0, 100.0

    def wall(self):
        return self.w

    def mono(self):
        return self.m

    def tick(self, ms):
        self.m += ms / 1000.0
        self.w += ms / 1000.0


@pytest.fixture(autouse=True)
def _vocab(monkeypatch):
    monkeypatch.delenv(xt.ENV_DISABLE, raising=False)
    xt.register_vocabulary("tool", ["try_run"])
    xt.register_vocabulary("agent", ["builtin"])


@pytest.fixture
def root(tmp_path):
    return tmp_path / "exec-trace"


def make(
    root,
    *,
    kind="ai_task",
    ref="task1",
    status=xt.STATUS_OK,
    fail_code=None,
    attempts=1,
    tools=1,
    tokens=None,
    run_ms=500,
    execution=None,
    finish=True,
):
    clock = Clock()
    t = xt.ExecTrace(kind, clock=clock, store=xt.TraceStore(root), task_ref=ref)
    for n in range(1, attempts + 1):
        attrs = {"agent": "builtin"}
        if execution:
            attrs["execution"] = execution
        a = t.start_span("agent_run", "agent", attempt=n, attributes=attrs)
        for _ in range(tools):
            s = t.start_span("tool_call", "tool", parent=a, attributes={"tool": "try_run"})
            clock.tick(10)
            t.end_span(s)
        m = t.start_span("model_request", "model", parent=a)
        clock.tick(run_ms)
        usage = (
            {"source": "reported", "input_tokens": tokens, "output_tokens": 1} if tokens else None
        )
        t.end_span(m, usage=usage)
        last = n == attempts
        bad = last and fail_code
        t.end_span(
            a, xt.STATUS_FAILED if bad else xt.STATUS_OK, error_code=fail_code if bad else None
        )
    if finish:
        t.finish(status, error_code=fail_code)
    return t


def rows_of(root, results=None):
    records, _ = ea.load_traces(root)
    return ea.case_rows(records, results)


def test_row_fields_come_from_the_trace(root):
    t = make(
        root,
        kind="fix_session",
        ref="r1",
        status=xt.STATUS_FAILED,
        fail_code="timeout_x",
        attempts=2,
        tools=3,
        tokens=100,
        execution="safe",
    )
    (row,) = rows_of(root)
    assert row["trace_id"] == t.trace_id
    assert row["operation_kind"] == "fix_session"
    assert row["environment_kind"] == "safe"
    assert row["failure_stage"] == "agent_run"
    assert row["error_class"] == "timeout_x" and row["error_class_source"] == "trace"
    assert row["attempts"] == 2 and row["agent_runs"] == 2
    assert row["tool_calls"] == 6 and row["model_requests"] == 2
    assert row["reported_tokens"] == 202
    assert row["completeness"] == "complete"
    assert row["duration_ms"] > 0
    assert row["stage_ms"]["model_request"] >= 1000


def test_verdict_and_verification_are_null_without_guardian_input(root):
    make(root, status=xt.STATUS_OK)
    make(root, ref="t2", status=xt.STATUS_FAILED, fail_code="boom")
    for row in rows_of(root):
        assert row["verdict"] is None and row["verdict_pass"] is None
        assert row["verification"] is None and row["case_id"] is None


def test_verdict_is_not_derived_from_trace_status(root):
    # trace says ok, guardian says failed -> the verdict is the guardian's; and vice versa
    make(root, ref="okref", status=xt.STATUS_OK)
    make(root, ref="badref", status=xt.STATUS_FAILED, fail_code="boom")
    results = {
        "cases": [
            {
                "id": "c-ok-trace",
                "status": "failed",
                "task_refs": ["okref"],
                "failure_category": {"category": "figure_capture"},
            },
            {"id": "c-bad-trace", "status": "passed", "task_refs": ["badref"]},
        ]
    }
    by = {r["case_id"]: r for r in rows_of(root, results)}
    assert by["c-ok-trace"]["verdict"] == "failed" and by["c-ok-trace"]["verdict_pass"] is False
    assert by["c-bad-trace"]["verdict"] == "passed" and by["c-bad-trace"]["verdict_pass"] is True
    # guardian taxonomy wins over the trace code, and says where it came from
    assert by["c-ok-trace"]["error_class"] == "figure_capture"
    assert by["c-ok-trace"]["error_class_source"] == "guardian"
    assert by["c-bad-trace"]["error_class"] == "boom"


def test_bench_rows_link_through_session_ref_and_supply_verification(root):
    t = make(root, ref="sess1")
    results = {
        "rows": [
            {
                "id": "s1",
                "variant": "ref",
                "outcome": "completed",
                "figure_verdict": "verified",
                "goal": "verified",
                "trace_ids": [t.trace_id],
            }
        ]
    }
    (row,) = rows_of(root, results)
    assert row["case_id"] == "s1/ref" and row["verdict_pass"] is True
    assert row["verification"] == "verified"


def test_insufficient_samples_are_null_never_a_number(root):
    for i in range(ea.MIN_SAMPLES - 1):
        make(root, ref=f"t{i}")
    agg = ea.aggregate(rows_of(root))
    d = agg["task_duration_ms"]
    assert d["n"] == ea.MIN_SAMPLES - 1 and d["insufficient_samples"] is True
    assert d["mean"] is None and d["p50"] is None and d["p95"] is None
    assert agg["first_pass_rate"]["rate"] is None
    make(root, ref="enough")
    d = ea.aggregate(rows_of(root))["task_duration_ms"]
    assert d["insufficient_samples"] is False and d["n"] == ea.MIN_SAMPLES
    assert d["p50"] is not None and d["p95"] >= d["p50"]


def test_rates_and_grouping_by_stable_class(root):
    cases = []
    for i in range(6):
        ref = f"f{i}"
        code = "dep_missing" if i < 4 else "other_code"
        make(root, ref=ref, status=xt.STATUS_FAILED, fail_code=code)
        cases.append({"id": f"c{i}", "status": "failed", "task_refs": [ref]})
    for i in range(2):
        make(root, ref=f"p{i}", attempts=2 if i else 1)
        cases.append({"id": f"p{i}", "status": "passed", "task_refs": [f"p{i}"]})
    agg = ea.aggregate(rows_of(root, {"cases": cases}))
    assert agg["error_class_frequencies"] == {"dep_missing": 4, "other_code": 2}
    rf = agg["repeated_failure_ratio"]
    assert (rf["k"], rf["n"]) == (6, 6) and "root cause" in rf["note"]
    assert agg["final_pass_rate"]["k"] == 2 and agg["final_pass_rate"]["n"] == 8
    assert (
        agg["first_pass_rate"]["k"] == 1
    )  # the passed task that needed attempt 2 is not first-pass
    assert agg["missing_verification_ratio"]["k"] == 8


def test_auto_repair_rate_only_counts_fix_session_traces(root):
    cases = []
    for i in range(5):
        make(root, kind="fix_session", ref=f"x{i}")
        cases.append(
            {"id": f"x{i}", "status": "passed" if i < 3 else "failed", "task_refs": [f"x{i}"]}
        )
    for i in range(5):
        make(root, kind="ai_task", ref=f"y{i}")
        cases.append({"id": f"y{i}", "status": "failed", "task_refs": [f"y{i}"]})
    ar = ea.aggregate(rows_of(root, {"cases": cases}))["auto_repair_success_rate"]
    assert (ar["k"], ar["n"], ar["rate"]) == (3, 5, 0.6)


def test_corrupt_and_foreign_files_are_skipped_and_counted(root):
    make(root, ref="good")
    root.mkdir(exist_ok=True)
    (root / "garbage.json").write_text("{not json", encoding="utf-8")
    (root / "wrongshape.json").write_text('{"trace": {"no": "id"}}', encoding="utf-8")
    records, corrupt = ea.load_traces(root)
    assert len(records) == 1 and corrupt == 2


def test_open_record_is_read_and_flagged_incomplete(root):
    make(root, ref="live", finish=False)
    files = [p.name for p in root.iterdir()]
    assert any(n.endswith(".open.json") for n in files)
    (row,) = rows_of(root)
    assert row["completeness"] == "incomplete"


def test_baseline_comparison_reports_deltas_and_null_when_thin(root, tmp_path):
    cur, base = tmp_path / "cur", tmp_path / "base"
    for i in range(6):
        make(cur, ref=f"c{i}", run_ms=900)
        make(base, ref=f"b{i}", run_ms=300)
    for i in range(2):
        make(base, kind="fix_session", ref=f"bf{i}")
    rep_base = ea.build_report(base)
    rep = ea.build_report(cur, baseline=rep_base)
    assert rep["delta"]["task_duration_ms.p50"] > 0
    assert rep["baseline"]["rows"] == 8
    assert rep["delta"]["final_pass_rate.rate"] is None  # no verdicts anywhere -> no delta


def test_cli_writes_schema_and_never_touches_the_trace_dir(root, tmp_path):
    make(root, ref="cli")
    before = sorted((p.name, p.stat().st_mtime_ns) for p in root.iterdir())
    out = tmp_path / "out.json"
    assert ea.main(["--traces", str(root), "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["schema"] == ea.SCHEMA == "tavotto.exectrace-analysis/1"
    assert rep["guardian_results_supplied"] is False and rep["traces_read"] == 1
    assert sorted((p.name, p.stat().st_mtime_ns) for p in root.iterdir()) == before
    assert (
        ea.main(["--traces", str(root), "--out", str(out), "--baseline", str(tmp_path / "no")]) == 2
    )


def test_a_bench_row_passes_only_by_taf_benchs_own_final_ok_including_the_goal():
    """Codex #1457：通过与否就是 `taf_bench.final_ok`（outcome + figure_verdict + goal），这里不另写一份。"""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "tb", Path(__file__).resolve().parent.parent / "scripts" / "dev" / "taf_bench.py"
    )
    tb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tb)
    base = {"id": "FB-1", "variant": "ref", "outcome": "completed", "figure_verdict": "verified"}
    for goal in ("verified", "not_applicable", "failed", "unknown", None):
        row = dict(base, goal=goal)
        entry = ea._case_entries({"rows": [row]})[0]
        assert entry["passed"] is tb.final_ok(row), goal
    assert ea._case_entries({"rows": [dict(base, goal="failed")]})[0]["passed"] is False


def test_bench_rows_link_through_their_recorded_task_refs(root):
    """taf_bench 每行记的 `task_refs: [会话 id]` 就是 Trace 的 `task_ref`；无需 trace_ids。"""
    t = make(root, ref="sess9")
    results = {
        "rows": [
            {
                "id": "s9",
                "variant": "ref",
                "outcome": "completed",
                "figure_verdict": "verified",
                "goal": "verified",
                "task_refs": ["sess9"],
            }
        ]
    }
    (row,) = rows_of(root, results)
    assert row["case_id"] == "s9/ref" and row["verdict_pass"] is True
    assert t is not None
