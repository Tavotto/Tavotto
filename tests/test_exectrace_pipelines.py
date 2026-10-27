"""Execution Trace PR2（ADR 0151 §PR2）：真实执行边界上的 Span——AI 改图、智能修复、准备、导出。

判据的主语：**业务模块在真实入口上跑完之后**，进程级记录器 / 落盘存储里的那份 Trace（不是插桩函数的返回值）。
Agent CLI 换成一段 python（`test_ai_figure_edit` 的夹具）、修复用剧本假模型（`test_fixasks` 的夹具）；
准备那一条起**真服务 + 真 worker**，读它自己数据目录下落盘的 Trace。不联网、不调真模型、不花钱。
"""

# ruff: noqa: F811 — 夹具（env / box）从别的测试文件导入复用，参数名与导入名相同

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import ai_bridge, ai_pricing, exectrace, fixsession
from tests.test_ai_figure_edit import (  # noqa: F401
    EDIT,
    FIX,
    SYNTAX,
    _project_text,
    _run,
    _wait,
    env,
)
from tests.test_fixasks import Scripted, box, start, wait_for, waiting_card  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _snap(trace) -> dict:
    snap = trace.snapshot()
    assert snap is not None, "Trace 被关掉了？"
    return snap


def _spans(snap, name=None):
    return [s for s in snap["spans"] if name is None or s["name"] == name]


def _root(snap):
    return next(s for s in snap["spans"] if s["span_id"] == snap["root_span_id"])


# ================================================================ AI 改图（ai_bridge + ai_tasks）


def test_a_normal_edit_records_accept_agent_phases_and_keeps_four_facts_apart(env):
    sid, _ = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "done" and '"new"' in _project_text(env)
    snap = _snap(sess["trace"])
    assert snap["status"] == "ok" and snap["complete"] is True and snap["task_ref"] == sid
    root = _root(snap)
    # Agent 退出 / 文件变了 / 刷新 / 任务结局：四件事分开，不合并成一个「成功」
    assert root["attributes"] == {"changed": True, "refresh": "ok", "outcome": "partial"}
    names = [s["name"] for s in snap["spans"]]
    assert names[:2] == ["task", "accept"]
    for phase in ("plan", "execute", "validate", "commit", "verify"):
        assert phase in names, names
    agent = _spans(snap, "agent_run")
    assert len(agent) == 1 and agent[0]["status"] == "ok"
    # 第三方 CLI 内部看不见：只有外层区间，标 opaque，没有虚构的模型 / 工具 Span
    assert agent[0]["attributes"] == {"agent": "codex", "opaque": True, "exit_ok": True}
    assert not _spans(snap, "model_request") and not _spans(snap, "tool_call")
    # 落盘、带 scope；别的项目读不到
    scope = exectrace.scope_for(sess["project"])
    assert exectrace.RECORDER.get(snap["trace_id"], scope=scope)["status"] == "ok"
    assert exectrace.RECORDER.get(snap["trace_id"], scope=exectrace.scope_for("/elsewhere")) is None
    assert sess["project"] not in json.dumps(snap)


def test_a_correction_is_a_second_agent_attempt_and_the_failed_check_stays_the_first_failure(env):
    env["codes"] = [SYNTAX, FIX]
    sid, _ = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "done"
    snap = _snap(sess["trace"])
    runs = _spans(snap, "agent_run")
    assert [r["attempt"] for r in runs] == [1, 2] and all(r["status"] == "ok" for r in runs)
    validates = _spans(snap, "validate")
    assert [(v["attempt"], v["status"]) for v in validates] == [(1, "failed"), (2, "ok")]
    assert validates[0]["error_code"] == "precommit_not_verified"
    assert [c["status"] for c in _spans(snap, "correct")] == ["ok"]
    # 最终成功不抹掉第一次失败的证据
    assert snap["first_failure"]["name"] == "validate" and snap["first_failure"]["attempt"] == 1
    assert snap["status"] == "ok"


def test_a_failing_agent_is_attributed_to_its_run_not_to_a_later_phase(env):
    env["code"] = "raise SystemExit(3)"
    sid, _ = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "failed"
    snap = _snap(sess["trace"])
    run = _spans(snap, "agent_run")[0]
    assert run["status"] == "failed" and run["error_code"] == "agent_exit_nonzero"
    assert snap["first_failure"]["name"] == "agent_run"
    assert snap["status"] == "failed" and _root(snap)["attributes"]["changed"] is False


def test_cancel_freezes_the_trace_and_late_writes_do_not_pollute_it(env):
    env["code"] = "time.sleep(30)"
    sid, _ = _run(env)
    wait_for(lambda: ai_bridge.SESSIONS[sid]["proc"].poll() is None)
    assert ai_bridge.cancel(sid)
    sess = _wait(sid)
    snap = _snap(sess["trace"])
    assert snap["status"] == "cancelled"
    assert any(e["name"] == "cancel_requested" for e in snap["events"])
    assert not [
        s for s in snap["spans"] if s["status"] in ("running", "ok") and s["name"] == "agent_run"
    ]
    trace = sess["trace"]
    # 取消之后迟到的观察（例如进程退出后才读到的最后一行）只计数
    assert trace.start_span("tool_call", "tool") is None
    assert trace.snapshot()["late_count"] >= 1
    assert len(trace.snapshot()["spans"]) == len(snap["spans"])


def test_an_idempotent_resend_produces_no_second_execution_and_no_second_trace(env):
    sid = ai_bridge.run(
        "codex",
        "fig1.py",
        "把标题改成 new",
        str(env["figs"]),
        client_request_id="rid-exectrace-0001",
    )
    with pytest.raises(ai_bridge.DuplicateRequest):
        ai_bridge.run(
            "codex",
            "fig1.py",
            "把标题改成 new",
            str(env["figs"]),
            client_request_id="rid-exectrace-0001",
        )
    _wait(sid)
    mine = [
        t for t in list(exectrace.RECORDER._live.values()) if t.session_ref == "rid-exectrace-0001"
    ]
    assert len(mine) == 1 and mine[0].task_ref == sid
    assert len(env["prompts"]) == 1  # 只执行了一次


# ================================================================ 内置执行器的事件 → 模型请求 / 工具


ANTHROPIC = {"base_url": "https://api.anthropic.com"}


def _runner_trace():
    t = exectrace.ExecTrace("ai_task")
    run = t.start_span("agent_run", "agent", attributes={"agent": "builtin"})
    return t, run


def test_a_reported_usage_line_becomes_a_model_request_with_a_priced_estimate():
    t, run = _runner_trace()
    line = json.dumps(
        {
            "type": "usage",
            "turn": 1,
            "input_tokens": 12_000,
            "output_tokens": 800,
            "cached_input_tokens": 10_000,
            "ms": 1500,
            "reported": True,
        }
    )
    ai_bridge._trace_runner_event(t, run, line, "claude-sonnet-5-5", ANTHROPIC)
    span = _spans(_snap(t), "model_request")[0]
    assert span["parent_span_id"] == run and span["timing"] in ("reported", "point")
    assert span["attributes"] == {"provider": "anthropic", "model": "claude-sonnet-5-5"}
    usage = span["usage"]
    assert (usage["source"], usage["input_tokens"], usage["cached_input_tokens"]) == (
        "reported",
        12_000,
        10_000,
    )
    # (2000 × 2 + 10000 × 0.10 + 800 × 10) / 1e6
    assert usage["cost"] == {
        "amount": pytest.approx(0.013),
        "currency": "USD",
        "pricing_version": ai_pricing.PRICING_VERSION,
        "estimated": True,
    }


def test_an_estimated_usage_or_an_unknown_endpoint_never_gets_a_cost():
    t, run = _runner_trace()
    est = {"type": "usage", "input_tokens": 100, "output_tokens": 10, "ms": 5, "reported": False}
    ai_bridge._trace_runner_event(t, run, json.dumps(est), "claude-sonnet-5-5", ANTHROPIC)
    custom = dict(est, reported=True)
    gateway = {"base_url": "https://my-gateway.example.org/anthropic"}
    ai_bridge._trace_runner_event(t, run, json.dumps(custom), "claude-sonnet-5-5", gateway)
    a, b = _spans(_snap(t), "model_request")
    assert a["usage"]["source"] == "estimated" and a["usage"]["cost"] is None
    assert b["usage"]["source"] == "reported" and b["usage"]["cost"] is None
    assert b["attributes"] == {}  # 自定义端点的模型名不进 Trace


def test_tool_lines_are_point_observations_and_free_text_never_enters_the_trace():
    t, run = _runner_trace()
    secret = "CANARY /Users/alice/raw.csv sk-ant-xyz"
    for line in (
        json.dumps({"type": "tool", "name": "edit_file", "ok": True, "summary": secret}),
        json.dumps({"type": "tool", "name": "rm -rf", "ok": False}),
        json.dumps({"type": "text", "text": secret}),
        "not json " + secret,
        "{broken",
    ):
        ai_bridge._trace_runner_event(t, run, line, None, None)
    snap = _snap(t)
    tools = _spans(snap, "tool_call")
    assert [(s["status"], s["timing"], s["attributes"]) for s in tools] == [
        ("ok", "point", {"tool": "edit_file"}),
        ("failed", "point", {}),
    ]
    assert "CANARY" not in json.dumps(snap) and "alice" not in json.dumps(snap)


def test_the_runner_tool_vocabulary_matches_every_tool_the_runner_defines():
    """词表只认静态注册表：`agent_runner/tools.py` 里每个 `Tool("…")` 的名字都在 `TOOL_NAMES` 里（AST，不是子串）。"""
    from tavotto.engine.agent_runner import tools

    tree = ast.parse(
        (ROOT / "src/tavotto/engine/agent_runner/tools.py").read_text(encoding="utf-8")
    )
    defined = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "Tool"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert defined and defined == set(tools.TOOL_NAMES)
    assert set(tools.TOOL_NAMES) <= exectrace.vocabulary("tool")


def test_pricing_tiers_and_unknown_models():
    assert ai_pricing.estimate(
        "anthropic", "claude-haiku-5-5", input_tokens=100_000, cached=0, output=0
    ) == pytest.approx(0.01)
    assert ai_pricing.estimate(
        "anthropic", "claude-haiku-5-5", input_tokens=100_001, cached=0, output=0
    ) == pytest.approx(0.0500005)
    assert ai_pricing.estimate(
        "openai", "gpt-5", input_tokens=1_000_000, cached=1_000_000, output=0
    ) == pytest.approx(0.125)
    assert (
        ai_pricing.estimate("anthropic", "claude-opus-4-1", input_tokens=10, cached=0, output=0)
        is None
    )
    assert ai_pricing.estimate(None, "gpt-5", input_tokens=10, cached=0, output=0) is None
    assert ai_pricing.vendor_of({"base_url": "https://api.anthropic.com/"}) == "anthropic"
    assert ai_pricing.vendor_of({"base_url": "https://api.anthropic.com.evil.example"}) is None
    assert set(ai_pricing.SOURCES) == {v for v, _ in ai_pricing.TABLE}


# ================================================================ 智能修复（fixsession + run_tool）


def _fix_snap(s):
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    return _snap(s.trace)


def test_an_approved_fix_has_one_tool_span_per_call_and_the_wait_is_its_own_span(box):
    model = Scripted(
        [("read_run_report", {})],
        [("propose_fix", {"summary": "装 numpy"})],
        [("finish", {"outcome": "fixed", "summary": "好了"})],
    )
    s = start(box, model)
    card = waiting_card(s)
    fixsession.SESSIONS.answer(s.id, "pj", card["id"], "yes")
    snap = _fix_snap(s)
    assert snap["status"] == "ok" and _root(snap)["attributes"] == {"outcome": "fixed"}
    agent = _spans(snap, "agent_run")[0]
    tools = _spans(snap, "tool_call")
    # `run_tool` 是唯一采集点：工具 Span 数 == 审计行数（不重复记）
    rows = (box.data / "ai" / "fix_audit.jsonl").read_text("utf-8").splitlines()
    assert [t["attributes"]["tool"] for t in tools] == ["read_run_report", "propose_fix", "finish"]
    assert len(tools) == len(rows)
    assert all(t["parent_span_id"] == agent["span_id"] for t in tools)
    wait = _spans(snap, "user_wait")
    assert len(wait) == 1 and wait[0]["parent_span_id"] == tools[1]["span_id"]
    assert wait[0]["status"] == "ok" and wait[0]["attributes"] == {"approved_by": "user"}
    assert any(e["name"] == "approval_granted" for e in snap["events"])
    # 等用户的时间在 wait 里，不在模型请求里
    assert all(m["parent_span_id"] == agent["span_id"] for m in _spans(snap, "model_request"))
    assert "装 numpy" not in json.dumps(snap)


def test_a_refused_install_is_recorded_as_the_users_decision(box):
    model = Scripted(
        [("propose_fix", {"summary": "装 numpy"})],
        [("finish", {"outcome": "not_fixed", "summary": "用户不让装"})],
    )
    s = start(box, model)
    card = waiting_card(s)
    fixsession.SESSIONS.answer(s.id, "pj", card["id"], "no")
    snap = _fix_snap(s)
    wait = _spans(snap, "user_wait")[0]
    assert wait["status"] == "ok" and wait["attributes"] == {"approved_by": "denied"}
    assert any(e["name"] == "approval_denied" for e in snap["events"])
    assert snap["status"] == "failed" and snap["error_code"] == "not_fixed"
    assert _root(snap)["attributes"] == {"outcome": "not_fixed"}


def test_an_unanswered_card_is_a_timed_out_wait_not_a_slow_model(box, monkeypatch):
    monkeypatch.setattr(fixsession, "ASK_TIMEOUT", 0.3)
    s = start(box, Scripted([("propose_fix", {"summary": "x"})]))
    snap = _fix_snap(s)
    wait = _spans(snap, "user_wait")[0]
    assert wait["status"] == "timeout" and wait["error_code"] == "approval_timeout"
    assert wait["duration_ms"] >= 250
    assert all(m["duration_ms"] < wait["duration_ms"] for m in _spans(snap, "model_request"))


def test_cancelling_a_fix_while_a_card_is_open_ends_the_wait_and_the_trace_cancelled(box):
    s = start(box, Scripted([("propose_fix", {"summary": "x"})]))
    waiting_card(s)
    fixsession.SESSIONS.cancel(s.id, "pj")
    snap = _fix_snap(s)
    assert snap["status"] == "cancelled"
    assert _spans(snap, "user_wait")[0]["status"] == "cancelled"
    assert not [x for x in snap["spans"] if x["status"] == "running"]


def test_fix_sessions_in_two_projects_never_share_a_trace(box):
    s = start(box, Scripted([("finish", {"outcome": "fixed", "summary": "a"})]), scope="pj")
    _fix_snap(s)
    tid = s.trace.trace_id
    assert exectrace.RECORDER.get(tid, scope=exectrace.scope_for("pj")) is not None
    assert exectrace.RECORDER.get(tid, scope=exectrace.scope_for("other")) is None


# ================================================================ 准备：真服务 + 真 worker

try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None


@pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（not_run）")
@pytest.mark.parametrize("broken", [False, True], ids=["ready", "script-fails"])
def test_a_real_preparation_leaves_a_trace_on_disk_with_the_stage_facts_mapped(tmp_path, broken):
    from tests.support import foundation_app as fa

    proj = tmp_path / "proj"
    shutil.copytree(ROOT / "tests" / "fixtures" / "foundation" / "single_file_csv", proj)
    proc = subprocess.run(
        [WORKER_PY, "figure.py"],
        cwd=proj,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env={
            "PATH": "/usr/bin:/bin",
            "MPLCONFIGDIR": str(tmp_path / "mpl"),
            "MPLBACKEND": "Agg",
            **{
                k: os.environ[k]
                for k in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP")
                if os.environ.get(k)
            },
        },
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    if broken:
        # 静态扫描仍认得这张图出自它（savefig 原样在），一跑就炸在执行阶段
        src = (proj / "figure.py").read_text(encoding="utf-8")
        (proj / "figure.py").write_text("raise RuntimeError('boom')\n" + src, encoding="utf-8")
    work = tmp_path / "work"
    with fa.running_app(proj, work) as app:
        _, panels = app.call("/api/panels", timeout=30)
        panel = next(p for p in panels["panels"] if p["id"].replace("\\", "/") == "figure.pdf")
        state = app.prepare(panel["id"])
        expected = "error" if broken else "ready"
        assert state["result"]["status"] == expected, state["result"]
        plan_id = state["plan"]["plan_id"]
        store = work / "data" / "cache" / "exec-trace"
        found = wait_for(
            lambda: [
                json.loads(p.read_text(encoding="utf-8"))["trace"]
                for p in store.glob("*.json")
                if not p.name.endswith(".open.json")
                and json.loads(p.read_text(encoding="utf-8"))["trace"].get("task_ref") == plan_id
            ],
            timeout=60,
        )
    snap = found[0]
    run = _spans(snap, "script_run")[0]
    assert run["timing"] == "measured" and run["duration_ms"] > 0
    stages = [e["attributes"] for e in snap["events"] if e["name"] == "stage"]
    assert stages and stages[0]["phase"] == "plan"
    if broken:
        assert snap["status"] == "failed" and run["status"] == "failed"
        assert {"phase": "execute", "outcome": "failed"} in stages
        assert snap["first_failure"]["name"] == "script_run"
    else:
        assert snap["status"] == "ok" and run["status"] == "ok"
        assert all(s["outcome"] == "ok" for s in stages)
    text = json.dumps(snap)
    assert str(proj) not in text and "figure.py" not in text and "data.csv" not in text


# ================================================================ 导出 / 采集器故障


_ORIGINAL = {
    "scope": "original",
    "filename": "fig",
    "formats": ["pdf"],
    "original": {"figure_id": "p1.pdf", "w_mm": 70.6, "h_mm": 52.9, "source_kind": "vector"},
}


def test_a_failing_export_maps_its_stage_facts_and_names_the_error(tmp_path):
    from tavotto.engine import exportjob, exportreq

    job = exportjob.prepare(_ORIGINAL, tmp_path / "out", project_id="pjX")

    def produce(job, tmp_dir):
        raise exportreq.ExportRequestError("export_render_failed", "源打不开", {"id": "fig"})

    exportjob.run(job, produce)
    snap = _snap(job.exec_trace)
    assert snap["status"] == "failed" and snap["error_code"] == "export_render_failed"
    assert _root(snap)["attributes"] == {"outcome": "failed"}
    stages = [e["attributes"] for e in snap["events"] if e["name"] == "stage"]
    assert {"phase": "source", "outcome": "failed"} in stages
    assert exectrace.RECORDER.get(snap["trace_id"], scope=exectrace.scope_for("pjX")) is not None
    # 没有项目上下文的调用方（探针 / 测试）不开 Trace
    assert isinstance(exportjob.prepare(_ORIGINAL, tmp_path / "o2").exec_trace, exectrace.NullTrace)


def test_a_broken_collector_never_changes_the_edit(env, monkeypatch):
    """T15：采集器内部故障（每次投影都抛）——改图照常完成、提交、刷新；Trace 只记 internal_errors。"""

    def boom(*a, **k):
        raise RuntimeError("collector bug")

    monkeypatch.setattr(exectrace, "project_attributes", boom)
    sid, calls = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "done" and '"new"' in _project_text(env) and calls == ["fig1.py"]
    # 连根 Span 都建不起来 → 记录器交出 NullTrace（同接口、什么都不记）；建起来了就只计 internal_errors
    snap = sess["trace"].snapshot()
    assert snap is None or snap["internal_errors"] > 0


def test_tracing_switched_off_changes_nothing_in_the_edit(env, monkeypatch):
    monkeypatch.setenv(exectrace.ENV_DISABLE, "1")
    sid, _ = _run(env)
    sess = _wait(sid)
    assert sess["status"] == "done" and '"new"' in _project_text(env)
    assert isinstance(sess["trace"], exectrace.NullTrace)


# ================================================================ Codex #1453：落盘不在业务线程上


def test_a_stalled_disk_never_blocks_the_caller_and_the_final_state_still_lands(
    tmp_path, monkeypatch
):
    """P1：导出建作业在全局导出锁里开 Trace。盘卡住时调用方必须立刻返回（写盘在后台写线程），盘恢复后终局照样落盘。"""
    import threading
    import time as _time

    from tavotto.engine import atomicio

    gate = threading.Event()
    real = atomicio.write_bytes

    def stalled(path, data, **kw):
        gate.wait(10)
        return real(path, data, **kw)

    monkeypatch.setattr(atomicio, "write_bytes", stalled)
    store = exectrace.TraceStore(tmp_path / "xt", background=True)
    t0 = _time.monotonic()
    t = exectrace.ExecTrace("export", store=store, scope="pjA")
    t.end_span(t.start_span("export", "phase"))
    t.finish("ok")
    assert _time.monotonic() - t0 < 0.5, "业务线程在等磁盘"
    assert not (tmp_path / "xt" / f"{t.trace_id}.json").exists()
    gate.set()
    assert store.flush(5.0)
    saved = store.load(t.trace_id, scope="pjA")
    assert saved["status"] == "ok"
    assert not (tmp_path / "xt" / f"{t.trace_id}.open.json").exists()


def test_the_process_recorder_writes_in_the_background():
    assert exectrace.RECORDER._store._background is True
