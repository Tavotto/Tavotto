"""`siwc-responses` 协议（ADR 0201 §二）：全部是 **Mock**——合成的 SSE 字节，从不联网、从不用真实 ChatGPT 账号。

判据的主语：

* 禁用参数：**注入的 `post` 缝实际收到的请求体**（字段降级 `apply` 之后、包括每一次重试），不是 `build()` 的中间量。
* 结局：`loop.run` 的 `done` 事件（`reason` / `detail`）与 `post` 被调用的次数（「不重试」= 恰好一次）。
* 文件：候选目录里**磁盘上的字节**。
* 隐私：runner 进程入口真实写出的 stdout / stderr（令牌金丝雀），并先证明令牌确实到过请求头。

真服务未验证的点（namespace 工具形状、completed 是否带 output 等）在 `siwc_wire` 模块说明里；这里两种形状都覆盖。
"""

from __future__ import annotations

import io
import json
import logging
import signal
import threading
import time
from pathlib import Path

import pytest

from tavotto.engine.agent_runner import loop, siwc_wire, tools, wires

TOKEN = "siwc-canary-ACCESS-9f31"
SCRIPT = 'import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4], color="red")\nplt.show()\n'


# --------------------------------------------------------------------------- 夹具
@pytest.fixture
def cand(tmp_path) -> Path:
    d = tmp_path / "cand"
    d.mkdir()
    (d / "fig.py").write_text(SCRIPT, encoding="utf-8")
    return d


def _envelope(cand: Path, **kw) -> tools.RunEnvelope:
    return tools.RunEnvelope(
        task_type="figure_edit",
        cwd=cand,
        target="fig.py",
        instruction="make the line blue",
        limits=kw.pop("limits", tools.Limits()),
        **kw,
    )


def _sse(*events: dict, done: bool = False, deltas: int = 3) -> bytes:
    """合成一条流：每个事件前面垫几帧 `*.delta`（真实流里它们占绝大部分字节），可选 `[DONE]`。"""
    out = [b": keep-alive\n\n"]
    for ev in events:
        for i in range(deltas):
            out.append(
                b"event: response.output_text.delta\ndata: "
                + json.dumps({"type": "response.output_text.delta", "delta": f"x{i}"}).encode()
                + b"\n\n"
            )
        out.append(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
    if done:
        out.append(b"data: [DONE]\n\n")
    return b"".join(out)


def _usage(i=10, o=5) -> dict:
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


def _msg(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _call(name: str, args: dict, cid: str) -> dict:
    return {
        "type": "function_call",
        "id": f"fc_{cid}",
        "call_id": cid,
        "name": name,
        "arguments": json.dumps(args),
    }


def _completed(*items: dict, with_output: bool = True) -> bytes:
    done_items = [{"type": "response.output_item.done", "item": it} for it in items]
    resp = {"id": "resp_1", "status": "completed", "usage": _usage()}
    if with_output:
        resp["output"] = list(items)
    return _sse(*done_items, {"type": "response.completed", "response": resp})


class Server:
    """`post` 缝：按顺序回放回复，记下每一次请求（url / 头 / 体 / 是否带了 SSE 读取器）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, url, headers, body, *, timeout, reader=None):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": json.loads(json.dumps(body)),
                "reader": reader,
            }
        )
        status, data = self.replies.pop(0)
        if 200 <= status < 300 and reader is not None:
            data = reader(io.BytesIO(data))  # 与真实传输同一个读取器：增量帧在这里被丢掉
        return status, {}, data


def _model(server, **kw) -> loop.HttpModel:
    return loop.HttpModel(
        siwc_wire.SiwcResponsesWire(),
        base_url=siwc_wire.BASE_URL,
        api_key=TOKEN,
        model="gpt-test",
        retry_base=0.0,
        post=server,
        **kw,
    )


def _run(cand, server, **kw):
    events: list[dict] = []
    cancel = kw.pop("cancel", None)
    res = loop.run(
        _model(server, **kw), tools.TOOLSETS["figure_edit"], _envelope(cand), events.append, cancel
    )
    assert events and events[-1]["type"] == "done"
    return res, events[-1], events


def _assert_compliant(req: dict) -> None:
    body = req["body"]
    assert set(body) <= set(siwc_wire._ALLOWED_FIELDS), sorted(body)
    for f in siwc_wire.FORBIDDEN_FIELDS:
        assert f not in body, f
    assert body["stream"] is True and body["store"] is False
    # 思考：只在选了 SIWC_EFFORTS 里的档位时发，且一定连着 include 加密思考（2026-11-25 真服务核对）
    if "reasoning" in body:
        assert body["reasoning"]["effort"] in siwc_wire.SIWC_EFFORTS
        assert body["include"] == ["reasoning.encrypted_content"]
    else:
        assert "include" not in body
    assert isinstance(body["input"], list)
    for it in body["input"]:
        assert it.get("role") != "system"
        # 回放的 reasoning 条目必须带 encrypted_content（store: false 下不带的会被拒）
        assert it.get("type") != "reasoning" or (
            "reasoning" in body and it.get("encrypted_content")
        )
    for t in body.get("tools") or []:
        assert t["type"] == "namespace" and t["name"] == siwc_wire.NAMESPACE
        assert all(inner["type"] == "function" for inner in t["tools"])
    assert req["url"] == "https://api.openai.com/v1/responses"
    assert req["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert req["headers"]["Accept"] == "text/event-stream"
    assert req["reader"] is siwc_wire.compact_sse


# --------------------------------------------------------------------------- 单轮 / 多轮 / 工具
def test_single_turn_text(cand):
    server = Server([(200, _completed(_msg("nothing to change")))])
    res, done, _ = _run(cand, server)
    assert res.ok and done["reason"] == "completed"
    assert len(server.requests) == 1
    _assert_compliant(server.requests[0])
    assert server.requests[0]["body"]["instructions"]  # 系统提示只走 instructions


def test_multi_turn_tool_calls_edit_the_file_and_replay_statelessly(cand):
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": []}
    server = Server(
        [
            (200, _completed(reasoning, _call("read_file", {"path": "fig.py"}, "c1"))),
            (
                200,
                _completed(
                    _call("tavotto.edit_file", {"old": 'color="red"', "new": 'color="blue"'}, "c2")
                ),
            ),
            (200, _completed(_msg("done"))),
        ]
    )
    res, done, events = _run(cand, server)
    assert res.ok and done["reason"] == "completed", done
    assert (cand / "fig.py").read_text() == SCRIPT.replace('color="red"', 'color="blue"')
    assert len(server.requests) == 3
    for req in server.requests:  # 每一次实际发出的请求都合规（含回放）
        _assert_compliant(req)
    second = server.requests[1]["body"]["input"]
    assert [it.get("type") for it in second[1:]] == ["function_call", "function_call_output"]
    assert second[2]["call_id"] == "c1" and "plt.plot" in json.dumps(second[2]["output"])
    third = server.requests[2]["body"]["input"]
    assert third[-1]["call_id"] == "c2"
    # 无状态：每一轮都带完整历史，不用 previous_response_id
    assert len(third) > len(second)


def test_completed_without_output_uses_item_done_events(cand):
    server = Server(
        [
            (200, _completed(_call("read_file", {"path": "fig.py"}, "c1"), with_output=False)),
            (200, _completed(_msg("ok"), with_output=False)),
        ]
    )
    res, done, _ = _run(cand, server)
    assert res.ok, done
    assert server.requests[1]["body"]["input"][-1]["type"] == "function_call_output"


def test_tool_error_is_fed_back_and_the_loop_continues(cand):
    server = Server(
        [
            (200, _completed(_call("read_file", {"path": "../outside.txt"}, "c1"))),
            (200, _completed(_msg("gave up"))),
        ]
    )
    res, done, _ = _run(cand, server)
    assert res.ok, done
    fed = server.requests[1]["body"]["input"][-1]
    assert fed["type"] == "function_call_output" and fed["call_id"] == "c1"
    assert (cand / "fig.py").read_text() == SCRIPT


def test_forbidden_fields_never_sent_even_when_the_loop_passes_them(cand):
    wire = siwc_wire.SiwcResponsesWire()
    req = wire.build(
        base_url=siwc_wire.BASE_URL,
        api_key=TOKEN,
        model="m",
        system="sys",
        tools=[wires.ToolSpec("read_file", "read", {"type": "object", "properties": {}})],
        history=[wires.Msg(role="user", text="hi")],
        effort="high",
        max_output_tokens=32768,
        thinking="adaptive",
    )
    _assert_compliant({**req.__dict__})


# --------------------------------------------------------------------------- 终态与失败
def test_stream_without_terminal_event_is_retried_then_fails(cand):
    broken = _sse({"type": "response.output_item.done", "item": _msg("half")})
    server = Server([(200, broken)] * 2)
    res, done, _ = _run(cand, server, max_attempts=2)
    assert not res.ok and (done["reason"], done["detail"]) == ("network", "stream_incomplete")
    assert len(server.requests) == 2


def test_stream_interruption_then_success(cand):
    server = Server([(200, _sse()), (200, _completed(_msg("ok")))])
    res, done, _ = _run(cand, server)
    assert res.ok and len(server.requests) == 2


def _failed(code: str) -> bytes:
    return _sse(
        {
            "type": "response.failed",
            "response": {"id": "r", "status": "failed", "error": {"code": code, "message": "x"}},
        }
    )


def _err(code: str) -> bytes:
    return json.dumps({"error": {"code": code, "message": "PRIVATE_SERVER_TEXT"}}).encode()


@pytest.mark.parametrize(
    "status,data,reason,detail,attempts",
    [
        # 用量超限：不重试（流中途与预流两种都覆盖）
        (200, _failed("subscription_sharing_usage_limit_exceeded"), "quota", "plan_usage_limit", 1),
        (429, _err("subscription_sharing_usage_limit_exceeded"), "quota", "plan_usage_limit", 1),
        # 暂时不可用：有界退避，用尽后 network
        (
            200,
            _failed("subscription_sharing_usage_unavailable"),
            "network",
            "plan_usage_unavailable",
            3,
        ),
        (503, _err("subscription_sharing_user_unavailable"), "network", "plan_user_unavailable", 3),
        # 不具备资格：不是 auth（官方禁止因此循环 OAuth），不重试
        (403, _err("subscription_sharing_user_not_eligible"), "refused", "plan_not_eligible", 1),
        (403, b'{"detail":"region"}', "refused", "plan_forbidden", 1),
        # 配置类：不重试同一请求体
        (
            400,
            _err("subscription_sharing_unsupported_capability"),
            "internal",
            "plan_unsupported_capability",
            1,
        ),
        (
            403,
            _err("subscription_sharing_route_not_supported"),
            "internal",
            "plan_route_not_supported",
            1,
        ),
        (403, _err("chatpass_v2_scope_not_authorized"), "internal", "plan_scope_not_authorized", 1),
        (401, _err("subscription_sharing_invalid_user"), "auth", "plan_invalid_user", 1),
        (401, b"{}", "auth", "http_401", 1),
        # 没有闭集码的 429：通用规则（重试后按限流）
        (429, b"{}", "quota", "rate_limited", 3),
    ],
)
def test_error_codes(cand, status, data, reason, detail, attempts):
    server = Server([(status, data)] * attempts)
    res, done, events = _run(cand, server, max_attempts=3)
    assert not res.ok and (done["reason"], done["detail"]) == (reason, detail), done
    assert len(server.requests) == attempts
    assert "PRIVATE_SERVER_TEXT" not in json.dumps(events)


def test_no_protocol_switch_or_field_degradation(cand):
    """404 / 「字段不被接受」的 400 在别的协议上会换到备选协议 / 剥字段重发。这里即使给了备选路由也一律只发一次、不换。"""
    for status, body in (
        (404, b"not found"),
        (400, b'{"error":{"message":"unsupported parameter: \\"include\\""}}'),
    ):
        server = Server([(status, body)] * 3)
        res, done, _ = _run(
            cand,
            server,
            max_attempts=3,
            fallbacks=[(wires.WIRES["openai-responses"], siwc_wire.BASE_URL)],
        )
        assert not res.ok and len(server.requests) == 1, status
        assert done.get("wire_switched_from", "") == ""
        assert server.requests[0]["reader"] is siwc_wire.compact_sse


def test_retried_requests_stay_compliant(cand):
    server = Server(
        [
            (503, _err("subscription_sharing_usage_unavailable")),
            (200, _sse()),  # 流断了
            (200, _completed(_call("read_file", {"path": "fig.py"}, "c1"))),
            (503, b"{}"),
            (200, _completed(_msg("ok"))),
        ]
    )
    res, done, _ = _run(cand, server, max_attempts=4)
    assert res.ok, done
    assert len(server.requests) == 5
    for req in server.requests:
        _assert_compliant(req)


def test_server_side_truncation_discards_the_partial_turn(cand):
    trunc = _sse(
        {"type": "response.output_item.done", "item": _call("read_file", {"path": "fig.py"}, "c1")},
        {
            "type": "response.incomplete",
            "response": {"id": "r", "incomplete_details": {"reason": "max_output_tokens"}},
        },
    )
    server = Server([(200, trunc), (200, _completed(_msg("ok")))])
    res, done, events = _run(cand, server)
    assert res.ok, done
    # 截断轮的工具调用不执行，也不进下一轮的回放
    assert not any(e.get("type") == "tool" for e in events)
    assert all(it.get("type") != "function_call" for it in server.requests[1]["body"]["input"])
    _assert_compliant(server.requests[1])


def test_incomplete_for_other_reasons_is_refused(cand):
    data = _sse(
        {
            "type": "response.incomplete",
            "response": {
                "id": "r",
                "incomplete_details": {"reason": "content_filter"},
                "output": [],
            },
        }
    )
    res, done, _ = _run(cand, Server([(200, data)]))
    assert not res.ok and done["reason"] == "refused"


def test_cancel_while_streaming(cand):
    started = threading.Event()

    def slow(url, headers, body, *, timeout, reader=None):
        started.set()
        time.sleep(5)
        return 200, {}, _completed(_msg("late"))

    cancel = threading.Event()
    threading.Thread(target=lambda: (started.wait(2), cancel.set()), daemon=True).start()
    t0 = time.monotonic()
    res, done, _ = _run(cand, slow, cancel=cancel)
    assert not res.ok and done["reason"] == "cancelled"
    assert time.monotonic() - t0 < 3
    assert (cand / "fig.py").read_text() == SCRIPT


# --------------------------------------------------------------------------- SSE 读取器
class _Chunked(io.RawIOBase):
    def __init__(self, data: bytes, size: int):
        self.data, self.size, self.reads = data, size, 0

    def read(self, n=-1):
        self.reads += 1
        piece, self.data = self.data[: self.size], self.data[self.size :]
        return piece


def test_compact_drops_deltas_and_stops_after_terminal():
    tail = b"event: response.output_text.delta\ndata: {}\n\n" * 1000
    raw = _completed(_msg("hi")) + tail
    src = _Chunked(raw, 7)  # 帧跨块切开
    kept = siwc_wire.compact_sse(src)
    assert b".delta" not in kept and b"keep-alive" not in kept
    assert siwc_wire.fold(kept)["output"][0]["content"][0]["text"] == "hi"
    assert src.data, "终态之后不再读"


def test_compact_caps_total_bytes(monkeypatch):
    monkeypatch.setattr(siwc_wire, "MAX_STREAM_BYTES", 10_000)
    raw = b"event: response.output_text.delta\ndata: {}\n\n" * 1000
    with pytest.raises(wires.RunnerFailure) as exc:
        siwc_wire.compact_sse(io.BytesIO(raw))
    assert exc.value.detail == "response_too_large"


def test_fold_handles_data_only_frames_and_crlf():
    resp = {"id": "r", "status": "completed", "output": [_msg("ok")], "usage": _usage()}
    raw = (
        "data: " + json.dumps({"type": "response.completed", "response": resp}) + "\r\n\r\n"
    ).encode()
    assert siwc_wire.fold(siwc_wire.compact_sse(io.BytesIO(raw)))["status"] == "completed"


def test_data_only_frame_whose_type_is_not_the_first_key_survives():
    resp = {"id": "r", "status": "completed", "output": [_msg("ok")], "usage": _usage()}
    big = {
        "response": resp,
        "padding": "p" * 5000,
        "type": "response.completed",
    }  # type 远在 peek 之外
    item = {"item": _msg("ok"), "pad": "q" * 5000, "type": "response.output_item.done"}
    raw = ("data: " + json.dumps(item) + "\n\n" + "data: " + json.dumps(big) + "\n\n").encode()
    folded = siwc_wire.fold(siwc_wire.compact_sse(io.BytesIO(raw)))
    assert folded["status"] == "completed" and folded["output"][0]["type"] == "message"


class _ThenHang(io.RawIOBase):
    """先给出全部字节，之后再读就当作「服务端不关连接」——读取器不该再读。"""

    def __init__(self, data: bytes):
        self.data = data

    def read1(self, n=-1):
        if not self.data:
            raise AssertionError("read after terminal event")
        piece, self.data = self.data[:n], self.data[n:]
        return piece

    read = read1


@pytest.mark.parametrize("late", [True, False])
def test_data_only_terminal_frame_stops_reading(late):
    """只有 data 行的终态帧：type 在 4096 字节之后、或前面先出现嵌套条目的 type，都要认出终态立即返回（Codex #2132 P2）。"""
    resp = {"output": [_msg("ok")], "id": "r", "status": "completed", "usage": _usage()}
    frame = (
        {"response": resp, "pad": "p" * 5000, "type": "response.completed"}
        if late
        else {"response": resp, "type": "response.completed"}
    )
    raw = ("data: " + json.dumps(frame) + "\n\n").encode()
    kept = siwc_wire.compact_sse(_ThenHang(raw))
    assert siwc_wire.fold(kept)["status"] == "completed"


def test_multiline_data_frames_round_trip():
    resp = {"id": "r", "status": "completed", "output": [_msg("ok")], "usage": _usage()}
    text = json.dumps({"type": "response.completed", "response": resp}, indent=1)
    raw = (
        "event: response.completed\n" + "".join(f"data: {ln}\n" for ln in text.split("\n")) + "\n"
    ).encode()
    kept = siwc_wire.compact_sse(io.BytesIO(raw))
    assert siwc_wire.fold(kept)["output"][0]["content"][0]["text"] == "ok"


def test_error_event_maps_closed_codes():
    raw = _sse({"type": "error", "code": "subscription_sharing_usage_limit_exceeded"})
    with pytest.raises(wires.RunnerFailure) as exc:
        siwc_wire.fold(raw)
    assert exc.value.reason == "quota"


# --------------------------------------------------------------------------- 开关与进程入口
def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(siwc_wire.ENV_FLAG, raising=False)
    assert not siwc_wire.enabled()
    for v in ("", "0", "true", "yes"):
        assert not siwc_wire.enabled({siwc_wire.ENV_FLAG: v})
    assert siwc_wire.enabled({siwc_wire.ENV_FLAG: "1"})


def test_siwc_is_not_a_regular_wire():
    """不在 `WIRES` 里：保存时探测、`--fallback`、AI 修复的 `build_model` 都碰不到它。"""
    assert siwc_wire.WIRE_NAME not in wires.WIRES


def _cli(monkeypatch, cand, tmp_path, capsys, *, flag: bool, extra=(), replies=()):
    from tavotto.engine.agent_runner import runner_cli

    server = Server(replies)
    actual = loop.HttpModel

    def model(*args, **kwargs):
        return actual(*args, **kwargs, post=server)

    monkeypatch.setattr(loop, "HttpModel", model)
    monkeypatch.setenv("TAVOTTO_AGENT_API_KEY", TOKEN)
    if flag:
        monkeypatch.setenv(siwc_wire.ENV_FLAG, "1")
    else:
        monkeypatch.delenv(siwc_wire.ENV_FLAG, raising=False)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("make the line blue", encoding="utf-8")
    argv = [
        "--cwd", str(cand), "--target", "fig.py", "--wire", siwc_wire.WIRE_NAME,
        "--model", "gpt-test", "--prompt-file", str(prompt), *extra,
    ]  # fmt: skip
    product = logging.getLogger("tavotto")
    saved_log = (list(product.handlers), product.propagate)
    saved_sig = {
        n: signal.getsignal(getattr(signal, n)) for n in ("SIGTERM", "SIGINT") if hasattr(signal, n)
    }
    try:
        code = runner_cli.main(argv)
    finally:
        product.handlers, product.propagate = saved_log
        for n, h in saved_sig.items():
            signal.signal(getattr(signal, n), h)
    out = capsys.readouterr()
    lines = [json.loads(x) for x in out.out.splitlines() if x.strip()]
    return code, lines, out, server


GOOD = ("--base-url", siwc_wire.BASE_URL, "--auth", "bearer")


@pytest.mark.parametrize(
    "flag,extra",
    [
        (False, GOOD),  # 开关没开
        (True, ("--base-url", siwc_wire.BASE_URL)),  # 不是 bearer
        (True, ("--base-url", "https://example.test/v1", "--auth", "bearer")),  # 令牌不去别处
        (True, (*GOOD, "--effort", "minimal")),  # SIWC 拒 minimal（真服务 unsupported_value）
        (True, (*GOOD, "--fallback", "openai-chat=https://api.openai.com/v1")),
    ],
)
def test_cli_refuses_without_any_request(monkeypatch, cand, tmp_path, capsys, flag, extra):
    code, lines, _out, server = _cli(monkeypatch, cand, tmp_path, capsys, flag=flag, extra=extra)
    assert code == 1 and lines[-1]["type"] == "done"
    assert (lines[-1]["reason"], lines[-1]["detail"]) == ("internal", "bad_args")
    assert server.requests == []


def test_cli_end_to_end_never_leaks_the_token(monkeypatch, cand, tmp_path, capsys):
    replies = [
        (200, _completed(_call("read_file", {"path": "fig.py"}, "c1"))),
        (200, _completed(_msg(f"echo {TOKEN}"))),  # 服务端回显令牌：出口也得抹掉
    ]
    code, lines, out, server = _cli(
        monkeypatch, cand, tmp_path, capsys, flag=True, extra=GOOD, replies=replies
    )
    assert code == 0 and lines[-1]["reason"] == "completed"
    assert server.requests[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"  # 令牌确实发出去过
    assert TOKEN not in out.out and TOKEN not in out.err


# --------------------------------------------------------------------------- 真实传输（本机回环，`wires.post_json` 默认路径）
class _SseServer:
    """127.0.0.1 上的假服务：分块写 SSE；`drop_after` 字节后断开；`hang_after` 字节后停住不动（连接不关）。"""

    def __init__(self, bodies, *, chunk=5, drop_after=None, hang_after=None):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.bodies, self.requests = list(bodies), []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                outer.requests.append(
                    (
                        {k.lower(): v for k, v in self.headers.items()},
                        json.loads(self.rfile.read(n)),
                    )
                )
                data = outer.bodies[min(len(outer.requests), len(outer.bodies)) - 1]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                sent = 0
                for i in range(0, len(data), chunk):
                    if hang_after is not None and sent >= hang_after:
                        time.sleep(10)
                        return
                    if drop_after is not None and sent >= drop_after:
                        self.close_connection = True
                        return  # 不发终止块：流在中途断掉
                    piece = data[i : i + chunk]
                    self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                    self.wfile.flush()
                    sent += len(piece)
                self.wfile.write(b"0\r\n\r\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _real(cand, srv, **kw):
    events: list[dict] = []
    model = loop.HttpModel(
        siwc_wire.SiwcResponsesWire(),
        base_url=srv.url,
        api_key=TOKEN,
        model="gpt-test",
        retry_base=0.0,
        **kw.pop("model_kw", {}),
    )
    res = loop.run(
        model, tools.TOOLSETS["figure_edit"], _envelope(cand), events.append, kw.pop("cancel", None)
    )
    return res, events[-1]


def test_real_transport_chunked_stream(cand):
    srv = _SseServer(
        [_completed(_call("read_file", {"path": "fig.py"}, "c1")), _completed(_msg("ok"))], chunk=7
    )
    try:
        res, done = _real(cand, srv)
    finally:
        srv.close()
    assert res.ok, done
    headers, body = srv.requests[0]
    assert (
        headers["accept"] == "text/event-stream" and headers["authorization"] == f"Bearer {TOKEN}"
    )
    assert body["stream"] is True and "max_output_tokens" not in body


def test_real_transport_connection_dropped_mid_stream(cand):
    srv = _SseServer([_completed(_msg("never finishes"))], chunk=16, drop_after=64)
    try:
        res, done = _real(cand, srv, model_kw={"max_attempts": 2})
    finally:
        srv.close()
    assert not res.ok and done["reason"] == "network", done
    assert len(srv.requests) == 2  # 可重试，用尽后失败


def test_real_transport_cancel_while_bytes_arrive(cand):
    srv = _SseServer([_completed(_msg("slow"))], chunk=8, hang_after=40)
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    t0 = time.monotonic()
    try:
        res, done = _real(cand, srv, cancel=cancel)
    finally:
        srv.close()
    assert not res.ok and done["reason"] == "cancelled"
    assert time.monotonic() - t0 < 5


def test_real_transport_stops_reading_after_the_terminal_event(cand):
    """终态帧之后服务端一直不关连接：读取器在终态处就停，不等到超时（证明传输层真的走了 `reader`）。"""
    body = _completed(_msg("ok"))
    srv = _SseServer(
        [body + b"event: response.output_text.delta\ndata: {}\n\n"], chunk=8, hang_after=len(body)
    )
    t0 = time.monotonic()
    try:
        res, done = _real(cand, srv, model_kw={"request_timeout": 8})
    finally:
        srv.close()
    assert res.ok, done
    assert time.monotonic() - t0 < 4


# --------------------------------------------------------------------------- 思考强度（2026-11-25 真服务核对后打开）
def _build(effort, history):
    return siwc_wire.SiwcResponsesWire().build(
        base_url=siwc_wire.BASE_URL,
        api_key=TOKEN,
        model="m",
        system="sys",
        tools=[],
        history=history,
        effort=effort,
        max_output_tokens=1024,
    )


def test_effort_sends_reasoning_with_encrypted_include():
    req = _build("high", [wires.Msg(role="user", text="hi")])
    assert req.body["reasoning"] == {"effort": "high"}
    assert req.body["include"] == ["reasoning.encrypted_content"]
    _assert_compliant({**req.__dict__})


@pytest.mark.parametrize("effort", [None, "minimal", "none", "adaptive"])
def test_unsupported_or_missing_effort_sends_no_reasoning(effort):
    req = _build(effort, [wires.Msg(role="user", text="hi")])
    assert "reasoning" not in req.body and "include" not in req.body


def test_replay_keeps_only_encrypted_reasoning_items():
    turn = wires.Turn(
        text="",
        tool_calls=[wires.ToolCall("c1", "read_file", {"path": "a"})],
        native=[
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"},
            {"type": "reasoning", "id": "rs_2", "summary": []},
            _call("read_file", {"path": "a"}, "c1"),
        ],
    )
    hist = [
        wires.Msg(role="user", text="go"),
        wires.Msg(role="assistant", turn=turn),
        wires.Msg(role="tool", results=[wires.ToolResult("c1", "read_file", "x")]),
    ]
    with_effort = [
        it.get("id") for it in _build("medium", hist).body["input"] if it.get("type") == "reasoning"
    ]
    assert with_effort == ["rs_1"]
    without = [it for it in _build(None, hist).body["input"] if it.get("type") == "reasoning"]
    assert without == []


@pytest.mark.parametrize(
    ("effort", "ok"),
    [
        (None, True),
        ("low", True),
        ("xhigh", True),
        ("max", True),
        ("minimal", False),
        ("none", False),
    ],
)
def test_runner_accepts_only_siwc_efforts(monkeypatch, effort, ok):
    monkeypatch.setenv(siwc_wire.ENV_FLAG, "1")
    assert (
        siwc_wire.runner_args_ok(
            base_url=siwc_wire.BASE_URL, auth="bearer", fallback=[], effort=effort, thinking="none"
        )
        is ok
    )
