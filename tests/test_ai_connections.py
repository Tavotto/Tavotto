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
from urllib.parse import urlparse

import pytest

from tavotto import app as appmod, security
from tavotto.engine import (
    ai_agents,
    ai_bridge,
    ai_catalog,
    ai_connections,
    ai_convert,
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


def _https_host(url: str) -> str | None:
    """URL 解析后的主机名（非 https 返回 None）：断言用解析比对，不用 startswith / in 的子串判断。"""
    parts = urlparse(url)
    return parts.hostname if parts.scheme == "https" else None


def _caps(refresh=True) -> dict:
    return ai_bridge.capabilities(refresh=refresh)


def _vendor(caps: dict, vid: str) -> dict | None:
    return next((v for v in caps["vendors"] if v["id"] == vid), None)


def _conn(caps: dict, cid: str) -> dict | None:
    return next((c for c in caps["connections"] if c["id"] == cid), None)


def _save(pid="k1", agent="claude", base="https://api.deepseek.com/anthropic", key=KEY, **kw):
    """存一条 provider → `[那一条]`（单元素列表：沿用 `_save(...)[0]` 的写法，但永远是刚存的这条，不是列表里的第一条）。"""
    ai_providers.save(
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
    return [ai_providers.get(pid)]


# --------------------------------------------------------------------------- vendors
def test_vendors_are_one_entry_per_vendor_with_their_access_methods(world):
    caps = _caps()
    ids = [v["id"] for v in caps["vendors"]]
    assert len(ids) == len(set(ids))
    # 同一家的 Claude 行与 Codex 行合成一个 Key 方式（DeepSeek 有 deepseek / deepseek-codex 两行）
    ds = _vendor(caps, "deepseek")
    assert [m["id"] for m in ds["methods"]] == ["deepseek:key"]
    assert "deepseek-codex" not in ids
    # 行上声明了 `vendor` 的方式并进那一家：每家一条
    assert "kimi-code" not in ids and "dashscope-coding" not in ids
    assert "anthropic-api" not in ids and "openai-api" not in ids
    assert "chatgpt" not in ids and "minimax-oauth" not in ids
    # 独立算一遍期望：厂商数 = 目录里 (vendor 字段 或 brand 首选行 id) 的去重数 + custom
    rows = [r for r in ai_catalog.load_vendors() if r["region"] != "custom" and r["base_url"]]
    primary: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["agent"] != "claude"):
        primary.setdefault(r["brand"], r)
    expect = {p.get("vendor") or p["id"] for p in primary.values()} | {"custom"}
    assert set(ids) == expect
    assert ds["connect"] == "key" and ds["region"] == "cn" and ds["logo"] == "deepseek"
    assert _https_host(ds["key_url"]) == "platform.deepseek.com" and ds["available"] is True
    assert ds["label"] == "DeepSeek" and ds["label_key"] == "vendor.deepseek"
    assert ds["methods"][0]["catalog_id"] == "deepseek" and ds["methods"][0]["kind"] == "key"
    assert ds["methods"][0]["label_key"] == "method.apiKey"


def test_vendors_merge_login_and_api_key_methods_of_one_vendor(world):
    caps = _caps()
    openai, anthropic = _vendor(caps, "openai"), _vendor(caps, "anthropic")
    # 订阅在前、API Key 在后
    assert [(m["id"], m["kind"]) for m in openai["methods"]] == [
        ("openai:login", "login"),
        ("openai-api:key", "key"),
    ]
    assert [(m["id"], m["kind"]) for m in anthropic["methods"]] == [
        ("anthropic:login", "login"),
        ("anthropic-api:key", "key"),
    ]
    login, key = openai["methods"]
    assert (login["agent"], login["connection_id"], login["label_key"]) == (
        "codex",
        "sub:codex",
        "method.subscription",
    )
    assert key["catalog_id"] == "openai-api"
    assert _https_host(key["key_url"]) == "platform.openai.com"
    assert (openai["label"], openai["logo"], openai["connect"]) == ("OpenAI", "openai", "login")
    assert (anthropic["label"], anthropic["logo"]) == ("Claude", "anthropic")
    assert openai["label_key"] is None and anthropic["label_key"] is None
    assert openai["key_url"] == key["key_url"], "旧字段取自第一个 Key 方式"
    # Kimi / 百炼：两种 Key 并成一家
    kimi, bailian = _vendor(caps, "moonshot"), _vendor(caps, "dashscope")
    assert [(m["catalog_id"], m["label_key"]) for m in kimi["methods"]] == [
        ("moonshot", "method.apiKey"),
        ("kimi-code", "method.kimiCode"),
    ]
    assert [(m["catalog_id"], m["label_key"]) for m in bailian["methods"]] == [
        ("dashscope", "method.apiKey"),
        ("dashscope-coding", "method.codingPlan"),
    ]
    assert kimi["label"] == "Kimi" and kimi["label_key"] == "vendor.kimi"
    assert any(m.get("key_hint_key") == "vendorKey.kimiCode" for m in kimi["methods"])
    # 不跨 region 合并
    assert (
        _vendor(caps, "minimax")["region"] == "cn"
        and _vendor(caps, "minimax-global")["region"] == "global"
    )
    assert _vendor(caps, "zai") is not None and _vendor(caps, "zhipu")["region"] == "cn"


def test_each_vendor_has_the_contract_fields_and_a_known_connect_kind(world):
    for v in _caps()["vendors"]:
        assert {"id", "label", "logo", "region", "connect", "available"} <= set(v)
        assert v["connect"] in ("key", "login", "oauth")
        assert v["region"] in ("cn", "global", "custom")
        assert v["logo"]


def test_login_method_availability_follows_whether_the_cli_is_installed(world):
    caps = _caps()
    for vid in ("openai", "anthropic"):
        v = _vendor(caps, vid)
        assert v["methods"][0]["available"] is True and v["available"] is True
    world["cli"] = False
    ai_bridge.invalidate_capabilities()
    caps = _caps()
    for vid in ("openai", "anthropic"):
        v = _vendor(caps, vid)
        assert v["methods"][0]["available"] is False  # 仍列出（前端另给安装提示），但不可用
        assert v["methods"][1]["available"] is True and v["available"] is True  # API Key 照样能连


def test_oauth_method_only_appears_when_a_client_id_is_configured(world, monkeypatch):
    assert [m["kind"] for m in _vendor(_caps(), "minimax")["methods"]] == ["key"]
    assert _vendor(_caps(), "minimax-oauth") is None
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    v = _vendor(_caps(), "minimax")
    assert [(m["id"], m["kind"]) for m in v["methods"]] == [
        ("minimax:oauth", "oauth"),
        ("minimax:key", "key"),
    ]
    oauth = v["methods"][0]
    assert oauth["connection_id"] == "minimax-oauth" and oauth["label_key"] == "method.account"
    assert (v["connect"], v["logo"], v["available"]) == ("oauth", "minimax", True)


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
    assert (codex["vendor"], codex["route"], codex["state"]) == ("openai", "cli:codex", "ready")
    assert (claude["vendor"], claude["route"], claude["state"]) == (
        "anthropic",
        "cli:claude",
        "ready",
    )
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


def test_oauth_record_prefers_the_cli_and_falls_back_to_the_builtin_executor(world, monkeypatch):
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    ai_providers.store_oauth(
        ai_oauth._record_defaults("minimax", "cn"),
        {"provider": "minimax", "access": "tok-secret", "refresh": "r", "expires_ms": 9 * 10**12},
    )
    caps = _caps()
    c = _conn(caps, "minimax-oauth")
    assert c["vendor"] == "minimax" and c["method"] == "minimax:oauth"
    assert c["state"] == "ready" and c["route"] == "cli:claude"
    assert "tok-secret" not in json.dumps(caps)
    world["cli"] = False  # 没装 CLI：账号登录的连接走内置执行器（Bearer 令牌）
    ai_bridge.invalidate_capabilities()
    c = _conn(_caps(), "minimax-oauth")
    assert c["state"] == "ready" and c["route"] == "builtin"
    assert ai_bridge.resolve_connection("minimax-oauth")[0] == "builtin"
    # 令牌没了（退出登录）→ 需要重新登录，不是 error
    ai_providers.update_oauth("minimax-oauth", None)
    assert _conn(_caps(), "minimax-oauth")["state"] == "needs_auth"


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
    assert fixsession.connection() is None  # 没 Key / 明文 http：都不算
    _save("good", base="https://api.moonshot.cn/anthropic", key=KEY2, default_model="kimi-k3")
    conn = fixsession.connection()
    assert conn == {
        "wire": "anthropic",
        "base_url": "https://api.moonshot.cn/anthropic",
        "api_key": KEY2,
        "model": "kimi-k3",
        "auth": "key",
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
        self.bodies: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.bodies.append(raw.decode("utf-8", errors="replace"))
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

    def spy(cmd, cwd, env, stderr_log, *rest):
        spawned.append((list(cmd), dict(env)))
        return real_popen(cmd, cwd, env, stderr_log, *rest)

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


# --------------------------------------------------------------------------- Codex 评审 #1404 的四条
def test_updating_with_another_origin_requires_a_fresh_key(client, world):
    world["fetch"] = (["m1"], None)
    cid = _post(client, vendor="deepseek", api_key=KEY).get_json()["connection"]["id"]
    world["fetched"].clear()
    # 换了来源又不给新密钥：拒绝，不发请求、不保存（旧密钥不能被送到新主机）
    resp = _post(client, vendor="deepseek", id=cid, base_url="https://evil.example.org/anthropic")
    assert resp.status_code == 400 and resp.get_json()["params"]["reason"] == "key_origin_mismatch"
    assert world["fetched"] == []
    assert ai_providers.get(cid)["base_url"] == "https://api.deepseek.com/anthropic"
    # 同源（换路径）可以复用旧密钥
    ok = _post(client, vendor="deepseek", id=cid, base_url="https://api.deepseek.com/other")
    assert ok.status_code == 200 and world["fetched"] == [("https://api.deepseek.com/other", KEY)]
    # 换来源并给了新密钥：只把新密钥发往新主机
    world["fetched"].clear()
    ok = _post(client, vendor="deepseek", id=cid, api_key=KEY2, base_url="https://gw.example.org/x")
    assert ok.status_code == 200 and world["fetched"] == [("https://gw.example.org/x", KEY2)]


def test_changing_the_key_keeps_the_chosen_model(client, world):
    world["fetch"] = (["a", "b", "c"], None)
    cid = _post(client, vendor="deepseek", api_key=KEY, model="b").get_json()["connection"]["id"]
    assert ai_providers.get(cid)["default_model"] == "b"
    again = _post(client, vendor="deepseek", id=cid, api_key=KEY2).get_json()["connection"]
    assert again["default_model"] == "b"
    assert (
        _post(client, vendor="deepseek", id=cid, api_key=KEY2, model="c").get_json()["connection"][
            "default_model"
        ]
        == "c"
    )


def _needs_cli_login(monkeypatch):
    monkeypatch.setattr(
        ai_agents.ClaudeAgent,
        "readiness",
        lambda self, argv: ai_agents.ReadinessResult("needs_auth", "x"),
    )
    monkeypatch.setattr(ai_agents.ClaudeAgent, "supports_readiness_probe", True)
    ai_bridge.invalidate_capabilities()


def test_oauth_connection_does_not_depend_on_the_clis_own_login(world, monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    _needs_cli_login(monkeypatch)
    ai_providers.store_oauth(
        ai_oauth._record_defaults("minimax", "cn"),
        {"provider": "minimax", "access": "tok", "refresh": "r", "expires_ms": 9 * 10**12},
    )  # 登录后没有被激活：旧判据下 CLI usable=false
    assert _caps()["agents"][1]["usable"] is False
    c = _conn(_caps(), "minimax-oauth")
    assert c["state"] == "ready" and c["route"] == "cli:claude"
    assert ai_bridge.resolve_connection("minimax-oauth")[0] == "claude"
    # run() 的可用性闸也按「所选连接自带凭据」放行：走到「脚本不存在」才停（还没 spawn）
    with pytest.raises(RuntimeError, match="脚本不存在"):
        ai_bridge.run("claude", "nope.py", "p", str(tmp_path), endpoint_id="minimax-oauth")
    # 没指定连接 = 只有 CLI 自己的登录态，照旧被拒
    with pytest.raises(ai_bridge.AgentError) as ei:
        ai_bridge.run("claude", "nope.py", "p", str(tmp_path))
    assert ei.value.code == "ai_agent_needs_auth"


def test_builtin_always_runs_in_a_candidate_even_when_taf_is_off(chain):
    config.set_ai_settings({"taf": {"enabled": False}})
    chain["serve"]([READ_TURN, EDIT_TURN, END_TURN])
    sid, _events = _run(chain)
    sess = _wait(sid)
    argv, _env = chain["spawned"][0]
    cwd = argv[argv.index("--cwd") + 1]
    assert Path(cwd).parent == ai_figure_edit.CANDIDATE_DIR, "子进程只拿到候选目录"
    assert str(chain["figs"]) not in " ".join(argv)
    assert sess["status"] == "done" and ai_history.get(sid)["task_type"] == "figure_edit"


def test_builtin_failing_midway_never_touches_the_original_script(chain):
    config.set_ai_settings({"taf": {"enabled": False}})
    # 模型已经在候选里改了脚本，下一轮才 401
    chain["serve"]([READ_TURN, EDIT_TURN, (401, {}, {"error": {"type": "authentication_error"}})])
    sid, _events = _run(chain)
    sess = _wait(sid)
    assert sess["status"] == "failed"
    assert (chain["figs"] / "fig1.py").read_text(encoding="utf-8") == SCRIPT


def test_builtin_refuses_to_start_when_no_candidate_can_be_made(chain, monkeypatch):
    config.set_ai_settings({"taf": {"enabled": False}})
    chain["serve"]([END_TURN])

    def boom(*a, **k):
        raise OSError("no space")

    monkeypatch.setattr(ai_figure_edit, "create_candidate", boom)
    with pytest.raises(OSError):
        _run(chain)
    assert chain["spawned"] == [], "绝不把真实项目路径交给内置执行器"
    assert (chain["figs"] / "fig1.py").read_text(encoding="utf-8") == SCRIPT


# --------------------------------------------------------------------------- 用户裁决（10-25）：转换任务 / 账号登录 / 回环 http / 思考强度
def _loopback_gateway_conn(host: str):
    return _save("lb", base=f"http://{host}/anthropic")[0]


@pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8080", "[::1]:8080", "localhost"])
def test_loopback_http_gateways_are_usable_by_the_builtin_executor(world, host):
    rec = _loopback_gateway_conn(host)
    assert ai_providers.builtin_conn(rec) is not None
    world["cli"] = False
    c = _conn(_caps(), "lb")
    assert c["state"] == "ready" and c["route"] == "builtin"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.2",
        "gateway.example.org",
        "localhost.evil.example",
        "[::2]:80",
        "169.254.169.254",
        "[fe80::1]",
        "8.8.8.8",
        "0.0.0.0",
    ],
)
def test_other_plain_http_gateways_stay_refused(world, host):
    rec = _loopback_gateway_conn(host)
    assert ai_providers.builtin_conn(rec) is None
    world["cli"] = False
    c = _conn(_caps(), "lb")
    assert c["state"] == "error" and c["error"] == "base_url_scheme"


def test_posting_an_http_gateway_follows_the_network_policy(client, world):
    for url in ("http://169.254.169.254", "http://gw.example.org", "http://8.8.8.8:80"):
        resp = _post(client, vendor="custom", api_key=KEY, base_url=url, wire="anthropic")
        assert resp.status_code == 400 and resp.get_json()["params"]["reason"] == "insecure_url", (
            url
        )
    for url in (
        "http://127.0.0.1:9",
        "http://localhost:9",
        "http://[::1]:9",
        "http://10.1.2.3:8000",
        "http://192.168.0.7",
        "http://100.64.1.1",
    ):
        world["fetch"] = (["m"], None)
        resp = _post(client, vendor="custom", api_key=KEY, base_url=url, wire="anthropic")
        assert resp.status_code == 200, url


def _oauth_rec(access="tok-CANARY-9d2", expires=9 * 10**12):
    ai_providers.store_oauth(
        ai_oauth._record_defaults("minimax", "cn"),
        {"provider": "minimax", "access": access, "refresh": "r-CANARY", "expires_ms": expires},
    )
    return ai_providers.get("minimax-oauth")


def test_oauth_token_goes_to_the_environment_with_bearer_auth_only(world):
    rec = _oauth_rec()
    cmd, env = ai_bridge._cmd("builtin", "p", "/c", endpoint=rec, target="a.py")
    assert cmd[cmd.index("--auth") + 1] == "bearer"
    assert env == {ai_agents.BUILTIN_KEY_ENV: "tok-CANARY-9d2"}
    assert "tok-CANARY" not in " ".join(cmd) and "r-CANARY" not in " ".join(cmd)
    # Key 类连接不带 --auth bearer
    key_rec = _save("k")[0]
    assert "--auth" not in ai_bridge._cmd("builtin", "p", "/c", endpoint=key_rec, target="a.py")[0]


def test_bearer_wire_sends_authorization_only():
    from tavotto.engine.agent_runner import wires

    def headers(bearer_only):
        wire = wires.AnthropicWire(bearer_only=bearer_only)
        req = wire.build(
            base_url="https://x.example",
            api_key="T",
            model="m",
            system="s",
            tools=[],
            history=[],
            effort=None,
            max_output_tokens=10,
        )
        return req.headers

    assert headers(True) == {"Authorization": "Bearer T", "anthropic-version": "2023-06-01"}
    assert headers(False)["x-api-key"] == "T" and headers(False)["Authorization"] == "Bearer T"


def test_fix_session_accepts_oauth_records_and_refreshes_expired_tokens(world, monkeypatch):
    monkeypatch.setattr(ai_bridge, "agent_caps", lambda _id: None)
    _oauth_rec(expires=1)  # 早已过期
    calls = []

    def fresh(rec):
        calls.append(rec["id"])
        return {
            **rec,
            "oauth": {**rec["oauth"], "access": "tok-NEW-0001", "expires_ms": 9 * 10**12},
        }

    monkeypatch.setattr(ai_oauth, "ensure_fresh", fresh)
    # 只问「有没有」：不联网刷新
    assert fixsession.available() is True and calls == []
    conn = fixsession.connection()
    assert calls == ["minimax-oauth"]
    assert (
        conn["api_key"] == "tok-NEW-0001"
        and conn["auth"] == "bearer"
        and conn["wire"] == "anthropic"
    )
    model, red = fixsession.build_model(conn)
    assert red.contains("tok-NEW-0001") and "tok-NEW-0001" not in repr(model)

    # 刷不动（要重新登录）→ 不算连着
    def needs(rec):
        raise ai_oauth.NeedsLogin("x")

    monkeypatch.setattr(ai_oauth, "ensure_fresh", needs)
    assert fixsession.connection() is None


def test_builtin_run_refreshes_an_expiring_token_before_spawning(chain, monkeypatch):
    server = chain["serve"]([READ_TURN, EDIT_TURN, END_TURN])
    ai_providers.delete("k")
    ai_providers.store_oauth(
        {**ai_oauth._record_defaults("minimax", "cn"), "base_url": server.url},
        {"provider": "minimax", "access": "tok-OLD", "refresh": "r", "expires_ms": 1},
    )
    ai_providers.save({"id": "minimax-oauth", "base_url": server.url, "default_model": "m1"})
    refreshed = []

    def fresh(rec):
        refreshed.append(1)
        return ai_providers.update_oauth(
            rec["id"], {**rec["oauth"], "access": "tok-NEW-0001", "expires_ms": 9 * 10**12}
        )

    monkeypatch.setattr(ai_oauth, "ensure_fresh", fresh)
    sid, _events = _run(chain, endpoint_id="minimax-oauth")
    _wait(sid)
    assert refreshed, "spawn 之前先刷新"
    argv, env = chain["spawned"][0]
    assert env[ai_agents.BUILTIN_KEY_ENV] == "tok-NEW-0001" and "tok-NEW-0001" not in " ".join(argv)
    hdrs = server.requests[0][1]
    assert hdrs.get("authorization") == "Bearer tok-NEW-0001" and "x-api-key" not in hdrs


# ---- 思考强度
X5 = ["low", "medium", "high", "xhigh", "max"]
M4 = ["low", "medium", "high", "max"]
LMH = ["low", "medium", "high"]
ANTH_MODELS = [
    "claude-opus-5-5",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-5-20251101",
    "claude-fable-5-1",
    "claude-mythos-preview",
]


def test_efforts_follow_the_catalog_origin_and_model(world):
    _save(
        "a", base="https://api.anthropic.com", default_model="claude-opus-5-5", models=ANTH_MODELS
    )
    _save(
        "o",
        agent="codex",
        base="https://api.openai.com/v1",
        default_model="gpt-5",
        models=["gpt-5", "gpt-6.1-sol", "gpt-4o", "o3", "gpt-5-chat-latest"],
    )
    _save("d", base="https://api.deepseek.com/anthropic")
    world["cli"] = False
    caps = _caps()
    a, o, d = _conn(caps, "a"), _conn(caps, "o"), _conn(caps, "d")
    assert a["efforts"] == X5 and a["default_effort"] == "medium"  # 默认型号 opus-5-5：默认 medium
    assert o["efforts"] == LMH and o["default_effort"] == "medium"
    assert d["efforts"] == [] and d["default_effort"] is None, "没确认支持的网关不给"
    # 档位和默认档位都按型号（官方文档 2026-10-25 核对）：xhigh 只有 5 系列 / opus-4-7 / opus-4-8
    per = {m["id"]: (m["efforts"], m["default_effort"]) for m in a["model_details"]}
    assert per == {
        "claude-opus-5-5": (X5, "medium"),
        "claude-opus-4-6": (M4, "high"),
        "claude-sonnet-4-6": (M4, "high"),
        "claude-haiku-4-5": ([], None),
        "claude-opus-4-5-20251101": (
            [],
            None,
        ),  # 只配合 budget_tokens 的 extended thinking：runner 不支持
        "claude-fable-5-1": (X5, "high"),
        "claude-mythos-preview": (M4, "high"),
    }
    per = {m["id"]: m["efforts"] for m in o["model_details"]}
    assert per == {
        "gpt-5": LMH,
        "gpt-6.1-sol": LMH,
        "gpt-4o": [],
        "o3": LMH,
        "gpt-5-chat-latest": [],
    }
    # 默认型号不支持 → 连接级 efforts 为空
    config.update_ai_settings(
        lambda ai: {
            "providers": [
                {**p, "default_model": "claude-haiku-4-5"} if p["id"] == "a" else p
                for p in ai["providers"]
            ]
        }
    )
    assert _conn(_caps(), "a")["efforts"] == []
    config.update_ai_settings(
        lambda ai: {
            "providers": [
                {**p, "default_model": "claude-opus-4-6"} if p["id"] == "a" else p
                for p in ai["providers"]
            ]
        }
    )
    c = _conn(_caps(), "a")
    assert (c["efforts"], c["default_effort"]) == (M4, "high"), "默认档位跟默认型号走，不是一个常量"
    f = ai_catalog.efforts_for
    anth = "https://api.anthropic.com/"
    assert f("claude", anth, "claude-opus-4.6") == M4, "点与横线等价"
    assert f("claude", anth, "claude-opus-4-6-20260101") == M4
    assert f("claude", anth, "claude-opus-4-7") == X5 and f("claude", anth, "claude-opus-4-8") == X5
    assert (
        f("claude", anth, "claude-sonnet-5-5") == X5 and f("claude", anth, "claude-haiku-5-5") == X5
    )
    assert (
        f("claude", anth, "claude-opus-4-5") == [] and f("claude", anth, "claude-sonnet-4-5") == []
    )
    assert ai_catalog.effort_support("claude", anth, "claude-haiku-5-5")["default"] == "medium"
    assert f("claude", anth) == [], "没给型号 = 不确定"
    assert f("claude", "http://api.anthropic.com", "claude-opus-4-6") == []  # 来源（scheme）不同
    assert f("codex", "https://api.anthropic.com", "gpt-5") == []  # 家族不同
    assert (
        f("codex", "https://api.openai.com/v1", "o1-mini") == []
        and f("codex", "https://api.openai.com/v1", "o1") == LMH
    )


def test_catalog_row_level_efforts_are_honoured_and_validated(tmp_path, monkeypatch):
    import json as _json

    raw = _json.loads(ai_catalog.CATALOG_PATH.read_text(encoding="utf-8"))
    raw["vendors"][0]["efforts"] = ["low", "high"]
    path = tmp_path / "cat.json"
    path.write_text(_json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(ai_catalog, "CATALOG_PATH", path)
    row = raw["vendors"][0]
    assert ai_catalog.efforts_for(row["agent"], row["base_url"]) == ["low", "high"]
    raw["vendors"][0]["efforts"] = ["extreme"]
    path.write_text(_json.dumps(raw), encoding="utf-8")
    with pytest.raises(ai_catalog.CatalogError):
        ai_catalog.efforts_for(row["agent"], row["base_url"])


def test_effort_args_are_sent_only_where_the_catalog_confirms_support(world):
    a = _save("a", base="https://api.anthropic.com", default_model="claude-opus-4-6")[0]
    d = _save("d", base="https://api.deepseek.com/anthropic")[0]
    o = _save("o", agent="codex", base="https://api.openai.com/v1", default_model="gpt-5")[0]
    old = _save("old", base="https://api.anthropic.com", default_model="claude-sonnet-4-5")[0]
    assert ai_providers.builtin_effort_args(a, "high") == [
        "--effort",
        "high",
        "--thinking",
        "adaptive",
    ]
    assert ai_providers.builtin_effort_args(a, "max")[1] == "max"
    assert ai_providers.builtin_effort_args(o, "medium") == ["--effort", "medium"]
    assert ai_providers.builtin_effort_args(o, "max") == [], "Responses 没有 max 档"
    assert ai_providers.builtin_effort_args(d, "high") == [], "网关不确定就不发"
    assert ai_providers.builtin_effort_args(a, "bogus") == []
    assert ai_providers.builtin_effort_args(a, None) == []
    cmd, _ = ai_bridge._cmd("builtin", "p", "/c", effort="high", endpoint=a, target="x.py")
    assert (
        cmd[cmd.index("--effort") + 1] == "high" and cmd[cmd.index("--thinking") + 1] == "adaptive"
    )
    cmd, _ = ai_bridge._cmd("builtin", "p", "/c", effort="high", endpoint=d, target="x.py")
    assert "--effort" not in cmd and "--thinking" not in cmd
    # 同一官方地址、不支持 adaptive 的型号（包括请求里临时换的型号）：不带 effort 也不带 thinking
    for rec, model in ((old, None), (a, "claude-haiku-4-5"), (o, "gpt-4o")):
        cmd, _ = ai_bridge._cmd(
            "builtin", "p", "/c", model=model, effort="high", endpoint=rec, target="x.py"
        )
        assert "--effort" not in cmd and "--thinking" not in cmd, (rec["id"], model)
    cmd, _ = ai_bridge._cmd(
        "builtin", "p", "/c", model="claude-sonnet-4-6", effort="xhigh", endpoint=a, target="x.py"
    )
    assert "--effort" not in cmd, "sonnet-4-6 没有 xhigh 档（只有 5 系列 / opus-4-7 / opus-4-8）"
    cmd, _ = ai_bridge._cmd(
        "builtin", "p", "/c", model="claude-opus-4-7", effort="xhigh", endpoint=a, target="x.py"
    )
    assert (
        cmd[cmd.index("--effort") + 1] == "xhigh" and cmd[cmd.index("--thinking") + 1] == "adaptive"
    )
    assert ai_providers.builtin_effort_args(o, "xhigh") == [], "OpenAI 保守只给 low/medium/high"
    cmd, _ = ai_bridge._cmd(
        "builtin", "p", "/c", model="claude-sonnet-4-6", effort="high", endpoint=a, target="x.py"
    )
    assert cmd[cmd.index("--thinking") + 1] == "adaptive"


def test_effort_reaches_the_model_request_body(chain, monkeypatch):
    server = chain["serve"]([END_TURN])
    # 把本机测试网关当作「官方来源」，支持 high
    monkeypatch.setattr(ai_catalog, "is_effort_origin", lambda agent, base: base == server.url)
    monkeypatch.setattr(
        ai_catalog,
        "effort_support",
        lambda agent, base, model=None: {"efforts": ["low", "medium", "high"], "default": "medium"},
    )
    sid, _ = _run(chain, effort="high")
    _wait(sid)
    import json as _json

    body = _json.loads(server.bodies[0])
    assert body["output_config"] == {"effort": "high"} and body["thinking"] == {"type": "adaptive"}


def test_effort_is_not_sent_to_an_unconfirmed_gateway(chain):
    server = chain["serve"]([END_TURN])
    sid, _ = _run(chain, effort="high")
    _wait(sid)
    import json as _json

    body = _json.loads(server.bodies[0])
    assert "output_config" not in body and "thinking" not in body


# ---- 生成可编辑版本走内置执行器
ORIGINAL_PIL = (
    'from PIL import Image\nImg = Image.new("RGB", (40, 20), "white")\nImg.save("out.png")\n'
)
GOOD_MPL = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2])\nfig.savefig('out.png')\n"


def _tool_turn(tid, name, args):
    return (
        200,
        {},
        {
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": args}],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 30, "output_tokens": 5},
        },
    )


@pytest.fixture
def convert_chain(chain, monkeypatch, tmp_path):
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "convert")
    config.set_ai_settings({})
    figs = chain["figs"]
    (figs / "fig.py").write_bytes(ORIGINAL_PIL.encode())
    (figs / "fig.R").write_bytes(b"png('out.png'); plot(1:3)\n")
    runs: list[tuple] = []

    def runner(figures_dir, rel, write_roots=None):
        runs.append((rel, write_roots))
        return {
            "stems": ["out"],
            "size_mm": [100.0, 50.0],
            "svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
        }

    chain["runner"], chain["runs"] = runner, runs
    yield chain
    ai_tasks.unregister(ai_convert.TASK_TYPE)


def _convert(box, script="fig.py", **conv):
    events: list = []
    sid = ai_bridge.run(
        "builtin",
        script,
        "生成可编辑的 Matplotlib 版本",
        str(box["figs"]),
        on_event=lambda n, d: events.append((n, d)),
        task_hooks={"runner": box["runner"]},
        endpoint_id="k",
        convert={"library": "pillow", "original_rel": None, **conv},
    )
    return sid, events


@pytest.mark.parametrize(
    ("script", "extra", "orig"),
    [
        ("fig.py", {}, ORIGINAL_PIL),
        ("fig.R", {"library": None, "source_language": "r"}, "png('out.png'); plot(1:3)\n"),
    ],
    ids=["python", "r"],
)
def test_builtin_converts_to_an_editable_script_through_the_normal_pipeline(
    convert_chain, script, extra, orig
):
    box = convert_chain
    server = box["serve"](
        [
            _tool_turn("t1", "read_file", {"path": script}),
            _tool_turn("t2", "create_file", {"content": GOOD_MPL}),
            _tool_turn("t3", "check", {}),
            END_TURN,
        ]
    )
    sid, events = _convert(box, script, **extra)
    sess = _wait(sid)
    figs = box["figs"]
    assert sess["status"] == "done", sess.get("transcript")
    # check 回调真的走了校验流水（Agent 自己要的那一次 + 流水线提交前检查的那一次），且带着候选目录写入根
    assert len(box["runs"]) >= 2 and all(r[1] for r in box["runs"])
    assert "check passed" in server.bodies[3], "Agent 拿到了校验结论"
    argv, env = box["spawned"][0]
    assert argv[argv.index("--task") + 1] == "convert_to_editable" and "--check-ipc" in argv
    assert argv[argv.index("--target") + 1] == script
    assert argv[argv.index("--output") + 1] == "fig_matplotlib.py"
    assert KEY not in " ".join(argv) and env[ai_agents.BUILTIN_KEY_ENV] == KEY
    # 候选就绪、未采用：项目里原脚本字节不变、没有新文件
    st = ai_convert.status(sid, str(figs.resolve()))
    assert st["state"] == "ready", st
    assert (figs / script).read_bytes() == orig.encode()
    assert not (figs / "fig_matplotlib.py").exists()
    assert ai_history.get(sid)["provider"] == "builtin"
    # 采用：只新建兄弟脚本
    made = ai_convert.adopt(sid, str(figs.resolve()))
    assert made["name"] == "fig_matplotlib.py"
    assert (figs / "fig_matplotlib.py").read_text(encoding="utf-8") == GOOD_MPL
    assert (figs / script).read_bytes() == orig.encode()


def test_builtin_convert_check_reports_failures_back_to_the_agent(convert_chain):
    box = convert_chain
    box["runner"] = lambda figures_dir, rel, write_roots=None: {
        "error": {"code": "script_error", "message": "boom"}
    }
    server = box["serve"](
        [
            _tool_turn("t1", "create_file", {"content": GOOD_MPL}),
            _tool_turn("t2", "check", {}),
            END_TURN,
        ]
    )
    sid, _ = _convert(box)
    _wait(sid)
    assert "check failed" in server.bodies[2]


def test_builtin_convert_cannot_touch_the_original_or_project_files(convert_chain):
    box = convert_chain
    server = box["serve"](
        [
            _tool_turn("t1", "edit_file", {"path": "fig.py", "old": "Image", "new": "X"}),
            _tool_turn("t2", "create_file", {"path": "../evil.py", "content": "x"}),
            END_TURN,
        ]
    )
    sid, _ = _convert(box)
    _wait(sid)
    figs = box["figs"]
    assert (figs / "fig.py").read_bytes() == ORIGINAL_PIL.encode()
    assert sorted(p.name for p in figs.iterdir() if p.is_file()) == ["fig.R", "fig.py", "fig1.py"]
    assert "error" in server.bodies[1].lower()


def test_convert_endpoint_accepts_a_connection(client, world, monkeypatch, tmp_path):
    figs = tmp_path / "pj"
    figs.mkdir()
    (figs / "fig.py").write_text("print(1)\n", encoding="utf-8")
    appmod.reset_projects()
    appmod.open_project(str(figs))
    seen = []
    monkeypatch.setattr(
        appmod.engine_ai, "run", lambda agent, *a, **kw: seen.append((agent, kw)) or "s1"
    )
    _save("a", default_model="m2")
    world["cli"] = False
    ai_bridge.invalidate_capabilities()
    resp = client.post(
        "/api/ai/convert", json={"script": "fig.py", "library": "pillow", "connection": "a"}
    )
    assert resp.status_code == 200, resp.get_json()
    assert (seen[0][0], seen[0][1]["endpoint_id"], seen[0][1]["model"]) == ("builtin", "a", "m2")
    assert (
        client.post(
            "/api/ai/convert", json={"script": "fig.py", "library": "pillow", "connection": "ghost"}
        ).status_code
        == 404
    )
    appmod.reset_projects()


# --------------------------------------------------------------------------- 按厂商合并接入方式（用户裁决 10-25）
def test_post_by_a_methods_catalog_id_creates_that_connection(client, world):
    world["fetch"] = (["gpt-5"], None)
    conn = _post(client, vendor="openai-api", api_key=KEY).get_json()["connection"]
    assert conn["vendor"] == "openai" and conn["method"] == "openai-api:key"
    assert conn["logo"] == "openai" and conn["label"] == "OpenAI API"
    stored = ai_providers.get(conn["id"])
    assert stored["agent"] == "codex" and stored["base_url"] == "https://api.openai.com/v1"
    assert world["fetched"][-1][0] == "https://api.openai.com/v1"
    kimi_code = _post(client, vendor="kimi-code", api_key=KEY2).get_json()["connection"]
    assert kimi_code["vendor"] == "moonshot" and kimi_code["method"] == "kimi-code:key"
    assert ai_providers.get(kimi_code["id"])["base_url"] == "https://api.kimi.com/coding"
    # 用 `method` 字段（`<catalog_id>:key`）同样可以
    anth = _post(client, method="anthropic-api:key", api_key=KEY).get_json()["connection"]
    assert anth["vendor"] == "anthropic" and anth["label"] == "Claude API"


def test_post_by_a_vendor_id_takes_its_first_key_method(client, world):
    world["fetch"] = (["m"], None)
    for vendor, method in (
        ("openai", "openai-api:key"),
        ("anthropic", "anthropic-api:key"),
        ("moonshot", "moonshot:key"),
        ("minimax", "minimax:key"),
    ):
        assert (
            _post(client, vendor=vendor, api_key=KEY).get_json()["connection"]["method"] == method
        )


@pytest.mark.parametrize(
    "body",
    [
        {"vendor": "chatgpt"},
        {"vendor": "claude"},
        {"vendor": "minimax-oauth"},
        {"method": "openai:login"},
        {"method": "minimax:oauth"},
        {"method": "nope:key"},
        {"method": 3},
    ],
)
def test_login_and_oauth_methods_are_never_key_connections(client, world, body):
    resp = _post(client, api_key=KEY, **body)
    assert resp.status_code == 400 and resp.get_json()["code"] == "endpoint_invalid"
    assert ai_providers.list_providers() == []


def test_official_key_connections_carry_efforts_on_the_builtin_route(client, world):
    world["fetch"] = (["claude-opus-5-5", "gpt-5"], None)
    world["cli"] = False
    # 型号清单缓存按（地址、密钥指纹）落在数据目录里、跨用例共用：这里用独有的密钥，免得读到别的用例缓存的清单
    a = _post(client, vendor="anthropic-api", api_key="sk-conn-EFFORT-a1").get_json()["connection"]
    o = _post(client, vendor="openai-api", api_key="sk-conn-EFFORT-o1").get_json()["connection"]
    assert a["route"] == o["route"] == "builtin"
    assert a["efforts"] == X5 and a["default_effort"] == "medium"
    assert o["efforts"] == LMH
    # 有 CLI 且 builtin_default=false：官方 Key 走 CLI + 注入，不给 Claude 之外的档位承诺
    world["cli"] = True
    ai_bridge.invalidate_capabilities()
    assert _conn(_caps(), a["id"])["route"] == "cli:claude"


def test_connections_report_their_vendor_and_method_for_all_sources(world, monkeypatch):
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    _save("a", base="https://api.anthropic.com")
    _save("kc", base="https://api.kimi.com/coding/")
    _save("mm", base="https://api.minimaxi.com/anthropic")
    _oauth_rec()
    caps = _caps()
    got = {c["id"]: (c["vendor"], c["method"]) for c in caps["connections"]}
    assert got == {
        "sub:codex": ("openai", "openai:login"),
        "sub:claude": ("anthropic", "anthropic:login"),
        "a": ("anthropic", "anthropic-api:key"),
        "kc": ("moonshot", "kimi-code:key"),
        "mm": ("minimax", "minimax:key"),
        "minimax-oauth": ("minimax", "minimax:oauth"),
    }
    assert _conn(caps, "kc")["logo"] == "kimi", "logo 取自它自己那一行，不是厂商的"
    # 每条连接的 vendor 都在 vendors 里，method 都在该厂商的 methods 里
    index = {v["id"]: {m["id"] for m in v["methods"]} for v in caps["vendors"]}
    assert all(c["method"] in index[c["vendor"]] for c in caps["connections"])


def test_login_accepts_method_ids_too(client, world, monkeypatch):
    started = []
    monkeypatch.setattr(
        ai_bridge, "start_login", lambda agent: started.append(agent) or {"started": True}
    )
    assert client.post("/api/ai/connections/openai:login/login").status_code == 200
    assert client.post("/api/ai/connections/anthropic:login/login").status_code == 200
    assert started == ["codex", "claude"]
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "placeholder-not-a-real-client-id")
    monkeypatch.setattr(
        ai_oauth, "start", lambda provider, region=None: {"flow_id": "f", "provider": provider}
    )
    assert client.post("/api/ai/connections/minimax:oauth/login").get_json()["flow_id"] == "f"


# --------------------------------------------------------------------------- Codex 评审：更新保留省略字段 / 转换 check 可取消
def test_updating_a_custom_connection_keeps_its_address_and_wire(client, world):
    world["fetch"] = (["m"], None)
    cid = _post(
        client,
        vendor="custom",
        api_key=KEY,
        base_url="https://llm.example.org/v1",
        wire="openai-responses",
        model="my-model",
        label="我的网关",
    ).get_json()["connection"]["id"]
    resp = _post(client, vendor="custom", id=cid, api_key=KEY2)  # 只换 Key
    assert resp.status_code == 200, resp.get_json()
    stored = ai_providers.get(cid)
    assert (stored["api_key"], stored["base_url"], stored["agent"]) == (
        KEY2,
        "https://llm.example.org/v1",
        "codex",
    )
    assert (stored["default_model"], stored["label"]) == ("my-model", "我的网关")
    # 省略 vendor 也行（还是它自己的方式）；显式给了才覆盖
    assert _post(client, id=cid, api_key=KEY).status_code == 200
    assert ai_providers.get(cid)["agent"] == "codex"
    _post(client, vendor="custom", id=cid, api_key=KEY, wire="anthropic")
    assert ai_providers.get(cid)["agent"] == "claude"


def test_updating_a_catalog_connection_keeps_its_protocol_family_and_address(client, world):
    world["fetch"] = (["deepseek-flash"], None)
    _save(
        "cx",
        agent="codex",
        base="https://api.deepseek.com",
        wire_api="responses",
        default_model="deepseek-flash",
    )
    resp = _post(client, vendor="deepseek", id="cx", api_key=KEY2)
    assert resp.status_code == 200, resp.get_json()
    stored = ai_providers.get("cx")
    assert (stored["agent"], stored["base_url"], stored["wire_api"]) == (
        "codex",
        "https://api.deepseek.com",
        "responses",
    )
    assert world["fetched"][-1] == ("https://api.deepseek.com", KEY2)
    # 换成别家的方式 = 不是同一条：拒绝，不动已存记录
    resp = _post(client, vendor="zhipu", id="cx", api_key=KEY)
    assert resp.status_code == 400 and resp.get_json()["params"]["reason"] == "vendor_mismatch"
    assert ai_providers.get("cx")["api_key"] == KEY2


def test_cancelling_during_a_builtin_check_aborts_the_validation(convert_chain):
    box = convert_chain
    entered, released, killed = threading.Event(), threading.Event(), []

    def runner(figures_dir, rel, write_roots=None):
        entered.set()
        released.wait(30)
        return {"stems": ["out"], "size_mm": [1.0, 1.0], "svg": None}

    def force_cancel(figures_dir, rel, write_roots=None):
        killed.append(rel)
        released.set()
        return True

    runner.force_cancel = force_cancel
    box["runner"] = runner
    box["serve"](
        [
            _tool_turn("t1", "create_file", {"content": GOOD_MPL}),
            _tool_turn("t2", "check", {}),
            END_TURN,
        ]
    )
    sid, _events = _convert(box)
    try:
        assert entered.wait(30), "判据的前提：校验运行已经在跑"
        assert ai_bridge.cancel(sid) is True
        deadline = time.monotonic() + 15
        while not killed and time.monotonic() < deadline:
            time.sleep(0.05)
        assert killed, "取消到来时在跑的校验 worker 被中止"
    finally:
        released.set()
    sess = _wait(sid)
    assert sess["status"] == "cancelled"
    assert not (box["figs"] / "fig_matplotlib.py").exists()


def test_updating_a_catalog_connection_keeps_a_customised_address(client, world):
    world["fetch"] = (["m1"], None)
    _save("tuned", base="https://api.deepseek.com/my-proxy/anthropic")
    resp = _post(client, vendor="deepseek", id="tuned", api_key=KEY2)
    assert resp.status_code == 200, resp.get_json()
    assert ai_providers.get("tuned")["base_url"] == "https://api.deepseek.com/my-proxy/anthropic"
    assert world["fetched"][-1][0] == "https://api.deepseek.com/my-proxy/anthropic"


def test_fix_connection_skips_keys_the_provider_already_rejected(client, world, monkeypatch):
    monkeypatch.setattr(ai_bridge, "agent_caps", lambda _id: None)
    world["fetch"] = (None, "unauthorized")
    bad = _post(client, vendor="zhipu", api_key=KEY).get_json()["connection"]
    assert bad["state"] == "error" and ai_providers.get(bad["id"])["last_error"] == "unauthorized"
    ai_providers.set_active("claude", bad["id"])  # 旧版还把它「选中」着：照样不能挑它
    assert fixsession.connection() is None and fixsession.available() is False, (
        "全被拒：交给 CLI 兜底"
    )
    _save("good", base="https://api.moonshot.cn/anthropic", key=KEY2, default_model="kimi-k3")
    assert fixsession.connection()["api_key"] == KEY2
    # 换了新 Key（清掉 last_error）后它又能被挑中
    world["fetch"] = (["glm-5.3"], None)
    _post(client, vendor="zhipu", id=bad["id"], api_key=KEY)
    assert fixsession.connection()["api_key"] == KEY


# ---- 官方 Key 连接的档位：先查 CLI 自己的型号目录缓存，查不到再用文档核对过的兜底表
def _write_codex_cache(rows):
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "models_cache.json").write_text(json.dumps({"models": rows}), encoding="utf-8")


def _write_claude_catalog(models):
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "cache" / "model-catalog"
    d.mkdir(parents=True, exist_ok=True)
    (d / "acct.json").write_text(
        json.dumps({"fetchedAt": 1, "catalog": {"config": {"models": models}}}), encoding="utf-8"
    )


def _codex_row(slug, levels, default):
    return {
        "slug": slug,
        "visibility": "list",
        "supported_reasoning_levels": [{"effort": e} for e in levels],
        "default_reasoning_level": default,
    }


def test_official_openai_efforts_come_from_the_codex_model_cache_first(world):
    _write_codex_cache(
        [
            _codex_row("gpt-5", ["minimal", "low", "medium", "high", "xhigh"], "high"),
            _codex_row(
                "gpt-9-future", ["low", "xhigh", "max", "none"], "low"
            ),  # max / none 线上发不出去：被滤掉
            _codex_row("gpt-5-noeffort", [], None),  # 目录明确说没有档位
        ]
    )
    _save(
        "o",
        agent="codex",
        base="https://api.openai.com/v1",
        default_model="gpt-5",
        models=["gpt-5", "gpt-9-future", "gpt-5-noeffort", "gpt-6.1-sol", "o3"],
    )
    world["cli"] = False
    c = _conn(_caps(), "o")
    per = {m["id"]: (m["efforts"], m["default_effort"]) for m in c["model_details"]}
    assert per["gpt-5"] == (["minimal", "low", "medium", "high", "xhigh"], "high"), (
        "原样用目录给的，含 minimal / xhigh"
    )
    assert per["gpt-9-future"] == (["low", "xhigh"], "low")
    assert per["gpt-5-noeffort"] == ([], None), "目录明确给了空 = 没有档位，不退回兜底"
    assert per["gpt-6.1-sol"] == (LMH, "medium") and per["o3"] == (LMH, "medium"), (
        "不在目录里的型号用兜底表"
    )
    assert (c["efforts"], c["default_effort"]) == per["gpt-5"]
    # 发送前核对用同一个函数
    rec = ai_providers.get("o")
    assert ai_providers.builtin_effort_args(rec, "xhigh", "gpt-5") == ["--effort", "xhigh"]
    assert ai_providers.builtin_effort_args(rec, "minimal", "gpt-5") == ["--effort", "minimal"]
    assert (
        ai_providers.builtin_effort_args(rec, "xhigh", "gpt-6.1-sol") == []
    )  # 兜底表只有 low/medium/high
    assert ai_providers.builtin_effort_args(rec, "max", "gpt-9-future") == []
    assert ai_providers.builtin_effort_args(rec, "low", "gpt-5-noeffort") == []


def test_official_anthropic_efforts_come_from_the_claude_model_catalog_first(world):
    _write_claude_catalog(
        [
            {
                "id": "claude-opus-4-6",
                "section": "main",
                "runtime": {"effort_levels": ["low", "high"], "default_effort": "low"},
            },
            {
                "id": "claude-haiku-4-5",
                "section": "main",
                "runtime": {
                    "effort_levels": ["low", "medium", "minimal"],
                    "default_effort": "medium",
                },
            },
        ]
    )
    _save(
        "a",
        base="https://api.anthropic.com",
        default_model="claude-opus-4-6",
        models=["claude-opus-4-6", "claude-haiku-4-5", "claude-opus-5-5", "claude-sonnet-4-5"],
    )
    world["cli"] = False
    c = _conn(_caps(), "a")
    per = {m["id"]: (m["efforts"], m["default_effort"]) for m in c["model_details"]}
    assert per["claude-opus-4-6"] == (["low", "high"], "low"), (
        "目录说什么就是什么（压过文档兜底表的 M4）"
    )
    assert per["claude-haiku-4-5"] == (["low", "medium"], "medium"), (
        "目录给的 minimal anthropic 协议发不了：滤掉"
    )
    assert per["claude-opus-5-5"] == (X5, "medium"), "不在目录里：文档核对过的兜底表"
    assert per["claude-sonnet-4-5"] == ([], None)
    rec = ai_providers.get("a")
    assert ai_providers.builtin_effort_args(rec, "high", "claude-opus-4-6")[:2] == [
        "--effort",
        "high",
    ]
    assert ai_providers.builtin_effort_args(rec, "max", "claude-opus-4-6") == [], "目录没给 max"
    assert ai_providers.builtin_effort_args(rec, "xhigh", "claude-opus-5-5")[:2] == [
        "--effort",
        "xhigh",
    ]


def test_catalogs_are_ignored_for_third_party_gateways_and_when_unreadable(world):
    _write_codex_cache([_codex_row("gpt-5", ["low", "high"], "low")])
    _write_claude_catalog(
        [
            {
                "id": "claude-opus-4-6",
                "section": "main",
                "runtime": {"effort_levels": ["low"], "default_effort": "low"},
            }
        ]
    )
    _save("gw", base="https://api.deepseek.com/anthropic", default_model="claude-opus-4-6")
    _save("gx", agent="codex", base="https://api.deepseek.com", default_model="gpt-5")
    world["cli"] = False
    for cid in ("gw", "gx"):
        c = _conn(_caps(), cid)
        assert c["efforts"] == [] and c["default_effort"] is None, "第三方网关即使型号重名也不给"
    # 缓存损坏：退回兜底表，不抛
    (Path(os.environ["CODEX_HOME"]) / "models_cache.json").write_text("{not json", encoding="utf-8")
    _save("o", agent="codex", base="https://api.openai.com/v1", default_model="gpt-5", key=KEY2)
    assert _conn(_caps(), "o")["efforts"] == LMH


def test_runner_accepts_the_catalog_efforts_for_its_wire():
    from tavotto.engine.agent_runner import wires as w

    assert "xhigh" in w.ANTHROPIC_EFFORTS and "minimal" not in w.ANTHROPIC_EFFORTS
    assert w.RESPONSES_EFFORTS == ("minimal", "low", "medium", "high", "xhigh")
    body = (
        w.OpenAIResponsesWire()
        .build(
            base_url="https://x.example",
            api_key="K",
            model="m",
            system="s",
            tools=[],
            history=[],
            effort="xhigh",
            max_output_tokens=10,
        )
        .body
    )
    assert body["reasoning"] == {"effort": "xhigh"}


# ---- 改图任务的内置执行器也有 `check`（与转换任务同一条 IPC，回调是提交前同一套检查）
def _plot_parts(text: str) -> dict:
    """假渲染：把脚本里 `ax.plot(...)` 那一行当作“数据”，指纹 = 它的哈希；真正的指纹比较是 `figureverify.data_unchanged`。"""
    import hashlib

    line = next((ln for ln in text.splitlines() if "ax.plot(" in ln), "")
    return {"y": hashlib.sha256(line.encode()).hexdigest()[:12]}


def _manifest_of(text: str) -> dict:
    return {
        "elements": [
            {
                "gid": "axes_0.line_0",
                "identity": "line",
                "data_fingerprint": {"status": "ok", "parts": _plot_parts(text)},
            }
        ]
    }


@pytest.fixture
def edit_check_chain(chain):
    runs: list[tuple] = []

    def runner(figures_dir, rel, write_roots=None):
        runs.append((rel, write_roots))
        text = (Path(figures_dir) / rel).read_text(encoding="utf-8")  # 临时校验文件，紧挨原脚本
        return {
            "stems": ["Fig1"],
            "size_mm": None,
            "svg": None,
            "manifests": {"Fig1": _manifest_of(text)},
        }

    chain["runs"] = runs
    chain["hooks"] = {"runner": runner, "check_before": lambda: {"Fig1": _manifest_of(SCRIPT)}}
    yield chain


def _edit(tid, old, new):
    return _tool_turn(tid, "edit_file", {"old": old, "new": new})


def _edit_check_run(box):
    return _run(box, task_hooks=box["hooks"])


def test_builtin_edit_check_reports_real_findings_then_passes(edit_check_chain):
    box = edit_check_chain
    server = box["serve"](
        [
            READ_TURN,
            _edit("e1", "[1, 4, 9]", "[1, 4, 10]"),  # 破坏数据
            _tool_turn("c1", "check", {}),
            _edit("e2", "[1, 4, 10]", "[1, 4, 9]"),  # 改回
            _edit("e3", '"old"', '"new"'),
            _tool_turn("c2", "check", {}),
            END_TURN,
        ]
    )
    sid, _ = _edit_check_run(box)
    sess = _wait(sid)
    assert sess["status"] == "done", sess.get("transcript")
    argv = box["spawned"][0][0]
    assert "--check-ipc" in argv
    assert "not_available" not in " ".join(server.bodies)
    first = json.loads(server.bodies[3])["messages"][-1]["content"][0]["content"]
    first = first if isinstance(first, str) else json.dumps(first)
    assert "data_changed" in first and "axes_0.line_0" in first and "Fig1" in first, first
    second = json.loads(server.bodies[6])["messages"][-1]["content"][0]["content"]
    second = second if isinstance(second, str) else json.dumps(second)
    assert "check passed" in second and "data unchanged" in second, second
    assert len(box["runs"]) == 2 and all(r[1] for r in box["runs"]), "两次都真渲染，且带写入根绊线"
    assert '"new"' in (box["figs"] / "fig1.py").read_text(encoding="utf-8")
    assert not list(box["figs"].glob("tavotto_convert_probe_*")), "校验临时文件已删"


def test_builtin_edit_check_catches_a_syntax_error_without_rendering(edit_check_chain):
    box = edit_check_chain
    server = box["serve"](
        [
            READ_TURN,
            _edit("e1", 'ax.set_title("old")', 'ax.set_title("old"'),
            _tool_turn("c1", "check", {}),
            _edit("e2", 'ax.set_title("old"', 'ax.set_title("old")'),
            END_TURN,
        ]
    )
    sid, _ = _edit_check_run(box)
    _wait(sid)
    got = json.loads(server.bodies[3])["messages"][-1]["content"][0]["content"]
    assert got.startswith("check failed") and "第 7 行有语法错误" in got, got
    assert box["runs"] == [], "语法都过不了，不渲染"


def test_builtin_edit_check_is_capped_at_three_runs(edit_check_chain):
    box = edit_check_chain
    server = box["serve"](
        [READ_TURN, _edit("e1", '"old"', '"new"')]
        + [_tool_turn(f"c{i}", "check", {}) for i in range(4)]
        + [END_TURN]
    )
    sid, _ = _edit_check_run(box)
    _wait(sid)
    assert len(box["runs"]) == 3, "第 4 次不再调回调"
    last = json.loads(server.bodies[6])["messages"][-1]["content"][0]
    assert last["is_error"] is True and "already called 3 times" in last["content"], last


def test_builtin_edit_check_without_a_render_hook_still_runs_the_l1_checks(edit_check_chain):
    box = edit_check_chain
    box["hooks"] = {}
    server = box["serve"](
        [READ_TURN, _edit("e1", '"old"', '"new"'), _tool_turn("c1", "check", {}), END_TURN]
    )
    sid, _ = _edit_check_run(box)
    _wait(sid)
    got = json.dumps(json.loads(server.bodies[3])["messages"][-1]["content"])
    assert "check passed" in got and "rendering is not available" in got, got
