"""「连接 AI」后端（`engine/ai_connections.py`）：厂商目录、连接视图、路由、管理接口，以及内置执行器接进改图执行路径。

判据的主语（先说清）：

* 视图：`/api/ai/capabilities` 返回的 `vendors` / `connections` 里**用户能看到的事实**（一家一条、谁出现、谁 ready、走哪条路）；
  旧字段（`agents[]` 只有两家 CLI）一个不变——旧前端会把多出来的 agent 当成执行器画出来。
* 路由：`(CLI 装没装, ai.builtin_default, TAVOTTO_AI_BUILTIN)` 的完整矩阵 × 记录家族，断言的是 `resolve_connection` 的结论，
  且**直接解析到那条 provider**，不看 `active_provider`。
* 内置执行器整链：真子进程（`python -m tavotto agent-run`）+ 127.0.0.1 上的假模型服务；断言磁盘上的脚本、历史库、事件流、
  假服务器**真的收到了**密钥（先证明金丝雀到过，才能说「别处没有」），再断言 argv / 事件 / 历史库 / 日志里没有。
* 管理接口：错 body 一律 400 且什么都没保存；密钥不在任何响应 / 日志里；订阅类不可删。

**绝不联网**：`ai_catalog.fetch_models` 被换成桩，假模型服务只听 127.0.0.1。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tavotto import app as appmod, security
from tavotto.engine import (
    ai_agents,
    ai_bridge,
    ai_catalog,
    ai_connections,
    ai_figure_edit,
    ai_history,
    ai_oauth,
    ai_providers,
    ai_tasks,
    config,
    fixsession,
)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
KEY = "sk-conn-CANARY-4f2a9c"
KEY2 = "sk-conn-CANARY-77b1de"


# --------------------------------------------------------------------------- 公共夹具
@pytest.fixture(autouse=True)
def _world(monkeypatch, tmp_path_factory):
    """两家 CLI 默认「装了且能启动」（用例里 `world["cli"]` 翻成 False = 一家都没装）；不读真实的 ~/.codex ~/.claude；
    型号清单的网络请求换成桩（默认拿不到：`network`）。"""
    state = {"cli": True, "fetch": (None, "network"), "fetched": []}
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: (
            [ai_agents.CliCandidate(f"/usr/bin/{agent.id}", "path")] if state["cli"] else []
        ),
    )
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "test 0.0.0")
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-home")))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path_factory.mktemp("codex-home")))
    monkeypatch.delenv(ai_connections.ENV_BUILTIN, raising=False)
    monkeypatch.delenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", raising=False)

    def fake_fetch(base_url, api_key):
        state["fetched"].append((base_url, api_key))
        return state["fetch"]

    monkeypatch.setattr(ai_catalog, "fetch_models", fake_fetch)
    ai_bridge.invalidate_capabilities()
    yield state
    ai_bridge.invalidate_capabilities()


@pytest.fixture
def world(_world):
    return _world


@pytest.fixture
def client():
    appmod.app.config["TESTING"] = True
    appmod.reset_projects()
    yield appmod.app.test_client()
    appmod.reset_projects()


def _caps(refresh=True) -> dict:
    return ai_bridge.capabilities(refresh=refresh)


def _vendor(caps: dict, vid: str) -> dict | None:
    return next((v for v in caps["vendors"] if v["id"] == vid), None)


def _conn(caps: dict, cid: str) -> dict | None:
    return next((c for c in caps["connections"] if c["id"] == cid), None)


def _save(pid="k1", agent="claude", base="https://api.deepseek.com/anthropic", key=KEY, **kw):
    return ai_providers.save(
        {
            "id": pid,
            "label": kw.pop("label", pid),
            "agent": agent,
            "base_url": base,
            "api_key": key,
            "models": kw.pop("models", ["m1", "m2"]),
            "default_model": kw.pop("default_model", "m1"),
            **kw,
        }
    )


# --------------------------------------------------------------------------- vendors
def test_vendors_merge_rows_of_the_same_brand_into_one(world):
    caps = _caps()
    ids = [v["id"] for v in caps["vendors"]]
    assert len(ids) == len(set(ids))
    # DeepSeek 在目录里有 claude 行与 codex 行（deepseek / deepseek-codex）：界面上只有一家，且 id 取首选的 Anthropic 行
    assert "deepseek" in ids and "deepseek-codex" not in ids
    assert "kimi-code" in ids and "kimi-code-codex" not in ids
    # 独立算一遍期望：不同 brand 的数量（不含官方登录行与自定义行）+ 1 个 custom
    rows = ai_catalog.load_vendors()
    brands = {r["brand"] for r in rows if r["region"] != "custom" and r["base_url"]}
    keyed = [v for v in caps["vendors"] if v["connect"] == "key"]
    assert len(keyed) == len(brands) + 1
    assert [v["id"] for v in keyed].count("custom") == 1
    ds = _vendor(caps, "deepseek")
    assert ds["connect"] == "key" and ds["region"] == "cn" and ds["logo"] == "deepseek"
    assert ds["key_url"].startswith("https://") and ds["available"] is True
    assert ds["label"] == "DeepSeek" and ds["label_key"] == "vendor.deepseek"
    # 官方登录行（base_url 为空）不是 Key 厂商——它们由订阅类承担
    assert "anthropic" not in ids and "openai" not in ids


def test_each_vendor_has_the_contract_fields_and_a_known_connect_kind(world):
    for v in _caps()["vendors"]:
        assert {"id", "label", "logo", "region", "connect", "available"} <= set(v)
        assert v["connect"] in ("key", "login", "oauth")
        assert v["region"] in ("cn", "global", "custom")
        assert v["logo"]


def test_subscription_vendors_follow_whether_the_cli_is_installed(world):
    caps = _caps()
    chatgpt, claude = _vendor(caps, "chatgpt"), _vendor(caps, "claude")
    assert (chatgpt["connect"], chatgpt["logo"], chatgpt["available"]) == ("login", "openai", True)
    assert (claude["connect"], claude["logo"], claude["available"]) == ("login", "anthropic", True)
    world["cli"] = False
    ai_bridge.invalidate_capabilities()
    caps = _caps()
    assert _vendor(caps, "chatgpt")["available"] is False  # 仍列出（前端另给安装提示），但不可用
    assert _vendor(caps, "claude")["available"] is False


def test_oauth_vendor_only_appears_when_a_client_id_is_configured(world, monkeypatch):
    assert _vendor(_caps(), "minimax-oauth") is None
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    v = _vendor(_caps(), "minimax-oauth")
    assert v["connect"] == "oauth" and v["logo"] == "minimax" and v["available"] is True


def test_vendor_order_follows_the_system_language(world, monkeypatch):
    monkeypatch.setattr(ai_connections, "locale_region", lambda: "cn")
    cn = _caps()
    assert cn["vendors"][0]["region"] == "cn" and cn["locale_region"] == "cn"
    monkeypatch.setattr(ai_connections, "locale_region", lambda: "global")
    glob = _caps()
    assert glob["vendors"][0]["connect"] == "login" and glob["locale_region"] == "global"
    assert glob["vendors"][-1]["id"] == "custom"


def test_locale_region_reads_the_environment(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert ai_connections.locale_region() == "cn"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert ai_connections.locale_region() == "global"


def test_legacy_agents_list_never_contains_the_builtin_executor(world):
    caps = _caps()
    assert [a["id"] for a in caps["agents"]] == ["codex", "claude"]
    assert ai_agents.agent_ids() == ("codex", "claude")
    assert ai_agents.get_agent(ai_agents.BUILTIN_ID) is not None  # 找得到，但不是用户可选的 Agent
    assert ai_agents.BUILTIN_ID not in json.dumps(
        [a["id"] for a in caps["agents"]] + list(caps["endpoints"])
    )
    for old in ("endpoints", "presets", "oauth", "checked_at_ms"):
        assert old in caps  # 旧字段一个不丢


# --------------------------------------------------------------------------- connections
def test_subscription_connections_come_from_the_installed_clis(world):
    caps = _caps()
    codex, claude = _conn(caps, "sub:codex"), _conn(caps, "sub:claude")
    assert (codex["vendor"], codex["route"], codex["state"]) == ("chatgpt", "cli:codex", "ready")
    assert (claude["vendor"], claude["route"], claude["state"]) == ("claude", "cli:claude", "ready")
    world["cli"] = False
    ai_bridge.invalidate_capabilities()
    assert _caps()["connections"] == []  # 没装 CLI、没存 Key：没有任何连接


def test_needs_login_is_reported_for_a_subscription(world, monkeypatch):
    monkeypatch.setattr(
        ai_agents.ClaudeAgent,
        "readiness",
        lambda self, argv: ai_agents.ReadinessResult("needs_auth", "x"),
    )
    monkeypatch.setattr(ai_agents.ClaudeAgent, "supports_readiness_probe", True)
    ai_bridge.invalidate_capabilities()
    assert _conn(_caps(), "sub:claude")["state"] == "needs_auth"


def test_several_key_connections_are_ready_at_the_same_time(world):
    _save("a", base="https://api.deepseek.com/anthropic", key=KEY)
    _save(
        "b",
        base="https://api.moonshot.cn/anthropic",
        key=KEY2,
        models=["kimi-k3"],
        default_model="kimi-k3",
    )
    _save("c", base="https://api.deepseek.com/anthropic", key=KEY2, label="备用")
    # 旧版的「每家族只激活一个」不再限制连接：什么都不激活，三条也都 ready
    caps = _caps()
    a, b, c = _conn(caps, "a"), _conn(caps, "b"), _conn(caps, "c")
    assert [x["state"] for x in (a, b, c)] == ["ready"] * 3
    assert (a["vendor"], b["vendor"]) == ("deepseek", "moonshot")
    assert a["logo"] == "deepseek" and b["label"] == "Kimi"
    assert a["label"] != c["label"], "同厂商两条要能分清"
    assert b["models"] == ["kimi-k3"] and b["default_model"] == "kimi-k3"
    assert KEY not in json.dumps(caps) and KEY2 not in json.dumps(caps)


def test_codex_family_record_maps_to_the_same_vendor(world):
    _save("cx", agent="codex", base="https://api.deepseek.com", wire_api="responses")
    assert _conn(_caps(), "cx")["vendor"] == "deepseek"


def test_unknown_address_is_a_custom_connection(world):
    _save("mine", base="https://llm.example.org/anthropic")
    c = _conn(_caps(), "mine")
    assert c["vendor"] == "custom" and c["logo"] == ai_catalog.GENERIC_ICON_KEY


def test_connection_without_a_key_needs_auth_and_legacy_empty_address_is_hidden(world):
    _save("nokey", key="")
    _save("official", base="", key=KEY)
    caps = _caps()
    assert _conn(caps, "nokey")["state"] == "needs_auth"
    assert _conn(caps, "official") is None


def test_incompatible_legacy_record_is_an_error_connection(world):
    _save("old", agent="codex", base="https://api.deepseek.com", wire_api="chat")
    # _clean 已把 chat 纠正成 responses；直接写一条旧形状进配置
    config.update_ai_settings(
        lambda ai: {
            "providers": [
                {**p, "wire_api": "chat"} if p["id"] == "old" else p for p in ai["providers"]
            ]
        }
    )
    c = _conn(_caps(), "old")
    assert c["state"] == "error" and c["error"] == ai_providers.INCOMPATIBLE_CODEX_CHAT


def test_oauth_record_is_a_connection_routed_to_the_cli_only(world, monkeypatch):
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    ai_providers.store_oauth(
        ai_oauth._record_defaults("minimax", "cn"),
        {"provider": "minimax", "access": "tok-secret", "refresh": "r", "expires_ms": 9 * 10**12},
    )
    caps = _caps()
    c = _conn(caps, "minimax-oauth")
    assert c["vendor"] == "minimax-oauth" and c["state"] == "ready" and c["route"] == "cli:claude"
    assert "tok-secret" not in json.dumps(caps)
    world["cli"] = False
    ai_bridge.invalidate_capabilities()
    c = _conn(_caps(), "minimax-oauth")
    assert c["state"] == "error" and c["error"] == "cli_unavailable"  # Bearer 令牌内置循环不走


# --------------------------------------------------------------------------- 路由矩阵
def _route(rec_id: str) -> str:
    return _conn(_caps(), rec_id)["route"]


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("cli", [True, False])
@pytest.mark.parametrize("default_builtin", [False, True])
@pytest.mark.parametrize("env", [None, "0", "1"])
def test_routing_matrix(world, monkeypatch, agent, cli, default_builtin, env):
    base = "https://api.deepseek.com/anthropic" if agent == "claude" else "https://api.deepseek.com"
    _save("k", agent=agent, base=base)
    world["cli"] = cli
    config.set_ai_settings({"builtin_default": default_builtin})
    if env is not None:
        monkeypatch.setenv(ai_connections.ENV_BUILTIN, env)
    ai_bridge.invalidate_capabilities()
    if env == "1":
        want = "builtin"
    elif env == "0":
        want = f"cli:{agent}" if cli else None
    elif cli and not default_builtin:
        want = f"cli:{agent}"
    else:
        want = "builtin"
    c = _conn(_caps(), "k")
    if want is None:
        assert c["state"] == "error" and c["error"] == "cli_unavailable"
        with pytest.raises(ai_connections.ConnectionRefused) as ei:
            ai_bridge.resolve_connection("k")
        assert ei.value.code == "ai_agent_not_usable"
        return
    assert c["route"] == want and c["state"] == "ready"
    got_agent, rec = ai_bridge.resolve_connection("k")
    assert got_agent == ("builtin" if want == "builtin" else agent) and rec["id"] == "k"


def test_no_cli_means_the_builtin_executor_is_used_by_default(world):
    _save("k")
    world["cli"] = False
    assert _route("k") == "builtin"
    assert ai_bridge.resolve_connection("k")[0] == "builtin"


def test_insecure_gateway_falls_back_to_the_cli_when_builtin_cannot_take_it(world):
    _save("k", base="http://gateway.example.org/anthropic")
    config.set_ai_settings({"builtin_default": True})
    assert _route("k") == "cli:claude"  # 内置循环只收 https / 本机；CLI 照旧能接
    world["cli"] = False
    c = _conn(_caps(), "k")
    assert c["state"] == "error" and c["error"] == "base_url_scheme"


def test_connection_resolves_to_its_own_provider_not_the_active_one(world):
    _save("a")
    _save("b", base="https://api.moonshot.cn/anthropic")
    ai_providers.set_active("claude", "a")
    agent, rec = ai_bridge.resolve_connection("b")
    assert (agent, rec["id"]) == ("claude", "b")
    assert ai_bridge.resolve_connection("sub:claude") == ("claude", None)
    assert ai_bridge.resolve_connection("sub:codex") == ("codex", None)
    with pytest.raises(ai_connections.ConnectionRefused) as ei:
        ai_bridge.resolve_connection("nope")
    assert (ei.value.code, ei.value.status) == ("connection_not_found", 404)


# --------------------------------------------------------------------------- POST /api/ai/connections
def _post(client, **body):
    return client.post("/api/ai/connections", json=body)


def test_post_saves_a_key_connection_and_never_echoes_the_key(client, world, caplog):
    caplog.set_level(logging.DEBUG)
    world["fetch"] = (["deepseek-v4-pro", "deepseek-flash", "other"], None)
    resp = _post(client, vendor="deepseek", api_key=f"  {KEY} ")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    conn = body["connection"]
    assert body["ok"] is True and conn["state"] == "ready" and conn["vendor"] == "deepseek"
    assert conn["models"] == ["deepseek-v4-pro", "deepseek-flash", "other"]
    assert conn["default_model"] == "deepseek-v4-pro" and conn["route"] == "cli:claude"
    assert world["fetched"] == [
        ("https://api.deepseek.com/anthropic", KEY)
    ]  # 试取用的是去空白后的 Key
    stored = ai_providers.get(conn["id"])
    assert stored["api_key"] == KEY and stored["agent"] == "claude"
    # 密钥不在响应、不在 capabilities、不在旧 endpoints 视图、不在日志
    for blob in (
        resp.get_data(as_text=True),
        client.get("/api/ai/capabilities").get_data(as_text=True),
        caplog.text,
    ):
        assert KEY not in blob
    # 返回的连接 id 就是 capabilities 里那条
    assert _conn(_caps(), conn["id"])["state"] == "ready"


def test_post_failing_to_list_models_still_saves_but_reports_an_error(client, world):
    world["fetch"] = (None, "network")
    conn = _post(client, vendor="moonshot", api_key=KEY).get_json()["connection"]
    assert conn["state"] == "error" and conn["error"] == "network"
    assert conn["models"] == ["kimi-k3", "kimi-k2.7-code"]  # 退回目录里的型号提示
    assert ai_providers.get(conn["id"])["api_key"] == KEY
    # 断网是暂时的：不落盘，下一次读取照常 ready
    assert _conn(_caps(), conn["id"])["state"] == "ready"


def test_post_rejected_key_is_remembered_as_an_error(client, world):
    world["fetch"] = (None, "unauthorized")
    conn = _post(client, vendor="zhipu", api_key=KEY).get_json()["connection"]
    assert conn["state"] == "error" and conn["error"] == "unauthorized"
    assert _conn(_caps(), conn["id"])["error"] == "unauthorized"
    # 用新 Key 更新同一条（带 id）→ 错误清掉
    world["fetch"] = (["glm-5.3"], None)
    again = _post(client, vendor="zhipu", api_key=KEY2, id=conn["id"]).get_json()["connection"]
    assert again["id"] == conn["id"] and again["state"] == "ready"
    assert ai_providers.get(conn["id"])["api_key"] == KEY2
    assert len(ai_providers.list_providers()) == 1


def test_post_twice_for_one_vendor_makes_two_connections(client, world):
    first = _post(client, vendor="deepseek", api_key=KEY).get_json()["connection"]
    second = _post(client, vendor="deepseek", api_key=KEY2, label="备用").get_json()["connection"]
    assert first["id"] != second["id"]
    caps = _caps()
    assert _conn(caps, first["id"])["state"] == _conn(caps, second["id"])["state"] == "ready"
    assert _conn(caps, first["id"])["label"] != _conn(caps, second["id"])["label"]


def test_post_custom_vendor_needs_base_url_and_wire(client, world):
    ok = _post(
        client,
        vendor="custom",
        api_key=KEY,
        base_url="https://llm.example.org/v1",
        wire="openai-responses",
        model="my-model",
    )
    conn = ok.get_json()["connection"]
    assert (
        ok.status_code == 200 and conn["default_model"] == "my-model" and conn["vendor"] == "custom"
    )
    stored = ai_providers.get(conn["id"])
    assert stored["agent"] == "codex" and stored["wire_api"] == "responses"
    anth = _post(
        client, vendor="custom", api_key=KEY, base_url="https://llm.example.org", wire="anthropic"
    )
    assert ai_providers.get(anth.get_json()["connection"]["id"])["agent"] == "claude"


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "x",
        {},
        {"vendor": "deepseek"},
        {"vendor": "deepseek", "api_key": ""},
        {"vendor": "deepseek", "api_key": 5},
        {"vendor": "deepseek", "api_key": "has space"},
        {"vendor": "deepseek", "api_key": "line\nbreak"},
        {"vendor": "deepseek", "api_key": "k" * 5000},
        {"vendor": "nope", "api_key": KEY},
        {"vendor": 3, "api_key": KEY},
        {"vendor": "chatgpt", "api_key": KEY},
        {"vendor": "claude", "api_key": KEY},
        {"vendor": "minimax-oauth", "api_key": KEY},
        {"vendor": "custom", "api_key": KEY},
        {"vendor": "custom", "api_key": KEY, "base_url": "https://x.example.org"},
        {"vendor": "custom", "api_key": KEY, "wire": "anthropic"},
        {"vendor": "custom", "api_key": KEY, "base_url": "https://x.example.org", "wire": "chat"},
        {"vendor": "deepseek", "api_key": KEY, "base_url": "http://evil.example.org"},
        {"vendor": "deepseek", "api_key": KEY, "base_url": "ftp://x"},
        {"vendor": "deepseek", "api_key": KEY, "base_url": 7},
        {"vendor": "deepseek", "api_key": KEY, "model": ["x"]},
        {"vendor": "deepseek", "api_key": KEY, "model": "m" * 300},
        {"vendor": "deepseek", "api_key": KEY, "label": 1},
        {"vendor": "deepseek", "api_key": KEY, "id": "nope"},
        {"vendor": "deepseek", "api_key": KEY, "id": 4},
    ],
)
def test_post_refuses_malformed_bodies_and_saves_nothing(client, world, body):
    resp = client.post("/api/ai/connections", json=body)
    j = resp.get_json()
    assert resp.status_code in (400, 404), j
    assert j["code"] in ("endpoint_invalid", "connection_not_found")
    assert ai_providers.list_providers() == []
    assert world["fetched"] == [], "校验没过就不该碰网络（更不该把 Key 发出去）"
    assert KEY not in resp.get_data(as_text=True)


def test_post_with_a_garbage_body_is_a_400(client, world):
    resp = client.post("/api/ai/connections", data="not json", content_type="application/json")
    assert resp.status_code == 400 and resp.get_json()["code"] == "endpoint_invalid"


def test_post_accepts_a_loopback_gateway(client, world):
    world["fetch"] = (["m"], None)
    conn = _post(
        client, vendor="custom", api_key=KEY, base_url="http://127.0.0.1:8123", wire="anthropic"
    ).get_json()["connection"]
    assert conn["state"] == "ready" and conn["route"] == "cli:claude"


# --------------------------------------------------------------------------- DELETE / login
def test_delete_removes_a_key_connection_and_clears_its_active_flag(client, world):
    _save("a")
    ai_providers.set_active("claude", "a")
    resp = client.delete("/api/ai/connections/a")
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert ai_providers.get("a") is None and ai_providers.active_id("claude") is None
    assert _conn(_caps(), "a") is None


def test_delete_refuses_subscriptions_and_unknown_ids(client, world):
    resp = client.delete("/api/ai/connections/sub:claude")
    assert resp.status_code == 409 and resp.get_json()["code"] == "connection_not_deletable"
    assert _conn(_caps(), "sub:claude") is not None
    resp = client.delete("/api/ai/connections/ghost")
    assert resp.status_code == 404 and resp.get_json()["code"] == "connection_not_found"


def test_login_for_a_subscription_starts_the_official_login(client, world, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(
        ai_bridge,
        "start_login",
        lambda agent: started.append(agent) or {"started": True, "mode": "background"},
    )
    resp = client.post("/api/ai/connections/sub:codex/login")
    assert resp.status_code == 200 and resp.get_json() == {
        "ok": True,
        "started": True,
        "mode": "background",
    }
    assert started == ["codex"]
    client.post("/api/ai/connections/sub:claude/login")
    assert started == ["codex", "claude"]


def test_login_errors_use_the_agent_error_funnel(client, world, monkeypatch):
    def boom(agent):
        raise ai_bridge.AgentError("ai_agent_not_installed", {"agent": agent})

    monkeypatch.setattr(ai_bridge, "start_login", boom)
    resp = client.post("/api/ai/connections/sub:codex/login")
    assert resp.status_code == 409 and resp.get_json()["code"] == "ai_agent_not_installed"


def test_login_for_oauth_returns_a_flow_id_and_needs_a_client_id(client, world, monkeypatch):
    assert client.post("/api/ai/connections/minimax-oauth/login").status_code == 404
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    seen = []
    flow = {"flow_id": "f1", "provider": "minimax", "state": "waiting", "user_code": "ABCD"}
    monkeypatch.setattr(
        ai_oauth, "start", lambda provider, region=None: seen.append((provider, region)) or flow
    )
    resp = client.post("/api/ai/connections/minimax-oauth/login", json={"region": "global"})
    assert resp.status_code == 200 and resp.get_json()["flow_id"] == "f1"
    assert seen == [("minimax", "global")]
    # 普通 Key 连接没有登录这回事
    _save("a")
    assert client.post("/api/ai/connections/a/login").status_code == 404


def test_connection_endpoints_reject_requests_without_a_session(monkeypatch):
    state, _nonce = security.new_browser_state(5055)
    monkeypatch.setitem(appmod.app.config, security.STATE_KEY, state)
    c = appmod.app.test_client()
    for method, path in (
        ("POST", "/api/ai/connections"),
        ("DELETE", "/api/ai/connections/a"),
        ("POST", "/api/ai/connections/sub:codex/login"),
    ):
        resp = c.open(path, method=method, json={}, base_url="http://127.0.0.1:5055")
        assert resp.status_code == 401, (method, path)
        assert resp.get_json()["code"] == "session_auth_required"
    assert ai_providers.list_providers() == []


# --------------------------------------------------------------------------- 旧接口兼容 + /api/ai/run 的 connection
@pytest.fixture
def project(tmp_path, monkeypatch):
    import pymupdf

    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1.pdf")
    doc.close()
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}
                },
            }
        ),
        encoding="utf-8",
    )
    appmod.reset_projects()
    appmod.open_project(str(figs))
    calls: list[dict] = []

    def fake_run(agent, script, prompt, figures_dir, **kw):
        calls.append({"agent": agent, **kw})
        return "s1"

    monkeypatch.setattr(appmod.engine_ai, "run", fake_run)
    yield calls
    appmod.reset_projects()


def _run_req(client, **extra):
    return client.post("/api/ai/run", json={"id": "Fig1.pdf", "prompt": "p", **extra})


def test_run_with_a_connection_resolves_agent_endpoint_and_model(client, world, project):
    _save("a", models=["m1", "m2"], default_model="m2")
    _save("other", base="https://api.moonshot.cn/anthropic")
    ai_providers.set_active("claude", "other")  # 旧的 active 不参与
    assert _run_req(client, connection="a").status_code == 200
    call = project[-1]
    assert (call["agent"], call["endpoint_id"], call["model"]) == ("claude", "a", "m2")
    # 请求里显式给的型号优先
    _run_req(client, connection="a", model="m1")
    assert project[-1]["model"] == "m1"
    # 订阅类：明确用官方登录态（endpoint ""，不是「没指定」），型号交给 CLI
    _run_req(client, connection="sub:codex")
    assert (project[-1]["agent"], project[-1]["endpoint_id"], project[-1]["model"]) == (
        "codex",
        "",
        None,
    )


def test_run_with_a_connection_routes_to_builtin_when_no_cli(client, world, project):
    _save("a")
    world["cli"] = False
    ai_bridge.invalidate_capabilities()
    assert _run_req(client, connection="a").status_code == 200
    assert (project[-1]["agent"], project[-1]["endpoint_id"]) == ("builtin", "a")


def test_run_with_a_bad_connection_is_refused_before_spawning(client, world, project):
    r = _run_req(client, connection="ghost")
    assert r.status_code == 404 and r.get_json()["code"] == "connection_not_found"
    assert _run_req(client, connection=5).status_code == 400
    _save("a")
    world["cli"] = False
    os.environ[ai_connections.ENV_BUILTIN] = "0"
    try:
        ai_bridge.invalidate_capabilities()
        r = _run_req(client, connection="a")
        assert r.status_code == 409 and r.get_json()["code"] == "ai_agent_not_usable"
    finally:
        del os.environ[ai_connections.ENV_BUILTIN]
    assert project == []


def test_run_without_a_connection_keeps_the_legacy_fields(client, world, project):
    _save("a")
    _run_req(client, agent="claude", endpoint="a", model="mm")
    call = project[-1]
    assert (call["agent"], call["endpoint_id"], call["model"]) == ("claude", "a", "mm")
    _run_req(client)
    assert (project[-1]["agent"], project[-1]["endpoint_id"]) == ("codex", None)


def test_legacy_endpoint_api_still_works(client, world):
    client.put(
        "/api/ai/endpoints",
        json={
            "id": "x",
            "label": "x",
            "agent": "claude",
            "base_url": "https://api.example.com",
            "api_key": KEY,
        },
    )
    caps = client.get("/api/ai/capabilities?refresh=1").get_json()
    assert [e["id"] for e in caps["endpoints"]] == ["x"] and "api_key" not in caps["endpoints"][0]
    assert _conn(caps, "x") is not None  # 旧接口存的也是一条连接
    client.delete("/api/ai/endpoints/x")
    assert ai_providers.list_providers() == []


# --------------------------------------------------------------------------- AI 修复的连接挑选
def test_fix_connection_takes_the_first_ready_key_connection(world, monkeypatch):
    monkeypatch.setattr(ai_bridge, "agent_caps", lambda _id: None)
    assert fixsession.connection() is None
    _save("nokey", key="")
    _save("http", base="http://gw.example.org/anthropic")
    ai_providers.store_oauth(
        ai_oauth._record_defaults("minimax", "cn"),
        {"provider": "minimax", "access": "tok", "refresh": "r", "expires_ms": 9 * 10**12},
    )
    assert fixsession.connection() is None  # 没 Key / 明文 http / 账号登录：都不算
    _save("good", base="https://api.moonshot.cn/anthropic", key=KEY2, default_model="kimi-k3")
    conn = fixsession.connection()
    assert conn == {
        "wire": "anthropic",
        "base_url": "https://api.moonshot.cn/anthropic",
        "api_key": KEY2,
        "model": "kimi-k3",
    }
    # 什么都没「选中」也算连着；老用户选中的那条仍排在最前
    _save("second", base="https://api.deepseek.com/anthropic", key=KEY)
    assert fixsession.connection()["api_key"] == KEY2
    ai_providers.set_active("claude", "second")
    assert fixsession.connection()["api_key"] == KEY
    # codex 家族的 Responses 记录也能用
    _save("cx", agent="codex", base="https://api.deepseek.com", default_model="deepseek-flash")
    ai_providers.delete("second")
    ai_providers.delete("good")
    assert fixsession.connection()["wire"] == "openai-responses"


def test_ai_fix_available_follows_the_connection_view(client, world, monkeypatch):
    monkeypatch.setattr(ai_bridge, "agent_caps", lambda _id: None)  # 只看 API 连接这一半
    assert client.get("/api/ai/capabilities?refresh=1").get_json()["ai_fix_available"] is False
    _save("a")
    assert client.get("/api/ai/capabilities?refresh=1").get_json()["ai_fix_available"] is True


# --------------------------------------------------------------------------- 内置执行器：命令形状
def test_builtin_command_puts_the_key_in_the_environment_only(world):
    rec = _save("k")[0]
    prompt = "PROMPT-CANARY 把线改成蓝色"
    cmd, env = ai_bridge._cmd(
        "builtin", prompt, "/tmp/cand", model="mm", endpoint=rec, target="fig1.py"
    )
    assert cmd[cmd.index("--wire") + 1] == "anthropic"
    assert cmd[cmd.index("--base-url") + 1] == "https://api.deepseek.com/anthropic"
    assert (
        cmd[cmd.index("--cwd") + 1] == "/tmp/cand" and cmd[cmd.index("--target") + 1] == "fig1.py"
    )
    assert cmd[cmd.index("--model") + 1] == "mm" and "agent-run" in cmd
    assert KEY not in " ".join(cmd) and prompt not in " ".join(cmd)
    assert env == {ai_agents.BUILTIN_KEY_ENV: KEY}


def test_builtin_key_env_name_is_the_one_the_runner_reads():
    from tavotto.engine.agent_runner import __main__ as runner

    assert ai_agents.BUILTIN_KEY_ENV == runner.ENV_API_KEY


def test_builtin_command_for_codex_family_uses_the_responses_wire(world):
    rec = _save("cx", agent="codex", base="https://api.deepseek.com")[0]
    cmd, _env = ai_bridge._cmd("builtin", "p", "/c", endpoint=rec, target="a.py")
    assert cmd[cmd.index("--wire") + 1] == "openai-responses"
    assert cmd[cmd.index("--model") + 1] == "m1"  # 没指定型号 = 记录的默认型号


@pytest.mark.parametrize(
    "mutate",
    [
        {"api_key": ""},
        {"base_url": "http://gw.example.org"},
        {"base_url": "https://u:p@x.example.org"},
        {"models": [], "default_model": None},
    ],
)
def test_builtin_refuses_an_unusable_record_before_spawning(world, mutate):
    rec = {**_save("k")[0], **mutate}
    with pytest.raises(ai_bridge.AgentError) as ei:
        ai_bridge._cmd("builtin", "p", "/c", endpoint=rec, target="a.py")
    assert ei.value.code == "ai_agent_not_usable"
    with pytest.raises(ai_bridge.AgentError):
        ai_bridge._cmd("builtin", "p", "/c", endpoint=None, target="a.py")


def test_require_usable_for_builtin_means_there_is_a_key_connection(world, monkeypatch):
    with pytest.raises(ai_bridge.AgentError) as ei:
        ai_bridge.require_usable("builtin")
    assert ei.value.code == "ai_agent_not_usable"
    _save("k")
    assert ai_bridge.require_usable("builtin")["usable"] is True
    monkeypatch.setenv(ai_connections.ENV_BUILTIN, "0")
    with pytest.raises(ai_bridge.AgentError):
        ai_bridge.require_usable("builtin")


def test_launcher_prefix_is_shared_with_the_mcp_launcher(monkeypatch, tmp_path):
    assert ai_agents.tavotto_launcher() == [sys.executable, "-m", "tavotto"]
    assert fixsession.mcp_launcher() == [*ai_agents.tavotto_launcher(), "fix-mcp"]
    exe = tmp_path / "Tavotto.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert ai_agents.tavotto_launcher() == [str(exe)]  # 同目录没有 console 版：退回自己
    from tavotto.engine import locate

    cli = tmp_path / locate.CLI_NAME
    cli.write_text("", encoding="utf-8")
    assert ai_agents.tavotto_launcher() == [str(cli)]
    assert fixsession.mcp_launcher() == [str(cli), "fix-mcp"]


def test_agent_run_is_a_registered_subcommand_and_really_runs(tmp_path):
    from tavotto.engine import cli as engine_cli

    assert "agent-run" in engine_cli.COMMANDS
    cand = tmp_path / "cand"
    cand.mkdir()
    (cand / "fig.py").write_text('plt.plot(color="red")\n', encoding="utf-8")
    script = tmp_path / "demo.json"
    script.write_text(
        json.dumps(
            {
                "turns": [
                    {"tool_calls": [{"name": "read_file", "args": {"path": "fig.py"}}]},
                    {
                        "tool_calls": [
                            {"name": "edit_file", "args": {"old": '"red"', "new": '"blue"'}}
                        ]
                    },
                    {"text": "done"},
                ]
            }
        ),
        encoding="utf-8",
    )
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "tavotto",
            "agent-run",
            "--cwd",
            str(cand),
            "--target",
            "fig.py",
            "--wire",
            "demo",
            "--demo-script",
            str(script),
        ],
        input="make it blue",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(SRC)},
        timeout=60,
    )
    events = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
    assert p.returncode == 0 and events[-1]["type"] == "done" and events[-1]["ok"] is True
    assert '"blue"' in (cand / "fig.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- 内置执行器整链（真子进程 + 本机假模型服务）
SCRIPT = """import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 4, 9])
    ax.set_title("old")
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""

READ_TURN = (
    200,
    {},
    {
        "content": [
            {"type": "tool_use", "id": "toolu_0", "name": "read_file", "input": {"path": "fig1.py"}}
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 40, "output_tokens": 8},
    },
)
EDIT_TURN = (
    200,
    {},
    {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "edit_file",
                "input": {"old": '"old"', "new": '"new"'},
            }
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 50, "output_tokens": 10},
    },
)
END_TURN = (
    200,
    {},
    {
        "content": [{"type": "text", "text": "标题已改。"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 60, "output_tokens": 5},
    },
)


class FakeModel:
    """127.0.0.1 上的假 Anthropic 服务：按序回放（最后一条重复）；条目是 callable 时先执行它（用来阻塞）。记录路径与头。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[tuple[str, dict]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                item = outer.responses[min(len(outer.requests), len(outer.responses)) - 1]
                if callable(item):
                    item()
                    item = (500, {}, {})
                status, _h, payload = item
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def chain(world, monkeypatch, tmp_path):
    """真 `ai_bridge.run("builtin", …)`：TAF 候选工作区 + 真子进程 + 假模型服务；记录每次 spawn 的 argv / 环境。"""
    for n in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(n, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snap")
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    monkeypatch.setattr(
        ai_bridge, "_spawn_env", lambda cmd0, extra: {**os.environ, "PYTHONPATH": str(SRC), **extra}
    )
    config.set_ai_settings({"taf": {"enabled": True}})
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    spawned: list[tuple[list[str], dict]] = []
    real_popen = ai_bridge._popen

    def spy(cmd, cwd, env, stderr_log, stdin_text=None):
        spawned.append((list(cmd), dict(env)))
        return real_popen(cmd, cwd, env, stderr_log, stdin_text)

    monkeypatch.setattr(ai_bridge, "_popen", spy)
    servers: list[FakeModel] = []

    def serve(responses):
        s = FakeModel(responses)
        servers.append(s)
        _save("k", base=s.url, key=KEY, default_model="m1")
        return s

    box = {"figs": figs, "spawned": spawned, "serve": serve}
    yield box
    for s in servers:
        s.close()
    ai_tasks.unregister(ai_figure_edit.TASK_TYPE)


def _run(box, **kw):
    events: list = []
    sid = ai_bridge.run(
        "builtin",
        "fig1.py",
        "把标题改成 new",
        str(box["figs"]),
        on_event=lambda n, d: events.append((n, d)),
        on_changed=lambda script: {"registry": {}, "assets": {}, "published": []},
        endpoint_id=kw.pop("endpoint_id", "k"),
        **kw,
    )
    return sid, events


def _wait(sid, timeout=60.0):
    pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
    if pump is not None:
        pump.join(timeout)
        assert not pump.is_alive(), "pump 线程没有收尾"
    return ai_bridge.SESSIONS[sid]


def test_builtin_edits_a_candidate_through_the_normal_session_lifecycle(chain, caplog):
    caplog.set_level(logging.DEBUG)
    server = chain["serve"]([READ_TURN, EDIT_TURN, END_TURN])
    sid, events = _run(chain)
    sess = _wait(sid)
    figs = chain["figs"]
    assert sess["status"] == "done" and sess["changed"] is True, sess.get("transcript")
    assert '"new"' in (figs / "fig1.py").read_text(encoding="utf-8"), (
        "过了提交前检查的候选写回了项目"
    )
    # 事件流：动作 + 模型的话，走和 CLI 同一套 ai.delta / ai.done
    kinds = [(d["kind"], d["text"]) for n, d in events if n == "ai.delta"]
    assert any(k == "action" and "edit_file" in t for k, t in kinds)
    assert ("message", "标题已改。") in kinds
    done = next(d for n, d in events if n == "ai.done")
    assert done["status"] == "done" and done["changed"] is True
    # 历史库与 TAF：provider = builtin，任务走 figure_edit 的候选 / 校验 / 提交
    row = ai_history.get(sid)
    assert row["provider"] == "builtin" and row["task_type"] == "figure_edit"
    assert row["status"] == "done" and row["outcome"] == "partial"
    assert not any(Path(p).exists() for p in (ai_figure_edit.CANDIDATE_DIR.glob("*")))
    # 回滚沿用改前快照
    ai_bridge.revert(sid)
    assert (figs / "fig1.py").read_text(encoding="utf-8") == SCRIPT
    # —— 密钥：先证明金丝雀到过假服务器（在头里），再断言别处没有 ——
    assert len(server.requests) == 3 and server.requests[0][0] == "/v1/messages"
    assert server.requests[0][1].get("x-api-key") == KEY
    (argv, env) = chain["spawned"][0]
    assert KEY not in " ".join(argv) and env[ai_agents.BUILTIN_KEY_ENV] == KEY
    assert "把标题改成" not in " ".join(argv), "提示词走 stdin，不进 argv"
    blob = json.dumps(row, default=str) + json.dumps(sess["transcript"]) + caplog.text
    blob += (ai_bridge.SNAP_DIR / f"{sid}.stderr.log").read_text(encoding="utf-8", errors="replace")
    blob += json.dumps([d for _, d in events], default=str)
    assert KEY not in blob


def test_builtin_bad_key_fails_cleanly_and_leaves_the_project_alone(chain):
    chain["serve"]([(401, {}, {"error": {"type": "authentication_error"}})])
    sid, events = _run(chain)
    sess = _wait(sid)
    assert sess["status"] == "failed" and sess["changed"] is False
    assert (chain["figs"] / "fig1.py").read_text(encoding="utf-8") == SCRIPT
    assert any("密钥无效" in d["text"] for n, d in events if n == "ai.delta")
    assert ai_history.get(sid)["outcome"] == "failed"


def test_builtin_cancel_stops_the_subprocess_and_frees_the_script(chain):
    release = threading.Event()
    server = chain["serve"]([lambda: release.wait(30)])
    sid, _events = _run(chain)
    deadline = time.monotonic() + 20
    while not server.requests and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.requests, "判据的前提：请求已在飞"
    proc = ai_bridge.SESSIONS[sid]["proc"]
    assert ai_bridge.cancel(sid) is True
    release.set()
    sess = _wait(sid)
    assert sess["status"] == "cancelled"
    assert proc.poll() is not None, "子进程已退出"
    assert (chain["figs"] / "fig1.py").read_text(encoding="utf-8") == SCRIPT
    assert ai_history.get(sid)["outcome"] == "cancelled"
    assert not ai_bridge.script_busy(chain["figs"] / "fig1.py")


def test_builtin_requires_an_explicit_usable_connection(chain):
    chain["serve"]([END_TURN])
    with pytest.raises(ai_bridge.AgentError) as ei:
        _run(chain, endpoint_id=None)  # 没指定 = 内置执行器没有「当前选中」
    assert ei.value.code == "ai_agent_not_usable"
    with pytest.raises(ai_bridge.AgentError):
        _run(chain, endpoint_id="ghost")
    assert chain["spawned"] == []
    assert (chain["figs"] / "fig1.py").read_text(encoding="utf-8") == SCRIPT


def test_builtin_does_not_take_conversion_tasks(chain):
    chain["serve"]([END_TURN])
    with pytest.raises(ai_bridge.AgentError) as ei:
        _run(chain, convert={"library": "x"})
    assert ei.value.code == "ai_agent_not_usable" and chain["spawned"] == []
