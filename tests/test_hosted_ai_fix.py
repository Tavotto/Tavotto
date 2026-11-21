"""内置 AI（Tavotto Agent，ADR 0200）× AI 修复端点：`POST /api/ai/fix` 在没有自己的 Key 也没有 CLI 时用它兜底。

主语：**HTTP 响应体与网关看到的请求**。夹具（client / fake_pool / sessions 与假网关）复用别的测试文件；假模型、假网关、假配置目录，不联网。
"""

# ruff: noqa: F811 — 夹具从别的测试文件导入复用，参数名与导入名相同
from __future__ import annotations

import time

import pytest

from tavotto.engine import ai_bridge, fixsession, prepsession
from tavotto.engine.agent_runner.redact import Redactor
from tavotto.engine.agent_runner.wires import RunnerFailure
from test_ai_fix_endpoint import Scripted, connect, poll
from test_hosted_ai import GATEWAY, LIMITS, TASK_TOKEN, TOMORROW_MS, err, gw  # noqa: F401
from test_preparation_api import client, fake_pool  # noqa: F401
from test_preparation_session import _create_ready, sessions  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated_fix(monkeypatch):
    monkeypatch.setattr(prepsession.SESSIONS, "ai_connected", lambda: False)
    monkeypatch.setattr(ai_bridge, "agent_caps", lambda _agent_id: None)  # 没有可用的 CLI
    fixsession.SESSIONS.reset_for_tests()
    yield
    fixsession.SESSIONS.reset_for_tests()


def use_model(monkeypatch, model, seen=None):
    def build(conn, **kw):
        if seen is not None:
            seen.append(conn)
        return model, Redactor(conn["api_key"])

    monkeypatch.setattr(fixsession, "build_model", build)


def _wait_close(gw, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if "/v1/task/close" in gw.paths():
            return True
        time.sleep(0.02)
    return False


def test_hosted_fix_opens_a_task_applies_its_limits_and_closes_it_when_the_session_ends(
    client, tmp_path, fake_pool, sessions, monkeypatch, gw
):
    _root, _pj, report = _create_ready(client, tmp_path)
    seen: list[dict] = []
    use_model(monkeypatch, Scripted([("finish", {"outcome": "not_fixed", "summary": "x"})]), seen)
    resp = client.post("/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"})
    assert resp.get_json()["ok"] is True
    fid = resp.get_json()["fix_id"]
    poll(client, fid, lambda b: b["state"] == "finished")
    assert _wait_close(gw)
    assert gw.kinds() == ["fix"] and gw.paths() == ["/v1/task", "/v1/task/close"]
    (conn,) = seen
    assert (
        conn["api_key"] == TASK_TOKEN and conn["base_url"] == GATEWAY and conn["limits"] == LIMITS
    )
    assert TASK_TOKEN not in client.get(f"/api/ai/fix/{fid}").get_data(as_text=True)
    assert TASK_TOKEN not in client.get("/api/ai/capabilities?refresh=1").get_data(as_text=True)


def test_hosted_fix_closes_the_task_when_the_user_cancels(
    client, tmp_path, fake_pool, sessions, monkeypatch, gw
):
    _root, _pj, report = _create_ready(client, tmp_path)
    use_model(monkeypatch, Scripted([("propose_fix", {"summary": "x"})]))
    fid = client.post(
        "/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"}
    ).get_json()["fix_id"]
    poll(client, fid, lambda b: b["state"] == "waiting")
    assert "/v1/task/close" not in gw.paths()  # 等用户回答的时候任务还开着
    client.post(f"/api/ai/fix/{fid}/cancel")
    poll(client, fid, lambda b: b["state"] == "finished")
    assert _wait_close(gw)
    assert gw.paths().count("/v1/task/close") == 1


def test_task_budget_exhausted_mid_session_ends_through_the_existing_budget_outcome(
    client, tmp_path, fake_pool, sessions, monkeypatch, gw
):
    _root, _pj, report = _create_ready(client, tmp_path)

    class Exhausted:
        def complete(self, **_kw):
            raise RunnerFailure(
                "budget", "tokens"
            )  # `classify_status` 把网关的 402 task_budget_exhausted 变成它

    use_model(monkeypatch, Exhausted())
    fid = client.post(
        "/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"}
    ).get_json()["fix_id"]
    done = poll(client, fid, lambda b: b["state"] == "finished")
    assert (
        done["outcome"] == "failed" and done["reason"] == "budget"
    )  # 与 fix_failed:budget 同一条路，不是新错误
    assert _wait_close(gw)


@pytest.mark.parametrize(
    ("status", "code"),
    [(402, "quota_exhausted"), (402, "daily_limit"), (403, "not_entitled"), (503, "paused")],
)
def test_open_failure_is_a_quiet_two_choice_result_not_an_error(
    client, tmp_path, fake_pool, sessions, monkeypatch, gw, status, code
):
    _root, _pj, report = _create_ready(client, tmp_path)
    use_model(monkeypatch, Scripted())
    gw.script.append(err(status, code, retry_at=TOMORROW_MS))
    resp = client.post("/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"})
    assert resp.status_code == 200
    assert resp.get_json() == {
        "ok": False,
        "reason": "ai_hosted_unavailable",
        "hosted": {"reason": code, "retry_at": TOMORROW_MS},
    }
    assert fixsession.SESSIONS.active() is None and "/v1/task/close" not in gw.paths()


def test_malformed_requests_do_not_open_a_task(client, gw):
    assert client.post("/api/ai/fix", json={"session_id": "s"}).status_code == 400
    assert client.post(
        "/api/ai/fix", json={"session_id": "nope", "script": "fig.py"}
    ).status_code in (404, 409)
    assert gw.calls == []


def test_hosted_goes_first_even_when_an_own_key_exists(
    client, tmp_path, fake_pool, sessions, monkeypatch, gw
):
    _root, _pj, report = _create_ready(client, tmp_path)
    seen: list[dict] = []
    connect(monkeypatch, Scripted([("finish", {"outcome": "not_fixed", "summary": "x"})]))
    use_model(monkeypatch, Scripted([("finish", {"outcome": "not_fixed", "summary": "x"})]), seen)
    body = client.post(
        "/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"}
    ).get_json()
    poll(client, body["fix_id"], lambda b: b["state"] == "finished")
    assert gw.kinds() == ["fix"] and seen[0]["api_key"] == TASK_TOKEN
    assert "hosted_fallback" not in body


@pytest.mark.parametrize(
    ("status", "code", "info"),
    [
        (402, "quota_exhausted", True),
        (402, "daily_limit", True),
        (403, "not_entitled", False),
        (503, "paused", False),
    ],
)
def test_open_failure_falls_back_to_the_own_key_and_only_quota_is_surfaced(
    client, tmp_path, fake_pool, sessions, monkeypatch, gw, status, code, info
):
    _root, _pj, report = _create_ready(client, tmp_path)
    seen: list[dict] = []
    connect(monkeypatch, Scripted())
    use_model(monkeypatch, Scripted([("finish", {"outcome": "not_fixed", "summary": "x"})]), seen)
    gw.script.append(err(status, code, retry_at=TOMORROW_MS))
    body = client.post(
        "/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"}
    ).get_json()
    assert body["ok"] is True
    poll(client, body["fix_id"], lambda b: b["state"] == "finished")
    assert seen[0]["api_key"] != TASK_TOKEN  # 退到了自己的 Key
    assert ("hosted_fallback" in body) is info
    if info:
        fb = body["hosted_fallback"]
        assert (fb["reason"], fb["retry_at"], fb["kind"]) == (code, TOMORROW_MS, "key")
        assert fb["label"]  # 连接弹窗列表里那条连接的显示名，前端拼「已经切换到您的 <名称> Key」
    assert "/v1/task/close" not in gw.paths()


def test_a_second_fix_on_the_same_prep_session_closes_the_extra_task(
    client, tmp_path, fake_pool, sessions, monkeypatch, gw
):
    _root, _pj, report = _create_ready(client, tmp_path)
    use_model(monkeypatch, Scripted([("propose_fix", {"summary": "x"})]))
    body = {"session_id": report["session_id"], "script": "fig.py"}
    first = client.post("/api/ai/fix", json=body).get_json()
    poll(client, first["fix_id"], lambda b: b["state"] == "waiting")
    again = client.post("/api/ai/fix", json=body).get_json()
    assert again == first  # 复用现成的会话
    assert gw.kinds() == ["fix", "fix"]  # 第二次也开了一个……
    assert gw.paths().count("/v1/task/close") == 1  # ……用不上的那个立刻关掉；第一个还在等用户
    client.post(f"/api/ai/fix/{first['fix_id']}/cancel")
    poll(client, first["fix_id"], lambda b: b["state"] == "finished")
    assert _wait_close(gw)
    assert gw.paths().count("/v1/task/close") == 2
