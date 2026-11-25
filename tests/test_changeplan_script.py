"""修改方案的脚本修改（ADR 0206 PR 6）：Agent 在候选里改、不写用户脚本；方案带 diff 与前后图；应用 = 两处备份后原子替换，撤销 = 按备份还原。

* AI 桥 `plan_only`：对所有连接强制走候选；越界（外部 CLI 不遵守 cwd，R-03）只能事后检出——目标脚本被改就按快照还原，别的文件只报告不动，
  还原前用户又改过则不覆盖；
* 端点：用假 worker / 假 Agent 走完整条后台任务链；应用在锁内核「脚本还是生成方案时那一版」。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from tavotto.engine import ai_bridge, ai_figure_edit, ai_history, ai_tasks, changeplan as cp, config
from tests.test_ai_figure_edit import SCRIPT
from tests.test_changeplan_endpoints import B0, PNG, env as plan_env, wait  # noqa: F401

NEW = SCRIPT.replace('"old"', '"new"')


# --------------------------------------------------------------------------- AI 桥：plan_only 与越界核对
@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda agent, endpoint_id: None)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cmd0, extra: dict(os.environ))
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    (figs / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    state = {"figs": figs, "cwd": [], "code": ""}
    # 开关关着（默认）：plan_only 也要走候选
    monkeypatch.setattr(config, "ai_settings", lambda: {})

    def _cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        state["cwd"].append(cwd)
        pre = f"import pathlib, sys; d = pathlib.Path(sys.argv[1]); proj = pathlib.Path({str(figs)!r}); "
        return [sys.executable, "-c", pre + state["code"], cwd], {}

    monkeypatch.setattr(ai_bridge, "_cmd", _cmd)
    yield state
    ai_tasks.unregister(ai_figure_edit.TASK_TYPE)


EDIT = "p = d / 'fig1.py'; p.write_text(p.read_text(encoding='utf-8').replace('\"old\"', '\"new\"'), encoding='utf-8')"
OVERREACH_TARGET = "(proj / 'fig1.py').write_text('# AI wrote me\\n', encoding='utf-8')"
OVERREACH_OTHERS = "; ".join(
    f"(proj / 'x{i}.txt').write_text('x', encoding='utf-8')" for i in range(7)
)


def _plan_run(state, code):
    state["code"] = code
    sid = ai_bridge.run("codex", "fig1.py", "把标题改成 new", str(state["figs"]), plan_only=True)
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(30)
        assert not pump.is_alive()
    out = ai_bridge.plan_outcome(sid, str(state["figs"]))
    assert out is not None
    return sid, out


def _script(state) -> str:
    return (state["figs"] / "fig1.py").read_text(encoding="utf-8")


def test_plan_only_edits_a_copy_and_never_writes_the_user_script(bridge):
    sid, out = _plan_run(bridge, EDIT)
    assert Path(bridge["cwd"][0]).parent == ai_figure_edit.CANDIDATE_DIR, "所有连接都在候选目录里跑"
    assert _script(bridge) == SCRIPT, "用户脚本一个字节没动"
    assert out["status"] == "done" and out["new_bytes"].decode() == NEW
    assert out["base_bytes"].decode() == SCRIPT and not out["overreach"]["touched"]
    assert not ai_bridge.SESSIONS[sid]["changed"] and not Path(bridge["cwd"][0]).exists()
    assert ai_bridge.SESSIONS[sid]["refresh"]["status"] == "skipped"


def test_plan_only_session_is_audited_but_not_listed_in_history_or_changes(bridge):
    project = str(bridge["figs"].resolve())
    sid, _out = _plan_run(bridge, EDIT)
    row = ai_history.get(sid)
    assert row is not None and row["plan_only"] == 1, "历史库照记（审计），带标"
    assert ai_history.list_sessions(project)["total"] == 0, "助手任务历史里不多一条"
    assert ai_history.list_sessions(project, status="done")["sessions"] == []
    assert ai_history.changes_since(project, 0)["rows"] == []
    assert ai_bridge.changes(project, 0)["sessions"] == []
    # 对照：同一项目里的普通改图会话照常出现
    state = dict(bridge)
    state["code"] = EDIT
    normal = ai_bridge.run("codex", "fig1.py", "把标题改成 new", project)
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{normal}"), None)
    if pump is not None:
        pump.join(30)
    assert [r["id"] for r in ai_history.list_sessions(project)["sessions"]] == [normal]


def test_plan_only_without_a_change_has_no_bytes(bridge):
    _sid, out = _plan_run(bridge, "pass")
    assert out["new_bytes"] is None


def test_cli_overwriting_the_target_in_place_is_restored_from_the_snapshot(bridge):
    _sid, out = _plan_run(bridge, OVERREACH_TARGET)
    assert _script(bridge) == SCRIPT, "越界改了目标脚本 → 已按起跑快照还原"
    over = out["overreach"]
    assert over["touched"] and over["target_restored"] and not over["target_conflict"]
    assert out["new_bytes"] is None


def test_cli_touching_other_files_lists_them_and_leaves_them_alone(bridge):
    _sid, out = _plan_run(bridge, OVERREACH_OTHERS)
    over = out["overreach"]
    assert over["touched"] and not over["target_restored"]
    assert over["files"] == [f"x{i}.txt" for i in range(5)] and over["total"] == 7, (
        "最多列 5 个，其余只报个数"
    )
    for i in range(7):
        assert (bridge["figs"] / f"x{i}.txt").read_text() == "x", "别的文件不动"
    assert out["new_bytes"] is None, "越界不出方案"


def test_restore_does_not_overwrite_when_the_user_edited_meanwhile(bridge, monkeypatch, tmp_path):
    script = bridge["figs"] / "fig1.py"
    cand = ai_figure_edit.create_candidate("guard1", script, bridge["figs"], "fig1.py")
    snap = tmp_path / "snap.py"
    snap.write_bytes(cand.base_bytes)
    script.write_text("# AI overreach\n", encoding="utf-8")
    real = ai_bridge.script_guard

    class Edit:
        """看过「被 AI 改了」之后、拿到锁之前，用户又保存了一次。"""

        def __init__(self, path):
            script.write_text("# the user saved again\n", encoding="utf-8")
            self.inner = real(path)

        def __enter__(self):
            return self.inner.__enter__()

        def __exit__(self, *a):
            return self.inner.__exit__(*a)

    monkeypatch.setattr(ai_bridge, "script_guard", Edit)
    out = ai_bridge._plan_guard(cand, snap)
    assert out["touched"] and out["target_conflict"] and not out["target_restored"]
    assert script.read_text() == "# the user saved again\n", "不覆盖用户这期间的改动"


# --------------------------------------------------------------------------- 端点：生成、应用、撤销
def _script_post(client, **over):
    body = {
        "id": "Kin.pdf",
        "patches": B0,
        "source": "assistant",
        "instruction": "把标题改成 new",
        "mode": "script",
        "connection": "conn1",
        **over,
    }
    return client.post("/api/change-plans", json=body)


@pytest.fixture
def sc(plan_env, monkeypatch):  # noqa: F811
    m, client, figs, worker, model, retired = plan_env
    (figs / "fig.py").write_text(SCRIPT, encoding="utf-8")
    state = {"outcome": None, "render": PNG, "runs": []}

    def fake_run(agent, script, prompt, root, **kw):
        state["runs"].append((agent, script, prompt, kw))
        return "sid1"

    def outcome(sid, project):
        base = (figs / "fig.py").read_bytes()
        return state["outcome"] or {
            "status": "done",
            "error": None,
            "outcome": "completed",
            "unmet": [],
            "new_bytes": NEW.encode(),
            "base_bytes": base,
            "base_sha256": "",
            "overreach": {},
        }

    monkeypatch.setattr(m.engine_ai, "run", fake_run)
    monkeypatch.setattr(m.engine_ai, "plan_outcome", outcome)
    monkeypatch.setattr(
        m, "_apply_connection", lambda body, agent, model, ep: ("codex", model, "", None)
    )
    monkeypatch.setattr(m, "_change_plan_script_render", lambda *a, **k: state["render"])
    monkeypatch.setattr(
        m,
        "_after_script_edit",
        lambda ctx, script: state.setdefault("refreshed", []).append(script),
    )
    monkeypatch.setattr(m, "_ai_task_hooks", lambda *a, **k: {})
    yield m, client, figs, state


def _make(client, **over):
    resp = _script_post(client, **over)
    assert resp.status_code == 202, resp.get_json()
    return wait(client, resp.get_json()["plan_id"])


def test_script_plan_carries_diff_and_images_and_never_touches_the_script(sc):
    m, client, figs, state = sc
    out = _make(client)
    assert out["state"] == "ready"
    plan = out["plan"]
    assert plan["kind"] == "script" and plan["source"] == "assistant"
    (group,) = plan["groups"]
    assert group["kind"] == "script" and group["atomic"] is True and group["changes"] == []
    info = group["script"]
    assert info["rel"] == "fig.py" and info["added"] == 1 and info["removed"] == 1
    assert '-    ax.set_title("old")' in info["diff"] and '+    ax.set_title("new")' in info["diff"]
    assert plan["base"]["script_sha256"] == info["base_sha256"]
    assert (figs / "fig.py").read_text() == SCRIPT
    assert state["runs"][0][3]["plan_only"] is True
    for name in ("before", "after"):
        assert client.get(plan["preview"][name]).data == PNG


@pytest.mark.parametrize(
    "outcome, code",
    [
        ({"new_bytes": None}, "change_plan_script_no_change"),
        (
            {"status": "failed", "outcome": "failed", "error": "precommit_not_verified"},
            "change_plan_script_check_failed",
        ),
        (
            {"status": "failed", "outcome": "failed", "error": "agent_exit_nonzero"},
            "change_plan_script_agent_failed",
        ),
        (
            {"status": "timeout", "outcome": "failed", "error": "timeout"},
            "change_plan_script_agent_failed",
        ),
    ],
)
def test_script_plan_failures_have_stable_codes_and_leave_the_script_alone(sc, outcome, code):
    m, client, figs, state = sc
    base = (figs / "fig.py").read_bytes()
    state["outcome"] = {
        "status": "done",
        "error": None,
        "outcome": "completed",
        "unmet": [],
        "new_bytes": NEW.encode(),
        "base_bytes": base,
        "base_sha256": "",
        "overreach": {},
        **outcome,
    }
    out = _make(client)
    assert out["state"] == "failed" and out["code"] == code
    assert (figs / "fig.py").read_text() == SCRIPT


def test_overreach_failure_reports_files_and_whether_the_script_was_restored(sc):
    m, client, figs, state = sc
    state["outcome"] = {
        "status": "failed",
        "error": "precommit_not_verified",
        "outcome": "failed",
        "unmet": [],
        "new_bytes": None,
        "base_bytes": SCRIPT.encode(),
        "base_sha256": "",
        "overreach": {
            "touched": True,
            "target_restored": True,
            "target_conflict": False,
            "files": ["a.txt", "b.txt"],
            "total": 7,
        },
    }
    out = _make(client)
    assert out["code"] == "change_plan_script_overreach"
    assert out["params"] == {
        "files": ["a.txt", "b.txt"],
        "total": 7,
        "restored": True,
        "conflict": False,
    }


@pytest.mark.parametrize(
    "over",
    [{"source": "review", "comments": []}, {"connection": None}],
    ids=["review-source-is-refused", "needs-a-connection"],
)
def test_script_mode_is_assistant_only_and_needs_a_connection(sc, over):
    _m, client, *_ = sc
    body = {
        "id": "Kin.pdf",
        "patches": B0,
        "source": "assistant",
        "instruction": "x",
        "mode": "script",
        "connection": "c",
    }
    body.update(over)
    body = {k: v for k, v in body.items() if v is not None}
    assert client.post("/api/change-plans", json=body).status_code == 400


def test_apply_backs_up_twice_replaces_atomically_and_undo_restores(sc):
    m, client, figs, state = sc
    plan_id = _make(client)["plan"]["id"]
    resp = client.post(f"/api/change-plans/{plan_id}/apply-script")
    assert resp.status_code == 200, resp.get_json()
    assert (figs / "fig.py").read_text() == NEW and state["refreshed"] == ["fig.py"]
    backup = resp.get_json()["applied"]["backup"]
    mirror = list((cp.config.data_dir() / "script_backups").rglob("original.py"))
    inproject = list((figs / "tavottofile" / "script-backups").rglob("original.py"))
    assert len(mirror) == 1 and len(inproject) == 1, "两处备份"
    assert mirror[0].read_text() == SCRIPT and backup
    again = client.post(f"/api/change-plans/{plan_id}/apply-script")
    assert again.get_json()["unchanged"] is True, "重复应用幂等"
    # 方案里记着已应用（重启后卡片仍认）
    assert (
        client.get(f"/api/change-plans/{plan_id}").get_json()["plan"]["applied"]["backup"] == backup
    )
    undo = client.post(f"/api/change-plans/{plan_id}/undo-script")
    assert undo.status_code == 200
    assert (figs / "fig.py").read_text() == SCRIPT
    assert client.get(f"/api/change-plans/{plan_id}").get_json()["plan"]["applied"] is None


def test_apply_refuses_when_the_script_changed_since_the_plan(sc):
    m, client, figs, state = sc
    plan_id = _make(client)["plan"]["id"]
    (figs / "fig.py").write_text(SCRIPT + "# edited by the user\n", encoding="utf-8")
    resp = client.post(f"/api/change-plans/{plan_id}/apply-script")
    assert resp.status_code == 409 and resp.get_json()["code"] == "change_plan_figure_changed"
    assert (figs / "fig.py").read_text() == SCRIPT + "# edited by the user\n"


def test_undo_does_not_overwrite_a_later_edit(sc):
    m, client, figs, state = sc
    plan_id = _make(client)["plan"]["id"]
    client.post(f"/api/change-plans/{plan_id}/apply-script")
    (figs / "fig.py").write_text(NEW + "# later\n", encoding="utf-8")
    resp = client.post(f"/api/change-plans/{plan_id}/undo-script")
    assert resp.status_code == 409 and resp.get_json()["code"] == "change_plan_script_undo_conflict"
    assert (figs / "fig.py").read_text() == NEW + "# later\n"


def test_undo_before_apply_and_unknown_plans_are_refused(sc):
    m, client, figs, state = sc
    plan_id = _make(client)["plan"]["id"]
    assert (
        client.post(f"/api/change-plans/{plan_id}/undo-script").get_json()["code"]
        == "change_plan_script_not_applied"
    )
    assert (
        client.post("/api/change-plans/" + "0" * 24 + "/apply-script").get_json()["code"]
        == "change_plan_unknown"
    )


def test_candidate_bytes_tampering_is_caught_before_writing(sc):
    m, client, figs, state = sc
    plan_id = _make(client)["plan"]["id"]
    (cp.plan_dir(m.current_ctx().id, plan_id) / cp.SCRIPT_FILE).write_text("evil", encoding="utf-8")
    resp = client.post(f"/api/change-plans/{plan_id}/apply-script")
    assert resp.get_json()["code"] == "change_plan_unknown"
    assert (figs / "fig.py").read_text() == SCRIPT


def test_a_figure_plan_is_not_a_script_plan(sc):
    m, client, figs, state = sc
    out = client.post(
        "/api/change-plans",
        json={"id": "Kin.pdf", "patches": B0, "source": "review", "comments": []},
    )
    assert out.status_code == 400
    _ = time
