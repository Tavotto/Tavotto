"""第三方接口预设与协议（`engine/ai_providers.py`）。

事实起点：Codex 新版已移除 `wire_api = "chat"`（读到就整个启动失败），国产厂商基本只有
Anthropic 兼容端点。所以：预设里没有 chat、国产一律走 Claude Code、已存的旧 chat 记录
不能静默坏着（后端标不可用 + spawn 前拒绝 + 能对上厂商就一步迁移）。
"""

import pytest

from tavotto.engine import ai_agents, ai_bridge, ai_providers, config


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    ai_bridge.invalidate_capabilities()
    yield
    ai_bridge.invalidate_capabilities()


def _legacy(pid, base_url, *, key="sk-legacy", wire="chat", agent="codex"):
    """直接写配置，模拟 `_clean` 之前存下的旧记录（走 save 会被规范化掉）。"""
    rec = {
        "id": pid,
        "label": pid,
        "agent": agent,
        "base_url": base_url,
        "api_key": key,
        "models": ["old-model"],
        "default_model": None,
        "wire_api": wire,
    }
    config.set_ai_settings({"providers": [*ai_providers.list_providers(), rec]})
    return rec


# ---- 结构性：任何地方都产不出 chat ------------------------------------------


def test_wire_apis_has_no_chat():
    assert ai_providers.WIRE_APIS == ("responses",)


def test_no_preset_uses_chat_and_no_domestic_vendor_is_on_codex():
    for p in ai_providers.PRESETS:
        assert p.get("wire_api", "responses") != "chat", p["id"]
        if p["agent"] == "codex":
            # codex 只许官方与自定义 Responses 兼容；国产厂商没有 Responses 协议
            assert p["id"] in ("openai", "custom-codex"), p["id"]
    ids = {p["id"] for p in ai_providers.PRESETS}
    assert not ids & {"deepseek-oai"}
    assert {"kimi-code", "minimax", "dashscope", "dashscope-coding"} <= ids
    by_id = {p["id"]: p for p in ai_providers.PRESETS}
    assert by_id["dashscope"]["agent"] == "claude"
    assert by_id["dashscope"]["base_url"] == "https://dashscope.aliyuncs.com/apps/anthropic"
    assert by_id["deepseek"]["models"] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert by_id["custom-codex"]["wire_api"] == "responses"


@pytest.mark.parametrize("wire", ["chat", "", None, "garbage", "responses"])
def test_clean_never_outputs_chat(wire):
    out = ai_providers._clean({"agent": "codex", "label": "x", "wire_api": wire})
    assert out["wire_api"] == "responses"
    saved = ai_providers.save({"id": "x", "agent": "codex", "label": "x", "wire_api": wire})
    assert all(p["wire_api"] == "responses" for p in saved)


@pytest.mark.parametrize("stored", ["chat", "responses", None, "weird"])
def test_spawn_overrides_never_emits_chat(stored):
    rec = {"agent": "codex", "base_url": "https://gw.example/v1", "api_key": "k", "label": "g"}
    if stored is not None:
        rec["wire_api"] = stored
    args, _env = ai_providers.spawn_overrides("codex", rec)
    joined = " ".join(args)
    assert 'wire_api="responses"' in joined
    assert '"chat"' not in joined


# ---- 不可用标记与 run 拒绝 ---------------------------------------------------


def test_public_flags_legacy_chat_record_and_migratability():
    known = _legacy("ds", "https://api.deepseek.com/v1")
    custom = _legacy("mine", "https://gw.example/v1")
    ok = _legacy("fine", "https://gw.example/v1", wire="responses")
    claude = _legacy("c", "https://api.deepseek.com/anthropic", wire="chat", agent="claude")
    assert ai_providers.public(known)["incompatible"] == "codex_chat_removed"
    assert ai_providers.public(known)["migratable"] is True
    assert ai_providers.public(custom)["incompatible"] == "codex_chat_removed"
    assert ai_providers.public(custom)["migratable"] is False
    assert ai_providers.public(ok)["incompatible"] is None
    assert ai_providers.public(claude)["incompatible"] is None  # chat 只对 codex 是问题
    assert "api_key" not in ai_providers.public(known)


def test_run_refuses_selected_legacy_endpoint_before_spawn(monkeypatch, tmp_path):
    _legacy("old", "https://api.deepseek.com/v1")
    ai_providers.set_active("codex", "old")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    spawned = []
    monkeypatch.setattr(ai_bridge.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.run("codex", "fig.py", "p", str(tmp_path))
    assert exc.value.code == "ai_endpoint_incompatible"
    assert exc.value.params["endpoint"] == "old"
    assert spawned == []
    # 显式指定同样拒绝
    ai_providers.set_active("codex", None)
    with pytest.raises(ai_bridge.AgentError) as exc2:
        ai_bridge.run("codex", "fig.py", "p", str(tmp_path), endpoint_id="old")
    assert exc2.value.code == "ai_endpoint_incompatible"
    assert spawned == []


def test_error_funnel_maps_incompatible_to_409():
    from tavotto import app as m

    with m.app.test_request_context():
        resp = m._agent_error(ai_bridge.AgentError("ai_endpoint_incompatible", {"endpoint": "old"}))
    assert resp[1] == 409 and resp[0].get_json()["code"] == "ai_endpoint_incompatible"


# ---- 迁移 --------------------------------------------------------------------


def test_migrate_keeps_key_clears_codex_active_and_leaves_claude_active():
    _legacy("dash", "https://dashscope.aliyuncs.com/compatible-mode/v1", key="sk-keep-me")
    ai_providers.save({"id": "my-claude", "agent": "claude", "label": "mine", "api_key": "k"})
    ai_providers.set_active("codex", "dash")
    ai_providers.set_active("claude", "my-claude")

    new = ai_providers.migrate_to_claude("dash")

    assert new["agent"] == "claude"
    assert new["api_key"] == "sk-keep-me"
    assert new["base_url"] == "https://dashscope.aliyuncs.com/apps/anthropic"
    assert new["models"] == ["qwen3.7-plus", "qwen3.8-max"]
    assert ai_providers.get("dash") is None
    assert ai_providers.active_id("codex") is None  # 清回官方登录
    assert ai_providers.active_id("claude") == "my-claude"  # 不替用户激活
    assert ai_providers.incompatibility(new) is None


def test_migrate_does_not_overwrite_an_existing_record_with_the_target_id():
    _legacy("old", "https://api.deepseek.com/v1", key="sk-old")
    ai_providers.save({"id": "deepseek", "agent": "claude", "label": "mine", "api_key": "sk-mine"})
    new = ai_providers.migrate_to_claude("old")
    assert new["id"] != "deepseek"
    assert ai_providers.get("deepseek")["api_key"] == "sk-mine"
    assert ai_providers.get("deepseek")["label"] == "mine"


def test_migrate_refuses_unknown_vendor_and_compatible_records():
    _legacy("mine", "https://gw.example/v1")
    _legacy("coding", "https://coding.dashscope.aliyuncs.com/v1")  # 另一套 Key，不算按量厂商
    _legacy("fine", "https://api.deepseek.com/v1", wire="responses")
    for pid in ("mine", "coding", "fine", "nope"):
        with pytest.raises(ValueError):
            ai_providers.migrate_to_claude(pid)
    assert ai_providers.get("mine") is not None  # 失败时一个字节没动


def test_migrate_endpoint_http():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    _legacy("ds", "https://api.deepseek.com/v1", key="sk-z")
    body = client.post("/api/ai/endpoints/ds/migrate").get_json()
    assert not any(e["id"] == "ds" for e in body["endpoints"])
    assert any(e["agent"] == "claude" and e["has_key"] for e in body["endpoints"])
    bad = client.post("/api/ai/endpoints/ds/migrate")
    assert bad.status_code == 400
