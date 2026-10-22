"""内置执行器 `engine/agent_runner`（ADR 0147，提议）第一阶段：协议层 / 工具 / 循环 / 预算 / 隐私 / 取消。

判据的主语（先说清）：

* 协议：**本机假服务器收到的请求体**（工具定义、回填、cache_control / reasoning）与**解析出的 Turn**——不是实现里的中间量；
  服务器线程是 127.0.0.1 上的 `http.server`，**从不联网**，也从不碰真实模型服务。
* 工具安全：**磁盘上的字节**。每条越界 / 错误用例都断言候选与候选外的文件逐字节不变，而不是只看返回的错误码。
* 隐私：**子进程真实的 stdout / stderr**（金丝雀串），且先证明金丝雀确实到过服务器（密钥在请求头、提示词与文件在请求体），
  否则「事件里没有」可能只是因为根本没发出去。
* 预算：done 事件里的 `reason` 与轮数；账本恒等式用「各轮之和」对「独立累加的合计」，并用破坏 `Ledger.add` 的反证钉住。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tavotto.engine.agent_runner import loop, tools, wires

SRC = Path(__file__).resolve().parent.parent / "src"

KEY_CANARY = "sk-canary-KEY-7d41e0"
PROMPT_CANARY = "PROMPT_CANARY_b83c"
FILE_CANARY = "FILE_CANARY_55aa"

SCRIPT = f'import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4], color="red")  # {FILE_CANARY}\nplt.show()\n'


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for n in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(n, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


@pytest.fixture
def cand(tmp_path) -> Path:
    d = tmp_path / "cand"
    d.mkdir()
    (d / "fig.py").write_text(SCRIPT, encoding="utf-8")
    (d / "notes.txt").write_text("keep me\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("OUTSIDE_SECRET\n", encoding="utf-8")
    return d


def _tree(d: Path) -> dict[str, bytes]:
    return {
        p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file() and not p.is_symlink()
    }


def envelope(cand: Path, **kw) -> tools.RunEnvelope:
    lim = kw.pop("limits", tools.Limits())
    return tools.RunEnvelope(
        task_type="figure_edit",
        cwd=cand,
        target=kw.pop("target", "fig.py"),
        instruction=kw.pop("instruction", "make the line blue"),
        limits=lim,
        **kw,
    )


def run_demo(cand: Path, script: dict, **kw):
    events: list[dict] = []
    cancel = kw.pop("cancel", None)
    model = kw.pop("model", None) or loop.DemoModel(script)
    res = loop.run(
        model, tools.TOOLSETS["figure_edit"], envelope(cand, **kw), events.append, cancel
    )
    return res, events


def done(events: list[dict]) -> dict:
    assert events and events[-1]["type"] == "done", events
    assert sum(e["type"] == "done" for e in events) == 1
    return events[-1]


def tool_set(cand: Path, **kw) -> dict[str, tools.Tool]:
    return {t.name: t for t in tools.TOOLSETS["figure_edit"].tools(envelope(cand, **kw))}


# ================================================================ 假服务器
class FakeModelServer:
    """127.0.0.1 上的假模型服务：按序回放响应（最后一条重复）；记录请求。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[tuple[str, dict, dict]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(n)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                outer.requests.append(
                    (self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                )
                i = min(len(outer.requests), len(outer.responses)) - 1
                item = outer.responses[i]
                if callable(item):
                    item()
                    item = (500, {}, {})
                status, headers, payload = item
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers.items():
                    self.send_header(k, v)
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
def serve():
    servers: list[FakeModelServer] = []

    def make(responses):
        s = FakeModelServer(responses)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


# 每种协议的两轮回放：第一轮 read_file 工具调用，第二轮最终文字
USAGE_A = {
    "input_tokens": 50,
    "output_tokens": 10,
    "cache_read_input_tokens": 20,
    "cache_creation_input_tokens": 5,
}
ANTHROPIC_TURNS = [
    (
        200,
        {},
        {
            "content": [
                {"type": "thinking", "thinking": "let me look", "signature": "sig-1"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_file",
                    "input": {"path": "fig.py"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": USAGE_A,
        },
    ),
    (
        200,
        {},
        {
            "content": [{"type": "text", "text": "Final answer."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 80, "output_tokens": 5},
        },
    ),
]
CHAT_TURNS = [
    (
        200,
        {},
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "fig.py"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 70,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        },
    ),
    (
        200,
        {},
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Final answer."},
                }
            ],
            "usage": {"prompt_tokens": 80, "completion_tokens": 5},
        },
    ),
]
RESP_TURNS = [
    (
        200,
        {},
        {
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"},
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_9",
                    "name": "read_file",
                    "arguments": '{"path": "fig.py"}',
                },
            ],
            "usage": {
                "input_tokens": 70,
                "output_tokens": 10,
                "input_tokens_details": {"cached_tokens": 20},
            },
        },
    ),
    (
        200,
        {},
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Final answer."}],
                }
            ],
            "usage": {"input_tokens": 80, "output_tokens": 5},
        },
    ),
]
WIRE_CASES = {
    "anthropic": (ANTHROPIC_TURNS, "/v1/messages"),
    "openai-chat": (CHAT_TURNS, "/chat/completions"),
    "openai-responses": (RESP_TURNS, "/responses"),
}


def http_run(cand, serve, wire, responses, *, effort=None, max_attempts=4, **kw):
    srv = serve(responses)
    model = loop.HttpModel(
        wires.WIRES[wire],
        base_url=srv.url,
        api_key=KEY_CANARY,
        model="m-1",
        effort=effort,
        retry_base=0.0,
        max_attempts=max_attempts,
        request_timeout=5,
    )
    res, events = run_demo(cand, {"turns": []}, model=model, **kw)
    return srv, res, events


# ================================================================ 协议层
class TestWires:
    @pytest.mark.parametrize("wire", sorted(WIRE_CASES))
    def test_two_round_conversation(self, cand, serve, wire):
        turns, path = WIRE_CASES[wire]
        srv, res, events = http_run(cand, serve, wire, turns)
        assert (res.ok, res.reason) == (True, "completed"), events
        assert [r[0] for r in srv.requests] == [path, path]
        # 事件流：usage / text / tool / done，工具事件只有摘要
        types = [e["type"] for e in events]
        assert types[-1] == "done" and types.count("tool") == 1 and types.count("text") == 1
        assert [e["text"] for e in events if e["type"] == "text"] == ["Final answer."]
        tool_ev = next(e for e in events if e["type"] == "tool")
        assert tool_ev == {
            "type": "tool",
            "name": "read_file",
            "ok": True,
            "summary": "read 3 lines",
        }
        d = done(events)
        assert d["turns"] == 2 and d["ledger_mismatch"] is False and d["changed"] is False
        assert d["usage"]["output_tokens"] == 15
        # 第二轮请求里带着第一轮的工具结果（文件内容回到了模型，而不是事件里）
        assert FILE_CANARY in json.dumps(srv.requests[1][2])
        assert FILE_CANARY not in json.dumps(events)

    def test_anthropic_request_shape(self, cand, serve):
        srv, res, events = http_run(cand, serve, "anthropic", ANTHROPIC_TURNS, effort="medium")
        assert res.ok
        _, h, body = srv.requests[0]
        assert h["x-api-key"] == KEY_CANARY and h["authorization"] == f"Bearer {KEY_CANARY}"
        assert h["anthropic-version"] == "2023-06-01"
        names = [t["name"] for t in body["tools"]]
        assert names == ["read_file", "list_dir", "edit_file", "write_file", "check"]
        assert all("input_schema" in t for t in body["tools"])
        # 缓存断点：系统提示块与最后一个工具定义
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert all("cache_control" not in t for t in body["tools"][:-1])
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
        assert body["max_tokens"] > 4096
        assert body["messages"][0]["role"] == "user"
        # 第二轮：assistant 原样回放（含 thinking 签名），tool_result 回填
        body2 = srv.requests[1][2]
        assert body2["messages"][1] == {
            "role": "assistant",
            "content": ANTHROPIC_TURNS[0][2]["content"],
        }
        tr = body2["messages"][2]
        assert tr["role"] == "user" and tr["content"][0]["type"] == "tool_result"
        assert tr["content"][0]["tool_use_id"] == "toolu_1" and "is_error" not in tr["content"][0]
        assert FILE_CANARY in tr["content"][0]["content"]
        # 系统提示 + 工具定义逐字稳定（缓存前缀）
        assert body["system"] == body2["system"] and body["tools"] == body2["tools"]
        # 用量：输入含缓存命中 / 写入
        assert events[0] == {
            "type": "usage",
            "turn": 1,
            "input_tokens": 75,
            "output_tokens": 10,
            "cached_input_tokens": 20,
        }

    def test_anthropic_without_effort_sends_no_thinking(self, cand, serve):
        srv, res, _ = http_run(cand, serve, "anthropic", ANTHROPIC_TURNS)
        assert res.ok and "thinking" not in srv.requests[0][2]

    def test_chat_request_shape(self, cand, serve):
        srv, res, _ = http_run(cand, serve, "openai-chat", CHAT_TURNS, effort="high")
        assert res.ok
        _, h, body = srv.requests[0]
        assert h["authorization"] == f"Bearer {KEY_CANARY}" and "x-api-key" not in h
        assert body["messages"][0]["role"] == "system"
        assert body["tools"][0]["type"] == "function"
        assert set(body["tools"][0]["function"]) == {"name", "description", "parameters"}
        assert "cache_control" not in json.dumps(body) and "reasoning" not in json.dumps(body)
        body2 = srv.requests[1][2]
        asst, toolmsg = body2["messages"][2], body2["messages"][3]
        assert asst["role"] == "assistant" and asst["tool_calls"][0]["id"] == "call_1"
        assert json.loads(asst["tool_calls"][0]["function"]["arguments"]) == {"path": "fig.py"}
        assert toolmsg["role"] == "tool" and toolmsg["tool_call_id"] == "call_1"
        assert FILE_CANARY in toolmsg["content"]
        assert body["messages"][0] == body2["messages"][0] and body["tools"] == body2["tools"]

    def test_responses_request_shape(self, cand, serve):
        srv, res, events = http_run(cand, serve, "openai-responses", RESP_TURNS, effort="high")
        assert res.ok
        _, h, body = srv.requests[0]
        assert h["authorization"] == f"Bearer {KEY_CANARY}"
        assert body["instructions"] and body["store"] is False
        assert body["tools"][0]["type"] == "function" and "name" in body["tools"][0]
        assert body["reasoning"] == {"effort": "high"}
        assert body["include"] == ["reasoning.encrypted_content"]
        items = srv.requests[1][2]["input"]
        assert [i.get("type") for i in items[1:]] == [
            "reasoning",
            "function_call",
            "function_call_output",
        ]
        assert items[-1]["call_id"] == "call_9" and FILE_CANARY in items[-1]["output"]
        assert events[0]["input_tokens"] == 70 and events[0]["cached_input_tokens"] == 20

    def test_responses_without_effort_sends_no_reasoning(self, cand, serve):
        srv, res, _ = http_run(cand, serve, "openai-responses", RESP_TURNS)
        assert (
            res.ok and "reasoning" not in srv.requests[0][2] and "include" not in srv.requests[0][2]
        )

    @pytest.mark.parametrize("wire", sorted(WIRE_CASES))
    def test_401_is_auth_and_not_retried(self, cand, serve, wire):
        srv, res, events = http_run(cand, serve, wire, [(401, {}, {"error": PROMPT_CANARY})])
        assert (res.ok, res.reason) == (False, "auth") and len(srv.requests) == 1
        assert PROMPT_CANARY not in json.dumps(events), "服务端错误体不进事件"

    @pytest.mark.parametrize("wire", sorted(WIRE_CASES))
    def test_429_then_success(self, cand, serve, wire):
        turns, _ = WIRE_CASES[wire]
        srv, res, _ = http_run(cand, serve, wire, [(429, {"Retry-After": "0"}, {}), *turns])
        assert res.ok and len(srv.requests) == 3

    @pytest.mark.parametrize("wire", sorted(WIRE_CASES))
    def test_5xx_exhausted_is_network(self, cand, serve, wire):
        srv, res, events = http_run(cand, serve, wire, [(503, {}, {})], max_attempts=3)
        assert (res.reason, res.detail) == ("network", "http_503") and len(srv.requests) == 3
        assert done(events)["reason"] == "network"

    def test_429_exhausted_is_quota_and_billing_429_is_immediate(self, cand, serve):
        srv, res, _ = http_run(cand, serve, "anthropic", [(429, {}, {})], max_attempts=2)
        assert (res.reason, len(srv.requests)) == ("quota", 2)
        srv, res, _ = http_run(
            cand, serve, "anthropic", [(429, {}, {"error": {"type": "insufficient_quota"}})]
        )
        assert (res.reason, res.detail, len(srv.requests)) == ("quota", "insufficient_quota", 1)

    def test_other_4xx_not_retried_and_redirect_not_followed(self, cand, serve):
        srv, res, _ = http_run(cand, serve, "anthropic", [(400, {}, {})])
        assert (res.reason, res.detail, len(srv.requests)) == ("network", "http_400", 1)
        srv, res, _ = http_run(cand, serve, "anthropic", [(302, {"Location": "/elsewhere"}, {})])
        assert (res.reason, res.detail, len(srv.requests)) == ("network", "redirect", 1)

    def test_https_opener_refuses_redirects_before_the_default_handler(self):
        """https 路径的重定向防线（回环 http 不经 opener，上面那条测不到它）：POST 体和密钥头不转交到别处。"""
        import urllib.error
        import urllib.request

        opener = wires._opener()
        redirect_handlers = [
            h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
        ]
        assert any(isinstance(h, wires._NoRedirect) for h in redirect_handlers)
        first = sorted(redirect_handlers, key=lambda h: h.handler_order)[0]
        assert isinstance(first, wires._NoRedirect), "必须排在默认的 HTTPRedirectHandler 前面"
        req = urllib.request.Request("https://api.example.com/x", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            first.redirect_request(req, None, 302, "Found", {}, "https://evil.example/")
        assert exc.value.code == 302

    def test_garbage_body_is_network(self, cand, serve):
        _, res, _ = http_run(cand, serve, "anthropic", [(200, {}, b"not json")])
        assert (res.reason, res.detail) == ("network", "bad_response")

    def test_oversized_response_is_refused(self, cand, serve, monkeypatch):
        monkeypatch.setattr(wires, "MAX_RESPONSE_BYTES", 64)
        _, res, _ = http_run(cand, serve, "anthropic", [(200, {}, b"x" * 500)])
        assert (res.reason, res.detail) == ("network", "response_too_large")

    @pytest.mark.parametrize(
        ("url", "code"),
        [
            ("http://example.com/v1", "base_url_scheme"),
            ("http://localhost:1234", "base_url_scheme"),
            ("ftp://x", "base_url_scheme"),
            ("https://user:pw@api.example.com", "bad_base_url"),
            ("https://api.example.com?x=1", "bad_base_url"),
            ("", "bad_base_url"),
        ],
    )
    def test_base_url_rules(self, url, code):
        assert wires.check_base_url(url) == code

    def test_base_url_accepts_https_and_loopback_http(self):
        assert wires.check_base_url("https://api.example.com/v1") is None
        assert wires.check_base_url("http://127.0.0.1:8080") is None

    def test_loopback_http_ignores_proxy_environment(self, cand, serve):
        """autouse 把代理指到死端口；回环 http 能通就证明它没走代理。"""
        turns, _ = WIRE_CASES["anthropic"]
        _, res, _ = http_run(cand, serve, "anthropic", turns)
        assert res.ok


# ================================================================ 工具
class TestToolSafety:
    def call(self, ts, name, **args):
        r = ts[name].handler(args)
        return r

    def test_path_escapes_are_tool_errors_and_change_nothing(self, cand, tmp_path):
        before_tree, outside = _tree(cand), (tmp_path / "secret.txt").read_bytes()
        ts = tool_set(cand)
        for p in (
            "../secret.txt",
            "..",
            "sub/../../secret.txt",
            str(tmp_path / "secret.txt"),
            "",
            "a\x00b",
        ):
            r = self.call(ts, "read_file", path=p)
            assert r.is_error and r.code == "path_outside", p
            assert "OUTSIDE_SECRET" not in r.content
        assert _tree(cand) == before_tree and (tmp_path / "secret.txt").read_bytes() == outside

    def test_symlink_out_of_candidate_is_refused(self, cand, tmp_path):
        try:
            (cand / "link.txt").symlink_to(tmp_path / "secret.txt")
            (cand / "linkdir").symlink_to(tmp_path, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlink support")
        ts = tool_set(cand)
        for p in ("link.txt", "linkdir/secret.txt"):
            r = self.call(ts, "read_file", path=p)
            assert r.is_error and r.code == "path_outside" and "OUTSIDE_SECRET" not in r.content
        listing = self.call(ts, "list_dir").content
        assert "link.txt  (symlink, not followed)" in listing
        assert (tmp_path / "secret.txt").read_bytes() == b"OUTSIDE_SECRET\n"

    def test_symlinked_target_cannot_be_written(self, cand):
        try:
            (cand / "alias.py").symlink_to(cand / "fig.py")
        except (OSError, NotImplementedError):
            pytest.skip("no symlink support")
        ts = tool_set(cand, target="alias.py")
        self.call(ts, "read_file", path="alias.py")
        before = _tree(cand)
        r = self.call(ts, "edit_file", old="red", new="blue")
        assert r.is_error and r.code == "not_target"
        assert _tree(cand) == before and (cand / "alias.py").is_symlink()

    def test_target_outside_cwd_is_refused_at_construction(self, cand):
        with pytest.raises(ValueError):
            tools.TOOLSETS["figure_edit"].tools(envelope(cand, target="../secret.txt"))
        res, events = run_demo(cand, {"turns": [{"text": "x"}]}, target="../secret.txt")
        assert (res.ok, res.reason, res.detail) == (False, "internal", "bad_target")
        assert done(events)["detail"] == "bad_target"

    def test_writing_a_non_target_file_is_refused(self, cand):
        ts = tool_set(cand)
        self.call(ts, "read_file", path="fig.py")
        before = _tree(cand)
        for tool, args in (
            ("edit_file", {"path": "notes.txt", "old": "keep", "new": "lose"}),
            ("write_file", {"path": "notes.txt", "content": "x"}),
            ("write_file", {"path": "new_file.py", "content": "x"}),
            ("edit_file", {"path": "../secret.txt", "old": "OUTSIDE", "new": "x"}),
        ):
            r = ts[tool].handler(args)
            assert r.is_error and r.code in {"not_target", "path_outside"}, (tool, args, r)
        assert _tree(cand) == before and not (cand / "new_file.py").exists()

    def test_edit_old_zero_or_many_times_is_a_tool_error(self, cand):
        (cand / "fig.py").write_text("a = 1\nb = 1\nc = 2\n", encoding="utf-8")
        ts = tool_set(cand)
        self.call(ts, "read_file", path="fig.py")
        before = _tree(cand)
        zero = self.call(ts, "edit_file", old="zzz", new="y")
        two = self.call(ts, "edit_file", old="= 1", new="= 9")
        assert (zero.is_error, zero.code) == (True, "old_not_found")
        assert (two.is_error, two.code) == (True, "old_not_unique") and "2 times" in two.content
        assert _tree(cand) == before
        ok = self.call(ts, "edit_file", old="b = 1", new="b = 9")
        assert not ok.is_error and ok.mutated and ok.summary == "changed 1 lines"
        assert (cand / "fig.py").read_text() == "a = 1\nb = 9\nc = 2\n"

    def test_edit_before_read_is_refused(self, cand):
        ts = tool_set(cand)
        before = _tree(cand)
        for tool, args in (
            ("edit_file", {"old": "red", "new": "blue"}),
            ("write_file", {"content": "x"}),
        ):
            r = ts[tool].handler(args)
            assert (r.is_error, r.code) == (True, "not_read")
        assert _tree(cand) == before
        # 读了别的文件不算读过目标
        self.call(ts, "read_file", path="notes.txt")
        assert self.call(ts, "edit_file", old="red", new="blue").code == "not_read"

    def test_oversized_and_binary_files(self, cand):
        (cand / "big.txt").write_bytes(b"x" * (tools.READ_LIMIT_BYTES + 1))
        (cand / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
        ts = tool_set(cand)
        assert self.call(ts, "read_file", path="big.txt").code == "too_large"
        assert self.call(ts, "read_file", path="bin.dat").code == "not_text"
        assert self.call(ts, "read_file", path="missing.py").code == "not_a_file"
        # 超大的目标脚本：读不了 → 也就改不了
        (cand / "fig.py").write_bytes(b"x" * (tools.READ_LIMIT_BYTES + 1))
        before = _tree(cand)
        assert self.call(ts, "read_file", path="fig.py").code == "too_large"
        assert self.call(ts, "edit_file", old="x", new="y").code == "not_read"
        assert self.call(ts, "write_file", content="y").code == "not_read"
        assert _tree(cand) == before

    def test_write_file_replaces_target_and_size_capped(self, cand):
        ts = tool_set(cand)
        self.call(ts, "read_file", path="fig.py")
        assert (
            self.call(ts, "write_file", content="x" * (tools.READ_LIMIT_BYTES + 1)).code
            == "too_large"
        )
        r = self.call(ts, "write_file", content="print('hi')\n")
        assert not r.is_error and r.mutated
        assert (cand / "fig.py").read_text() == "print('hi')\n"
        assert sorted(p.name for p in cand.iterdir()) == ["fig.py", "notes.txt"], "不留临时文件"

    def test_crlf_files_keep_their_line_endings(self, cand):
        (cand / "fig.py").write_bytes(b"a = 'red'\r\nb = 2\r\n")
        ts = tool_set(cand)
        r = self.call(ts, "read_file", path="fig.py")
        assert "\r" not in r.content
        assert not self.call(ts, "edit_file", old="'red'\nb", new="'blue'\nb").is_error
        assert (cand / "fig.py").read_bytes() == b"a = 'blue'\r\nb = 2\r\n"

    def test_unknown_tool_and_bad_arguments_never_raise(self, cand):
        tool = tools.TOOLSETS["figure_edit"].tools(envelope(cand))
        by = {t.name: t for t in tool}
        r = loop._execute(by, wires.ToolCall("1", "rm_rf", {}))
        assert (r.is_error, r.code) == (True, "unknown_tool")
        r = loop._execute(by, wires.ToolCall("2", "read_file", None))
        assert r.code == "bad_arguments"
        r = loop._execute(by, wires.ToolCall("3", "edit_file", {"old": 1, "new": 2}))
        assert r.code == "bad_arguments"

        def boom(_args):
            raise RuntimeError(FILE_CANARY)

        by["read_file"].handler = boom
        r = loop._execute(by, wires.ToolCall("4", "read_file", {"path": "fig.py"}))
        assert r.code == "tool_crashed" and FILE_CANARY not in r.content + r.summary

    def test_no_shell_network_or_mcp_tools(self, cand):
        assert [t.name for t in tools.TOOLSETS["figure_edit"].tools(envelope(cand))] == [
            "read_file",
            "list_dir",
            "edit_file",
            "write_file",
            "check",
        ]

    def test_check_is_injected_or_not_available(self, cand):
        assert tool_set(cand)["check"].handler({}).code == "not_available"
        ok = tool_set(cand, check=lambda: (False, "line 2: boom"))["check"].handler({})
        assert (
            not ok.is_error
            and ok.content == "line 2: boom"
            and ok.summary == "check found problems"
        )

        def bad():
            raise RuntimeError(FILE_CANARY)

        crashed = tool_set(cand, check=bad)["check"].handler({})
        assert crashed.code == "check_crashed" and FILE_CANARY not in crashed.content

    def test_list_dir_is_one_level(self, cand):
        (cand / "sub").mkdir()
        (cand / "sub" / "deep.py").write_text("x")
        out = tool_set(cand)["list_dir"].handler({}).content
        assert "sub/" in out and "deep.py" not in out and "fig.py" in out


# ================================================================ Demo 端到端 / 循环
def demo_edit_script() -> dict:
    return {
        "turns": [
            {
                "text": "Looking.",
                "tool_calls": [{"name": "read_file", "args": {"path": "fig.py"}}],
                "usage": {"input": 100, "output": 10},
            },
            {
                "expect": [{"name": "read_file", "summary_contains": "read 3 lines"}],
                "tool_calls": [
                    {"name": "edit_file", "args": {"old": 'color="red"', "new": 'color="blue"'}}
                ],
                "usage": {"input": 150, "output": 20},
            },
            {
                "expect": [{"name": "edit_file", "summary_contains": "changed 1 lines"}],
                "tool_calls": [{"name": "check", "args": {}}],
                "usage": {"input": 160, "output": 5},
            },
            {
                "expect": [{"name": "check", "summary_contains": "check passed"}],
                "text": "Changed the line color to blue.",
                "usage": {"input": 170, "output": 12},
            },
        ]
    }


class TestDemoEndToEnd:
    def test_read_edit_check_final(self, cand):
        calls = []
        res, events = run_demo(
            cand, demo_edit_script(), check=lambda: (calls.append(1) or True, "ok")
        )
        assert (res.ok, res.reason) == (True, "completed"), events
        assert (
            'color="blue"' in (cand / "fig.py").read_text()
            and "red" not in (cand / "fig.py").read_text()
        )
        assert (cand / "notes.txt").read_text() == "keep me\n" and calls == [1]
        shapes = [
            (e["type"], e.get("name"), e.get("ok")) for e in events if e["type"] in ("tool", "done")
        ]
        assert shapes == [
            ("tool", "read_file", True),
            ("tool", "edit_file", True),
            ("tool", "check", True),
            ("done", None, True),
        ]
        d = done(events)
        assert d["reason"] == "completed" and d["changed"] is True and d["turns"] == 4
        assert d["usage"] == {"input_tokens": 580, "output_tokens": 47, "cached_input_tokens": 0}
        assert d["ledger_mismatch"] is False
        assert {e["type"] for e in events} <= {"thinking", "tool", "text", "usage", "done"}

    def test_demo_mismatch_is_reported_not_raised(self, cand):
        script = demo_edit_script()
        script["turns"][1]["expect"][0]["summary_contains"] = "something else"
        res, events = run_demo(cand, script)
        assert (res.ok, res.reason, res.detail) == (False, "network", "demo_mismatch")

    def test_subprocess_cli_demo(self, cand, tmp_path):
        (tmp_path / "script.json").write_text(json.dumps(demo_edit_script()), encoding="utf-8")
        (tmp_path / "prompt.txt").write_text(PROMPT_CANARY, encoding="utf-8")
        p = run_cli(
            cand,
            "demo",
            "--demo-script",
            str(tmp_path / "script.json"),
            "--prompt-file",
            str(tmp_path / "prompt.txt"),
        )
        lines = [json.loads(ln) for ln in p.stdout.splitlines()]
        # 没注入 check 回调：check 是「not available」工具错误，模型照样收尾
        assert lines[-1]["type"] == "done"
        assert [ln["ok"] for ln in lines if ln["type"] == "tool"] == [True, True, False]
        assert 'color="blue"' in (cand / "fig.py").read_text()


# ================================================================ 预算与结局
class TestBudgets:
    def test_max_turns(self, cand):
        script = {"turns": [{"tool_calls": [{"name": "list_dir", "args": {}}]}] * 10}
        res, events = run_demo(cand, script, limits=tools.Limits(max_turns=3))
        assert (res.ok, res.reason, res.turns) == (False, "max_turns", 3)
        assert done(events)["turns"] == 3

    def test_token_budget(self, cand):
        step = {
            "tool_calls": [{"name": "list_dir", "args": {}}],
            "usage": {"input": 100, "output": 10},
        }
        res, _ = run_demo(cand, {"turns": [step] * 10}, limits=tools.Limits(max_total_tokens=250))
        assert (res.reason, res.detail, res.turns) == ("budget", "tokens", 3)

    def test_seconds_budget(self, cand):
        res, _ = run_demo(cand, {"turns": [{"text": "x"}]}, limits=tools.Limits(max_seconds=-1))
        assert (res.reason, res.detail, res.turns) == ("budget", "time", 0)

    def test_same_tool_error_three_times_stops(self, cand):
        bad = {"tool_calls": [{"name": "read_file", "args": {"path": "../secret.txt"}}]}
        res, events = run_demo(cand, {"turns": [bad] * 5})
        assert (res.reason, res.detail, res.turns) == ("tool_error_loop", "path_outside", 3)
        assert done(events)["ok"] is False

    def test_different_or_interrupted_errors_do_not_count(self, cand):
        bad = {"tool_calls": [{"name": "read_file", "args": {"path": "../secret.txt"}}]}
        good = {"tool_calls": [{"name": "list_dir", "args": {}}]}
        other = {"tool_calls": [{"name": "read_file", "args": {"path": "nope.py"}}]}
        script = {"turns": [bad, bad, good, bad, bad, other, bad, {"text": "done"}]}
        res, _ = run_demo(cand, script)
        assert (res.ok, res.reason) == (True, "completed")

    def test_truncated_output_is_budget(self, cand, serve):
        body = {
            "content": [{"type": "text", "text": "cut"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        _, res, _ = http_run(cand, serve, "anthropic", [(200, {}, body)])
        assert (res.reason, res.detail) == ("budget", "output_truncated")

    def test_ledger_identity_holds_and_breakage_is_recorded_not_raised(self, cand, monkeypatch):
        res, events = run_demo(cand, demo_edit_script(), check=lambda: (True, "ok"))
        led = res.ledger
        assert len(led.turns) == 4
        assert led.total_in == sum(t[0] for t in led.turns) and led.total_ms == sum(
            t[2] for t in led.turns
        )
        assert done(events)["ledger_mismatch"] is False
        # 反证：账本记账少记一笔 → 恒等式不成立，只记 ledger_mismatch，不抛、不改结局
        real_add = loop.Ledger.add

        def sloppy(self, usage, ms):
            real_add(self, usage, ms)
            self.total_in += 1

        monkeypatch.setattr(loop.Ledger, "add", sloppy)
        res2, events2 = run_demo(
            cand, {"turns": [{"text": "x", "usage": {"input": 5, "output": 5}}]}
        )
        assert res2.ok and done(events2)["ledger_mismatch"] is True

    def test_context_compression_replaces_old_reads(self, cand):
        seen: list[list[str]] = []

        class Spy(loop.DemoModel):
            def complete(self, *, history, **kw):
                seen.append([r.content for m in history if m.role == "tool" for r in m.results])
                return super().complete(history=history, **kw)

        read = {"tool_calls": [{"name": "read_file", "args": {"path": "fig.py"}}]}
        model = Spy({"turns": [read, read, read, {"text": "done"}]})
        res, _ = run_demo(cand, {}, model=model, limits=tools.Limits(compress_at_tokens=10))
        assert res.ok and res.compressions >= 2
        last = seen[-1]
        assert len(last) == 3 and last[0].startswith("[already read fig.py (lines 1-3, sha ")
        assert FILE_CANARY in last[-1], "最近一批结果原样保留"

    def test_cancel_before_start_and_internal_errors_are_done_events(self, cand):
        ev = threading.Event()
        ev.set()
        res, events = run_demo(cand, {"turns": [{"text": "x"}]}, cancel=ev)
        assert (res.ok, res.reason) == (False, "cancelled") and done(events)[
            "reason"
        ] == "cancelled"

        class Broken:
            def complete(self, **kw):
                raise RuntimeError(PROMPT_CANARY)

        res, events = run_demo(cand, {}, model=Broken())
        assert (res.reason, res.detail) == ("internal", "RuntimeError")
        assert PROMPT_CANARY not in json.dumps(events)


# ================================================================ 子进程：隐私与取消
def run_cli(cand, wire, *extra, key=None, timeout=60, stdin=None):
    env = {k: v for k, v in os.environ.items() if k != "TAVOTTO_AGENT_API_KEY"}
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONUTF8"] = "1"
    if key:
        env["TAVOTTO_AGENT_API_KEY"] = key
    argv = [
        sys.executable,
        "-m",
        "tavotto.engine.agent_runner",
        "--cwd",
        str(cand),
        "--target",
        "fig.py",
        "--wire",
        wire,
        *extra,
    ]
    p = subprocess.run(
        argv,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    assert KEY_CANARY not in " ".join(argv), "密钥不在 argv"
    return p


class TestProcessBoundary:
    def test_secrets_prompt_and_file_contents_never_reach_stdout_or_stderr(
        self, cand, serve, tmp_path
    ):
        turns, _ = WIRE_CASES["anthropic"]
        # 失败变体：服务端错误体回显提示词
        for responses in (turns, [(500, {}, {"echo": PROMPT_CANARY, "file": FILE_CANARY})]):
            srv = serve(responses)
            p = run_cli(
                cand,
                "anthropic",
                "--base-url",
                srv.url,
                "--model",
                "m",
                "--retry-base",
                "0",
                key=KEY_CANARY,
                stdin=f"{PROMPT_CANARY} make it blue",
            )
            # 前提：金丝雀确实到过服务器（否则「没泄漏」可能只是没发出去）
            _, headers, body = srv.requests[0]
            assert headers["x-api-key"] == KEY_CANARY
            assert PROMPT_CANARY in json.dumps(body)
            if responses is turns:
                assert FILE_CANARY in json.dumps(srv.requests[1][2])
            for stream in (p.stdout, p.stderr):
                for canary in (KEY_CANARY, PROMPT_CANARY, FILE_CANARY):
                    assert canary not in stream, (canary, stream)
            lines = [json.loads(ln) for ln in p.stdout.splitlines()]
            assert lines[-1]["type"] == "done"
            assert p.returncode == (0 if responses is turns else 1)

    def test_missing_key_and_bad_base_url_are_done_events(self, cand):
        p = run_cli(
            cand, "anthropic", "--base-url", "https://x.invalid", "--model", "m", stdin="hi"
        )
        d = json.loads(p.stdout.splitlines()[-1])
        assert (d["ok"], d["reason"], d["detail"]) == (
            False,
            "auth",
            "no_key",
        ) and p.returncode == 1
        p = run_cli(
            cand,
            "anthropic",
            "--base-url",
            "http://example.com",
            "--model",
            "m",
            key=KEY_CANARY,
            stdin="hi",
        )
        d = json.loads(p.stdout.splitlines()[-1])
        assert (d["reason"], d["detail"]) == ("network", "base_url_scheme")
        assert KEY_CANARY not in p.stdout + p.stderr

    @pytest.mark.skipif(
        sys.platform == "win32", reason="SIGTERM 取消只在 POSIX 验证（Windows 走 Ctrl-Break）"
    )
    def test_sigterm_yields_done_cancelled(self, cand, serve):
        release = threading.Event()
        srv = serve([lambda: release.wait(20)])
        env = {**os.environ, "PYTHONPATH": str(SRC), "TAVOTTO_AGENT_API_KEY": KEY_CANARY}
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tavotto.engine.agent_runner",
                "--cwd",
                str(cand),
                "--target",
                "fig.py",
                "--wire",
                "anthropic",
                "--base-url",
                srv.url,
                "--model",
                "m",
            ],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            proc.stdin.write("make it blue")
            proc.stdin.close()
            deadline = time.monotonic() + 15
            while not srv.requests and time.monotonic() < deadline:
                time.sleep(0.05)
            assert srv.requests, "判据的前提：请求已在飞"
            t0 = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=10)
            assert time.monotonic() - t0 < 5, "取消要尽快收尾，不等请求超时"
        finally:
            release.set()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        d = json.loads(out.splitlines()[-1])
        assert (d["type"], d["ok"], d["reason"]) == ("done", False, "cancelled")
        assert proc.returncode == 1 and KEY_CANARY not in out + err
