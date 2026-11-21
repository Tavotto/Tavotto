"""结构化调用的「小型号 + 最低档」策略（ADR 0178「读懂数据」，2026-11-21 实测）。

主语：**一次调用实际发给服务端的请求体（型号 / reasoning.effort）与最终结局**——
* 只有 `ai_catalog.json` 的 `structured_small` 登记过的官方端点（现在：OpenAI 官方 + Responses + Key 连接）、且调用方显式开了 `auto_small` 才换型号；
  其它连接（Anthropic 官方、第三方 / 自定义端点、账号登录）发出的请求与关着时逐字相同；
* 这把 Key 用不了小型号（400 / 404 / 422 / 403）→ 同一总时限与请求预算内改用用户原本的型号与强度再来一次，并在进程内记一阵；
  timeout / quota / 密钥被拒 / 拒答 / 非法输出不换型号；
* 改图 / 修复路径的型号与强度不受影响；目录里 `none` 不进界面可选档位。
全部落在假传输上，不联网。
"""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import data_figure_cases as D  # noqa: E402

from tavotto.engine import (  # noqa: E402
    ai_catalog,
    ai_data_roles as R,
    ai_providers,
    ai_structured as S,
)
from tavotto.engine.agent_runner import wires  # noqa: E402

JSON_OK = '{"answer": 1}'
OFFICIAL = "https://api.openai.com/v1"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    S.reset_small_for_tests()
    yield
    S.reset_small_for_tests()


def validate_answer(obj: dict) -> dict:
    if obj.get("answer") != 1:
        raise ValueError("shape")
    return obj


def official(model="gpt-6.2-sol", **kw) -> dict:
    return {
        "wire": "openai-responses",
        "base_url": OFFICIAL,
        "api_key": "sk-test-NOT-A-REAL-KEY",
        "model": model,
        "auth": "key",
        "provider_id": "openai",
        **kw,
    }


def run(conn, script, *, auto_small=True, **kw):
    t = D.FakeTransport(list(script))
    res = S.call_structured(
        system="SYS",
        user="USR",
        validate=validate_answer,
        conn=conn,
        post=t,
        auto_small=auto_small,
        **kw,
    )
    return res, t


def ok_body(wire="openai-responses"):
    return D.reply_body(wire, JSON_OK, in_tokens=1000, out_tokens=100)


def effort_of(call: dict):
    return (call["body"].get("reasoning") or {}).get("effort")


# --------------------------------------------------------------------------- 换型号
def test_official_openai_key_uses_the_small_model_with_effort_none():
    res, t = run(official("gpt-6.2-sol"), [ok_body()])
    assert res.ok and not res.fell_back and res.model == "gpt-6-luna"
    assert len(t.calls) == 1
    assert t.calls[0]["body"]["model"] == "gpt-6-luna" and effort_of(t.calls[0]) == "none"


def test_the_policy_is_off_unless_the_caller_opts_in():
    res, t = run(official("gpt-6.2-sol"), [ok_body()], auto_small=False)
    assert res.ok and t.calls[0]["body"]["model"] == "gpt-6.2-sol"
    default = S.call_structured(
        system="s",
        user="u",
        validate=validate_answer,
        conn=official(),
        post=D.FakeTransport([ok_body()]),
    )
    assert default.model == "gpt-6.2-sol"  # 默认关


def test_a_user_who_already_picked_the_small_model_is_left_alone():
    res, t = run(official("gpt-6-luna"), [ok_body()])
    assert res.ok and t.calls[0]["body"]["model"] == "gpt-6-luna"
    assert S.small_plan(official("GPT-6-Luna")) is None  # 大小写不敏感


def test_benchmark_connection_with_explicit_effort_is_never_switched():
    conn = official("gpt-6.2-sol", effort="low")
    res, t = run(conn, [ok_body()])
    assert t.calls[0]["body"]["model"] == "gpt-6.2-sol" and effort_of(t.calls[0]) == "low"


def _same_requests(conn, wire):
    a, ta = run(conn, [D.reply_body(wire, JSON_OK)], auto_small=True)
    b, tb = run(conn, [D.reply_body(wire, JSON_OK)], auto_small=False)
    assert a.ok and b.ok
    assert [c["url"] for c in ta.calls] == [c["url"] for c in tb.calls]
    assert [c["body"] for c in ta.calls] == [c["body"] for c in tb.calls]
    return ta


@pytest.mark.parametrize(
    "conn",
    [
        {
            **D.fake_conn("anthropic"),
            "base_url": "https://api.anthropic.com",
            "model": "claude-sonnet-5-5",
        },
        {**official("gpt-6.2-sol"), "base_url": "https://api.deepseek.com/v1"},  # 第三方网关
        {**official("gpt-6.2-sol"), "base_url": "https://relay.example.com/v1"},  # 自填中转站
        {**official("gpt-6.2-sol"), "wire": "openai-chat"},  # 同端点另一种协议
        {**official("gpt-6.2-sol"), "auth": "bearer"},  # 账号登录
        {**official("gpt-6.2-sol"), "base_url": "https://api.openai.com.evil.example/v1"},
    ],
    ids=["anthropic", "third-party", "custom", "chat-wire", "oauth", "lookalike-host"],
)
def test_other_connections_send_byte_identical_requests(conn):
    assert S.small_plan(conn) is None
    _same_requests(conn, conn["wire"])


# --------------------------------------------------------------------------- 兜底
@pytest.mark.parametrize(
    ("status", "reason"),
    [(400, "transport"), (404, "transport"), (422, "transport"), (403, "auth")],
)
def test_unusable_small_model_falls_back_once_then_is_remembered(status, reason):
    res, t = run(official("gpt-6.2-sol"), [status, ok_body()])
    assert res.ok and res.fell_back and res.model == "gpt-6.2-sol"
    assert (res.requests, res.attempts) == (2, 2)
    assert [c["body"]["model"] for c in t.calls] == ["gpt-6-luna", "gpt-6.2-sol"]
    assert effort_of(t.calls[0]) == "none"
    # 原型号这一次发的强度 = 没有自动策略时的强度（这里没有存储的连接记录 = 不发 reasoning）
    plain, tp = run(official("gpt-6.2-sol"), [ok_body()], auto_small=False)
    assert t.calls[1]["body"] == tp.calls[0]["body"]
    # 负缓存期内：下一张表直接用原型号，只发一次
    res2, t2 = run(official("gpt-6.2-sol"), [ok_body()])
    assert res2.ok and not res2.fell_back and len(t2.calls) == 1
    assert t2.calls[0]["body"]["model"] == "gpt-6.2-sol"
    assert S.effective_choice(official("gpt-6.2-sol"), "low")[0] == "gpt-6.2-sol"


def test_the_memory_is_per_connection_identity_and_expires(monkeypatch):
    run(official("gpt-6.2-sol"), [400, ok_body()])
    other_key = official("gpt-6.2-sol", api_key="sk-another-key")
    assert S.small_plan(other_key) is not None  # 换了 Key = 另一个身份
    assert S.small_plan(official("gpt-6.2-sol", provider_id="other")) is not None
    real = time.time()
    monkeypatch.setattr(S.time, "time", lambda: real + S.SMALL_UNAVAILABLE_TTL_S + 1)
    assert S.small_plan(official("gpt-6.2-sol")) is not None  # 到期忘记


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ([401], "auth"),
        ([402], "quota"),
        ([429], "quota"),
        ([500], "transport"),
        ([wires.RunnerFailure("network", "TimeoutError", retryable=True)], "timeout"),
        ([D.reply_body("openai-responses", "not json at all")] * 2, "invalid_output"),
    ],
    ids=["401", "402", "429", "500", "timeout", "not-json"],
)
def test_other_failures_do_not_switch_model(script, reason):
    res, t = run(official("gpt-6.2-sol"), script)
    assert not res.ok and res.reason == reason and not res.fell_back
    assert {c["body"]["model"] for c in t.calls} == {"gpt-6-luna"}
    assert S.small_plan(official("gpt-6.2-sol")) is not None  # 也不记「不可用」


def test_no_retry_when_the_request_budget_has_one_left():
    res, t = run(official("gpt-6.2-sol"), [400], max_requests=1)
    assert (
        not res.ok and (res.reason, res.detail) == ("transport", "http_400") and res.requests == 1
    )
    assert len(t.calls) == 1 and not res.fell_back
    res2, t2 = run(official("gpt-6.2-sol"), [ok_body()], max_requests=1)  # 已记下不可用：直接原型号
    assert res2.ok and t2.calls[0]["body"]["model"] == "gpt-6.2-sol"


def test_fallback_shares_the_total_deadline():
    def slow_400(body):
        time.sleep(0.25)
        return 400

    res, t = run(
        official("gpt-6.2-sol"),
        [slow_400, ok_body()],
        limits=S.Limits(timeout_s=0.2),
    )
    assert not res.ok and len(t.calls) == 1  # 第一次就用完了总时限：不再试


def test_both_requests_count_and_both_models_are_priced():
    res, t = run(
        official("gpt-6.2-sol"),
        [D.reply_body("openai-responses", "{", in_tokens=500, out_tokens=10), 400, ok_body()],
    )
    # 小型号先回一次非法 JSON（重试一次，回 400）→ 换原型号成功：三个请求
    assert res.ok and res.fell_back and res.requests == 3
    assert res.input_tokens == 500 + 1000 and res.output_tokens == 10 + 100


# --------------------------------------------------------------------------- 缓存键与用量
def test_cache_identity_follows_the_effective_model_and_effort():
    a = R._identity(official("gpt-6.2-sol"))
    assert a == "openai|gpt-6-luna|none"
    assert (
        R._identity(official("gpt-6.1-terra")) == a
    )  # 实际用的是同一个小型号 + 强度 = 同一判断来源
    assert R._identity({**D.fake_conn("anthropic")}) == "fake|fake-model|low"
    assert (
        R._identity(official("gpt-6-luna")) == "openai|gpt-6-luna|low"
    )  # 用户自选的小型号：原有强度逻辑
    S.mark_small_unusable(official("gpt-6.2-sol"), "gpt-6-luna")
    assert R._identity(official("gpt-6.2-sol")) == "openai|gpt-6.2-sol|low"


def test_the_bench_override_turns_the_policy_off_for_identity_and_calls():
    old = R.set_overrides(auto_small=False)
    try:
        assert R._identity(official("gpt-6.2-sol")) == "openai|gpt-6.2-sol|low"
    finally:
        R.restore_overrides(old)


def test_usage_records_the_model_actually_used_and_the_fallback():
    job = R.Job("j", "p", "k", "t")
    R._add_usage(job, S.StructuredResult(False, reason="transport", requests=1, model="gpt-6-luna"))
    R._add_usage(job, S.StructuredResult(True, requests=1, model="gpt-6.2-sol", fell_back=True))
    assert (
        job.usage["model"] == "gpt-6.2-sol"
        and job.usage["requests"] == 2
        and job.usage["fell_back"]
    )


# --------------------------------------------------------------------------- 目录
LMH = ["low", "medium", "high"]


@pytest.mark.parametrize(
    ("model", "hidden"),
    [
        ("gpt-6-luna", ["none"]),
        ("gpt-6.1-terra", ["none"]),
        ("gpt-6.2-sol", []),
        ("gpt-6-sol", []),
        ("gpt-6-future", []),
    ],
)
def test_catalog_efforts_per_model(model, hidden):
    assert ai_catalog.effort_support("codex", OFFICIAL, model)["efforts"] == LMH  # 界面档位列表不变
    assert ai_catalog.verified_efforts("codex", OFFICIAL, model) == [*LMH, *hidden]


def test_catalog_prefix_match_is_first_hit_wins_and_specific_rows_come_first():
    rows = json.loads(ai_catalog.CATALOG_PATH.read_text(encoding="utf-8"))["effort_origins"][1][
        "models"
    ]
    prefixes = [r["prefix"] for r in rows]
    assert prefixes.index("gpt-6-luna") < prefixes.index("gpt-6")
    assert prefixes.index("gpt-6.1-terra") < prefixes.index("gpt-6")
    assert prefixes.index("gpt-6.2-sol") < prefixes.index("gpt-6")
    assert ai_catalog.verified_efforts("codex", OFFICIAL, "gpt-6.1-terra-mini-x")[-1:] == [
        "none"
    ]  # 前缀机制：同前缀的变体同规则


def test_structured_small_table_is_declared_checked_and_openai_only():
    assert ai_catalog.structured_small(OFFICIAL, "openai-responses") == {
        "model": "gpt-6-luna",
        "effort": "none",
    }
    assert ai_catalog.structured_small("https://api.openai.com", "openai-responses") is not None
    assert ai_catalog.structured_small(OFFICIAL, "openai-chat") is None
    assert ai_catalog.structured_small("https://api.anthropic.com", "anthropic") is None
    assert ai_catalog.structured_small("https://relay.example.com/v1", "openai-responses") is None
    raw = json.loads(ai_catalog.CATALOG_PATH.read_text(encoding="utf-8"))
    assert {r["origin"] for r in raw["structured_small"]} == {"https://api.openai.com"}
    for r in raw["structured_small"]:
        assert r["source"].strip()
        assert r["effort"] in ai_catalog.verified_efforts(r["agent"], r["origin"], r["model"])


def test_a_broken_catalog_row_disables_the_policy_instead_of_failing_the_call(monkeypatch):
    def broken(*a, **k):
        raise ai_catalog.CatalogError("bad")

    monkeypatch.setattr(ai_catalog, "structured_small", broken)
    res, t = run(official("gpt-6.2-sol"), [ok_body()])
    assert res.ok and t.calls[0]["body"]["model"] == "gpt-6.2-sol"


# --------------------------------------------------------------------------- none 的出路与改图 / 修复不受影响
def test_wire_can_send_none_but_user_selectable_efforts_do_not_include_it():
    assert "none" not in wires.RESPONSES_EFFORTS and "none" in wires.RESPONSES_WIRE_EFFORTS
    body = (
        wires.OpenAIResponsesWire()
        .build(
            base_url="https://x.example",
            api_key="K",
            model="m",
            system="s",
            tools=[],
            history=[],
            effort="none",
            max_output_tokens=10,
        )
        .body
    )
    assert body["reasoning"] == {"effort": "none"}
    body = (
        wires.OpenAIResponsesWire()
        .build(
            base_url="https://x.example",
            api_key="K",
            model="m",
            system="s",
            tools=[],
            history=[],
            effort="bogus",
            max_output_tokens=10,
        )
        .body
    )
    assert "reasoning" not in body


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-6.1-terra", "gpt-6.2-sol", "gpt-6-sol"])
def test_edit_assistant_and_repair_paths_never_get_none_or_a_switched_model(
    model, tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "CODEX_HOME", str(tmp_path / "codex")
    )  # 不读本机 Codex 的型号缓存：只看目录表
    rec = {
        "id": "openai",
        "agent": "codex",
        "base_url": OFFICIAL,
        "api_key": "sk-x",
        "default_model": model,
        "models": [model],
    }
    for effort in ("none", "low", "medium", "high"):
        args = ai_providers.builtin_effort_args(rec, effort, model)
        assert args == ([] if effort == "none" else ["--effort", effort])
        argv, _env = ai_providers._builtin_overrides(rec, model, effort)
        assert (
            argv[argv.index("--model") + 1] == model
        )  # 改图 / 修复用用户选的型号，不受结构化策略影响
        assert "none" not in argv
    assert ai_providers.EffortResolver("codex", OFFICIAL).support(model)["efforts"] == LMH
