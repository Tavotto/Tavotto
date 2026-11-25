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
    acc = siwc_store.Account(
        client_id=CID, subject="s", models=({"id": "gpt-a", "efforts": ["low", "high"]},)
    ).with_tokens(
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
    assert req["body"]["reasoning"] == {"effort": "high"}  # 型号声明了 high：和 Key 连接一样发
    assert req["body"]["include"] == ["reasoning.encrypted_content"]


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


# --------------------------------------------------------------------------- Codex #2212 的两条
def _expiring_provider(calls):
    """令牌还剩 2 分钟（够一次很短的调用，不够一次任务）；刷新函数记下自己被调了几次。"""

    def refresh(ts):
        calls.append(1)
        return siwc_auth.TokenSet("acc-new", "ref-new", siwc_auth._now_ms() + 3_600_000, ts.scopes)

    return siwc_auth.MemoryProvider(
        siwc_auth.TokenSet(
            TOKEN, "ref", siwc_auth._now_ms() + 120_000, frozenset({siwc_auth.REQUIRED_SCOPE})
        ),
        refresh=refresh,
    )


def test_rejected_token_is_marked_so_the_next_call_refreshes(env):
    """服务端 401 拒了还没过期的令牌：AI 修复 / 结构化调用（都经 build_model）下次先刷新，不再拿同一个令牌（不清凭据）。"""
    calls = []
    prov = siwc_auth.MemoryProvider(
        siwc_auth.TokenSet(
            TOKEN, "ref", siwc_auth._now_ms() + 3_600_000, frozenset({siwc_auth.REQUIRED_SCOPE})
        ),
        refresh=lambda ts: (
            calls.append(1)
            or siwc_auth.TokenSet("acc-new", "ref-new", siwc_auth._now_ms() + 3_600_000, ts.scopes)
        ),
    )
    siwc_auth.set_provider_for_tests(prov)
    conn = ai_connections.first_key_connection(refresh=False)

    def post(url, headers, body, *, timeout, reader=None):
        return 401, {}, b'{"error": {"code": "subscription_sharing_invalid_user"}}'

    model, _ = fixsession.build_model(conn, post=post, max_attempts=1)
    import threading
    import time as _t

    from tavotto.engine.agent_runner import wires

    with pytest.raises(RunnerFailure) as ei:
        model.complete(
            system="s",
            tools=[],
            history=[wires.Msg(role="user", text="hi")],
            max_output_tokens=16,
            cancel=threading.Event(),
            deadline=_t.monotonic() + 30,
        )
    assert ei.value.reason == "auth"
    assert prov.access_token() == "acc-new" and calls == [1]


def test_structured_call_never_refreshes_beyond_its_budget(env):
    """剩余时限不够一次刷新（REFRESH_NEEDS_S）：不联网刷新、不等锁；现有令牌够这次用就照常发。"""
    from dataclasses import replace

    calls = []
    siwc_auth.set_provider_for_tests(_expiring_provider(calls))
    sent = []

    def post(url, headers, body, *, timeout, reader=None):
        sent.append(headers.get("Authorization"))
        data = _sse_completed('{"ok": true}')
        return 200, {}, (reader(io.BytesIO(data)) if reader else data)

    short = replace(ai_structured.DEFAULT_LIMITS, timeout_s=10.0)
    res = ai_structured.call_structured(
        system="s", user="u", validate=lambda o: o, post=post, limits=short
    )
    assert res.ok and calls == [] and sent == [f"Bearer {TOKEN}"]


def test_structured_call_with_a_stale_token_and_no_budget_fails_fast(env):
    calls = []
    prov = _expiring_provider(calls)
    siwc_auth.set_provider_for_tests(prov)
    from dataclasses import replace

    tiny = replace(ai_structured.DEFAULT_LIMITS, timeout_s=10.0)
    prov.mark_rejected("http_401")  # 令牌被拒过：必须刷新，但预算不够
    res = ai_structured.call_structured(
        system="s", user="u", validate=lambda o: o, post=lambda *a, **k: (500, {}, b""), limits=tiny
    )
    assert (res.ok, res.reason, res.detail) == (False, "no_connection", "token_unavailable")
    assert calls == []


def test_cached_token_does_not_wait_for_a_refresh_in_progress(env):
    import threading

    prov = _expiring_provider([])
    held = threading.Event()
    release = threading.Event()

    def hold():
        with prov._lock:
            held.set()
            release.wait(5)

    th = threading.Thread(target=hold)
    th.start()
    held.wait(5)
    try:
        with pytest.raises(siwc_auth.TokenUnavailable):
            prov.access_token(min_valid_s=5, allow_refresh=False)
    finally:
        release.set()
        th.join()
    assert prov.access_token(min_valid_s=5, allow_refresh=False) == TOKEN
