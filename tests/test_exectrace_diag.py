"""执行追踪的诊断面（ADR 0151 PR4）：故障归因、出门投影、诊断包 `exec-traces.json`（schema 9）、用户导出单份诊断。

判据的主语：`exectrace_diag` 对**真实 ExecTrace 快照**给出的结论；诊断包 / 导出端点经 Flask 测试客户端拿到的**字节**。
"""

# ruff: noqa: F811 — 夹具（client）从别的测试文件导入复用，参数名与导入名相同

from __future__ import annotations

import io
import json
import zipfile

from tavotto.engine import exectrace as xt, exectrace_diag as diag
from tests.test_preparation_api import _open, _project, client  # noqa: F401

CANARY = [
    "sk-ant-CANARYKEY0123456789",
    "/Users/alice/raw_spectra.csv",
    "make the bars thicker CANARY",
]


def _trace(kind="ai_task", **kw):
    xt.register_vocabulary("tool", ["install_packages", "try_run", "read_file"])
    return xt.ExecTrace(kind, **kw)


# ================================================================ 归因


def test_origin_cascade_and_slowest_are_three_different_answers():
    t = _trace()
    run = t.start_span("agent_run", "agent", attributes={"agent": "builtin"})
    slow = t.start_span("model_request", "model", parent=run)
    t.end_span(slow, reported_duration_ms=0)
    tool = t.start_span("tool_call", "tool", parent=run, attributes={"tool": "try_run"})
    t.end_span(tool, "failed", error_code="script_crashed")
    val = t.start_span("validate", "verify")
    t.end_span(val, "failed", error_code="precommit_not_verified")
    t.end_span(run)
    t.finish("failed", error_code="precommit_not_verified")
    a = diag.attribution(t.snapshot())
    assert a["origin"] == {"stage": "tool_call", "error_code": "script_crashed", "attempt": 1}
    assert a["cascade"] == [{"stage": "validate", "error_code": "precommit_not_verified"}]
    assert a["final_status"] == "failed" and a["final_error_code"] == "precommit_not_verified"
    assert a["slowest"]["is_cause"] is False
    assert a["verified_by_execution"] == "unknown"  # 没有验证 / 运行区间：说不出就是 unknown


def test_a_failed_task_without_a_recorded_failure_is_unknown_not_a_guess():
    t = _trace()
    t.end_span(t.start_span("plan", "phase"))
    t.finish("failed", error_code="executor_error")
    a = diag.attribution(t.snapshot())
    assert a["origin"] == {"stage": "unknown", "error_code": "executor_error", "attempt": None}
    assert a["cascade"] == []


def test_repair_refusal_budget_timeout_repeats_and_real_verification_are_all_evidence_based():
    t = _trace("fix_session")
    run = t.start_span("agent_run", "agent")
    for _ in range(3):
        t.end_span(
            t.start_span("tool_call", "tool", parent=run, attributes={"tool": "install_packages"})
        )
    tool = t.start_span("tool_call", "tool", parent=run, attributes={"tool": "install_packages"})
    wait = t.start_span("user_wait", "wait", parent=tool)
    t.end_span(wait, attributes={"approved_by": "denied"})
    t.end_span(tool, "failed", error_code="not_approved")
    m = t.start_span("model_request", "model", parent=run, attempt=2)
    t.end_span(m, "timeout", error_code="model_timeout")
    t.end_span(run, "failed", error_code="budget_exhausted")
    t.finish("failed", error_code="not_fixed")
    a = diag.attribution(t.snapshot())
    assert a["repair_attempted"] is True and a["user_refused"] is True
    assert a["budget_exhausted"] is True and a["timed_out"] is True
    assert a["repeated_tool_calls"] == {"install_packages": 4}
    assert a["retried_spans"] == 1
    # 一次真实执行（准备的 script_run）说了算
    p = _trace("preparation")
    p.end_span(p.start_span("script_run", "process"), "failed", error_code="script_crashed")
    p.finish("failed", error_code="script_crashed")
    assert diag.attribution(p.snapshot())["verified_by_execution"] == "failed"


def test_interrupted_and_truncated_records_are_said_out_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(xt, "MAX_SPANS", 3)
    t = _trace()
    for _ in range(4):
        t.start_span("tool_call", "tool")
    t.finish("interrupted", error_code="interrupted")
    doc = diag.document(t.snapshot())
    assert doc["note"] == "interrupted"
    assert doc["attribution"]["interrupted"] is True and doc["attribution"]["complete"] is False
    assert doc["trace"]["truncated"] is True


# ================================================================ 出门投影


def test_the_export_projection_revalidates_and_drops_local_refs_and_unknown_fields():
    t = _trace(task_ref="sessAbc123", session_ref="rid-12345678", scope="pjX")
    s = t.start_span("tool_call", "tool", attributes={"tool": "read_file"})
    t.end_span(s)
    t.event("tool_requested")
    t.event("approval_granted", span_id=s)
    t.finish("ok")
    snap = t.snapshot()
    # 本地快照被篡改（盘上被别的东西写过）：出门投影照样按形状挡
    snap["spans"][1]["error_code"] = CANARY[0]
    snap["spans"][1]["attributes"]["tool"] = CANARY[1]
    snap["spans"][1]["name"] = CANARY[2]
    snap["spans"][1]["extra"] = CANARY[0]
    snap["prompt"] = CANARY[2]
    proj = diag.export_projection(snap)
    blob = json.dumps(proj, ensure_ascii=False)
    for c in CANARY + ["sessAbc123", "rid-12345678", "pjX", "alice", "CANARY"]:
        assert c not in blob
    assert proj["spans"][1]["name"] is None and "extra" not in proj["spans"][1]
    assert [e["name"] for e in proj["events"]] == ["approval_granted"]  # 冗余事件不出门
    assert diag.export_projection({"trace_id": "../x", "spans": []}) is None


# ================================================================ 诊断包 / 导出端点


def _bundle(client, pj) -> dict:
    res = client.get("/api/diagnostics/bundle", query_string={"pj": pj})
    assert res.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(res.data))
    return {n: z.read(n) for n in z.namelist()}


def test_the_bundle_carries_this_projects_exec_traces_failures_first_and_nothing_else(
    client, tmp_path
):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    pa, pb = _open(client, a), _open(client, b)
    ok = xt.begin("preparation", task_ref="planOK", scope=xt.scope_for(pa))
    ok.end_span(ok.start_span("script_run", "process"))
    ok.finish("ok")
    bad = xt.begin("ai_task", task_ref="sessBad", scope=xt.scope_for(str(a.resolve())))
    run = bad.start_span("agent_run", "agent", attributes={"agent": "codex", "opaque": True})
    bad.end_span(run, "failed", error_code="agent_exit_nonzero")
    bad.finish("failed", error_code="agent_exit_nonzero")
    other = xt.begin("ai_task", task_ref="sessOther", scope=xt.scope_for(str(b.resolve())))
    other.finish("failed", error_code="agent_exit_nonzero")
    files = _bundle(client, pa)
    manifest = json.loads(files["manifest.json"])
    assert manifest["schema_version"] == 9
    section = json.loads(files["exec-traces.json"])
    ids = [d["trace"]["trace_id"] for d in section["traces"]]
    assert bad.trace_id in ids and ok.trace_id in ids and other.trace_id not in ids
    assert ids.index(bad.trace_id) < ids.index(ok.trace_id)  # 失败在前
    first = section["traces"][ids.index(bad.trace_id)]
    assert first["attribution"]["origin"]["stage"] == "agent_run"
    text = files["exec-traces.json"].decode()
    assert str(a) not in text and "sessBad" not in text and "planOK" not in text
    assert b"exec-traces.json" in files["README.txt"]
    # 别的项目的包里没有 A 的
    assert bad.trace_id not in files_text(_bundle(client, pb))


def files_text(files: dict) -> str:
    return "".join(v.decode("utf-8", "replace") for v in files.values())


def test_no_traces_means_no_file_and_disabled_tracing_means_no_file(client, tmp_path, monkeypatch):
    pj = _open(client, _project(tmp_path, "p"))
    assert "exec-traces.json" not in _bundle(client, pj)
    xt.begin("export", task_ref="jobX", scope=xt.scope_for(pj)).finish("ok")
    monkeypatch.setenv(xt.ENV_DISABLE, "1")
    assert "exec-traces.json" not in _bundle(client, pj)


def test_the_user_can_export_one_trace_diagnostic_and_only_from_its_own_project(client, tmp_path):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    pa, pb = _open(client, a), _open(client, b)
    t = xt.begin("ai_task", task_ref="sessExp1", scope=xt.scope_for(str(a.resolve())))
    t.finish("cancelled")
    res = client.get(
        "/api/exec-trace/diagnostic", query_string={"kind": "ai_task", "ref": "sessExp1", "pj": pa}
    )
    assert res.status_code == 200 and "attachment" in res.headers["Content-Disposition"]
    doc = res.get_json()
    assert (
        doc["schema"] == "tavotto.exec-trace-diagnostic" and doc["trace"]["status"] == "cancelled"
    )
    assert doc["attribution"]["origin"] is None  # 取消不是失败，没有根因
    assert "sessExp1" not in res.get_data(as_text=True)
    other = client.get("/api/exec-trace/diagnostic", query_string={"id": t.trace_id, "pj": pb})
    assert other.status_code == 404 and other.get_json() == {
        "available": False,
        "reason": "not_found",
    }
    assert client.get("/api/exec-trace/diagnostic", query_string={"pj": pa}).status_code == 400


def test_a_finished_verify_phase_is_not_a_verified_result():
    """Codex #1457：AI 任务的 verify 阶段 ok 只说明那一段走完了；提交后检查 unknown（结局 partial）时不能说 verified。"""
    for outcome, expected in (
        ("partial", "unknown"),
        ("completed", "verified"),
        ("failed", "unknown"),
    ):
        t = _trace()
        t.end_span(t.start_span("verify", "verify"))
        t.finish("ok" if outcome != "failed" else "failed", attributes={"outcome": outcome})
        assert diag.attribution(t.snapshot())["verified_by_execution"] == expected, outcome
