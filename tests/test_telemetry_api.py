"""本地遥测 API 与 AI 埋点的敏感边界。

这里守的是**结构性**的保证：不是「我们记得不要发提示词」，而是「提示词
在白名单面前根本发不出去」。
"""

from __future__ import annotations

import json

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import telemetry


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    return m.app.test_client()


@pytest.fixture
def project(tmp_path):
    """一个最小项目：一张 PDF 面板 + 一个登记好的脚本。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1_保密项目.pdf")
    doc.close()
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1_保密项目"]}
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    m.open_project(str(figs))
    yield figs
    m.reset_projects()


# ---------------------------------------------------------------------------
# /api/telemetry/settings
# ---------------------------------------------------------------------------
def test_settings_never_leaks_the_install_id(client, telemetry_sent):
    body = client.get("/api/telemetry/settings").get_json()
    assert body["consent"] == "enabled" and body["enabled"] is True
    ident = telemetry.install_id()
    assert ident
    blob = json.dumps(body)
    assert ident not in blob
    assert "install_id" not in body


def test_patch_settings_round_trip(client, monkeypatch):
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    monkeypatch.setattr(telemetry, "_post", lambda payload: None)
    assert client.get("/api/telemetry/settings").get_json()["consent"] == "unset"

    body = client.patch(
        "/api/telemetry/settings", json={"consent": "enabled", "source": "first_run"}
    ).get_json()
    assert body["consent"] == "enabled" and body["enabled"] is True

    body = client.patch("/api/telemetry/settings", json={"consent": "disabled"}).get_json()
    assert body["consent"] == "disabled" and body["enabled"] is False
    telemetry.reset_for_tests()


def test_patch_settings_rejects_garbage(client):
    assert client.patch("/api/telemetry/settings", json={"consent": "maybe"}).status_code == 400


def test_settings_reports_hard_disable(client, monkeypatch):
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    body = client.get("/api/telemetry/settings").get_json()
    assert body["hard_disabled"] is True and body["enabled"] is False


# ---------------------------------------------------------------------------
# /api/telemetry/event
# ---------------------------------------------------------------------------
def test_event_endpoint_accepts_allowlisted_event(client, telemetry_sent):
    resp = client.post(
        "/api/telemetry/event",
        json={"event": "figure_opened", "properties": {"asset_kind": "pdf", "editable": True}},
    )
    assert resp.status_code == 200 and resp.get_json()["accepted"] is True
    assert telemetry.flush(5.0)
    assert [p["event"] for p in telemetry_sent] == ["figure_opened"]


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "user_script_uploaded", "properties": {}},
        {"event": "figure_opened", "properties": {"filename": "Fig1.pdf"}},
        {"event": "figure_opened", "properties": {"asset_kind": "/Users/me/figs"}},
        {"event": "figure_edit_completed", "properties": {"patch_count": {"a": 1}}},
        {"event": 42, "properties": {}},
        {"event": "figure_opened", "properties": "not-an-object"},
    ],
)
def test_event_endpoint_rejects_anything_off_the_allowlist(client, telemetry_sent, payload):
    resp = client.post("/api/telemetry/event", json=payload)
    assert resp.status_code == 400
    # 拒绝时不回显收到了什么——那正是不该被记录、也不该被回声出去的东西
    assert "Fig1.pdf" not in resp.get_data(as_text=True)
    assert "/Users/me" not in resp.get_data(as_text=True)
    assert telemetry.flush(5.0)
    assert telemetry_sent == []


def test_event_endpoint_sends_nothing_without_consent(client, monkeypatch):
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    telemetry.reset_for_tests()
    box: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", box.append)
    resp = client.post(
        "/api/telemetry/event",
        json={"event": "canvas_created", "properties": {"creation_kind": "blank"}},
    )
    assert resp.status_code == 200 and resp.get_json()["accepted"] is False
    assert telemetry.flush(5.0)
    assert box == []
    telemetry.reset_for_tests()


# ---------------------------------------------------------------------------
# AI：最敏感的一条边界
# ---------------------------------------------------------------------------
SECRET_PROMPT = (
    "把 /Users/me/secret/论文数据.csv 里的第三条曲线改成红色，"
    "顺便用我的 API key sk-abcdef0123456789"
)


def _run_ai(client, agent="codex", **extra):
    return client.post(
        "/api/ai/run",
        json={
            "agent": agent,
            "id": "Fig1_保密项目.pdf",
            "prompt": SECRET_PROMPT,
            "gid": "axes_0.lines_2",
            "label": "第三条曲线",
            "scope": "element",
            "target": "/Users/me/secret/论文数据.csv",
            "canvas": "投稿版 Figure 3",
            **extra,
        },
    )


def test_ai_invocation_never_transmits_the_prompt(client, project, telemetry_sent, monkeypatch):
    monkeypatch.setattr(m.engine_ai, "run", lambda *a, **kw: "sess-123")
    assert _run_ai(client).status_code == 200
    assert telemetry.flush(5.0)
    (event,) = [p for p in telemetry_sent if p["event"] == "ai_assistant_invoked"]
    assert event["properties"]["agent"] == "codex"
    blob = json.dumps(telemetry_sent, ensure_ascii=False)
    for forbidden in (
        SECRET_PROMPT,
        "sk-abcdef0123456789",
        "论文数据.csv",
        "/Users/me/secret",
        "Fig1_保密项目",
        "fig1.py",
        "axes_0.lines_2",
        "第三条曲线",
        "投稿版 Figure 3",
        "sess-123",
        "element",
    ):
        assert forbidden not in blob, f"{forbidden} 泄漏进了遥测事件"


def test_ai_agent_name_is_an_enum_not_free_text(client, project, telemetry_sent, monkeypatch):
    monkeypatch.setattr(m.engine_ai, "run", lambda *a, **kw: "sess-9")
    assert _run_ai(client, agent="我自己的 agent").status_code == 200
    assert telemetry.flush(5.0)
    (event,) = [p for p in telemetry_sent if p["event"] == "ai_assistant_invoked"]
    assert event["properties"]["agent"] == "other"


def test_failed_ai_start_captures_nothing(client, project, telemetry_sent, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("codex 没装")

    monkeypatch.setattr(m.engine_ai, "run", boom)
    assert _run_ai(client).status_code == 500
    assert telemetry.flush(5.0)
    assert [p for p in telemetry_sent if p["event"] == "ai_assistant_invoked"] == []


def test_ai_start_survives_telemetry_failure(client, project, telemetry_sent, monkeypatch):
    monkeypatch.setattr(m.engine_ai, "run", lambda *a, **kw: "sess-7")
    monkeypatch.setattr(telemetry, "_post", lambda _p: (_ for _ in ()).throw(OSError("代理挂了")))
    monkeypatch.setattr(
        telemetry, "validate", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("埋点炸了"))
    )
    resp = _run_ai(client)
    assert resp.status_code == 200
    assert resp.get_json()["session"] == "sess-7"
