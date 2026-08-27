"""编码 Agent 的 HTTP 面：`/api/ai/agents/<id>/…` 的行为与稳定错误码。

会话认证（ADR 0008）由 `tests/test_browser_auth.py` 统一看护；这里盯的是
「未知 id 一律拒」「禁用的 Agent 派不了活」「验证不过不落盘」这几条。
"""

import json

import pytest

from tavotto.engine import ai_agents, ai_bridge, config


@pytest.fixture
def client(monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    ai_bridge.invalidate_capabilities()
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    yield m.app.test_client()
    ai_bridge.invalidate_capabilities()


def _installed(monkeypatch):
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate(f"/x/{agent.id}", "path")],
    )
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    ai_bridge.invalidate_capabilities()


def _agent(body, agent_id):
    return next(a for a in body["agents"] if a["id"] == agent_id)


def test_capabilities_shape(client, monkeypatch):
    _installed(monkeypatch)
    body = client.get("/api/ai/capabilities?refresh=1").get_json()
    assert set(body) == {"agents", "endpoints", "presets", "checked_at_ms"}
    assert isinstance(body["checked_at_ms"], int) and body["checked_at_ms"] > 0
    codex = _agent(body, "codex")
    for key in (
        "id",
        "display_name",
        "icon_key",
        "state",
        "installed",
        "enabled",
        "usable",
        "version",
        "executable_path",
        "path_override",
        "detection_source",
        "models",
        "default_model",
        "efforts",
        "default_effort",
        "endpoint",
        "active_endpoint_id",
        "features",
        "diagnostics",
    ):
        assert key in codex, key
    # 旧形状不再出现——不许悄悄留一份第二权威
    assert "providers" not in body and "settings" not in body
    assert "active" not in body


def test_unknown_agent_id_is_rejected_by_every_endpoint(client):
    for resp in (
        client.patch("/api/ai/agents/opencode", json={"enabled": True}),
        client.post("/api/ai/agents/opencode/install"),
        client.get("/api/ai/agents/opencode/install"),
    ):
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "ai_agent_unknown"


def test_toggle_enabled_returns_fresh_capabilities(client, monkeypatch):
    _installed(monkeypatch)
    client.get("/api/ai/capabilities?refresh=1")
    body = client.patch("/api/ai/agents/codex", json={"enabled": False}).get_json()
    codex = _agent(body, "codex")
    assert codex["enabled"] is False and codex["state"] == "disabled"
    assert codex["usable"] is False
    assert config.ai_agent_settings()["codex"]["enabled"] is False


def test_enabling_an_uninstalled_agent_is_refused(client, monkeypatch):
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])
    ai_bridge.invalidate_capabilities()
    resp = client.patch("/api/ai/agents/codex", json={"enabled": True})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "ai_agent_not_installed"


def test_invalid_path_override_is_refused_and_nothing_is_written(client, monkeypatch):
    _installed(monkeypatch)
    resp = client.patch("/api/ai/agents/codex", json={"path_override": "/definitely/not/here"})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "ai_agent_executable_invalid"
    assert "path_override" not in config.ai_agent_settings().get("codex", {})


def test_clearing_path_override_is_an_explicit_action(client, tmp_path, monkeypatch):
    exe = tmp_path / "codex"
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)  # 校验要求可执行位，fixture 得是真的
    monkeypatch.setattr(ai_agents, "probe_version_detailed", lambda argv: ("v1", None))
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: [
            ai_agents.CliCandidate(
                (override if override is not None else ai_agents.path_override(agent.id))
                or f"/x/{agent.id}",
                "custom" if (override or ai_agents.path_override(agent.id)) else "path",
            )
        ],
    )
    body = client.patch("/api/ai/agents/codex", json={"path_override": str(exe)}).get_json()
    assert _agent(body, "codex")["path_override"] == str(exe)
    body = client.patch("/api/ai/agents/codex", json={"path_override": ""}).get_json()
    assert _agent(body, "codex")["path_override"] is None


def test_run_refuses_a_disabled_agent(client, tmp_path, monkeypatch):
    """禁用只影响 Tavotto 用不用它——但这条判据必须在后端，
    不能只靠前端把它从选择器里藏掉。"""
    _installed(monkeypatch)
    client.get("/api/ai/capabilities?refresh=1")
    ai_bridge.set_agent_enabled("codex", False)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.run("codex", "fig.py", "prompt", str(tmp_path))
    assert exc.value.code == "ai_agent_disabled"


def test_telemetry_agent_falls_back_to_the_enum_not_the_registry(client, monkeypatch):
    """遥测白名单取自 EVENTS 表，不是注册表。

    注册表一加第三个 Agent，「在注册表里」就恒真，那个 id 会被原样透出，
    而 capture() 只收表里那几个值 → 该 Agent 的调用被静默丢弃。
    （PR #128 评审 P2）
    """
    from tavotto.engine import telemetry

    allowed = telemetry.EVENTS["ai_assistant_invoked"]["agent"]["values"]
    assert set(allowed) == {"codex", "claude", "other"}

    import inspect

    from tavotto import app as m

    src = inspect.getsource(m.api_ai_run)
    # 判据必须来自 EVENTS 表；拿注册表当白名单是被这条用例挡住的写法
    assert 'EVENTS["ai_assistant_invoked"]["agent"]["values"]' in src
    assert "agent_ids()" not in src


def test_install_endpoints_never_take_a_package_name(client, monkeypatch):
    """请求体里塞包名不该有任何效果——包名只从适配器取。"""
    started: list[str] = []
    monkeypatch.setattr(
        ai_bridge,
        "start_install",
        lambda agent_id: started.append(agent_id) or {"status": "running"},
    )
    resp = client.post(
        "/api/ai/agents/codex/install", json={"package": "evil-package", "agent": "claude"}
    )
    assert resp.status_code == 200 and started == ["codex"]


def test_install_status_is_per_agent(client, monkeypatch):
    monkeypatch.setattr(ai_bridge, "_INSTALLS", {"codex": {"status": "running"}})
    assert client.get("/api/ai/agents/codex/install").get_json()["status"] == "running"
    assert client.get("/api/ai/agents/claude/install").get_json()["status"] == "idle"


def test_capabilities_response_is_not_cached(client, monkeypatch):
    _installed(monkeypatch)
    resp = client.get("/api/ai/capabilities")
    assert resp.headers["Cache-Control"] == "no-store"


def test_diagnostics_check_ids_follow_the_registry(client, monkeypatch):
    _installed(monkeypatch)
    checks = client.get("/api/diagnostics").get_json()["checks"]
    ids = {c["id"] for c in checks}
    assert {"cli_codex", "cli_claude"} <= ids
    label = next(c["label"] for c in checks if c["id"] == "cli_claude")
    assert label == "Claude Code CLI"  # 显示名来自注册表，不再靠 capitalize()


def test_readiness_details_never_leak_account_info(client, monkeypatch):
    """就绪检查读到的账号信息（邮箱 / 组织 / 订阅档）一个字节都不出现在
    capabilities 里——那是 API 响应，会进诊断包、会被贴进 issue。"""
    _installed(monkeypatch)
    monkeypatch.setattr(
        ai_agents,
        "_run_probe",
        lambda argv, timeout=10: (
            0,
            json.dumps(
                {"loggedIn": True, "email": "someone@example.com", "orgName": "Someone's Org"}
            ),
        ),
    )
    ai_bridge.invalidate_capabilities()
    blob = client.get("/api/ai/capabilities?refresh=1").get_data(as_text=True)
    assert "example.com" not in blob and "Someone" not in blob
