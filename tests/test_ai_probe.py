"""保存连接时的协议探测与 openai-chat 连接（`engine/ai_probe.py` + `ai_providers.protocol` + `ai_connections` 路由 / `/check`）。

判据的主语（先说清）：

* 探测：对 `wires.post_json` 的**每一次出站请求**（地址、体里有没有工具定义与小的长度上限）与**探测结论**（闭集原因码、选了哪个协议、
  记录的 API 根）；响应体（金丝雀字符串）不许出现在返回值里。桩住的是传输 `post_json`，所以 wires 的 `build` / `parse` 是真的。
* 数据模型：磁盘上 `ai.providers[]` 记录里的 `protocol` / `protocol_ok`——换 Key、改名之后还在；地址 / 型号变了 `protocol_ok` 失效。
* 路由：`route_for` 对 chat 记录的完整矩阵（强制开关 × `builtin_default` × CLI 有无），**没有任何一格是 `cli:*`**；
  `spawn_overrides` 的 codex 分支、`resolve`（当前选中）、`ai_bridge.run`（显式指到）同样关死。
* 旧的 codex + `wire_api="chat"` 记录行为不变（仍 `codex_chat_removed`，不被自动改成新协议）。

**绝不联网**：`ai_probe.detect` 默认被 conftest 桩成「没结论」，这里换回真的并桩掉 `wires.post_json`。
"""

from __future__ import annotations

import json

import pytest

from tavotto import app as appmod
from tavotto.engine import (
    ai_agents,
    ai_bridge,
    ai_catalog,
    ai_connections,
    ai_probe,
    ai_providers,
    config,
)
from tavotto.engine.agent_runner import wires

KEY = "sk-probe-CANARY-31c9"
CANARY = "BODY-CANARY-9e1f"  # 响应体里的金丝雀：任何返回值里都不许出现

CHAT_OK = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
RESP_OK = {"status": "completed", "output": [{"type": "message", "content": []}]}
ANTH_OK = {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}

_REAL_DETECT = ai_probe.detect  # conftest 的 autouse 夹具在用例开始前才换掉它


def _j(obj, status=200):
    return status, {}, json.dumps(obj).encode()


def _html(status=200):
    return status, {}, f"<html><body>{CANARY}</body></html>".encode()


class Gateway:
    """桩 `wires.post_json`：`routes` = {url: (status, headers, body) | 可调用(body)->同形}；没登记的 URL 回 404 JSON。记录每次调用。"""

    def __init__(self, routes=None, *, fail=False):
        self.routes = dict(routes or {})
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, *, timeout, max_bytes=None):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if self.fail:
            raise wires.RunnerFailure("network", "ConnectionRefusedError", retryable=True)
        hit = self.routes.get(url)
        if hit is None:
            return _j({"error": {"message": "Invalid URL"}}, 404)
        return hit(body) if callable(hit) else hit

    @property
    def urls(self):
        return [c["url"] for c in self.calls]


@pytest.fixture
def real_probe(monkeypatch):
    monkeypatch.setattr(ai_probe, "detect", _REAL_DETECT)


@pytest.fixture
def gw(monkeypatch, real_probe):
    g = Gateway()
    monkeypatch.setattr(wires, "post_json", g)
    return g


@pytest.fixture(autouse=True)
def _world(monkeypatch, tmp_path_factory):
    """两家 CLI 默认「装了且能启动」；不读真实 ~/.codex ~/.claude；型号清单的网络请求换成桩。"""
    state = {"cli": True, "fetch": (["m1"], None)}
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
    monkeypatch.setattr(ai_catalog, "fetch_models", lambda *_a: state["fetch"])
    ai_bridge.invalidate_capabilities()
    yield state
    ai_bridge.invalidate_capabilities()


@pytest.fixture
def client():
    appmod.app.config["TESTING"] = True
    appmod.reset_projects()
    yield appmod.app.test_client()
    appmod.reset_projects()


def _post(client, **body):
    return client.post("/api/ai/connections", json=body)


def _caps():
    return ai_bridge.capabilities(refresh=True)


def _conn(cid):
    return next(c for c in _caps()["connections"] if c["id"] == cid)


def _chat_rec(pid="gw", **kw):
    rec = {
        "id": pid,
        "label": pid,
        "agent": "codex",
        "base_url": "https://gw.example.org/v1",
        "api_key": KEY,
        "models": ["m1"],
        "default_model": "m1",
        "protocol": "openai-chat",
        **kw,
    }
    ai_providers.save(rec)
    return ai_providers.get(pid)


# =========================================================================== classify
@pytest.mark.parametrize(
    ("status", "body", "want"),
    [
        (200, json.dumps(CHAT_OK).encode(), "ok"),
        (200, b"<!doctype html><html></html>", "web_page"),
        (200, b'{"error": {"message": "boom"}}', "bad_reply"),  # 200 的错误对象不是成功
        (200, b"not json", "bad_reply"),
        (302, b"", "web_page"),
        (401, b'{"error": {"message": "bad key"}}', "unauthorized"),
        (403, b'{"error": {"message": "forbidden"}}', "unauthorized"),
        (403, b"<html>cf</html>", "web_page"),
        (401, b"<html></html>", "unauthorized"),
        (402, b"{}", "quota"),
        (429, b'{"error": {"message": "rate"}}', "quota"),
        (403, b'{"error": {"message": "insufficient quota"}}', "quota"),
        (404, b'{"error": {"message": "Invalid URL (POST /v1/messages)"}}', "wrong_protocol"),
        (404, b"<html>not found</html>", "wrong_protocol"),
        (405, b"{}", "wrong_protocol"),
        (404, b'{"error": {"message": "The model `x` does not exist"}}', "no_model"),
        (503, b'{"error": {"message": "No available channel for model x"}}', "wrong_protocol"),
        (503, b'{"error": {"message": "overloaded"}}', "network"),
        (502, b"<html>bad gateway</html>", "network"),
        (500, b'{"error": {"message": "internal"}}', "network"),
        (400, b'{"error": {"message": "model not found"}}', "no_model"),
    ],
)
def test_classify_closed_set(status, body, want):
    got, retry = ai_probe.classify(wires.WIRES["openai-chat"], status, body)
    assert got == want and retry is False
    assert got in ai_probe.REASONS


def test_classify_asks_for_max_completion_tokens_retry():
    body = b'{"error": {"message": "Unsupported parameter: use max_completion_tokens instead"}}'
    assert ai_probe.classify(wires.WIRES["openai-chat"], 400, body)[1] is True


# =========================================================================== detect
def test_only_chat_passes_and_root_gets_v1(gw):
    gw.routes = {
        "https://gw.example.org/chat/completions": _html(),  # 中转站首页
        "https://gw.example.org/v1/chat/completions": _j(CHAT_OK),
    }
    r = ai_probe.detect("https://gw.example.org", KEY, "m1")
    assert r["protocol"] == "openai-chat" and r["base_url"] == "https://gw.example.org/v1"
    assert r["token_field"] == "max_tokens" and r["conclusive"] and r["reason"] is None
    # 先按用户原样试，再试换算后的
    assert gw.urls == [
        "https://gw.example.org/chat/completions",
        "https://gw.example.org/v1/chat/completions",
    ]
    assert CANARY not in json.dumps(r)


def test_request_is_minimal_with_one_tool_and_bounded(gw):
    gw.routes = {"https://gw.example.org/v1/chat/completions": _j(CHAT_OK)}
    ai_probe.detect("https://gw.example.org/v1", KEY, "m1")
    (call,) = gw.calls
    body = call["body"]
    assert body["max_tokens"] <= 16 and len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "ping"
    assert call["timeout"] <= ai_probe.PER_REQUEST_S
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"


def test_html_200_everywhere_is_a_failure_not_a_success(gw):
    gw.routes = {
        f"https://gw.example.org{p}": _html()
        for p in (
            "/chat/completions",
            "/v1/chat/completions",
            "/responses",
            "/v1/responses",
            "/v1/messages",
        )
    }
    r = ai_probe.detect("https://gw.example.org", KEY, "m1")
    assert r["protocol"] is None and r["reason"] == "web_page" and r["conclusive"]
    assert set(r["reasons"]) == {"openai-chat", "openai-responses", "anthropic"}
    assert CANARY not in json.dumps(r)


def test_404_everywhere_is_wrong_protocol(gw):
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "m1")
    assert r["protocol"] is None and r["reason"] == "wrong_protocol"
    assert set(r["reasons"].values()) == {"wrong_protocol"}


def test_401_is_unauthorized_and_wins_as_primary(gw):
    gw.routes = {
        "https://gw.example.org/v1/chat/completions": _j({"error": {"message": "x"}}, 401),
    }
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "m1")
    assert r["reasons"]["openai-chat"] == "unauthorized"
    assert r["reason"] == "unauthorized" and r["protocol"] is None


def test_network_failure_stops_the_host_and_is_inconclusive(monkeypatch, real_probe):
    g = Gateway(fail=True)
    monkeypatch.setattr(wires, "post_json", g)
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "m1")
    assert r["protocol"] is None and r["conclusive"] is False and r["reason"] == "network"
    assert len(g.calls) == 1, "同一主机连不上就别重复试"


def test_no_model_means_no_request(gw):
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "")
    assert gw.calls == [] and r["conclusive"] is False and r["reason"] == "no_model"


def test_claude_model_tries_anthropic_first_and_strips_v1(gw):
    gw.routes = {
        "https://gw.example.org/v1/messages": _j(ANTH_OK),
        "https://gw.example.org/v1/chat/completions": _j(CHAT_OK),
    }
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "claude-sonnet-5")
    assert r["protocol"] == "anthropic" and r["base_url"] == "https://gw.example.org"
    assert gw.urls[-1] == "https://gw.example.org/v1/messages", "找到就停，不再试别的协议（省额度）"
    assert "x-api-key" in gw.calls[-1]["headers"]


def test_openai_host_with_gpt_prefers_responses(gw):
    gw.routes = {
        "https://api.openai.com/v1/responses": _j(RESP_OK),
        "https://api.openai.com/v1/chat/completions": _j(CHAT_OK),
    }
    r = ai_probe.detect("https://api.openai.com/v1", KEY, "gpt-5")
    assert r["protocol"] == "openai-responses"
    # 同样的型号放在第三方中转站上 = chat
    gw.routes = {
        "https://gw.example.org/v1/responses": _j(RESP_OK),
        "https://gw.example.org/v1/chat/completions": _j(CHAT_OK),
    }
    assert ai_probe.detect("https://gw.example.org/v1", KEY, "gpt-5")["protocol"] == "openai-chat"


def test_user_choice_wins_when_it_passes(gw):
    gw.routes = {
        "https://gw.example.org/v1/responses": _j(RESP_OK),
        "https://gw.example.org/v1/chat/completions": _j(CHAT_OK),
    }
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "m1", preferred="openai-responses")
    assert r["protocol"] == "openai-responses"


def test_only_own_protocol_is_checked_for_catalog_vendors(gw):
    r = ai_probe.detect(
        "https://api.deepseek.com/anthropic",
        KEY,
        "m1",
        protocols=("anthropic",),
        root_fix=False,
    )
    assert r["protocol"] is None and set(r["reasons"]) == {"anthropic"}
    assert gw.urls == ["https://api.deepseek.com/anthropic/v1/messages"]


def test_chat_retries_with_max_completion_tokens(gw):
    def route(body):
        if "max_tokens" in body:
            return _j(
                {"error": {"message": "Unsupported parameter: use max_completion_tokens"}}, 400
            )
        return _j(CHAT_OK)

    gw.routes = {"https://gw.example.org/v1/chat/completions": route}
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "o3-mini")
    assert r["protocol"] == "openai-chat" and r["token_field"] == "max_completion_tokens"


def test_responses_drops_a_rejected_optional_field_before_judging(gw):
    """Codex #1965 P2：Responses 中转站点名拒收 `max_output_tokens`——runner 能剥掉重发，探测也要，别记成不可用。"""

    def route(body):
        if "max_output_tokens" in body:
            return _j({"error": {"message": "Unsupported parameter: 'max_output_tokens'"}}, 400)
        assert body.get("store") is False  # store 永不剥
        return _j(RESP_OK)

    gw.routes = {"https://gw.example.org/v1/responses": route}
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "gpt-x", preferred="openai-responses")
    assert r["protocol"] == "openai-responses"
    assert len(gw.calls) == 2


def test_total_budget_is_bounded(monkeypatch, real_probe):
    g = Gateway()
    monkeypatch.setattr(wires, "post_json", g)
    clock = [0.0]
    monkeypatch.setattr(ai_probe.time, "monotonic", lambda: clock[0])

    def slow(url, headers, body, *, timeout, max_bytes=None):
        clock[0] += timeout  # 每条都耗满上限
        return _j({"error": {"message": "x"}}, 404)

    monkeypatch.setattr(wires, "post_json", slow)
    r = ai_probe.detect("https://gw.example.org", KEY, "m1")
    assert clock[0] <= ai_probe.TOTAL_S + 1e-9 and r["protocol"] is None


# =========================================================================== 数据模型
def test_protocol_survives_key_swap_and_rename():
    rec = _chat_rec()
    assert rec["protocol"] == "openai-chat" and rec["agent"] == "codex"
    ai_providers.save({"id": "gw", "api_key": "sk-new-key-1"})  # 换 Key
    ai_providers.save({"id": "gw", "label": "新名字"})  # 改名
    got = ai_providers.get("gw")
    assert got["protocol"] == "openai-chat" and got["label"] == "新名字"
    assert got["wire_api"] == "responses", "不复用 wire_api"
    assert ai_providers.public(got)["protocol"] == "openai-chat"


def test_protocol_must_match_family_and_be_in_the_closed_set():
    assert "protocol" not in _chat_rec("a", agent="claude", protocol="openai-chat")
    assert "protocol" not in _chat_rec("b", protocol="anthropic")
    assert "protocol" not in _chat_rec("c", protocol="chat")
    assert ai_providers.protocol_of(ai_providers.get("c")) == "openai-responses"


def test_protocol_ok_is_dropped_when_address_or_model_change():
    rec = _chat_rec(
        protocol_ok={
            "base_url": "https://gw.example.org/v1",
            "model": "m1",
            "protocol": "openai-chat",
        }
    )
    assert rec["protocol_ok"]["model"] == "m1"
    ai_providers.save({"id": "gw", "default_model": "m2", "models": ["m1", "m2"]})
    assert "protocol_ok" not in ai_providers.get("gw")
    _chat_rec(
        protocol_ok={
            "base_url": "https://gw.example.org/v1",
            "model": "m2",
            "protocol": "openai-chat",
        },
        default_model="m2",
        models=["m1", "m2"],
    )
    assert "protocol_ok" in ai_providers.get("gw")
    ai_providers.save({"id": "gw", "base_url": "https://other.example.org/v1"})
    assert "protocol_ok" not in ai_providers.get("gw")


def test_legacy_codex_chat_record_is_unchanged():
    ai_providers.save(
        {
            "id": "old",
            "agent": "codex",
            "base_url": "https://api.deepseek.com",
            "api_key": KEY,
            "models": ["m"],
            "default_model": "m",
        }
    )
    ai = config.ai_settings()
    ai["providers"] = [{**p, "wire_api": "chat"} for p in ai["providers"]]
    config.set_ai_settings(ai)
    rec = ai_providers.get("old")
    assert ai_providers.incompatibility(rec) == "codex_chat_removed"
    assert "protocol" not in rec and not ai_providers.builtin_only(rec)
    assert ai_providers.public(rec)["migratable"] == "codex_responses"
    assert ai_providers.builtin_conn(rec) is None
    assert _conn("old")["state"] == "error" and _conn("old")["error"] == "codex_chat_removed"


# =========================================================================== 路由矩阵
@pytest.mark.parametrize("cli", [True, False])
@pytest.mark.parametrize("default", [True, False])
@pytest.mark.parametrize("forced", [None, "1", "0"])
def test_chat_route_matrix_never_reaches_a_cli(monkeypatch, _world, cli, default, forced):
    _world["cli"] = cli
    if forced is None:
        monkeypatch.delenv(ai_connections.ENV_BUILTIN, raising=False)
    else:
        monkeypatch.setenv(ai_connections.ENV_BUILTIN, forced)
    config.set_ai_settings({"builtin_default": default})
    rec = _chat_rec()
    ai_bridge.invalidate_capabilities()
    infos = {a["id"]: a for a in _caps()["agents"]}
    route, why = ai_connections.route_for(rec, infos)
    if forced == "0":
        assert (route, why) == (None, "builtin_only")
    else:
        assert (route, why) == ("builtin", None)
    conn = _conn("gw")
    assert conn["route"] != "cli:codex" and conn["route"] == "builtin"
    assert conn["protocol"] == "openai-chat"
    if forced == "0":
        assert conn["state"] == "error" and conn["error"] == "builtin_only"
        with pytest.raises(ai_connections.ConnectionRefused) as ei:
            ai_connections.resolve_connection("gw", infos)
        assert (
            ei.value.code == "ai_agent_not_usable" and ei.value.params["reason"] == "builtin_only"
        )
    else:
        assert ai_connections.resolve_connection("gw", infos)[0] == "builtin"


def test_chat_with_unusable_builtin_does_not_fall_back_to_cli():
    rec = _chat_rec(
        base_url="http://example.org/v1"
    )  # 明文 http 公网：内置不接，普通记录会退回 CLI
    infos = {a["id"]: a for a in _caps()["agents"]}
    assert infos["codex"]["usable"] is True
    route, why = ai_connections.route_for(rec, infos)
    assert route is None and why == "base_url_scheme"
    plain = {**rec, "protocol": "openai-responses"}
    assert ai_connections.route_for(plain, infos)[0] == "cli:codex"


def test_chat_builtin_conn_wire_and_runner_args():
    rec = _chat_rec()
    conn = ai_providers.builtin_conn(rec)
    assert conn["wire"] == "openai-chat" and "token_field" not in conn
    args, env = ai_providers.spawn_overrides("builtin", rec)
    assert args[:2] == ["--wire", "openai-chat"] and "--max-tokens-field" not in args
    assert env[ai_agents.BUILTIN_KEY_ENV] == KEY
    rec = _chat_rec(
        protocol_ok={
            "base_url": "https://gw.example.org/v1",
            "model": "m1",
            "protocol": "openai-chat",
            "token_field": "max_completion_tokens",
        }
    )
    args, _env = ai_providers.spawn_overrides("builtin", rec)
    assert args[args.index("--max-tokens-field") + 1] == "max_completion_tokens"
    # AI 修复挑到它：`build_model` 建出 OpenAIChatWire 并带上长度字段
    from tavotto.engine import fixsession

    model, _red = fixsession.build_model(ai_providers.builtin_conn(rec))
    assert model.wire.name == "openai-chat"


def test_spawn_overrides_never_injects_chat_into_codex():
    rec = _chat_rec()
    assert ai_providers.spawn_overrides("codex", rec) == ([], {})
    assert ai_providers.spawn_overrides("codex", {**rec, "protocol": "openai-responses"})[0]


def test_current_selection_of_a_chat_record_is_not_a_cli_endpoint():
    _chat_rec()
    ai_providers.set_active("codex", "gw")
    assert ai_providers.resolve("codex") is None
    assert ai_providers.resolve("builtin", "gw")["id"] == "gw"


def test_run_refuses_an_explicit_chat_endpoint_for_a_cli(tmp_path):
    _chat_rec()
    script = tmp_path / "f.py"
    script.write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(ai_bridge.AgentError) as ei:
        ai_bridge.run("codex", "f.py", "p", str(tmp_path), endpoint_id="gw")
    assert ei.value.code == "ai_agent_not_usable"


def test_ai_fix_picks_a_chat_connection():
    _chat_rec()
    conn = ai_connections.first_key_connection()
    assert conn is not None and conn["wire"] == "openai-chat"


# =========================================================================== 保存流程
def test_save_auto_stores_chat_when_only_chat_works(client, gw):
    gw.routes = {
        "https://gw.example.org/chat/completions": _html(),
        "https://gw.example.org/v1/chat/completions": _j(CHAT_OK),
    }
    resp = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org", model="m1"
    )
    body = resp.get_json()
    assert resp.status_code == 200, body
    conn = body["connection"]
    assert conn["state"] == "ready" and conn["route"] == "builtin"
    assert conn["protocol"] == "openai-chat" and conn["protocol_checked"] is True
    assert body["probe"]["checked"] and body["probe"]["protocol"] == "openai-chat"
    rec = ai_providers.get(conn["id"])
    assert rec["agent"] == "codex" and rec["base_url"] == "https://gw.example.org/v1"
    assert rec["protocol_ok"]["protocol"] == "openai-chat"
    assert KEY not in resp.get_data(as_text=True) and CANARY not in resp.get_data(as_text=True)


def test_save_with_explicit_wire_that_fails_switches_to_what_works(client, gw):
    gw.routes = {"https://gw.example.org/v1/chat/completions": _j(CHAT_OK)}
    body = _post(
        client,
        vendor="custom",
        api_key=KEY,
        base_url="https://gw.example.org/v1",
        model="m1",
        wire="openai-responses",
    ).get_json()
    assert body["probe"]["requested"] == "openai-responses"
    assert body["probe"]["protocol"] == "openai-chat" and body["probe"]["switched"] is True
    assert body["connection"]["protocol"] == "openai-chat"


def test_save_with_explicit_wire_that_passes_is_respected(client, gw):
    gw.routes = {
        "https://gw.example.org/v1/responses": _j(RESP_OK),
        "https://gw.example.org/v1/chat/completions": _j(CHAT_OK),
    }
    body = _post(
        client,
        vendor="custom",
        api_key=KEY,
        base_url="https://gw.example.org/v1",
        model="m1",
        wire="openai-responses",
    ).get_json()
    assert (
        body["connection"]["protocol"] == "openai-responses" and body["probe"]["switched"] is False
    )
    assert body["connection"]["route"] == "builtin"


def test_save_when_nothing_works_still_saves_with_a_stable_error(client, gw):
    resp = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org/v1", model="m1"
    )
    body = resp.get_json()
    assert resp.status_code == 200
    conn = body["connection"]
    assert conn["state"] == "error" and conn["error"] == "endpoint_unusable"
    assert conn["error_reason"] == "wrong_protocol"
    assert body["probe"]["checked"] and body["probe"]["reason"] == "wrong_protocol"
    rec = ai_providers.get(conn["id"])
    assert rec["last_error"] == "endpoint_unusable" and rec["probe_reason"] == "wrong_protocol"
    assert rec["api_key"] == KEY, "别让用户白填"
    assert ai_connections.first_key_connection() is None, "AI 修复不挑它"


def test_save_unauthorized_lands_as_last_error(client, gw):
    gw.routes = {"https://gw.example.org/v1/chat/completions": _j({"error": {"message": "x"}}, 401)}
    conn = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org/v1", model="m1"
    ).get_json()["connection"]
    assert conn["error"] == "unauthorized"
    assert ai_providers.get(conn["id"])["last_error"] == "unauthorized"


def test_save_offline_keeps_going_without_an_error(client):
    # 默认桩 = 没拿到任何结论（断网）：照旧保存、不记错误、不记探测结论
    conn = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org/v1", model="m1"
    ).get_json()["connection"]
    assert conn["state"] == "ready" and conn["protocol_checked"] is False
    assert "last_error" not in ai_providers.get(conn["id"])


def test_key_swap_keeps_the_chat_protocol(client, gw):
    gw.routes = {"https://gw.example.org/v1/chat/completions": _j(CHAT_OK)}
    cid = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org/v1", model="m1"
    ).get_json()["connection"]["id"]
    # 换 Key：服务商这次探测不出结论（断网）也不得把 chat 打回 responses
    gw.fail = True
    conn = _post(client, id=cid, api_key="sk-swapped-key-2").get_json()["connection"]
    assert conn["protocol"] == "openai-chat"
    assert ai_providers.get(cid)["api_key"] == "sk-swapped-key-2"
    assert ai_providers.get(cid)["agent"] == "codex"
    # 能探测时先试已存的协议
    gw.fail = False
    gw.routes["https://gw.example.org/v1/responses"] = _j(RESP_OK)
    conn = _post(client, id=cid, api_key="sk-swapped-key-3").get_json()["connection"]
    assert conn["protocol"] == "openai-chat" and gw.urls[-1].endswith("/chat/completions")


def test_catalog_vendor_checks_only_its_own_protocol(client, gw):
    gw.routes = {"https://api.deepseek.com/anthropic/v1/messages": _j(ANTH_OK)}
    conn = _post(client, vendor="deepseek", api_key=KEY, model="deepseek-v4-pro").get_json()[
        "connection"
    ]
    assert conn["state"] == "ready" and conn["protocol"] == "anthropic" and conn["protocol_checked"]
    assert set(u for u in gw.urls) == {"https://api.deepseek.com/anthropic/v1/messages"}
    # 失败：同一套错误码，但不换协议、不换地址
    gw.routes = {}
    conn = _post(client, id=conn["id"], api_key="sk-other-key-9").get_json()["connection"]
    assert conn["error"] == "endpoint_unusable" and conn["protocol"] == "anthropic"
    assert ai_providers.get(conn["id"])["base_url"] == "https://api.deepseek.com/anthropic"


# =========================================================================== /check
def test_check_endpoint_reprobes_and_updates(client, gw):
    cid = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org/v1", model="m1"
    ).get_json()["connection"]["id"]
    assert _conn(cid)["error"] == "endpoint_unusable"
    gw.routes = {"https://gw.example.org/v1/chat/completions": _j(CHAT_OK)}  # 服务商修好了
    resp = client.post(f"/api/ai/connections/{cid}/check")
    body = resp.get_json()
    assert resp.status_code == 200 and body["ok"] is True, body
    assert (
        body["connection"]["state"] == "ready" and body["connection"]["protocol"] == "openai-chat"
    )
    assert body["probe"]["protocol"] == "openai-chat"
    assert "last_error" not in ai_providers.get(cid) and "probe_reason" not in ai_providers.get(cid)
    assert KEY not in resp.get_data(as_text=True)
    # 又坏了
    gw.routes = {}
    body = client.post(f"/api/ai/connections/{cid}/check").get_json()
    assert body["connection"]["error"] == "endpoint_unusable"


@pytest.mark.parametrize("cid", ["sub:codex", "sub:claude", "nope", "minimax-oauth"])
def test_check_refuses_what_it_cannot_probe(client, gw, cid):
    resp = client.post(f"/api/ai/connections/{cid}/check")
    assert resp.status_code == 404 and resp.get_json()["code"] == "connection_not_found"
    assert gw.calls == []


def test_check_requires_a_session_like_every_endpoint(client, monkeypatch):
    # 旁路只有测试客户端；这里只证明路由挂在统一守卫之下（与 /login 同一个前缀）
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    assert "/api/ai/connections/<cid>/check" in rules


def test_check_of_a_legacy_incompatible_record_is_refused(client, gw):
    ai_providers.save(
        {
            "id": "old",
            "agent": "codex",
            "base_url": "https://api.deepseek.com",
            "api_key": KEY,
            "models": ["m"],
            "default_model": "m",
        }
    )
    ai = config.ai_settings()
    ai["providers"] = [{**p, "wire_api": "chat"} for p in ai["providers"]]
    config.set_ai_settings(ai)
    assert client.post("/api/ai/connections/old/check").status_code == 404
    assert gw.calls == []


def test_5xx_is_transient_no_conclusion_and_does_not_stop_other_protocols(gw):
    gw.routes = {
        "https://gw.example.org/v1/chat/completions": _j({"error": {"message": "x"}}, 502),
        "https://gw.example.org/v1/responses": _j(RESP_OK),
    }
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "m1")
    assert r["reasons"]["openai-chat"] == "network" and r["protocol"] == "openai-responses"
    gw.routes = {
        u: _j({"error": {"message": "x"}}, 500)
        for u in (
            "https://gw.example.org/v1/chat/completions",
            "https://gw.example.org/v1/responses",
            "https://gw.example.org/v1/messages",
        )
    }
    r = ai_probe.detect("https://gw.example.org/v1", KEY, "m1")
    assert r["conclusive"] is False and r["protocol"] is None


def test_save_with_5xx_everywhere_is_not_persisted_as_unusable(client, gw):
    gw.routes = {
        u: _j({"error": {"message": "x"}}, 500)
        for u in (
            "https://gw.example.org/v1/chat/completions",
            "https://gw.example.org/v1/responses",
            "https://gw.example.org/v1/messages",
        )
    }
    body = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org/v1", model="m1"
    ).get_json()
    assert body["probe"]["checked"] is False and body["connection"]["state"] == "ready"
    assert "last_error" not in ai_providers.get(body["connection"]["id"])


@pytest.mark.parametrize(
    ("status", "persisted"),
    [(401, True), (404, True), (400, False), (429, False), (500, False)],
)
def test_catalog_vendor_persists_only_firm_failures(client, gw, status, persisted):
    msg = {"error": {"message": "x"}}
    gw.routes = {"https://api.deepseek.com/anthropic/v1/messages": _j(msg, status)}
    conn = _post(client, vendor="deepseek", api_key=KEY, model="deepseek-v4-pro").get_json()[
        "connection"
    ]
    rec = ai_providers.get(conn["id"])
    assert ("last_error" in rec) is persisted, (status, rec)


# =========================================================================== 叠栈：runner 备选协议与自救回报落盘
def _custom(pid="gw", **kw):
    return _chat_rec(pid, **kw)


def test_fallback_routes_convert_the_address_per_protocol():
    rec = _custom()  # chat @ https://gw.example.org/v1
    assert ai_providers.fallback_routes(rec, "m1") == [
        ("openai-responses", "https://gw.example.org/v1"),
        ("anthropic", "https://gw.example.org"),
    ]
    anth = _custom("a", agent="claude", protocol="anthropic", base_url="https://gw.example.org")
    assert ai_providers.fallback_routes(anth, "m1") == [
        ("openai-chat", "https://gw.example.org/v1"),
        ("openai-responses", "https://gw.example.org/v1"),
    ]
    # 目录厂商 / 账号登录不给备选
    catalog = _custom(
        "d", agent="claude", protocol="anthropic", base_url="https://api.deepseek.com/anthropic"
    )
    assert ai_providers.fallback_routes(catalog, "m1") == []


def test_spawn_overrides_pass_fallback_that_the_runner_accepts():
    from tavotto.engine.agent_runner import runner_cli

    rec = _custom()
    args, _env = ai_providers.spawn_overrides("builtin", rec)
    specs = [args[i + 1] for i, a in enumerate(args) if a == "--fallback"]
    assert specs == [
        "openai-responses=https://gw.example.org/v1",
        "anthropic=https://gw.example.org",
    ]
    # runner 的合规校验（同源、协议名、不重复）认这份
    assert (
        runner_cli._parse_fallbacks(specs, "openai-chat", "https://gw.example.org/v1") is not None
    )
    cat = _custom(
        "d", agent="claude", protocol="anthropic", base_url="https://api.deepseek.com/anthropic"
    )
    assert "--fallback" not in ai_providers.spawn_overrides("builtin", cat)[0]


def test_runtime_report_switches_protocol_and_records_fields():
    rec = _custom(
        "r",
        agent="codex",
        protocol="openai-responses",
        last_error="endpoint_unusable",
        probe_reason="wrong_protocol",
    )
    out = ai_providers.apply_runtime_report(
        "r",
        model="m1",
        report={
            "wire_used": "anthropic",
            "wire_switched_from": "openai-responses",
            "dropped_fields": ["include", "bogus"],
            "token_field": "",
        },
    )
    assert out["protocol"] == "anthropic" and out["agent"] == "claude"
    assert out["base_url"] == "https://gw.example.org"
    assert out["protocol_ok"] == {
        "base_url": "https://gw.example.org",
        "model": "m1",
        "protocol": "anthropic",
        "dropped_fields": ["include"],
    }
    assert "last_error" not in out and "probe_reason" not in out
    assert rec["id"] == "r"


def test_runtime_report_records_chat_token_field_without_switching():
    _custom("c")
    out = ai_providers.apply_runtime_report(
        "c", model="m1", report={"wire_used": "openai-chat", "token_field": "max_completion_tokens"}
    )
    assert out["protocol"] == "openai-chat"
    assert out["protocol_ok"]["token_field"] == "max_completion_tokens"
    # 下次起跑就带上
    args, _ = ai_providers.spawn_overrides("builtin", ai_providers.get("c"))
    assert args[args.index("--max-tokens-field") + 1] == "max_completion_tokens"


def test_runtime_report_is_ignored_when_it_cannot_be_trusted():
    _custom("c")
    assert ai_providers.apply_runtime_report("c", model="m1", report={"wire_used": ""}) is None
    assert (
        ai_providers.apply_runtime_report("c", model="other", report={"wire_used": "anthropic"})
        is None
    )
    assert (
        ai_providers.apply_runtime_report("nope", model="m1", report={"wire_used": "anthropic"})
        is None
    )
    # 运行期间地址被改：回报说的是旧地址，不写
    assert (
        ai_providers.apply_runtime_report(
            "c", model="m1", report={"wire_used": "anthropic"}, base_url="https://old.example/v1"
        )
        is None
    )
    assert ai_providers.get("c")["protocol"] == "openai-chat"
    # 目录厂商不传备选，runner 报来的换协议不信
    _custom(
        "d", agent="claude", protocol="anthropic", base_url="https://api.deepseek.com/anthropic"
    )
    assert (
        ai_providers.apply_runtime_report("d", model="m1", report={"wire_used": "openai-chat"})
        is None
    )


def test_builtin_classify_event_keeps_the_runner_report():
    agent = ai_agents.get_agent("builtin")
    st: dict = {}
    line = json.dumps(
        {
            "type": "done",
            "ok": True,
            "reason": "completed",
            "wire_used": "openai-chat",
            "wire_switched_from": "openai-responses",
            "dropped_fields": [],
            "token_field": "max_tokens",
        }
    )
    agent.classify_event(line, st)
    assert st["runner_report"]["wire_used"] == "openai-chat"


def test_fix_path_builds_fallbacks_and_persists_the_report():
    from tavotto.engine import fixsession

    rec = _custom("fx", protocol="openai-responses")
    conn = ai_providers.builtin_conn(rec)
    assert conn["provider_id"] == "fx" and conn["fallbacks"]
    model, _red = fixsession.build_model(conn)
    assert [w.name for w, _u in model._routes] == ["openai-responses", "openai-chat", "anthropic"]
    wrapped = fixsession._FinishAware(model, __import__("threading").Event())
    assert wrapped.report() == model.report()
    s = fixsession.FixSession(
        id="x",
        scope="p",
        script="a.py",
        prep_session_id="s",
        endpoint=("fx", "m1", rec["base_url"]),
    )
    fixsession.FixSessions._persist_runtime_report(
        s,
        {
            "wire_used": "openai-chat",
            "wire_switched_from": "openai-responses",
            "token_field": "max_tokens",
        },
    )
    assert ai_providers.get("fx")["protocol"] == "openai-chat"
    assert ai_providers.get("fx")["agent"] == "codex"


def test_fix_path_ignores_a_report_for_an_address_changed_mid_run():
    """Codex #1965 P2：修复会话期间用户改了地址——旧会话的回报不得写到新地址上。"""
    from tavotto.engine import fixsession

    _custom("fy", protocol="openai-responses")
    s = fixsession.FixSession(
        id="y",
        scope="p",
        script="a.py",
        prep_session_id="s",
        endpoint=("fy", "m1", "https://old-relay.example.test/v1"),
    )
    fixsession.FixSessions._persist_runtime_report(
        s, {"wire_used": "openai-chat", "wire_switched_from": "openai-responses"}
    )
    assert ai_providers.get("fy")["protocol"] == "openai-responses"


def test_probe_protocols_match_the_providers_list():
    """ai_probe 自带一份协议名（避免与 ai_providers 互引成环）：两份必须一致。"""
    assert ai_probe.PROTOCOLS == ai_providers.PROTOCOLS


def test_legacy_endpoint_form_save_also_detects_the_protocol(client, gw):
    """Codex #1965 P2：「其他兼容接口」表单仍走 PUT /api/ai/endpoints——存完也要探测，只说 chat 的中转站要被认出来。"""
    gw.routes = {"https://relay.example.test/v1/chat/completions": _j(CHAT_OK)}
    body = {
        "id": "relay",
        "label": "relay",
        "agent": "codex",
        "base_url": "https://relay.example.test/v1",
        "api_key": KEY,
        "default_model": "m1",
    }
    assert client.put("/api/ai/endpoints", json=body).status_code == 200
    rec = ai_providers.get("relay")
    assert rec["protocol"] == "openai-chat" and rec["protocol_ok"]["protocol"] == "openai-chat"
    n = len(gw.calls)
    # 只改名称：已有结论，不重探
    assert client.put("/api/ai/endpoints", json={**body, "label": "Relay 2"}).status_code == 200
    assert len(gw.calls) == n


def test_legacy_form_refuses_to_reuse_a_saved_key_on_a_new_origin(client, gw):
    """Codex #1965 P1：「检查连接」表单改了地址、Key 留空——不得把旧 Key 带去新主机（保存拒绝，探测也不发）。"""
    gw.routes = {"https://relay.example.test/v1/chat/completions": _j(CHAT_OK)}
    body = {
        "id": "relay",
        "label": "relay",
        "agent": "codex",
        "base_url": "https://relay.example.test/v1",
        "api_key": KEY,
        "default_model": "m1",
    }
    assert client.put("/api/ai/endpoints", json=body).status_code == 200
    n = len(gw.calls)
    moved = {k: v for k, v in body.items() if k != "api_key"}
    moved["base_url"] = "https://other-host.example.test/v1"
    r = client.put("/api/ai/endpoints", json=moved)
    out = r.get_json()
    assert r.status_code == 400 and out["code"] == "endpoint_invalid"
    assert out["params"] == {"reason": "key_origin_mismatch"}
    assert len(gw.calls) == n
    assert ai_providers.get("relay")["base_url"] == "https://relay.example.test/v1"
    # 同源换路径、Key 留空：照常保存
    same = {**moved, "base_url": "https://relay.example.test/openai/v1"}
    assert client.put("/api/ai/endpoints", json=same).status_code == 200
    assert all(c["url"].startswith("https://relay.example.test/") for c in gw.calls)


# =========================================================================== 型号清单失败 / models 列表
def test_passed_probe_with_no_model_list_is_not_an_error(client, gw, _world):
    _world["fetch"] = (None, "network")
    gw.routes = {"https://gw.example.org/v1/chat/completions": _j(CHAT_OK)}
    resp = _post(
        client, vendor="custom", api_key=KEY, base_url="https://gw.example.org/v1", model="m1"
    )
    conn = resp.get_json()["connection"]
    assert resp.status_code == 200 and conn["protocol_checked"] is True
    assert conn["state"] == "ready" and "error" not in conn


def test_unauthorized_fetch_error_still_overlays_an_error():
    conn = {"id": "x", "state": "ready", "protocol_checked": True}
    out = ai_connections.overlay_fetch_error(conn, "unauthorized")
    assert out["state"] == "error" and out["error"] == "unauthorized"
    # 没探测通过时，清单失败照旧算错；探测通过 + 网络失败不算
    unchecked = {"id": "x", "state": "ready", "protocol_checked": False}
    assert ai_connections.overlay_fetch_error(unchecked, "network")["error"] == "network"
    assert ai_connections.overlay_fetch_error(conn, "network") is conn


def test_models_list_is_saved_deduped_and_default_follows_model(client, gw, _world):
    _world["fetch"] = (None, "network")
    gw.routes = {"https://gw.example.org/v1/chat/completions": _j(CHAT_OK)}
    resp = _post(
        client,
        vendor="custom",
        api_key=KEY,
        base_url="https://gw.example.org/v1",
        models=["m1", "m2", "m2", " m3 "],
    )
    conn = resp.get_json()["connection"]
    assert resp.status_code == 200
    assert conn["default_model"] == "m1" and conn["models"] == ["m1", "m2", "m3"]
    assert ai_providers.get(conn["id"])["models"] == ["m1", "m2", "m3"]
    resp = _post(
        client,
        vendor="custom",
        api_key=KEY,
        base_url="https://gw.example.org/v1",
        model="m2",
        models=["m1"],
        id=conn["id"],
    )
    again = resp.get_json()["connection"]
    assert again["default_model"] == "m2" and set(again["models"]) == {"m1", "m2"}


@pytest.mark.parametrize(
    "models", ["m1", [1], [""], ["  "], ["x" * 201], [f"m{i}" for i in range(51)], {"a": 1}]
)
def test_bad_models_are_refused_with_a_reason(client, gw, models):
    resp = _post(
        client,
        vendor="custom",
        api_key=KEY,
        base_url="https://gw.example.org/v1",
        model="m1",
        models=models,
    )
    j = resp.get_json()
    assert resp.status_code == 400 and j["code"] == "endpoint_invalid"
    assert j["params"]["reason"] == "bad_models"
