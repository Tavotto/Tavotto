"""协议层：三种线上协议 + 出站传输。

`Wire` 只做两件事：把「系统提示 + 工具定义 + 中立的对话历史」变成请求，把回复体解析成一个 `Turn`。
传输（urllib 的网络细节）在本文件的 `post_json`；重试 / 预算 / 取消在 `loop`。

**出站形状**（`tests/test_outbound_https_trust.py` 按 AST 钉）：`_opener()` 是本模块唯一的 `build_opener`，
`build_opener(tlstrust.https_handler(ctx))`、不跟随重定向。回环 http 不经 opener：`http.client.HTTPConnection("127.0.0.1", …)`
直连 socket，不读代理环境变量（与 `session_client` 同一写法）。只许 https，或主机恰为字面量 `127.0.0.1` 的 http。

线上字段细节（Anthropic Messages / OpenAI Chat Completions / OpenAI Responses）按官方文档的记忆写，
每个类的注释里标「待核」的地方是没把握的。
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .. import tlstrust

USER_AGENT = "tavotto-agent-runner/1"
ANTHROPIC_VERSION = "2023-06-01"
#: 响应体上限（字节）。模型回复远小于它；超了当作异常回复，不读完。
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

EFFORTS = ("low", "medium", "high")
#: anthropic `thinking.budget_tokens`（按 effort；待核：新模型可能改用自适应思考 / effort 参数）
_THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 12000}


# --------------------------------------------------------------------------- 中立数据
@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    #: 解析后的参数；`None` = 模型给的参数不是合法 JSON 对象（循环把它变成工具错误）
    args: dict | None


@dataclass
class Usage:
    input_tokens: int = 0  # 含缓存命中 / 写入的全部输入 token
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    #: "end" | "tool_use" | "max_tokens" | "other"
    stop: str = "end"
    #: 思考内容的字符数（内容本身不出进程）
    thinking_chars: int = 0
    #: 协议私有的、回放这一轮时要原样带回的东西（anthropic 的 content 块含 thinking 签名；responses 的 output 项）
    native: Any = None


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False
    #: 稳定的短错误码（事件里用；连续同码 3 次终止）
    code: str = ""
    #: 事件里的一句话摘要（不含内容）
    summary: str = ""
    #: 上下文压缩时替换 content 的摘要；None = 不可压缩
    compact: str | None = None
    #: 这次调用改了文件
    mutated: bool = False


@dataclass
class Msg:
    """中立的对话消息：`user`（text）/ `assistant`（turn）/ `tool`（results）。"""

    role: str
    text: str = ""
    turn: Turn | None = None
    results: list[ToolResult] = field(default_factory=list)


class RunnerFailure(Exception):
    """一次模型调用失败。`reason` 属于 done 事件的闭集；`detail` 是短代码，不含任何内容。"""

    def __init__(self, reason: str, detail: str = "", *, retryable: bool = False):
        super().__init__(f"{reason}:{detail}")
        self.reason = reason
        self.detail = detail
        self.retryable = retryable


@dataclass
class Request:
    url: str
    headers: dict[str, str]
    body: dict


class Wire(Protocol):
    name: str

    def build(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        system: str,
        tools: list[ToolSpec],
        history: list[Msg],
        effort: str | None,
        max_output_tokens: int,
    ) -> Request: ...

    def parse(self, body: dict) -> Turn: ...


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _loads_args(raw: Any) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if raw in ("", None):
        return {}
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _bad(detail: str) -> RunnerFailure:
    return RunnerFailure("network", detail)


# --------------------------------------------------------------------------- anthropic
class AnthropicWire:
    """POST `{base}/v1/messages`。

    * 鉴权：`x-api-key` 与 `Authorization: Bearer` 都给（官方 / 各家网关各认一个），`anthropic-version: 2023-06-01`。
    * 提示缓存：系统提示块与**最后一个**工具定义末尾各加 `cache_control: {"type": "ephemeral"}`
      （缓存前缀顺序 tools → system → messages，所以稳定的两段在最前）。
    * 思考：给了 effort 才传 `thinking: {"type": "enabled", "budget_tokens": N}`，`max_tokens` 相应加上预算
      （要求 `max_tokens > budget_tokens`）。待核：较新的模型是否改用 adaptive thinking。
    * 工具：`tools[{name, description, input_schema}]`；回复 `content[]` 里的 `tool_use{id, name, input}`；
      回填为下一条 user 消息里的 `tool_result{tool_use_id, content, is_error}` 块。
    * 回放 assistant 轮时原样带回 `content` 块（含 `thinking` 块及其 `signature`，有工具调用的思考轮要求如此）。
    """

    name = "anthropic"

    def build(
        self, *, base_url, api_key, model, system, tools, history, effort, max_output_tokens
    ) -> Request:
        tool_defs = [
            {"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools
        ]
        if tool_defs:
            tool_defs[-1]["cache_control"] = {"type": "ephemeral"}
        msgs: list[dict] = []
        for m in history:
            if m.role == "user":
                msgs.append({"role": "user", "content": [{"type": "text", "text": m.text}]})
            elif m.role == "assistant" and m.turn is not None:
                msgs.append({"role": "assistant", "content": m.turn.native or []})
            elif m.role == "tool":
                msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.call_id,
                                "content": r.content,
                                **({"is_error": True} if r.is_error else {}),
                            }
                            for r in m.results
                        ],
                    }
                )
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": msgs,
        }
        if tool_defs:
            body["tools"] = tool_defs
        if effort in _THINKING_BUDGET:
            budget = _THINKING_BUDGET[effort]
            body["max_tokens"] = max_output_tokens + budget
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        headers = {
            "x-api-key": api_key,
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        return Request(base_url.rstrip("/") + "/v1/messages", headers, body)

    def parse(self, body: dict) -> Turn:
        content = body.get("content")
        if not isinstance(content, list):
            raise _bad("bad_response")
        turn = Turn(native=content)
        texts: list[str] = []
        for blk in content:
            if not isinstance(blk, dict):
                continue
            kind = blk.get("type")
            if kind == "text" and isinstance(blk.get("text"), str):
                texts.append(blk["text"])
            elif kind == "tool_use":
                turn.tool_calls.append(
                    ToolCall(
                        id=str(blk.get("id", "")),
                        name=str(blk.get("name", "")),
                        args=_loads_args(blk.get("input")),
                    )
                )
            elif kind == "thinking" and isinstance(blk.get("thinking"), str):
                turn.thinking_chars += len(blk["thinking"])
        turn.text = "".join(texts)
        u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        cache_read = _int(u.get("cache_read_input_tokens"))
        cache_write = _int(u.get("cache_creation_input_tokens"))
        turn.usage = Usage(
            input_tokens=_int(u.get("input_tokens")) + cache_read + cache_write,
            output_tokens=_int(u.get("output_tokens")),
            cached_input_tokens=cache_read,
        )
        stop = body.get("stop_reason")
        turn.stop = (
            "tool_use"
            if turn.tool_calls
            else {"end_turn": "end", "stop_sequence": "end", "max_tokens": "max_tokens"}.get(
                stop, "other"
            )
        )
        return turn


# --------------------------------------------------------------------------- openai chat
class OpenAIChatWire:
    """POST `{base}/chat/completions`（兼容网关的最大公约数）。

    * `Authorization: Bearer`；`tools=[{"type": "function", "function": {name, description, parameters}}]`。
    * 系统提示 + 工具定义放在最前且逐字稳定，吃 OpenAI 的自动前缀缓存（不发 `cache_control`，兼容网关会拒收未知字段）。
    * 回复 `choices[0].message`：`content` 文字、`tool_calls[{id, function: {name, arguments(JSON 字符串)}}]`；
      回填为 `{"role": "tool", "tool_call_id", "content"}`。
    * 长度上限字段用 `max_tokens`（兼容网关通用）。待核：OpenAI 自家推理模型要 `max_completion_tokens`。
    * 不传推理强度（本阶段只在 responses 上传；`reasoning_content` 若回了，只数字符、不回放）。
    """

    name = "openai-chat"

    def build(
        self, *, base_url, api_key, model, system, tools, history, effort, max_output_tokens
    ) -> Request:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            if m.role == "user":
                msgs.append({"role": "user", "content": m.text})
            elif m.role == "assistant" and m.turn is not None:
                a: dict[str, Any] = {"role": "assistant", "content": m.turn.text or None}
                if m.turn.tool_calls:
                    a["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(c.args or {}, ensure_ascii=False),
                            },
                        }
                        for c in m.turn.tool_calls
                    ]
                msgs.append(a)
            elif m.role == "tool":
                msgs += [
                    {"role": "tool", "tool_call_id": r.call_id, "content": r.content}
                    for r in m.results
                ]
        body: dict[str, Any] = {"model": model, "max_tokens": max_output_tokens, "messages": msgs}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.schema,
                    },
                }
                for t in tools
            ]
        return Request(
            base_url.rstrip("/") + "/chat/completions", {"Authorization": f"Bearer {api_key}"}, body
        )

    def parse(self, body: dict) -> Turn:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _bad("bad_response")
        msg = choices[0].get("message")
        if not isinstance(msg, dict):
            raise _bad("bad_response")
        turn = Turn(text=msg["content"] if isinstance(msg.get("content"), str) else "")
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") if isinstance(c, dict) else None
            if isinstance(fn, dict):
                turn.tool_calls.append(
                    ToolCall(
                        id=str(c.get("id", "")),
                        name=str(fn.get("name", "")),
                        args=_loads_args(fn.get("arguments")),
                    )
                )
        rc = msg.get("reasoning_content")
        turn.thinking_chars = len(rc) if isinstance(rc, str) else 0
        u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        details = u.get("prompt_tokens_details")
        turn.usage = Usage(
            input_tokens=_int(u.get("prompt_tokens")),
            output_tokens=_int(u.get("completion_tokens")),
            cached_input_tokens=_int(details.get("cached_tokens"))
            if isinstance(details, dict)
            else 0,
        )
        fin = choices[0].get("finish_reason")
        turn.stop = (
            "tool_use"
            if turn.tool_calls
            else {"stop": "end", "length": "max_tokens"}.get(fin, "other")
        )
        return turn


# --------------------------------------------------------------------------- openai responses
class OpenAIResponsesWire:
    """POST `{base}/responses`。

    * `instructions` = 系统提示；`input` = 条目数组；`tools=[{"type": "function", name, description, parameters}]`
      （Responses 的工具定义是扁平的，没有嵌套的 `function`）。
    * 回复 `output[]`：`message`（`content[].output_text`）、`function_call{call_id, name, arguments}`、`reasoning`；
      回填为 `{"type": "function_call_output", "call_id", "output"}`。回放 assistant 轮时把 `output` 条目原样带回。
    * 思考：给了 effort 才传 `reasoning: {"effort": …}`，并 `include: ["reasoning.encrypted_content"]`。
    * `store: false`（提示词里有用户未发表的脚本，不让服务端留存）。待核：无状态回放 `function_call` / `reasoning`
      条目（带原 id）在 `store=false` 下是否都被接受；这里按官方「无状态用法」的记忆写。
    """

    name = "openai-responses"

    def build(
        self, *, base_url, api_key, model, system, tools, history, effort, max_output_tokens
    ) -> Request:
        items: list[Any] = []
        for m in history:
            if m.role == "user":
                items.append({"role": "user", "content": m.text})
            elif m.role == "assistant" and m.turn is not None:
                items += list(m.turn.native or [])
            elif m.role == "tool":
                items += [
                    {"type": "function_call_output", "call_id": r.call_id, "output": r.content}
                    for r in m.results
                ]
        body: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": items,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                }
                for t in tools
            ]
        if effort in EFFORTS:
            body["reasoning"] = {"effort": effort}
            body["include"] = ["reasoning.encrypted_content"]
        return Request(
            base_url.rstrip("/") + "/responses", {"Authorization": f"Bearer {api_key}"}, body
        )

    def parse(self, body: dict) -> Turn:
        out = body.get("output")
        if not isinstance(out, list):
            raise _bad("bad_response")
        turn = Turn(native=out)
        texts: list[str] = []
        for it in out:
            if not isinstance(it, dict):
                continue
            kind = it.get("type")
            if kind == "message":
                for part in it.get("content") or []:
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "output_text"
                        and isinstance(part.get("text"), str)
                    ):
                        texts.append(part["text"])
            elif kind == "function_call":
                turn.tool_calls.append(
                    ToolCall(
                        id=str(it.get("call_id", "")),
                        name=str(it.get("name", "")),
                        args=_loads_args(it.get("arguments")),
                    )
                )
            elif kind == "reasoning":
                turn.thinking_chars += len(json.dumps(it.get("summary") or "", ensure_ascii=False))
        turn.text = "".join(texts)
        u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        details = u.get("input_tokens_details")
        turn.usage = Usage(
            input_tokens=_int(u.get("input_tokens")),
            output_tokens=_int(u.get("output_tokens")),
            cached_input_tokens=_int(details.get("cached_tokens"))
            if isinstance(details, dict)
            else 0,
        )
        inc = body.get("incomplete_details")
        truncated = (
            body.get("status") == "incomplete"
            and isinstance(inc, dict)
            and inc.get("reason") == "max_output_tokens"
        )
        turn.stop = "tool_use" if turn.tool_calls else ("max_tokens" if truncated else "end")
        return turn


WIRES: dict[str, Wire] = {
    w.name: w for w in (AnthropicWire(), OpenAIChatWire(), OpenAIResponsesWire())
}


# --------------------------------------------------------------------------- 传输
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """任何重定向都不跟（抛出去，终止链；同 `diagsend._NoRedirect`）：POST 体 + 密钥头不转交到别处。"""

    handler_order = 400

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _opener() -> urllib.request.OpenerDirector:
    """出站 HTTPS 的唯一 opener：平台原生证书校验（`tlstrust`）+ 不跟随重定向；代理沿用环境变量。"""
    ctx = tlstrust.client_context()
    opener = urllib.request.build_opener(tlstrust.https_handler(ctx))
    opener.add_handler(_NoRedirect())
    return opener


def check_base_url(base_url: str) -> str | None:
    """通过回 None；否则回短错误码。https，或主机恰为字面量 127.0.0.1 的 http；不带用户信息 / 查询 / 片段。"""
    try:
        u = urllib.parse.urlsplit(base_url)
        host = u.hostname
        _ = u.port
    except ValueError:
        return "bad_base_url"
    if u.username is not None or u.password is not None or u.query or u.fragment or not host:
        return "bad_base_url"
    if u.scheme == "https":
        return None
    if u.scheme == "http" and host == "127.0.0.1":
        return None
    return "base_url_scheme"


def post_json(
    url: str,
    headers: dict[str, str],
    body: dict,
    *,
    timeout: float,
    max_bytes: int | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """发一次 POST。任何 HTTP 状态都回 `(status, 小写头, 体)`（3xx 不跟随）；传输层失败抛 `RunnerFailure`
    （可重试的 network）。URL 与头不进异常文本。"""
    max_bytes = MAX_RESPONSE_BYTES if max_bytes is None else max_bytes
    bad = check_base_url(url)
    if bad:
        raise RunnerFailure("network", bad)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdrs = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        **headers,
    }
    u = urllib.parse.urlsplit(url)
    try:
        if u.scheme == "http":
            # 回环 http：直连 socket，不读代理设置
            conn = http.client.HTTPConnection("127.0.0.1", u.port or 80, timeout=timeout)
            try:
                path = u.path or "/"
                conn.request("POST", path, body=payload, headers=hdrs)
                resp = conn.getresponse()
                data = resp.read(max_bytes + 1)
                status, rh = resp.status, {k.lower(): v for k, v in resp.getheaders()}
            finally:
                conn.close()
        else:
            req = urllib.request.Request(url, data=payload, headers=hdrs, method="POST")
            try:
                with _opener().open(req, timeout=timeout) as resp:
                    data = resp.read(max_bytes + 1)
                    status = resp.status
                    rh = {k.lower(): v for k, v in resp.headers.items()}
            except urllib.error.HTTPError as err:
                try:
                    data = err.read(max_bytes + 1)
                finally:
                    err.close()
                status = err.code
                rh = {k.lower(): v for k, v in (err.headers.items() if err.headers else [])}
    except RunnerFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - 传输层五花八门；只留类型名，不留消息（里面可能有 URL）
        cert = tlstrust.cert_verification_error(exc)
        detail = "tls_cert" if cert else type(tlstrust.root_cause(exc)).__name__
        raise RunnerFailure("network", detail, retryable=not cert) from None
    if len(data) > max_bytes:
        raise RunnerFailure("network", "response_too_large")
    return status, rh, data
