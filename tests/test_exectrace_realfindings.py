"""真实 Codex 验证（2026-10-27，ADR 0151 PR4 修订）发现的五个问题，各一条回归。

判据的主语：真实入口（修复会话 + `run_tool`、准备的阶段轨迹映射）产出的 Trace 快照，与 `exectrace_diag.attribution` 的结论。
"""

# ruff: noqa: F811 — 夹具（box）从别的测试文件导入复用，参数名与导入名相同

from __future__ import annotations

import time

from tavotto.engine import exectrace as xt, exectrace_diag as diag, fixsession, trace as stagetrace
from tests.test_fixasks import Scripted, box, start, wait_for, waiting_card  # noqa: F401


def _done(s):
    wait_for(lambda: fixsession.SESSIONS.get(s.id, "pj")["state"] == "finished")
    return s.trace.snapshot()


def _tools(snap):
    return [x for x in snap["spans"] if x["name"] == "tool_call"]


def test_a_policy_blocked_read_is_not_the_first_failure_of_a_session_that_then_succeeds(box):
    secret = box.home / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("x", encoding="utf-8")
    s = start(
        box,
        Scripted(
            [("read_file", {"path": str(secret)})],
            [("finish", {"outcome": "fixed", "summary": "ok"})],
        ),
    )
    snap = _done(s)
    blocked = _tools(snap)[0]
    assert blocked["attributes"]["tool"] == "read_file"
    assert blocked["status"] == "cancelled" and blocked["attributes"]["approved_by"] == "blocked"
    assert blocked["error_code"]  # 码照留：哪道护栏挡的
    assert snap["first_failure"] is None
    a = diag.attribution(snap)
    assert a["origin"] is None and a["cascade"] == [] and a["blocked_calls"] == 1
    assert a["final_status"] == "ok" and a["verified_by_execution"] == "verified"


def test_a_failed_session_points_at_its_real_failure_not_at_a_blocked_read(box):
    secret = box.home / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("x", encoding="utf-8")
    s = start(
        box,
        Scripted(
            [("read_file", {"path": str(secret)})],
            [("try_import", {"env": "nope", "module": "numpy"})],
            [("finish", {"outcome": "not_fixed", "summary": "no"})],
        ),
    )
    snap = _done(s)
    a = diag.attribution(snap)
    assert snap["status"] == "failed"
    assert a["origin"]["stage"] != "unknown"
    assert a["origin"]["error_code"] not in ("outside_scope", "denied")
    assert all(c["error_code"] not in ("outside_scope", "denied") for c in a["cascade"])


def test_an_approved_card_makes_the_tool_and_its_wait_say_the_same_thing_and_slowest_excludes_waiting(
    box,
):
    s = start(
        box,
        Scripted(
            [("propose_fix", {"summary": "装 numpy"})],
            [("finish", {"outcome": "fixed", "summary": "ok"})],
        ),
    )
    card = waiting_card(s)
    time.sleep(0.3)  # 用户在想
    fixsession.SESSIONS.answer(s.id, "pj", card["id"], "yes")
    snap = _done(s)
    propose = next(t for t in _tools(snap) if t["attributes"]["tool"] == "propose_fix")
    wait = next(x for x in snap["spans"] if x["name"] == "user_wait")
    assert propose["attributes"]["approved_by"] == wait["attributes"]["approved_by"] == "user"
    slow = diag.attribution(snap)["slowest"]
    assert slow is None or slow["duration_ms"] < wait["duration_ms"]


def test_a_fix_session_counts_its_environment_changes_as_repair_attempts():
    t = xt.ExecTrace("fix_session")
    run = t.start_span("agent_run", "agent")
    xt.register_vocabulary("tool", ["install_packages", "read_file"])
    xt.register_vocabulary("tier", ["listed", "observe"])
    t.end_span(
        t.start_span(
            "tool_call", "tool", parent=run, attributes={"tool": "read_file", "tier": "observe"}
        )
    )
    t.end_span(
        t.start_span(
            "tool_call",
            "tool",
            parent=run,
            attributes={"tool": "install_packages", "tier": "listed"},
        )
    )
    t.end_span(run)
    t.finish("ok", attributes={"outcome": "fixed"})
    a = diag.attribution(t.snapshot())
    assert a["repair_attempts"] == 1 and a["repair_attempted"] is True


def test_stage_points_land_when_they_happened_not_when_they_were_observed():
    legacy = stagetrace.Trace()
    t = xt.ExecTrace("preparation")
    time.sleep(0.02)
    legacy.mark("plan")
    time.sleep(0.15)
    legacy.mark("execute")
    time.sleep(0.15)
    xt.observe_stage_trace(t, legacy.to_payload(), legacy_t0=legacy._t0)
    t.finish("ok")
    pts = [e["at_offset_ms"] for e in t.snapshot()["events"] if e["name"] == "stage"]
    end = t.snapshot()["spans"][0]["end_offset_ms"]
    assert len(pts) == 2 and 0 <= pts[0] < pts[1] < end
    assert 100 <= pts[1] - pts[0] <= 400  # 两点相隔约 150ms，不是挤在同一毫秒
    assert end - pts[1] >= 100
