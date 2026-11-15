"""内置执行器 runner 层的两个闭集自救：第一轮换协议、非必需字段降级（ADR 0147 修订 2026-11-15）。

判据的主语：**假服务器收到的请求**（路径 / 请求体里剩什么字段）、`done` 事件的回报字段、**子进程真实的 stdout / stderr**
（响应体里的金丝雀不许出现）。服务器是 127.0.0.1 上的 `http.server`，不联网。
"""

from __future__ import annotations

import json
import threading

import pytest

from tavotto.engine.agent_runner import fallback, loop, runner_cli, wires
from tests.test_agent_runner import (
    KEY_CANARY,
    RESP_TURNS,
    SCRIPT,
    FakeModelServer,
    run_cli,
    run_demo,
)

BODY_CANARY = "BODY_CANARY_9e21"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for n in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(n, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


@pytest.fixture
def cand(tmp_path):
    d = tmp_path / "cand"
    d.mkdir()
    (d / "fig.py").write_text(SCRIPT, encoding="utf-8")
    return d


@pytest.fixture
def serve():
    servers: list[FakeModelServer] = []

    def make(responses):
        servers.append(FakeModelServer(responses))
        return servers[-1]

    yield make
    for s in servers:
        s.close()


def _err(text: str) -> bytes:
    return json.dumps({"error": {"message": text}}).encode()


# ================================================================ 判据（纯函数）
class TestProtocolMismatchSignal:
    @pytest.mark.parametrize("status", [404, 405])
    def test_wrong_path_statuses_switch_without_any_phrase(self, status):
        assert fallback.protocol_mismatch(status, b"") is True
        assert fallback.protocol_mismatch(status, b"<html>nope</html>") is True

    @pytest.mark.parametrize("status", [400, 404, 503])
    @pytest.mark.parametrize("phrase", fallback.WRONG_PROTOCOL_PHRASES)
    def test_every_phrase_switches_on_its_statuses_case_insensitive(self, status, phrase):
        body = _err(f"xx {phrase.upper()} yy")
        assert fallback.protocol_mismatch(status, body) is True

    def test_phrase_list_is_exactly_the_agreed_closed_set(self):
        assert len(fallback.WRONG_PROTOCOL_PHRASES) == 15
        assert "not implemented" in fallback.WRONG_PROTOCOL_PHRASES
        assert "无可用渠道" in fallback.WRONG_PROTOCOL_PHRASES

    @pytest.mark.parametrize("status", [401, 403, 402, 408, 409, 422, 429, 500, 502, 504])
    @pytest.mark.parametrize("phrase", ["no available channel", "convert_request_failed"])
    def test_other_statuses_never_switch_even_with_a_phrase(self, status, phrase):
        assert fallback.protocol_mismatch(status, _err(phrase)) is False

    @pytest.mark.parametrize("status", [400, 503])
    def test_phrase_statuses_without_a_phrase_do_not_switch(self, status):
        assert fallback.protocol_mismatch(status, _err("model is overloaded")) is False
        assert fallback.protocol_mismatch(status, b"") is False


class TestFieldNaming:
    @pytest.mark.parametrize(
        "text,field,expected",
        [
            (
                "Unsupported parameter: 'max_tokens' is not supported with this model.",
                "max_tokens",
                True,
            ),
            ("Unknown parameter: 'store'.", "store", True),
            (
                '{"detail":[{"loc":["body","include"],"msg":"Extra inputs are not permitted"}]}',
                "include",
                True,
            ),
            (
                "unrecognized request argument supplied: max_output_tokens",
                "max_output_tokens",
                True,
            ),
            # 值的问题不算
            ("Invalid value for 'max_tokens': 99999 is above the maximum", "max_tokens", False),
            ("Unsupported value: 'store' does not support true", "store", False),
            ("max_tokens must be less than 4096", "max_tokens", False),
            # 普通英文词不带引号不算点名
            ("unknown error, please store the receipt", "store", False),
            # 点了别的字段
            ("Unknown parameter: 'temperature'.", "store", False),
        ],
    )
    def test_naming_rules(self, text, field, expected):
        assert fallback.names_field(400, _err(text), field) is expected

    def test_only_400_and_422_count(self):
        body = _err("Unknown parameter: 'store'.")
        assert fallback.names_field(422, body, "store") is True
        for status in (401, 403, 404, 429, 500):
            assert fallback.names_field(status, body, "store") is False


# ================================================================ 第一轮换协议（HttpModel）
def _http_model(primary, fallbacks, post, **kw):
    return loop.HttpModel(
        wires.WIRES[primary],
        base_url="https://api.example.test/v1",
        api_key=KEY_CANARY,
        model="m-1",
        retry_base=0.0,
        request_timeout=5,
        post=post,
        fallbacks=[(wires.WIRES[n], u) for n, u in fallbacks],
        **kw,
    )


def _ok_chat(text="done"):
    return (
        200,
        {},
        json.dumps(
            {
                "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        ).encode(),
    )


def _ok_resp(text="done"):
    return (
        200,
        {},
        json.dumps(
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        ).encode(),
    )


def _complete(model, history=None):
    history = history or [wires.Msg("user", text="hi")]
    return model.complete(
        system="s",
        tools=[],
        history=history,
        max_output_tokens=100,
        cancel=threading.Event(),
        deadline=__import__("time").monotonic() + 30,
    )


class TestWireSwitch:
    def test_switches_on_404_and_reports(self):
        seen = []

        def post(url, headers, body, *, timeout):
            seen.append((url, sorted(body)))
            return (404, {}, b"") if url.endswith("/chat/completions") else _ok_resp()

        m = _http_model("openai-chat", [("openai-responses", "https://api.example.test/v1")], post)
        turn = _complete(m)
        assert turn.text == "done"
        assert [u.rsplit("/", 1)[-1] for u, _ in seen] == ["completions", "responses"]
        assert m.report() == {
            "wire_used": "openai-responses",
            "wire_switched_from": "openai-chat",
            "dropped_fields": [],
            "token_field": "",
        }

    def test_switch_uses_the_fallbacks_own_base_url_and_does_not_burn_attempts(self):
        urls = []

        def post(url, headers, body, *, timeout):
            urls.append(url)
            return (405, {}, b"") if url.endswith("/v1/messages") else _ok_chat()

        m = _http_model(
            "anthropic",
            [("openai-chat", "https://api.example.test/v1")],
            post,
            max_attempts=1,
        )
        _complete(m)
        assert urls == [
            "https://api.example.test/v1/v1/messages",
            "https://api.example.test/v1/chat/completions",
        ]

    def test_chain_tries_in_order_then_fails_with_the_last_status(self):
        urls = []

        def post(url, headers, body, *, timeout):
            urls.append(url.rsplit("/", 1)[-1])
            return 404, {}, b""

        m = _http_model(
            "openai-chat",
            [
                ("openai-responses", "https://api.example.test/v1"),
                ("anthropic", "https://api.example.test"),
            ],
            post,
        )
        with pytest.raises(wires.RunnerFailure) as ei:
            _complete(m)
        assert (ei.value.reason, ei.value.detail) == ("network", "http_404")
        assert urls == ["completions", "responses", "messages"]
        assert m.report()["wire_used"] == ""
        assert m.report()["wire_switched_from"] == "openai-chat"

    @pytest.mark.parametrize("status", [401, 403, 429, 500, 502])
    def test_auth_quota_and_server_errors_never_switch(self, status):
        calls = []

        def post(url, headers, body, *, timeout):
            calls.append(url)
            return status, {}, _err("no available channel")

        m = _http_model(
            "openai-chat",
            [("openai-responses", "https://api.example.test/v1")],
            post,
            max_attempts=1,
        )
        with pytest.raises(wires.RunnerFailure):
            _complete(m)
        assert len(calls) == 1 and m.report()["wire_switched_from"] == ""

    def test_phrase_400_switches_but_plain_400_does_not(self):
        calls = []

        def post(url, headers, body, *, timeout):
            calls.append(url)
            if url.endswith("/chat/completions"):
                return 400, {}, _err("model not supported in /v1/chat/completions")
            return _ok_resp()

        m = _http_model("openai-chat", [("openai-responses", "https://api.example.test/v1")], post)
        _complete(m)
        assert len(calls) == 2

        calls.clear()

        def plain(url, headers, body, *, timeout):
            calls.append(url)
            return 400, {}, _err("bad json somewhere")

        m = _http_model("openai-chat", [("openai-responses", "https://api.example.test/v1")], plain)
        with pytest.raises(wires.RunnerFailure) as ei:
            _complete(m)
        assert ei.value.detail == "http_400" and len(calls) == 1

    def test_no_switch_after_a_successful_reply(self):
        n = {"i": 0}

        def post(url, headers, body, *, timeout):
            n["i"] += 1
            return _ok_chat() if n["i"] == 1 else (404, {}, b"")

        m = _http_model("openai-chat", [("openai-responses", "https://api.example.test/v1")], post)
        _complete(m)
        with pytest.raises(wires.RunnerFailure) as ei:
            _complete(m)
        assert ei.value.detail == "http_404"
        assert n["i"] == 2
        assert m.report()["wire_used"] == "openai-chat" and m.report()["wire_switched_from"] == ""

    def test_no_fallbacks_keeps_the_old_behaviour(self):
        def post(url, headers, body, *, timeout):
            return 404, {}, b""

        m = _http_model("openai-chat", [], post)
        with pytest.raises(wires.RunnerFailure) as ei:
            _complete(m)
        assert ei.value.detail == "http_404"

    def test_tool_executed_then_404_does_not_switch_end_to_end(self, cand, serve):
        """工具真的执行过之后再遇到 404：不换协议，结局 network/http_404，回报 wire_used 仍是原协议。"""
        edit = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": json.dumps({"path": "fig.py"}),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        srv = serve([(200, {}, edit), (404, {}, b"")])
        model = loop.HttpModel(
            wires.WIRES["openai-chat"],
            base_url=srv.url,
            api_key=KEY_CANARY,
            model="m-1",
            retry_base=0.0,
            request_timeout=5,
            fallbacks=[(wires.WIRES["openai-responses"], srv.url)],
        )
        res, events = run_demo(cand, {"turns": []}, model=model)
        assert (res.ok, res.reason, res.detail) == (False, "network", "http_404"), events
        assert [p for p, _, _ in srv.requests] == ["/chat/completions"] * 2
        assert any(e["type"] == "tool" and e["ok"] for e in events)  # 工具确实执行过
        done = events[-1]
        assert done["wire_used"] == "openai-chat" and done["wire_switched_from"] == ""


# ================================================================ 字段降级
class TestFieldDegrade:
    def test_responses_store_dropped_once_and_kept_dropped_next_turn(self):
        bodies = []

        def post(url, headers, body, *, timeout):
            bodies.append(json.loads(json.dumps(body)))
            if "store" in body:
                return 400, {}, _err("Unknown parameter: 'store'.")
            return _ok_resp()

        m = _http_model("openai-responses", [], post)
        _complete(m)
        _complete(m)
        assert ["store" in b for b in bodies] == [True, False, False]
        assert m.report()["dropped_fields"] == ["store"]

    def test_same_field_is_not_retried_again(self):
        """已经剥掉还被点名（服务端体不诚实）：不再试同一字段，按普通 400 失败。"""
        calls = []

        def post(url, headers, body, *, timeout):
            calls.append(sorted(body))
            return 400, {}, _err("Unknown parameter: 'store'.")

        m = _http_model("openai-responses", [], post)
        with pytest.raises(wires.RunnerFailure) as ei:
            _complete(m)
        assert ei.value.detail == "http_400" and len(calls) == 2

    def test_responses_include_and_max_output_tokens_dropped(self):
        bodies = []

        def post(url, headers, body, *, timeout):
            bodies.append(dict(body))
            if "include" in body or "max_output_tokens" in body:
                return (
                    422,
                    {},
                    _err("Unrecognized request arguments: 'include', 'max_output_tokens'"),
                )
            return _ok_resp()

        m = _http_model("openai-responses", [], post, effort="low")
        _complete(m)
        assert "include" not in bodies[-1] and "max_output_tokens" not in bodies[-1]
        assert bodies[-1]["reasoning"] == {"effort": "low"} and bodies[-1]["store"] is False
        assert m.report()["dropped_fields"] == ["include", "max_output_tokens"]

    def test_dropping_include_also_strips_reasoning_items_from_replay(self):
        bodies = []

        def post(url, headers, body, *, timeout):
            bodies.append(json.loads(json.dumps(body)))
            if "include" in body:
                return 400, {}, _err("Unknown parameter: 'include'.")
            return _ok_resp()

        m = _http_model("openai-responses", [], post, effort="low")
        replay = wires.Turn(
            native=[
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "c1",
                    "name": "read_file",
                    "arguments": "{}",
                },
            ],
            tool_calls=[wires.ToolCall("c1", "read_file", {})],
        )
        history = [
            wires.Msg("user", text="hi"),
            wires.Msg("assistant", turn=replay),
            wires.Msg("tool", results=[wires.ToolResult("c1", "read_file", "ok")]),
        ]
        _complete(m, history)
        types = [i.get("type") for i in bodies[-1]["input"] if isinstance(i, dict)]
        assert "reasoning" not in types and "function_call" in types
        assert "reasoning" in [i.get("type") for i in bodies[0]["input"] if isinstance(i, dict)]

    def test_chat_length_field_swaps_once_each_direction(self):
        for start, other in (
            ("max_tokens", "max_completion_tokens"),
            ("max_completion_tokens", "max_tokens"),
        ):
            seen = []

            def post(url, headers, body, *, timeout, start=start):
                seen.append([f for f in ("max_tokens", "max_completion_tokens") if f in body])
                if start in body:
                    return (
                        400,
                        {},
                        _err(f"Unsupported parameter: '{start}' is not supported with this model."),
                    )
                return _ok_chat()

            m = loop.HttpModel(
                wires.OpenAIChatWire(token_field=start),
                base_url="https://api.example.test/v1",
                api_key=KEY_CANARY,
                model="m-1",
                retry_base=0.0,
                post=post,
            )
            _complete(m)
            assert seen == [[start], [other]]
            rep = m.report()
            assert rep["token_field"] == other and rep["dropped_fields"] == [start]
            _complete(m)  # 下一轮沿用，不再试
            assert seen[-1] == [other] and len(seen) == 3

    def test_chat_both_length_fields_rejected_fails_after_one_swap(self):
        calls = []

        def post(url, headers, body, *, timeout):
            calls.append(1)
            return 400, {}, _err("Unsupported parameter: 'max_tokens' / 'max_completion_tokens'")

        m = _http_model("openai-chat", [], post)
        with pytest.raises(wires.RunnerFailure):
            _complete(m)
        assert len(calls) == 2

    def test_value_problems_and_other_statuses_do_not_degrade(self):
        calls = []

        def post(url, headers, body, *, timeout):
            calls.append(1)
            return 400, {}, _err("Invalid value for 'max_tokens': 99999 is above the maximum 4096")

        m = _http_model("openai-chat", [], post)
        with pytest.raises(wires.RunnerFailure):
            _complete(m)
        assert len(calls) == 1 and m.report()["dropped_fields"] == []

        calls.clear()

        def post401(url, headers, body, *, timeout):
            calls.append(1)
            return 401, {}, _err("Unknown parameter: 'max_tokens'")

        m = _http_model("openai-chat", [], post401)
        with pytest.raises(wires.RunnerFailure):
            _complete(m)
        assert len(calls) == 1

    def test_anthropic_has_nothing_to_degrade(self):
        calls = []

        def post(url, headers, body, *, timeout):
            calls.append(1)
            return 400, {}, _err("Unknown parameter: 'max_tokens'")

        m = _http_model("anthropic", [], post)
        with pytest.raises(wires.RunnerFailure):
            _complete(m)
        assert len(calls) == 1


# ================================================================ 回报与原文隔离
class TestReport:
    def test_done_event_carries_the_closed_report_fields(self, cand, serve):
        srv = serve([(404, {}, b""), *RESP_TURNS])
        model = loop.HttpModel(
            wires.WIRES["openai-chat"],
            base_url=srv.url,
            api_key=KEY_CANARY,
            model="m-1",
            retry_base=0.0,
            request_timeout=5,
            fallbacks=[(wires.WIRES["openai-responses"], srv.url)],
        )
        res, events = run_demo(cand, {"turns": []}, model=model)
        assert res.ok, events
        done = events[-1]
        assert {k: done[k] for k in loop.DONE_REPORT_FIELDS} == {
            "wire_used": "openai-responses",
            "wire_switched_from": "openai-chat",
            "dropped_fields": [],
            "token_field": "",
        }
        assert [p for p, _, _ in srv.requests][:2] == ["/chat/completions", "/responses"]

    def test_demo_and_unreporting_models_give_empty_values(self, cand):
        res, events = run_demo(cand, {"turns": [{"text": "ok"}]})
        done = events[-1]
        assert (done["wire_used"], done["wire_switched_from"], done["dropped_fields"]) == (
            "",
            "",
            [],
        )
        assert done["token_field"] == ""

    def test_report_is_filtered_to_the_closed_set(self):
        class Evil:
            def report(self):
                return {
                    "wire_used": 5,
                    "dropped_fields": ["store", BODY_CANARY],
                    "token_field": "max_tokens",
                }

        out = loop._model_report(Evil())
        assert out["wire_used"] == "" and out["dropped_fields"] == ["store"]

    def test_report_failure_never_breaks_the_run(self):
        class Boom:
            def report(self):
                raise RuntimeError("x")

        assert loop._model_report(Boom())["dropped_fields"] == []


class TestSubprocess:
    def test_cli_switch_end_to_end_and_error_body_never_leaves_the_process(self, cand, serve):
        srv = serve(
            [
                (404, {}, _err(f"{BODY_CANARY} no available channel")),
                *RESP_TURNS,
            ]
        )
        p = run_cli(
            cand,
            "openai-chat",
            "--base-url",
            srv.url,
            "--model",
            "m-1",
            "--fallback",
            f"openai-responses={srv.url}",
            "--retry-base",
            "0",
            key=KEY_CANARY,
            stdin="make the line blue",
        )
        assert p.returncode == 0, p.stderr
        events = [json.loads(line) for line in p.stdout.splitlines()]
        done = events[-1]
        assert done["ok"] and done["wire_used"] == "openai-responses"
        assert done["wire_switched_from"] == "openai-chat"
        assert BODY_CANARY not in p.stdout and BODY_CANARY not in p.stderr
        assert KEY_CANARY not in p.stdout and KEY_CANARY not in p.stderr
        assert [path for path, _, _ in srv.requests][:2] == ["/chat/completions", "/responses"]

    def test_cli_failure_after_switch_still_hides_the_body(self, cand, serve):
        srv = serve([(404, {}, _err(f"{BODY_CANARY}"))])
        p = run_cli(
            cand,
            "openai-chat",
            "--base-url",
            srv.url,
            "--model",
            "m-1",
            "--fallback",
            f"openai-responses={srv.url}",
            "--retry-base",
            "0",
            key=KEY_CANARY,
            stdin="hi",
        )
        assert p.returncode == 1
        done = json.loads(p.stdout.splitlines()[-1])
        assert (done["reason"], done["detail"]) == ("network", "http_404")
        assert BODY_CANARY not in p.stdout and BODY_CANARY not in p.stderr

    def test_cli_rejects_a_cross_origin_fallback_before_any_request(self, cand, serve):
        srv = serve([(200, {}, {})])
        p = run_cli(
            cand,
            "openai-chat",
            "--base-url",
            srv.url,
            "--model",
            "m-1",
            "--fallback",
            "openai-responses=https://evil.example.test/v1",
            key=KEY_CANARY,
            stdin="hi",
        )
        assert p.returncode == 1
        done = json.loads(p.stdout.splitlines()[-1])
        assert (done["reason"], done["detail"]) == ("internal", "bad_args")
        assert srv.requests == []


class TestFallbackSpecParsing:
    BASE = "https://api.example.test/v1"

    @pytest.mark.parametrize(
        "specs",
        [
            ["openai-responses"],  # 缺 =
            ["nope=https://api.example.test/v1"],  # 未知协议
            ["openai-chat=https://api.example.test/v1"],  # 与主协议相同
            [
                "openai-responses=https://api.example.test/v1",
                "openai-responses=https://api.example.test/v1",
            ],
            ["openai-responses=http://api.example.test/v1"],  # 非回环明文 http
            ["openai-responses=https://api.example.test:8443/v1"],  # 端口不同
            ["openai-responses=https://user:pw@api.example.test/v1"],
            ["demo=https://api.example.test/v1"],
        ],
    )
    def test_rejected(self, specs):
        assert runner_cli._parse_fallbacks(specs, "openai-chat", self.BASE) is None

    def test_accepted_keeps_order_and_allows_a_different_path(self):
        specs = [
            "openai-responses=https://api.example.test/v1",
            "anthropic=https://api.example.test",
        ]
        assert runner_cli._parse_fallbacks(specs, "openai-chat", self.BASE) == [
            ("openai-responses", "https://api.example.test/v1"),
            ("anthropic", "https://api.example.test"),
        ]
