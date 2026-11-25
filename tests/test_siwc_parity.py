"""SIWC 与 API Key 连接体验相同（用户 2026-11-25「SIWC 与 API 的体验完全相同」，ADR 0209 §七）。全 Mock，不联网。

判据的主语：
* `first_key_connection`（AI 修复 / 结构化调用 / 数据出图 / 改图方案「挑连接」的唯一口径）的返回值：只登录了 ChatGPT 时就是 SIWC；有 Key 时 Key 在前；
* `fixsession.build_model` 造出的模型对象：协议是 `siwc-responses`、令牌现取、Redactor 认得它；
* `ai_structured.call_structured` 真正发出去的请求（经注入的 `post` 缝）：官方地址、Bearer、`stream: true`、没有禁用字段；
* 计价：订阅额度不按 API 价目估钱。
"""

from __future__ import annotations

import io
import json

import pytest

from tavotto.engine import (
    ai_connections,
    ai_pricing,
    ai_providers,
    ai_structured,
    fixsession,
    siwc_auth,
    siwc_store,
)
from tavotto.engine.agent_runner import siwc_wire
from tavotto.engine.agent_runner.wires import RunnerFailure

CID = "oaiapp_parity1"
TOKEN = "acc-PARITY-SECRET"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(siwc_wire.ENV_FLAG, "1")
    monkeypatch.delenv(ai_connections.ENV_BUILTIN, raising=False)
    monkeypatch.setattr(ai_providers, "list_providers", lambda: [])
    acc = siwc_store.Account(client_id=CID, subject="s", models=({"id": "gpt-a"},)).with_tokens(
        siwc_auth.TokenSet(
            access=TOKEN,
            refresh="ref",
            expires_at_ms=siwc_auth._now_ms() + 3_600_000,
            scopes=frozenset({siwc_auth.REQUIRED_SCOPE}),
        )
    )
    siwc_store.save(acc)
    siwc_store.set_active(CID)
    siwc_auth.set_provider_for_tests(siwc_auth.MemoryProvider(acc.tokens()))
    yield monkeypatch
    siwc_auth.set_provider_for_tests(None)


def test_chatgpt_only_user_gets_siwc_for_every_pick(env):
    asked = ai_connections.first_key_connection(refresh=False)
    assert asked["provider_id"] == "siwc:chatgpt" and asked["wire"] == siwc_wire.WIRE_NAME
    assert asked["api_key"] == ""  # 只问「有没有」：不取令牌
    ran = ai_connections.first_key_connection(refresh=True)
    assert ran["api_key"] == TOKEN and ran["base_url"] == siwc_wire.BASE_URL
    assert ai_connections.builtin_usable() is True
    assert fixsession.available() is True


def test_saved_key_connection_still_comes_first(env):
    key = {
        "id": "k",
        "agent": "claude",
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key": "sk-x",
        "models": ["m"],
        "default_model": "m",
    }
    env.setattr(ai_providers, "list_providers", lambda: [key])
    assert ai_connections.first_key_connection()["provider_id"] == "k"


def test_flag_off_or_signed_out_means_no_siwc(env):
    env.delenv(siwc_wire.ENV_FLAG)
    assert ai_connections.first_key_connection() is None
    env.setenv(siwc_wire.ENV_FLAG, "1")
    siwc_store.save(siwc_store.active().signed_out())
    assert ai_connections.first_key_connection() is None


def test_build_model_uses_the_siwc_wire_and_fetches_the_token(env):
    conn = ai_connections.first_key_connection(refresh=False)
    model, redactor = fixsession.build_model(conn)
    assert model.wire.name == siwc_wire.WIRE_NAME
    assert TOKEN not in redactor.redact(f"token={TOKEN}")
    siwc_auth.set_provider_for_tests(siwc_auth.DisabledProvider())
    with pytest.raises(RunnerFailure) as ei:
        fixsession.build_model(conn)
    assert ei.value.reason == "auth"


def _sse_completed(text: str) -> bytes:
    item = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }
    resp = {
        "id": "r1",
        "status": "completed",
        "output": [item],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }
    evs = [
        {"type": "response.output_item.done", "item": item},
        {"type": "response.completed", "response": resp},
    ]
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in evs)


def test_structured_call_goes_out_compliant(env):
    sent = []

    def post(url, headers, body, *, timeout, reader=None):
        sent.append({"url": url, "headers": dict(headers), "body": json.loads(json.dumps(body))})
        data = _sse_completed('{"ok": true}')
        return 200, {}, (reader(io.BytesIO(data)) if reader else data)

    res = ai_structured.call_structured(
        system="s", user="u", validate=lambda o: o, post=post, effort="high"
    )
    assert res.ok, res
    (req,) = sent
    assert req["url"] == siwc_wire.BASE_URL + "/responses"
    assert req["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert req["body"]["stream"] is True and req["body"]["store"] is False
    assert not set(req["body"]) & set(siwc_wire.FORBIDDEN_FIELDS)
    assert "reasoning" not in req["body"]  # 思考强度：SIWC 上未经真服务验证，不发


def test_structured_call_without_token_is_no_connection(env):
    siwc_auth.set_provider_for_tests(siwc_auth.DisabledProvider())
    res = ai_structured.call_structured(
        system="s", user="u", validate=lambda o: o, post=lambda *a, **k: (500, {}, b"")
    )
    assert (res.ok, res.reason, res.detail) == (False, "no_connection", "needs_login")


def test_subscription_usage_is_not_priced_as_api(env):
    conn = ai_connections.first_key_connection()
    assert ai_pricing.vendor_of(conn) is None
    assert ai_pricing.vendor_of(ai_providers.siwc_record()) is None
