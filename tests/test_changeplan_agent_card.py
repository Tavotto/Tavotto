"""窗口里的 Agent 待办卡（ADR 0217 §五）的后端与插件两侧：

* 绑定会话出的方案记桌面口径的 `fileId` 与 `agent.panel / layout / stem`，没绑定的照旧；
* `GET /api/change-plans?agent=1` 只列这个项目、带面板、没过期、没落定的 Agent 方案，字段最小；
* `POST /api/change-plans/<id>/agent-applied` 记 `agent_applied`（不占 `applied`），先落定的赢；
* `suggest_status` 报 `by: desktop` 与勾过的全部 key（含脚本），`suggest_apply` 之后到的一方被拒。
"""

# ruff: noqa: F811  — 夹具从别的测试模块导入后在用例形参里同名使用（pytest 的写法）
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "codex-plugin" / "mcp"))

from tavotto.engine import changeplan as cp  # noqa: E402
from tavotto_mcp import bridge  # noqa: E402
from test_changeplan_endpoints import PNG, env  # noqa: E402, F401
from test_mcp_layout_binding import _open, _panel, _write_layout  # noqa: E402
from test_mcp_server import _body, _call, _clean_sessions, project  # noqa: E402, F401
from test_mcp_suggest import TODOS, worker  # noqa: E402, F401

# --------------------------------------------------------------------------- 插件一侧


def _plan(sid: str) -> dict:
    res = _call("tavotto_suggest_plan", {"session_id": sid, "todos": TODOS})
    assert not res.get("isError"), _body(res)
    return _body(res)


def _stored(project, plan_id: str) -> dict:
    plan = cp.load(bridge.project_id(str(project)), plan_id)
    assert plan is not None
    return plan


def test_bound_session_records_the_desktop_identity(project, worker):
    _write_layout(project, "组图", [_panel("o_p1")])
    sid = _open(project)["session_id"]
    plan = _stored(project, _plan(sid)["plan_id"])
    assert plan["base"]["target"] == "Fig1.pdf"  # 桌面口径的 fileId
    agent = plan["agent"]
    assert agent["session"] == sid and agent["panel"] == "o_p1" and agent["layout"] == "组图"
    assert agent["stem"] == bridge.get_session(sid).stem
    assert isinstance(agent["at"], float)


def test_unbound_session_is_unchanged(project, worker):
    sid = _open(project)["session_id"]
    plan = _stored(project, _plan(sid)["plan_id"])
    assert plan["base"]["target"] == bridge.get_session(sid).stem
    assert plan["agent"] == {"session": sid}


def test_plugin_lookups_still_work_on_a_bound_plan(project, worker):
    _write_layout(project, "组图", [_panel("o_p1")])
    sid = _open(project)["session_id"]
    out = _plan(sid)
    status = _body(_call("tavotto_suggest_status", {"session_id": sid, "plan_id": out["plan_id"]}))
    assert status["state"] == "proposed" and not status["stale"]


def test_desktop_applied_first_is_reported_and_blocks_the_plugin(project, worker):
    _write_layout(project, "组图", [_panel("o_p1")])
    sid = _open(project)["session_id"]
    out = _plan(sid)
    keys = {t["label"]: t["key"] for t in out["todos"]}
    chosen = [keys["横轴刻度字号改小"], keys["加误差线（SD）"]]
    pid = bridge.project_id(str(project))
    assert cp.settle_agent_plan(
        pid, out["plan_id"], "agent_applied", {"by": "desktop", "keys": chosen, "at": 1.0}
    )
    status = _body(_call("tavotto_suggest_status", {"session_id": sid, "plan_id": out["plan_id"]}))
    assert status["state"] == "applied" and status["by"] == "desktop"
    assert status["chosen"] == chosen
    res = _call(
        "tavotto_suggest_apply",
        {"session_id": sid, "plan_id": out["plan_id"], "keys": [chosen[0]]},
    )
    assert res["isError"] and _body(res)["code"] == "suggest_plan_applied"


def test_plugin_applied_first_refuses_the_desktop(project, worker):
    _write_layout(project, "组图", [_panel("o_p1")])
    sid = _open(project)["session_id"]
    out = _plan(sid)
    key = next(t["key"] for t in out["todos"] if t["kind"] == "figure")
    done = _call(
        "tavotto_suggest_apply", {"session_id": sid, "plan_id": out["plan_id"], "keys": [key]}
    )
    assert not done.get("isError"), _body(done)
    pid = bridge.project_id(str(project))
    assert not cp.settle_agent_plan(
        pid, out["plan_id"], "agent_applied", {"by": "desktop", "keys": [key]}
    )
    assert "agent_applied" not in _stored(project, out["plan_id"])


# --------------------------------------------------------------------------- 桌面端点一侧


def _save_agent_plan(project_id: str, plan_id: str, *, agent: dict | None, todos=2, **extra):
    plan = {
        "id": plan_id,
        "schema": 1,
        "version": 1,
        "base": {"target": "Fig1.pdf", "fingerprint": "sha256:x"},
        "base_patches": [],
        "candidate_patches": [],
        "groups": [],
        "unhandled": [],
        "todos": [
            {"key": f"t{i}", "label": f"待办{i}", "kind": "figure" if i % 2 == 0 else "script"}
            for i in range(todos)
        ],
        **({"agent": agent} if agent is not None else {}),
        **extra,
    }
    cp.save(project_id, plan, PNG, PNG)
    return plan


AGENT = {"session": "s1", "stem": "Fig1", "panel": "o_p1", "layout": "组图", "at": 100.0}


def test_list_requires_agent_flag(env):
    _m, client, *_ = env
    assert client.get("/api/change-plans").status_code == 400


def test_list_gives_only_bound_unsettled_plans_of_this_project(env):
    m, client, *_ = env
    with m.app.test_request_context("/"):
        pid = m.current_ctx().id
    ids = {
        n: f"{i:024x}"
        for i, n in enumerate(["mine", "unbound", "applied", "desk", "other", "old"], 1)
    }
    _save_agent_plan(pid, ids["mine"], agent=AGENT)
    _save_agent_plan(pid, ids["unbound"], agent={"session": "s1"})  # 没绑定：桌面不显示
    _save_agent_plan(pid, ids["applied"], agent=AGENT, applied={"by": "agent", "keys": []})
    _save_agent_plan(pid, ids["desk"], agent=AGENT, agent_applied={"by": "desktop", "keys": []})
    _save_agent_plan(pid, ids["old"], agent={**AGENT, "at": 5.0})
    old = cp.plan_dir(pid, ids["old"]) / "plan.json"
    t = time.time() - cp.PLAN_TTL_S - 60
    os.utime(old, (t, t))
    _save_agent_plan("otherproj0001", ids["other"], agent=AGENT)
    out = client.get("/api/change-plans?agent=1").get_json()
    assert [p["id"] for p in out["plans"]] == [ids["mine"]]
    assert out["plans"][0] == {
        "id": ids["mine"],
        "target": "Fig1.pdf",
        "panel": "o_p1",
        "layout": "组图",
        "created": 100.0,
        "todos": 2,
    }


def test_list_is_newest_first_and_bounded(env):
    m, client, *_ = env
    with m.app.test_request_context("/"):
        pid = m.current_ctx().id
    for i in range(1, cp.MAX_PLANS_PER_PROJECT + 1):
        _save_agent_plan(pid, f"{i:024x}", agent={**AGENT, "at": float(i)})
    out = client.get("/api/change-plans?agent=1").get_json()["plans"]
    assert len(out) <= cp.MAX_PLANS_PER_PROJECT
    assert [p["created"] for p in out] == sorted((p["created"] for p in out), reverse=True)


def test_plan_view_serves_agent_plans(env):
    m, client, *_ = env
    with m.app.test_request_context("/"):
        pid = m.current_ctx().id
    _save_agent_plan(pid, "a" * 24, agent=AGENT)
    out = client.get(f"/api/change-plans/{'a' * 24}").get_json()
    assert out["state"] == "ready" and out["plan"]["agent"]["panel"] == "o_p1"


def test_agent_applied_records_a_separate_field_and_first_settle_wins(env):
    m, client, *_ = env
    with m.app.test_request_context("/"):
        pid = m.current_ctx().id
    pl = "b" * 24
    _save_agent_plan(pid, pl, agent=AGENT)
    url = f"/api/change-plans/{pl}/agent-applied"
    assert client.post(url, json={"keys": ["nope"]}).status_code == 400
    assert client.post(url, json={"keys": []}).status_code == 400
    ok = client.post(url, json={"keys": ["t0", "t1", "t0"]})
    assert ok.status_code == 200
    stored = cp.load(pid, pl)
    assert stored["agent_applied"]["by"] == "desktop" and stored["agent_applied"]["keys"] == [
        "t0",
        "t1",
    ]
    assert not stored.get("applied")
    again = client.post(url, json={"keys": ["t0"]})
    assert again.status_code == 409 and again.get_json()["code"] == "change_plan_agent_settled"
    assert client.get("/api/change-plans?agent=1").get_json()["plans"] == []


def test_agent_applied_refuses_plugin_settled_and_non_agent_plans(env):
    m, client, *_ = env
    with m.app.test_request_context("/"):
        pid = m.current_ctx().id
    _save_agent_plan(pid, "c" * 24, agent=AGENT, applied={"by": "agent", "keys": ["t0"], "at": 1})
    r = client.post(f"/api/change-plans/{'c' * 24}/agent-applied", json={"keys": ["t0"]})
    assert r.status_code == 409 and r.get_json()["code"] == "change_plan_agent_settled"
    _save_agent_plan(pid, "d" * 24, agent=None)
    r = client.post(f"/api/change-plans/{'d' * 24}/agent-applied", json={"keys": ["t0"]})
    assert r.status_code == 404
    assert (
        client.post("/api/change-plans/zz/agent-applied", json={"keys": ["t0"]}).status_code == 404
    )
