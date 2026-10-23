"""准备会话报告带 `remedy`（ADR 0148）、`POST /api/ai/fix` 的占位契约、capabilities 的 `ai_fix_available`、诊断包「修复经过」。"""

# ruff: noqa: F811 — 夹具（client / fake_pool / sessions）从别的测试文件导入复用，参数名与导入名相同
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import diagdetail, fixrouter, pool as engine_pool, prepsession
from test_preparation_api import client, fake_pool  # noqa: F401 — 夹具复用
from test_preparation_session import (  # noqa: F401
    _act,
    _action,
    _create_ready,
    _settle,
    sessions,
)


@pytest.fixture(autouse=True)
def _no_real_ai_probe(monkeypatch):
    """默认当作没连 AI（不让用例去探测本机的 claude / codex）；要连的用例自己改。"""
    monkeypatch.setattr(prepsession.SESSIONS, "ai_connected", lambda: False)


GOLDEN = Path(__file__).parent / "golden"


def _run_to_failure(client, tmp_path, fake_pool, error):
    _root, _pj, report = _create_ready(client, tmp_path)
    fake_pool["error"] = error
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    return sid, _settle(client, sid)


def test_a_failed_run_reports_a_remedy_and_it_is_connect_ai_without_an_ai(
    client, tmp_path, fake_pool, sessions
):
    _sid, final = _run_to_failure(
        client, tmp_path, fake_pool, engine_pool.WorkerError("boom", code="script_error")
    )
    assert final["outcome"]["kind"] == "failed"
    assert final["remedy"] == {"kind": "connect_ai"}


def test_the_same_failure_with_an_ai_connected_is_ai_fix(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    monkeypatch.setattr(prepsession.SESSIONS, "ai_connected", lambda: True)
    _sid, final = _run_to_failure(
        client, tmp_path, fake_pool, engine_pool.WorkerError("boom", code="script_error")
    )
    assert final["remedy"] == {"kind": "ai_fix"}


def test_a_python_version_failure_reports_change_python(client, tmp_path, fake_pool, sessions):
    _sid, final = _run_to_failure(
        client,
        tmp_path,
        fake_pool,
        engine_pool.WorkerError("old python", code="project_env_unsupported_python"),
    )
    assert final["remedy"]["kind"] == "change_python"
    assert set(final["remedy"]) == {"kind", "from", "to"}


def test_a_session_that_has_not_failed_has_no_remedy(client, tmp_path, fake_pool, sessions):
    _root, _pj, report = _create_ready(client, tmp_path)
    assert report["remedy"] is None
    sid = report["session_id"]
    assert _act(client, sid, _action(report, "run")["id"], 1).status_code == 202
    done = _settle(client, sid)
    assert done["phase"] == "completed" and done["remedy"] is None


def test_a_remedy_in_the_report_is_always_a_known_shape(client, tmp_path, fake_pool, sessions):
    _sid, final = _run_to_failure(
        client, tmp_path, fake_pool, engine_pool.WorkerError("boom", code="script_error")
    )
    kind = final["remedy"]["kind"]
    assert kind in fixrouter.REMEDY_KINDS
    golden = json.loads((GOLDEN / "fix_remedy_vectors.json").read_text("utf-8"))
    assert sorted(set(final["remedy"]) - {"kind"}) == sorted(golden["fields"][kind])


# ---------------------------------------------------------------- 端点占位契约


def test_ai_fix_endpoint_is_a_stable_unavailable_stub(client):
    resp = client.post("/api/ai/fix", json={"session_id": "s1", "script": "fig.py"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": False, "reason": "ai_fix_unavailable"}


@pytest.mark.parametrize("body", [None, {}, {"session_id": "s"}, {"session_id": 1, "script": "x"}])
def test_ai_fix_endpoint_refuses_malformed_bodies(client, body):
    resp = client.post("/api/ai/fix", json=body)
    assert resp.status_code == 400


def test_capabilities_say_ai_fix_is_not_available_yet(client):
    resp = client.get("/api/ai/capabilities")
    assert resp.status_code == 200
    assert resp.get_json()["ai_fix_available"] is False


# ---------------------------------------------------------------- 诊断包「修复经过」


def test_fix_journey_lists_what_was_tried_and_the_last_error(client, tmp_path, fake_pool, sessions):
    _sid, _final = _run_to_failure(
        client, tmp_path, fake_pool, engine_pool.WorkerError("boom", code="script_error")
    )
    (sess,) = list(prepsession.SESSIONS._by_id.values())
    journeys = prepsession.SESSIONS.fix_journeys(sess.project_id)
    assert len(journeys) == 1
    j = journeys[0]
    assert j["script"] == "fig.py"
    assert j["remedy"]["kind"] in fixrouter.REMEDY_KINDS
    assert j["attempts"] and j["attempts"][-1]["code"] == "script_error"
    assert j["last_error"]["code"] == "script_error"


SECRET = "sk-CANARYSECRETKEY1234567890"


def test_the_journey_enters_the_bundle_and_secret_shapes_are_scrubbed():
    journey = [
        {
            "script": "fig.py",
            "tried": ["switch_native"],
            "remedy": {"kind": "ai_fix"},
            "attempts": [{"run": 1, "seq": 1, "outcome": "failed", "code": "script_error"}],
            "last_error": {
                "code": "script_error",
                "message": f'client = Api(api_key="{SECRET}")\nTokenError: {SECRET}',
            },
        }
    ]
    src = diagdetail.Sources(fix_journey=journey)
    (item,) = diagdetail.collect_runs(src, diagdetail.DetailOptions(), diagdetail.Scrubber())
    doc = json.loads(item.data)
    assert doc["fix_journey"], "有 journey 就进包"
    assert doc["fix_journey"][0]["remedy"] == {"kind": "ai_fix"}
    assert doc["fix_journey"][0]["attempts"][0]["code"] == "script_error"
    assert SECRET not in item.data.decode("utf-8")


def test_no_journey_is_an_empty_list_not_a_missing_key():
    (item,) = diagdetail.collect_runs(
        diagdetail.Sources(), diagdetail.DetailOptions(), diagdetail.Scrubber()
    )
    assert json.loads(item.data)["fix_journey"] == []
