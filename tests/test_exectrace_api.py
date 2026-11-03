"""`GET /api/exec-trace`（ADR 0151 PR3）：按当前项目认领的只读快照端点。

判据的主语：经 Flask 测试客户端（会话认证照常挂着的同一个 app）对**当前项目**发的请求、拿到的 JSON。
Trace 由进程级记录器直接生成（与业务链路里同一个 `exectrace.begin`），scope 用与生产相同的两把键。
"""

# ruff: noqa: F811 — 夹具（client）从别的测试文件导入复用，参数名与导入名相同

from __future__ import annotations

from pathlib import Path

from tavotto.engine import exectrace
from tests.test_preparation_api import _open, _project, client  # noqa: F401


def _ai_trace(root: Path, ref: str):
    t = exectrace.begin("ai_task", task_ref=ref, scope=exectrace.scope_for(str(root.resolve())))
    t.end_span(t.start_span("agent_run", "agent", attributes={"agent": "codex", "opaque": True}))
    return t


def test_an_ai_task_trace_is_found_by_its_session_in_the_current_project(client, tmp_path):
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    t = _ai_trace(root, "sessA1")
    t.finish("ok", attributes={"changed": True})
    resp = client.get(
        "/api/exec-trace", query_string={"kind": "ai_task", "ref": "sessA1", "pj": pj}
    )
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    body = resp.get_json()
    assert body["available"] is True and body["trace"]["trace_id"] == t.trace_id
    assert body["trace"]["status"] == "ok"
    # 也能按 id 取
    by_id = client.get("/api/exec-trace", query_string={"id": t.trace_id, "pj": pj}).get_json()
    assert by_id["trace"]["trace_id"] == t.trace_id
    assert str(root) not in resp.get_data(as_text=True)


def test_a_running_trace_is_served_live_and_project_ids_scope_fix_sessions(client, tmp_path):
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    t = exectrace.begin("fix_session", task_ref="prepX", scope=exectrace.scope_for(pj))
    span = t.start_span("tool_call", "tool")
    body = client.get(
        "/api/exec-trace", query_string={"kind": "fix_session", "ref": "prepX", "pj": pj}
    ).get_json()
    assert body["trace"]["status"] == "running"
    assert next(s for s in body["trace"]["spans"] if s["span_id"] == span)["status"] == "running"


def test_another_projects_trace_reads_as_not_found_even_by_id(client, tmp_path):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    pa, pb = _open(client, a), _open(client, b)
    t = _ai_trace(a, "sessOther")
    t.finish("ok")
    for qs in ({"kind": "ai_task", "ref": "sessOther"}, {"id": t.trace_id}):
        resp = client.get("/api/exec-trace", query_string={**qs, "pj": pb})
        assert resp.status_code == 404 and resp.get_json() == {
            "available": False,
            "reason": "not_found",
        }
    assert (
        client.get("/api/exec-trace", query_string={"id": t.trace_id, "pj": pa}).status_code == 200
    )


def test_bad_queries_and_disabled_tracing(client, tmp_path, monkeypatch):
    pj = _open(client, _project(tmp_path, "p"))
    for qs in ({}, {"kind": "ai_task"}, {"kind": "nope", "ref": "x"}):
        resp = client.get("/api/exec-trace", query_string={**qs, "pj": pj})
        assert resp.status_code == 400 and resp.get_json()["code"] == "bad_request"
    resp = client.get("/api/exec-trace", query_string={"kind": "ai_task", "ref": "never", "pj": pj})
    assert resp.status_code == 404 and resp.get_json()["reason"] == "not_found"
    monkeypatch.setenv(exectrace.ENV_DISABLE, "1")
    resp = client.get("/api/exec-trace", query_string={"kind": "ai_task", "ref": "x", "pj": pj})
    assert resp.status_code == 404 and resp.get_json() == {"available": False, "reason": "disabled"}


def test_a_trace_finished_long_ago_is_read_back_from_disk(client, tmp_path):
    """刷新页面 / 重启之后：内存里没有了，从保留期内的落盘记录恢复。"""
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    t = _ai_trace(root, "sessDisk")
    t.finish("failed", error_code="agent_exit_nonzero")
    store = exectrace.RECORDER._store
    assert store.flush(5.0)
    with exectrace.RECORDER._lock:
        exectrace.RECORDER._live.pop(t.trace_id)
    body = client.get(
        "/api/exec-trace", query_string={"kind": "ai_task", "ref": "sessDisk", "pj": pj}
    ).get_json()
    assert body["available"] and body["trace"]["error_code"] == "agent_exit_nonzero"
