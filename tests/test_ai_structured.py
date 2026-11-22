"""`engine/ai_structured.py`：无工具、单轮、只要结构化结果的模型调用（ADR 0178 修订 2026-11-19）。

主语：**调用方看到的结局**——任何输入、任何服务端行为，`call_structured` 要么回一个过了校验的 dict，要么回闭集原因码里的一个，永不抛、不发第二次传输失败的重试、
不带工具、不联网（本文件所有请求都落在假传输上，真传输与套接字被钉死）。假传输产出的是各协议**真实形状**的回复体，经真的 `wires` 解析与状态码分类。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import data_figure_cases as D  # noqa: E402

from tavotto.engine import ai_connections, ai_structured as S, fixsession  # noqa: E402,F401
from tavotto.engine.agent_runner import loop, wires  # noqa: E402

JSON_OK = '{"answer": 1}'
_REAL_FIND_CLI = S.find_cli_connection  # conftest 的 autouse 会把模块上的换成「没有」


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """真传输与套接字一律钉死：本文件任何路径摸到网络都当场红。"""

    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)


def validate_answer(obj: dict) -> dict:
    if obj.get("answer") != 1:
        raise ValueError("shape")
    return obj


def call(script, *, wire="anthropic", limits=S.DEFAULT_LIMITS, validate=validate_answer, **kw):
    transport = D.FakeTransport(list(script))
    res = S.call_structured(
        system="SYSTEM-TEXT-XYZ",
        user="USER-TEXT-XYZ",
        validate=validate,
        limits=limits,
        conn=D.fake_conn(wire),
        post=transport,
        **kw,
    )
    return res, transport


# --------------------------------------------------------------------------- 成功
@pytest.mark.parametrize("wire", D.FAKE_WIRES)
def test_success_on_every_wire_sends_one_toolless_request(wire):
    res, t = call([D.reply_body(wire, JSON_OK, in_tokens=700, out_tokens=40)], wire=wire)
    assert res.ok and res.value == {"answer": 1} and res.reason == ""
    assert (res.attempts, len(t.calls)) == (1, 1)
    assert (res.input_tokens, res.output_tokens, res.usage_reported) == (700, 40, True)
    assert res.wire == wire and res.model == "fake-model"
    body = t.calls[0]["body"]
    assert "tools" not in body and "tool_choice" not in body
    assert "SYSTEM-TEXT-XYZ" in json.dumps(body) and "USER-TEXT-XYZ" in json.dumps(body)


def test_fenced_and_prose_wrapped_json_is_accepted():
    for text in ('```json\n{"answer": 1}\n```', '好的，结果如下：{"answer": 1} 以上。'):
        res, t = call([D.reply_body("anthropic", text)])
        assert res.ok and len(t.calls) == 1


def test_cost_is_none_for_unpriced_endpoints_not_zero():
    res, _ = call([D.reply_body("anthropic", JSON_OK)])
    assert res.cost_usd is None  # `.invalid` 地址不冒充官方计价
    official = {
        **D.fake_conn("anthropic"),
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-5-5",
    }
    t = D.FakeTransport([D.reply_body("anthropic", JSON_OK, in_tokens=1000, out_tokens=100)])
    res = S.call_structured(system="s", user="u", validate=validate_answer, conn=official, post=t)
    assert res.ok and res.cost_usd == pytest.approx((1000 * 2.0 + 100 * 10.0) / 1_000_000)


# --------------------------------------------------------------------------- 重试：只在不是合法 JSON 时、至多一次
def test_one_retry_when_reply_is_not_json_and_it_carries_the_notice():
    res, t = call(
        [
            D.reply_body("anthropic", "不是 JSON", in_tokens=500, out_tokens=10),
            D.reply_body("anthropic", JSON_OK, in_tokens=600, out_tokens=20),
        ]
    )
    assert res.ok and res.attempts == 2 and len(t.calls) == 2
    assert (res.input_tokens, res.output_tokens) == (1100, 30)  # 两次累加
    assert S.RETRY_NOTICE in json.dumps(t.calls[1]["body"], ensure_ascii=False)
    assert S.RETRY_NOTICE not in json.dumps(t.calls[0]["body"], ensure_ascii=False)


@pytest.mark.parametrize("wire", D.FAKE_WIRES)
def test_two_non_json_replies_fail_as_invalid_output_with_exactly_two_posts(wire):
    res, t = call([D.reply_body(wire, "no"), D.reply_body(wire, "still no")], wire=wire)
    assert not res.ok and res.reason == "invalid_output" and res.detail == "not_json"
    assert len(t.calls) == 2 and res.attempts == 2


def test_max_retries_zero_means_no_second_request():
    res, t = call([D.reply_body("anthropic", "no")], limits=S.Limits(max_retries=0))
    assert res.reason == "invalid_output" and len(t.calls) == 1


def test_wrong_shape_is_not_retried():
    res, t = call([D.reply_body("anthropic", '{"answer": 2}')])
    assert (res.reason, res.detail, len(t.calls)) == ("invalid_output", "shape", 1)


def test_validate_raising_anything_or_returning_non_dict_is_invalid_output():
    def explode(obj):
        raise RuntimeError("secret detail must not leak")

    res, _ = call([D.reply_body("anthropic", JSON_OK)], validate=explode)
    assert res.reason == "invalid_output" and "secret" not in repr(res)
    res, _ = call([D.reply_body("anthropic", JSON_OK)], validate=lambda o: ["not", "dict"])
    assert res.reason == "invalid_output"


def test_a_json_array_or_nan_is_not_an_object():
    for text in ("[1, 2]", '{"answer": NaN}'):
        res, t = call([D.reply_body("anthropic", text), D.reply_body("anthropic", text)])
        assert res.reason == "invalid_output" and len(t.calls) == 2


# --------------------------------------------------------------------------- 拒答 / 超时 / 预算 / 传输
@pytest.mark.parametrize("wire", D.FAKE_WIRES)
def test_refusal_on_every_wire_is_refused_without_retry_and_still_counts_usage(wire):
    res, t = call([D.refusal_body(wire)], wire=wire)
    assert res.reason == "refused" and len(t.calls) == 1
    assert res.input_tokens == 900  # 拒答照样计费


def test_socket_timeout_and_total_deadline_both_map_to_timeout():
    res, t = call([wires.RunnerFailure("network", "TimeoutError", retryable=True)])
    assert (res.reason, len(t.calls)) == ("timeout", 1)  # 传输失败不重发

    def slow(body):
        time.sleep(0.6)
        return D.reply_body("anthropic", JSON_OK)

    started = time.monotonic()
    res, _ = call([slow], limits=S.Limits(timeout_s=0.2))
    assert res.reason == "timeout" and time.monotonic() - started < 0.55


def test_the_deadline_is_shared_by_both_attempts():
    """第一次用掉大半总时限，第二次只拿到剩下的那点（不是重新给满）。不按墙钟判结局：第二次一直不回，
    只有总时限能结束它；单次请求的超时是确定的「剩余」，慢 runner 上只会更小（#2085 合并组 macOS 抖红过）。"""
    first_s, limit_s = 0.15, 0.2

    def slow_bad(body):
        time.sleep(first_s)
        return D.reply_body("anthropic", "no")

    def never_answers(body):
        threading.Event().wait(5)  # 远超总时限：要是迟到的回复被收下，结局就不是 timeout
        return D.reply_body("anthropic", "no")

    res, t = call([slow_bad, never_answers], limits=S.Limits(timeout_s=limit_s))
    assert res.reason == "timeout"
    assert len(t.calls) in (1, 2)  # 慢机器上第一次就用完了总时限，第二次根本不发
    if len(t.calls) == 2:
        assert t.calls[1]["timeout"] <= limit_s - first_s


def test_input_over_budget_sends_nothing():
    res, t = call([], limits=S.Limits(max_input_chars=10))
    assert (res.reason, res.detail, len(t.calls), res.attempts) == ("over_budget", "input", 0, 0)


@pytest.mark.parametrize("wire", D.FAKE_WIRES)
def test_truncated_output_is_over_budget_and_not_retried(wire):
    res, t = call([D.truncated_body(wire)], wire=wire)
    assert (res.reason, res.detail, len(t.calls)) == ("over_budget", "output", 1)


def test_oversized_text_from_an_endpoint_that_ignores_max_tokens_is_over_budget():
    res, _ = call([D.reply_body("anthropic", "x" * 500)], limits=S.Limits(max_output_tokens=10))
    assert res.reason == "over_budget"


@pytest.mark.parametrize(
    ("status", "reason", "detail", "posts"),
    [
        (401, "auth", "http_401", 1),
        (403, "auth", "http_403", 1),
        (402, "quota", "http_402", 1),
        (429, "quota", "rate_limited", 1),
        (500, "transport", "http_500", 1),  # 本模块不重发传输失败
        (404, "transport", "http_404", 1),
    ],
)
def test_http_statuses_map_to_the_closed_set(status, reason, detail, posts):
    res, t = call([status])
    assert (res.reason, res.detail, len(t.calls)) == (reason, detail, posts)


def test_html_page_from_a_wrong_address_is_transport():
    res, _ = call([b"<html>login</html>"])
    assert (res.reason, res.detail) == ("transport", "html_response")


def test_tool_call_reply_is_invalid_output():
    body = {
        "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 5},
    }
    res, _ = call([body])
    assert (res.reason, res.detail) == ("invalid_output", "tool_call")


def test_failure_mapping_is_total_over_the_runner_reasons():
    mapped = {}
    for reason in loop.REASONS:
        out = S.map_failure(wires.RunnerFailure(reason, "x"))
        assert out[0] in S.REASONS, reason
        mapped[reason] = out[0]
    assert (
        mapped["refused"] == "refused" and mapped["auth"] == "auth" and mapped["quota"] == "quota"
    )
    assert mapped["budget"] == "timeout"
    assert S.map_failure(wires.RunnerFailure("network", "ReadTimeout"))[0] == "timeout"
    assert S.map_failure(wires.RunnerFailure("network", "http_502"))[0] == "transport"


def test_never_raises_even_when_the_connection_is_malformed():
    res = S.call_structured(
        system="s",
        user="u",
        validate=validate_answer,
        conn={"wire": "anthropic"},
        post=D.FakeTransport(),
    )
    assert not res.ok and res.reason == "transport" and res.detail == "internal"
    res = S.call_structured(system="s", user="u", validate=validate_answer, conn={"wire": "nope"})
    assert res.reason == "transport"


def test_result_and_failure_carry_no_prompt_reply_or_key():
    res, _ = call(
        [
            D.reply_body("anthropic", "回复里的内容 SECRET-REPLY"),
            D.reply_body("anthropic", "SECRET-REPLY"),
        ]
    )
    dumped = repr(res)
    for needle in (
        "SYSTEM-TEXT-XYZ",
        "USER-TEXT-XYZ",
        "SECRET-REPLY",
        "sk-test-NOT-A-REAL-KEY",
        "fake.invalid",
    ):
        assert needle not in dumped


def test_no_logging_and_the_key_stays_out_of_the_model_repr(caplog):
    caplog.set_level("DEBUG")
    res, _ = call([D.reply_body("anthropic", JSON_OK)])
    assert res.ok and caplog.records == []
    model, _red = fixsession.build_model(D.fake_conn("anthropic"))
    assert "sk-test" not in repr(model)


def test_limits_are_validated():
    with pytest.raises(ValueError):
        S.Limits(max_retries=2)
    with pytest.raises(ValueError):
        S.Limits(timeout_s=0)


# --------------------------------------------------------------------------- 连接
def test_resolve_connection_no_connection(monkeypatch):
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: None)
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    assert S.resolve_connection() == (None, "no_connection", "none")  # conftest：没有 CLI 连接
    res = S.call_structured(system="s", user="u", validate=validate_answer)
    assert (res.reason, res.attempts) == ("no_connection", 0)


def test_subscription_only_with_an_adapter_that_cannot_do_it_is_unsupported_connection(
    monkeypatch,
):
    """订阅登录里适配器没覆写 `build_structured_command` = `unsupported_connection`（能做的那条路见 `test_ai_structured_cli.py`）。"""
    from tavotto.engine import ai_agents, ai_bridge

    monkeypatch.setattr(S, "find_cli_connection", _REAL_FIND_CLI)
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: None)
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(
        ai_bridge,
        "capabilities",
        lambda refresh=False: {
            "connections": [{"id": "sub:codex", "state": "ready", "default_model": "m"}]
        },
    )
    monkeypatch.setattr(
        ai_agents.CodexAgent,
        "build_structured_command",
        ai_agents.AgentDefinition.build_structured_command,
    )
    res = S.call_structured(system="s", user="u", validate=validate_answer)
    assert (res.reason, res.detail) == ("unsupported_connection", "cli_only")


def test_builtin_switched_off_by_ops_is_no_connection(monkeypatch):
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: False)
    assert S.resolve_connection() == (None, "no_connection", "builtin_disabled")


def test_default_connection_is_the_first_key_connection(monkeypatch):
    conn = D.fake_conn("openai-chat")
    asked = []
    monkeypatch.setattr(
        ai_connections, "first_key_connection", lambda refresh=False: asked.append(refresh) or conn
    )
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    t = D.FakeTransport([D.reply_body("openai-chat", JSON_OK)])
    res = S.call_structured(system="s", user="u", validate=validate_answer, post=t)
    assert res.ok and res.wire == "openai-chat" and asked == [False]  # 挑连接不联网


def test_resolver_exceptions_stay_inside_the_never_raise_boundary(monkeypatch):
    def broken(refresh=False):
        raise OSError("disk gone")

    monkeypatch.setattr(ai_connections, "first_key_connection", broken)
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    res = S.call_structured(system="s", user="u", validate=validate_answer)
    assert (res.ok, res.reason) == (False, "transport")


# --------------------------------------------------------------------------- 解析
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}', {"a": 1}),
        ('说明 {"a": {"b": 2}} 完', {"a": {"b": 2}}),
        ("[1]", None),
        ("", None),
        ('{"a": Infinity}', None),
        ("{" * 5000, None),
    ],
)
def test_parse_json_object(text, expected):
    assert S.parse_json_object(text) == expected


# --------------------------------------------------------------------------- 实际请求数（传输层数）
def codex_shape(wire="openai-chat"):
    """Codex 复现的形状：404 换协议（不消耗尝试）+ 一次非 JSON 回复（重试）= 一次 `complete()` 序列里 3 个 HTTP 请求。"""
    conn = {**D.fake_conn(wire), "fallbacks": [("anthropic", "https://fake.invalid")]}
    script = [404, D.reply_body("anthropic", "不是 JSON"), D.reply_body("anthropic", JSON_OK)]
    return conn, D.FakeTransport(script)


def test_requests_are_counted_at_the_transport_not_per_attempt():
    conn, t = codex_shape()
    res = S.call_structured(system="s", user="u", validate=validate_answer, conn=conn, post=t)
    assert res.ok and res.attempts == 2 and res.requests == 3 == len(t.calls)


def test_max_requests_is_a_hard_cap_including_recovery_requests():
    conn, t = codex_shape()
    res = S.call_structured(
        system="s", user="u", validate=validate_answer, conn=conn, post=t, max_requests=2
    )
    assert (res.reason, res.detail) == ("over_budget", "requests")
    assert res.requests == 2 == len(t.calls)  # 第 3 个请求没有发出
    conn, t = codex_shape()
    res = S.call_structured(
        system="s", user="u", validate=validate_answer, conn=conn, post=t, max_requests=0
    )
    assert res.reason == "over_budget" and t.calls == []


# --------------------------------------------------------------------------- 解析连接算进总时限
def _stub_connection(monkeypatch, conn, *, oauth=False):
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: conn)
    from tavotto.engine import ai_oauth, ai_providers

    refreshed = []
    rec = {"id": "p1", "auth": "oauth" if oauth else "key"}
    monkeypatch.setattr(ai_providers, "get", lambda pid: rec)

    def slow_refresh(r):
        refreshed.append("oauth")
        time.sleep(5)
        return r

    monkeypatch.setattr(ai_oauth, "ensure_fresh", slow_refresh)
    monkeypatch.setattr(ai_providers, "ensure_api_root", lambda r: refreshed.append("root") or r)
    monkeypatch.setattr(ai_providers, "builtin_conn", lambda r, model=None: conn)
    return refreshed


def test_small_budget_never_starts_a_network_refresh(monkeypatch):
    conn = {**D.fake_conn("anthropic"), "provider_id": "p1"}
    refreshed = _stub_connection(monkeypatch, conn, oauth=True)
    started = time.monotonic()
    t = D.FakeTransport([D.reply_body("anthropic", JSON_OK)])
    res = S.call_structured(
        system="s", user="u", validate=validate_answer, limits=S.Limits(timeout_s=1.0), post=t
    )
    assert res.ok and refreshed == [] and time.monotonic() - started < 1.0


def test_enough_budget_refreshes_only_the_chosen_record(monkeypatch):
    conn = {**D.fake_conn("anthropic"), "provider_id": "p1"}
    refreshed = _stub_connection(monkeypatch, conn, oauth=False)
    c, why, _ = S.resolve_connection(60.0)
    assert c is conn and refreshed == ["root"]  # 非账号登录：只核地址，且只这一条
    refreshed = _stub_connection(monkeypatch, conn, oauth=True)
    monkeypatch.setattr(
        "tavotto.engine.ai_oauth.ensure_fresh", lambda r: refreshed.append("oauth") or r
    )
    S.resolve_connection(60.0)
    assert refreshed == ["oauth"]


def test_a_slow_resolution_returns_timeout_near_the_budget_and_sends_nothing(monkeypatch):
    conn = D.fake_conn("anthropic")

    def slow(refresh=False):
        time.sleep(0.4)
        return conn

    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(ai_connections, "first_key_connection", slow)
    t = D.FakeTransport([D.reply_body("anthropic", JSON_OK)])
    started = time.monotonic()
    res = S.call_structured(
        system="s", user="u", validate=validate_answer, limits=S.Limits(timeout_s=0.2), post=t
    )
    assert (res.reason, res.detail) == ("timeout", "resolve")
    assert t.calls == [] and time.monotonic() - started < 0.7


def test_needs_login_on_refresh_is_no_connection(monkeypatch):
    from tavotto.engine import ai_oauth

    conn = {**D.fake_conn("anthropic"), "provider_id": "p1"}
    _stub_connection(monkeypatch, conn, oauth=True)

    def broken(r):
        raise ai_oauth.NeedsLogin("x")

    monkeypatch.setattr(ai_oauth, "ensure_fresh", broken)
    assert S.resolve_connection(60.0) == (None, "no_connection", "needs_login")
