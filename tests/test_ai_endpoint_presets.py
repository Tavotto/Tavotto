"""第三方接口预设与协议（`engine/ai_providers.py`）。

事实起点：Codex 新版已移除 `wire_api = "chat"`（读到就整个启动失败），国产厂商基本只有
Anthropic 兼容端点。所以：预设里没有 chat、国产一律走 Claude Code、已存的旧 chat 记录
不能静默坏着（后端标不可用 + spawn 前拒绝 + 能对上厂商就一步迁移）。
"""

import json

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
            # （DeepSeek 官方原生支持 Responses，是唯一的厂商例外；百炼的 Responses 地址带
            # 用户的 WorkspaceId，推不出来，所以不加）
            assert p["id"] in (
                "openai",
                "custom-codex",
                "deepseek-codex",
                "kimi-code-codex",
            ), p["id"]
    ids = {p["id"] for p in ai_providers.PRESETS}
    assert not ids & {"deepseek-oai"}
    assert {"kimi-code", "minimax", "dashscope", "dashscope-coding"} <= ids
    by_id = {p["id"]: p for p in ai_providers.PRESETS}
    assert by_id["dashscope"]["agent"] == "claude"
    assert by_id["dashscope"]["base_url"] == "https://dashscope.aliyuncs.com/apps/anthropic"
    assert by_id["deepseek"]["models"] == ["deepseek-v4-pro", "deepseek-flash"]
    kc = by_id["kimi-code-codex"]
    assert kc["agent"] == "codex" and kc["wire_api"] == "responses"
    assert kc["base_url"] == "https://api.kimi.com/coding/v1"
    ds = by_id["deepseek-codex"]
    assert ds["agent"] == "codex" and ds["wire_api"] == "responses"
    assert ds["base_url"] == "https://api.deepseek.com"
    assert ds["models"] == ["deepseek-flash", "deepseek-v4-pro"]
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
    assert ai_providers.public(known)["migratable"] == "codex_responses"
    dash = _legacy("dash0", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert ai_providers.public(dash)["migratable"] == "claude"
    assert ai_providers.public(custom)["incompatible"] == "codex_chat_removed"
    assert ai_providers.public(custom)["migratable"] is None
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
    _legacy("old", "https://dashscope.aliyuncs.com/compatible-mode/v1", key="sk-old")
    ai_providers.save({"id": "dashscope", "agent": "claude", "label": "mine", "api_key": "sk-mine"})
    new = ai_providers.migrate("old")
    assert new["id"] != "dashscope"
    assert ai_providers.get("dashscope")["api_key"] == "sk-mine"
    assert ai_providers.get("dashscope")["label"] == "mine"


def test_migrate_to_claude_is_one_atomic_config_write(monkeypatch):
    _legacy("dash", "https://dashscope.aliyuncs.com/compatible-mode/v1", key="sk-keep-me")
    ai_providers.set_active("codex", "dash")
    real = config.update_ai_settings
    calls: list[dict] = []

    def counting(fn):
        def spy(ai):
            patch = fn(ai)
            calls.append(patch)
            return patch

        return real(spy)

    monkeypatch.setattr(config, "update_ai_settings", counting)
    new = ai_providers.migrate_to_claude("dash")
    assert len(calls) == 1
    assert set(calls[0]) == {"providers", "active_provider"}
    assert new["agent"] == "claude" and ai_providers.get("dash") is None
    assert ai_providers.active_id("codex") is None


def _migrate_with_failing_write(fail_from: int, monkeypatch):
    _legacy("dash", "https://dashscope.aliyuncs.com/compatible-mode/v1", key="sk-keep-me")
    ai_providers.set_active("codex", "dash")
    before = json.dumps(config.ai_settings(), sort_keys=True)
    real = config.update_ai_settings
    n = {"c": 0}

    def flaky(fn):
        n["c"] += 1
        if n["c"] >= fail_from:
            raise OSError("disk full")
        return real(fn)

    raised = False
    with monkeypatch.context() as mp:
        mp.setattr(config, "update_ai_settings", flaky)
        try:
            ai_providers.migrate_to_claude("dash")
        except OSError:
            raised = True
    return before, raised


def test_migrate_to_claude_write_failure_changes_nothing(monkeypatch):
    before, raised = _migrate_with_failing_write(1, monkeypatch)
    assert raised
    assert json.dumps(config.ai_settings(), sort_keys=True) == before
    assert ai_providers.get("dash") is not None
    assert ai_providers.active_id("codex") == "dash"
    assert not [p for p in ai_providers.list_providers() if p["agent"] == "claude"]


def test_migrate_to_claude_never_leaves_a_half_migrated_state(monkeypatch):
    """第 2 次写起才失败：分两次写的实现会留下「新 claude 记录 + 旧 codex 记录」并存。
    一次写的实现第 1 次就整体完成——状态只能是「原样」或「整体迁完」。"""
    before, raised = _migrate_with_failing_write(2, monkeypatch)
    if raised:
        assert json.dumps(config.ai_settings(), sort_keys=True) == before
    else:
        assert ai_providers.get("dash") is None
        assert [p["agent"] for p in ai_providers.list_providers()] == ["claude"]
        assert ai_providers.active_id("codex") is None


def test_migrate_refuses_unknown_vendor_and_compatible_records():
    _legacy("mine", "https://gw.example/v1")
    _legacy("coding", "https://coding.dashscope.aliyuncs.com/v1")  # 另一套 Key，不算按量厂商
    _legacy("fine", "https://api.deepseek.com/v1", wire="responses")
    for pid in ("mine", "coding", "fine", "nope"):
        with pytest.raises(ValueError):
            ai_providers.migrate(pid)
    with pytest.raises(ValueError):  # DeepSeek 记录不走「改由 Claude Code」入口
        ai_providers.migrate_to_claude("mine")
    assert ai_providers.get("mine") is not None  # 失败时一个字节没动


def test_migrate_endpoint_http():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    _legacy("ds", "https://dashscope.aliyuncs.com/compatible-mode/v1", key="sk-z")
    body = client.post("/api/ai/endpoints/ds/migrate").get_json()
    assert not any(e["id"] == "ds" for e in body["endpoints"])
    assert any(e["agent"] == "claude" and e["has_key"] for e in body["endpoints"])
    bad = client.post("/api/ai/endpoints/ds/migrate")
    assert bad.status_code == 400


# ---- DeepSeek：支持 Responses，原地迁移留在 Codex ---------------------------------


def test_deepseek_codex_preset_injects_official_responses_url():
    preset = next(p for p in ai_providers.PRESETS if p["id"] == "deepseek-codex")
    saved = ai_providers.save({**preset, "api_key": "sk-ds"})
    rec = next(p for p in saved if p["id"] == "deepseek-codex")
    args, env = ai_providers.spawn_overrides("codex", rec)
    joined = " ".join(args)
    assert 'base_url="https://api.deepseek.com"' in joined  # 无结尾斜杠，Codex 拼 /responses
    assert 'wire_api="responses"' in joined
    assert env == {ai_providers.CODEX_KEY_ENV: "sk-ds"}


def test_migrate_deepseek_in_place_keeps_id_key_and_codex_activation():
    _legacy("ds", "https://api.deepseek.com/v1", key="sk-keep")
    rec = ai_providers.get("ds")
    rec["models"] = ["deepseek-chat", "deepseek-reasoner", "mine"]
    rec["default_model"] = "deepseek-chat"
    config.set_ai_settings({"providers": [rec]})
    ai_providers.set_active("codex", "ds")

    new = ai_providers.migrate("ds")

    assert new["id"] == "ds" and new["agent"] == "codex"
    assert new["api_key"] == "sk-keep"
    assert new["base_url"] == "https://api.deepseek.com"
    assert new["wire_api"] == "responses"
    assert new["default_model"] == "deepseek-flash"
    assert "deepseek-chat" not in new["models"] and "deepseek-reasoner" not in new["models"]
    assert "mine" in new["models"] and "deepseek-v4-pro" in new["models"]
    assert ai_providers.active_id("codex") == "ds"  # 激活状态保留
    assert len(ai_providers.list_providers()) == 1  # 没有新建、没有删除
    assert ai_providers.incompatibility(new) is None
    assert ai_providers.public(new)["migratable"] is None


def test_run_dedupes_before_endpoint_compat_check(monkeypatch, tmp_path):
    """已受理请求的重发，即使接口事后变得不兼容也得到既有会话（ADR 0132），而不是 409。"""
    _legacy("old", "https://api.deepseek.com/v1")
    ai_providers.set_active("codex", "old")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    monkeypatch.setattr(
        ai_bridge, "find_request", lambda pk, rid: {"status": "ok", "session": "S1"}
    )
    spawned = []
    monkeypatch.setattr(ai_bridge.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    (tmp_path / "fig.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(ai_bridge.DuplicateRequest):
        ai_bridge.run("codex", "fig.py", "p", str(tmp_path), client_request_id="rid-1")
    assert spawned == []
    # 没有既有记录 → 照旧 409
    monkeypatch.setattr(ai_bridge, "find_request", lambda pk, rid: None)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.run("codex", "fig.py", "p", str(tmp_path), client_request_id="rid-2")
    assert exc.value.code == "ai_endpoint_incompatible"
    assert spawned == []
