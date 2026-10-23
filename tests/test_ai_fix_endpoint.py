"""`POST /api/ai/fix` 与卡片回答 / 取消端点（智能修复 D2b，ADR 0148 / 0149）。

判据的主语：**HTTP 响应体与 SSE 提示**（不是 `fixsession` 内部状态）；会话认证由 `tests/test_browser_auth.py` 统一看护。
假模型、假主目录、假配置目录；不联网。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool / sessions）从别的测试文件导入复用，参数名与导入名相同
from __future__ import annotations

import time

import pytest

from tavotto.engine import ai_providers, fixsession, prepsession
from tavotto.engine.agent_runner.redact import Redactor
from tavotto.engine.agent_runner.wires import ToolCall, Turn
from test_preparation_api import client, fake_pool  # noqa: F401 — 夹具复用
from test_preparation_session import _create_ready, sessions  # noqa: F401

KEY = "sk-" + "test-" + "CANARYkey1234567890"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    monkeypatch.setattr(prepsession.SESSIONS, "ai_connected", lambda: False)
    fixsession.SESSIONS.reset_for_tests()
    yield
    fixsession.SESSIONS.reset_for_tests()


class Scripted:
    def __init__(self, *steps):
        self.steps, self.calls = list(steps), 0

    def complete(self, **_kw) -> Turn:
        self.calls += 1
        if not self.steps:
            return Turn(text="done", stop="end")
        return Turn(
            tool_calls=[
                ToolCall(f"c{self.calls}_{i}", n, a) for i, (n, a) in enumerate(self.steps.pop(0))
            ],
            stop="tool_use",
        )


def connect(monkeypatch, model):
    ai_providers.save(
        {
            "id": "x",
            "label": "x",
            "agent": "claude",
            "base_url": "https://api.example.com",
            "api_key": KEY,
            "default_model": "m1",
        }
    )
    ai_providers.set_active("claude", "x")
    monkeypatch.setattr(fixsession, "build_model", lambda conn: (model, Redactor(conn["api_key"])))


def poll(client, fix_id, pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = client.get(f"/api/ai/fix/{fix_id}").get_json()
        if pred(body):
            return body
        time.sleep(0.02)
    raise AssertionError(f"timed out; last={body}")


def test_without_an_api_connection_the_endpoint_says_unavailable_and_capabilities_agree(client):
    resp = client.post("/api/ai/fix", json={"session_id": "s1", "script": "fig.py"})
    assert resp.status_code == 200 and resp.get_json() == {
        "ok": False,
        "reason": "ai_fix_unavailable",
    }
    assert client.get("/api/ai/capabilities").get_json()["ai_fix_available"] is False


def test_capabilities_say_available_once_an_api_connection_is_selected(client, monkeypatch):
    connect(monkeypatch, Scripted())
    body = client.get("/api/ai/capabilities?refresh=1").get_json()
    assert body["ai_fix_available"] is True
    assert KEY not in client.get("/api/ai/capabilities").get_data(as_text=True)


@pytest.mark.parametrize("body", [None, {}, {"session_id": "s"}, {"session_id": 1, "script": "x"}])
def test_endpoint_refuses_malformed_bodies(client, body):
    assert client.post("/api/ai/fix", json=body).status_code == 400


def test_unknown_preparation_session_is_a_session_error_not_a_fix(client, monkeypatch):
    connect(monkeypatch, Scripted())
    resp = client.post("/api/ai/fix", json={"session_id": "nope", "script": "fig.py"})
    assert resp.status_code in (404, 409) and resp.get_json().get("ok") is not True


def test_full_flow_start_card_answer_finish(client, tmp_path, fake_pool, sessions, monkeypatch):
    _root, _pj, report = _create_ready(client, tmp_path)
    model = Scripted(
        [("read_run_report", {})],
        [("ask_user", {"question": "数据在哪？", "options": [{"id": "a", "label": "A"}]})],
        [("finish", {"outcome": "fixed", "summary": "好了"})],
    )
    connect(monkeypatch, model)
    pokes: list[dict] = []
    from tavotto import app as m

    q = __import__("queue").Queue()
    m._sse_subs.append(q)
    try:
        resp = client.post(
            "/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"}
        )
        body = resp.get_json()
        assert resp.status_code == 200 and body["ok"] is True and body["fix_id"].startswith("fx_")
        fid = body["fix_id"]
        card = poll(client, fid, lambda b: b["state"] == "waiting")["card"]
        assert card["kind"] == "choice" and card["options"] == [{"id": "a", "label": "A"}]
        # 同一张准备会话再点 = 复用同一个修复会话；另一个并发请求 = 忙
        again = client.post(
            "/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"}
        )
        assert again.get_json() == {"ok": True, "fix_id": fid}
        # 坏回答 400；错 card id 409；对的才放行
        assert (
            client.post(
                f"/api/ai/fix/{fid}/answer", json={"card_id": card["id"], "answer": "zzz"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/ai/fix/{fid}/answer", json={"card_id": "bad", "answer": "a"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/ai/fix/{fid}/answer", json={"card_id": card["id"], "answer": "a"}
            ).status_code
            == 200
        )
        done = poll(client, fid, lambda b: b["state"] == "finished")
        assert done["outcome"] == "fixed" and done["summary"] == "好了"
        # SSE 只有「重读」提示：事件名 + 项目 + 会话 id，没有卡片内容
        while not q.empty():
            pokes.append(q.get_nowait())
    finally:
        m._sse_subs.remove(q)
    fix_events = [d for e, d in pokes if e == "ai.fix"]
    assert fix_events and all(set(d) == {"pj", "fix_id"} for d in fix_events)
    assert "数据在哪" not in repr(fix_events)


def test_cancel_endpoint_ends_the_session(client, tmp_path, fake_pool, sessions, monkeypatch):
    _root, _pj, report = _create_ready(client, tmp_path)
    connect(monkeypatch, Scripted([("propose_fix", {"summary": "x"})]))
    fid = client.post(
        "/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"}
    ).get_json()["fix_id"]
    poll(client, fid, lambda b: b["state"] == "waiting")
    assert client.post(f"/api/ai/fix/{fid}/cancel").status_code == 200
    assert poll(client, fid, lambda b: b["state"] == "finished")["outcome"] == "cancelled"


def test_unknown_fix_id_is_404_on_every_endpoint(client, tmp_path, fake_pool, sessions):
    _create_ready(client, tmp_path)  # 先有打开的项目（端点按项目 scope 找会话）
    assert client.get("/api/ai/fix/fx_nope").status_code == 404
    assert (
        client.post(
            "/api/ai/fix/fx_nope/answer", json={"card_id": "c", "answer": "yes"}
        ).status_code
        == 404
    )
    assert client.post("/api/ai/fix/fx_nope/cancel").status_code == 404
    assert client.post("/api/ai/fix/fx_nope/answer", data="x").status_code == 400


@pytest.mark.parametrize("bad", ["/etc/passwd.py", "../../outside.py", "other.py", ""])
def test_script_must_match_the_preparation_session_and_stay_in_the_project(
    client, tmp_path, fake_pool, sessions, monkeypatch, bad
):
    _root, _pj, report = _create_ready(client, tmp_path)
    connect(monkeypatch, Scripted())
    resp = client.post("/api/ai/fix", json={"session_id": report["session_id"], "script": bad})
    assert resp.status_code == 400 and resp.get_json()["code"] == "fix_script_mismatch"
    assert fixsession.SESSIONS.active() is None


def test_a_symlinked_script_escaping_the_project_is_refused(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    import os

    root, _pj, report = _create_ready(client, tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"print(1)\n")
    target = root / "fig.py"
    target.unlink()
    try:
        os.symlink(outside, target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    connect(monkeypatch, Scripted())
    resp = client.post("/api/ai/fix", json={"session_id": report["session_id"], "script": "fig.py"})
    assert resp.status_code == 400 and resp.get_json()["code"] == "fix_script_outside_project"
    assert fixsession.SESSIONS.active() is None
