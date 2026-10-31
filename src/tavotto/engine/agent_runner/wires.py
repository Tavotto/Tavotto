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

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .. import netpolicy, tlstrust
from .media import Image

USER_AGENT = "tavotto-agent-runner/1"
ANTHROPIC_VERSION = "2023-06-01"
#: 响应体上限（字节）。模型回复远小于它；超了当作异常回复，不读完。
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

EFFORTS = ("low", "medium", "high")
#: anthropic adaptive 的 `output_config.effort` 还多两档：xhigh（5 系列 / opus-4-7 / opus-4-8）与 max
ANTHROPIC_EFFORTS = (*EFFORTS, "xhigh", "max")
#: 调用方声明「思考怎么传」（不由 runner 猜；第三方兼容网关各不相同）：adaptive=新模型，budget=旧模型，none=都不发
#: Responses 的 `reasoning.effort` 取值（CLI 型号目录可能给 minimal / xhigh；没有 max，也不发 none）
RESPONSES_EFFORTS = ("minimal", *EFFORTS, "xhigh")
THINKING_MODES = ("adaptive", "budget", "none")
#: anthropic 旧式 `thinking.budget_tokens`（仅 `--thinking budget`；新模型上已弃用，不许发）
_THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 12000}


# --------------------------------------------------------------------------- 中立数据
@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict
    #: 风险档（ADR 0149）：只给 Tavotto 自己的策略层看，**绝不序列化给模型**（三种 wire 只发 name / description / schema）
    tier: Literal["observe", "run", "tavotto", "listed", "user_env", "outward"] = "observe"
    #: 这个工具会停下来等用户点头（策略层用；同样不发给模型）
    waits_for_user: bool = False


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
    #: 服务端报了合法的输入 / 输出计数。False = 缺 usage 或计数非法——loop 会按字符数保守估算记账
    reported: bool = True


def _reported(u: dict, *keys: str) -> bool:
    return all(
        isinstance(u.get(k), int) and not isinstance(u.get(k), bool) and u.get(k) >= 0 for k in keys
    )


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    #: "end" | "tool_use" | "max_tokens" | "other"
    stop: str = "end"
    #: stop == "other" 时服务端给的原值，经白名单清洗（只留小写字母数字下划线、≤ 24 字符），仅用于 detail
    stop_value: str = ""
    #: 思考内容的字符数（内容本身不出进程）
    thinking_chars: int = 0
    #: 协议私有的、回放这一轮时要原样带回的东西（anthropic 的 content 块含 thinking 签名；responses 的 output 项）
    native: Any = None


def clean_stop(value: object) -> str:
    """服务端给的停止原因只以白名单字符进 detail（它来自不可信的响应体）。"""
    if not isinstance(value, str):
        return "none"
    out = "".join(c for c in value.lower() if c.isascii() and (c.isalnum() or c == "_"))[:24]
    return out or "empty"


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
    #: Bounded visual evidence; only wire adapters consume it, never emit().
    images: list[Image] = field(default_factory=list)
    #: Actual injected check outcome; never inferred from its report text.
    check_passed: bool | None = None
    #: Trusted tool handler accepted an explicit final validation. The parent
    #: still runs its own candidate/integrity/adoption checks.
    completed: bool = False
    completion_text: str = ""


@dataclass
class Msg:
    """中立的对话消息：`user`（text）/ `assistant`（turn）/ `tool`（results）。"""

    role: str
    text: str = ""
    turn: Turn | None = None
    results: list[ToolResult] = field(default_factory=list)
    # Interrupted reasoning is temporary handoff state, never a tool operation.
    recovery_state: bool = False


def truncated_continuation(wire: str, turn: Turn) -> Turn | None:
    """Keep replayable reasoning, never an interrupted message or tool call.

    These opaque blocks stay inside the model conversation. Retaining them is
    neither executing a call nor accepting an incomplete answer as successful.
    """
    native = turn.native
    if wire == "openai-responses" and isinstance(native, list):
        items = [
            item for item in native if isinstance(item, dict) and item.get("type") == "reasoning"
        ]
        return Turn(native=items) if items else None
    if wire == "openai-chat" and isinstance(native, dict):
        reasoning = native.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            return Turn(native={"reasoning_content": reasoning})
    if wire == "anthropic" and isinstance(native, list):
        items = []
        for item in native:
            if not isinstance(item, dict):
                break
            kind = item.get("type")
            field = (
                "signature"
                if kind == "thinking"
                else "data"
                if kind == "redacted_thinking"
                else None
            )
            if field is None or not isinstance(item.get(field), str) or not item[field]:
                break  # An unfinished thinking block has no valid replay signature.
            items.append(item)
        return Turn(native=items) if items else None
    return None


def _replay_items(turn: Turn, kind: str, argument_key: str) -> list[dict]:
    """Keep malformed arguments in tool feedback, not in a second invalid request.

    A placeholder object does not authorize execution: the rejected original
    call already received bad_arguments, paired with this exact call identity.
    """
    invalid = {call.id for call in turn.tool_calls if call.args is None}
    identity = "call_id" if kind == "function_call" else "id"
    return [
        {**item, argument_key: "{}" if kind == "function_call" else {}}
        if isinstance(item, dict) and item.get("type") == kind and item.get(identity) in invalid
        else item
        for item in turn.native or []
    ]


class RunnerFailure(Exception):
    """一次模型调用失败。`reason` 属于 done 事件的闭集；`detail` 是短代码，不含任何内容。"""

    def __init__(
        self, reason: str, detail: str = "", *, retryable: bool = False, usage: Usage | None = None
    ):
        super().__init__(f"{reason}:{detail}")
        self.reason = reason
        self.detail = detail
        self.retryable = retryable
        #: 2xx 但终止于拒答 / 失败状态时，服务端报的用量照样计费——由 loop 记账后再结束
        self.usage = usage


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
        thinking: str = "none",
    ) -> Request: ...

    def parse(self, body: dict) -> Turn: ...


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _str_id(v: Any) -> str:
    """工具调用的对应 ID：只认非空字符串；null / 数字 / 其他类型一律当缺失（""），由 loop 拒绝执行。
    不能 `str(None)`——那会得到看似有效的 "None"。"""
    return v if isinstance(v, str) else ""


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


def _bad(detail: str, usage: Usage | None = None) -> RunnerFailure:
    return RunnerFailure("network", detail, usage=usage)


def _code(value: object) -> str:
    """服务端给的原因 / 错误码 → 短代码。只收 `[A-Za-z0-9_.-]{1,40}`，其余一律 `unknown`（不回显服务端文字）。"""
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", value)
        else "unknown"
    )


def _chat_usage(body: dict) -> Usage:
    u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    details = u.get("prompt_tokens_details")
    return Usage(
        input_tokens=_int(u.get("prompt_tokens")),
        output_tokens=_int(u.get("completion_tokens")),
        cached_input_tokens=_int(details.get("cached_tokens")) if isinstance(details, dict) else 0,
        reported=_reported(u, "prompt_tokens", "completion_tokens"),
    )


def _anthropic_usage(body: dict) -> Usage:
    u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    cache_read = _int(u.get("cache_read_input_tokens"))
    cache_write = _int(u.get("cache_creation_input_tokens"))
    return Usage(
        input_tokens=_int(u.get("input_tokens")) + cache_read + cache_write,
        output_tokens=_int(u.get("output_tokens")),
        cached_input_tokens=cache_read,
        reported=_reported(u, "input_tokens", "output_tokens"),
    )


def _responses_usage(body: dict) -> Usage:
    u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    details = u.get("input_tokens_details")
    return Usage(
        input_tokens=_int(u.get("input_tokens")),
        output_tokens=_int(u.get("output_tokens")),
        cached_input_tokens=_int(details.get("cached_tokens")) if isinstance(details, dict) else 0,
        reported=_reported(u, "input_tokens", "output_tokens"),
    )


def _refused(detail: str, usage: Usage | None = None) -> RunnerFailure:
    """服务端过滤 / 拒答（content_filter、refusal、被截断以外的 incomplete）：结局 `refused`。"""
    return RunnerFailure("refused", detail, usage=usage)


# --------------------------------------------------------------------------- anthropic
class AnthropicWire:
    """POST `{base}/v1/messages`。

    * 鉴权：`x-api-key` 与 `Authorization: Bearer` 都给（官方 / 各家网关各认一个），`anthropic-version: 2023-06-01`。
    * 提示缓存：系统提示块与**最后一个**工具定义末尾各加 `cache_control: {"type": "ephemeral"}`
      （缓存前缀顺序 tools → system → messages，所以稳定的两段在最前）。
    * 思考由调用方声明（`thinking` 参数 / `--thinking`）：`adaptive` 发 `thinking:{type:adaptive}`，有 effort 才加
      `output_config:{effort}`（新模型；`budget_tokens` 已弃用不发，自带 interleaved thinking、无需 beta 头）；`budget` 发旧式
      `thinking:{type:enabled,budget_tokens:N}`（N≥1024 且 < `max_tokens`，所以 `max_tokens` 加上预算）；`none`（默认）都不发。
    * 不做 assistant prefill（新模型会 400）；工具参数一律 `json.loads`。
    * 工具：`tools[{name, description, input_schema}]`；回复 `content[]` 里的 `tool_use{id, name, input}`；
      回填为下一条 user 消息里的 `tool_result{tool_use_id, content, is_error}` 块。
    * 回放 assistant 轮时原样带回 `content` 块（含 `thinking` 块及其 `signature`，有工具调用的思考轮要求如此）。
    """

    name = "anthropic"

    def __init__(self, bearer_only: bool = False):
        #: 账号登录（OAuth）的访问令牌只认 `Authorization: Bearer`；同时发 `x-api-key` 会让部分网关 401（Kimi 社区报告）
        self.bearer_only = bearer_only

    def build(
        self,
        *,
        base_url,
        api_key,
        model,
        system,
        tools,
        history,
        effort,
        max_output_tokens,
        thinking="none",
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
                msgs.append(
                    {"role": "assistant", "content": _replay_items(m.turn, "tool_use", "input")}
                )
            elif m.role == "tool":
                msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.call_id,
                                "content": (
                                    [{"type": "text", "text": r.content}]
                                    + [
                                        block
                                        for img in r.images
                                        for block in (
                                            {"type": "text", "text": img.label or "Image"},
                                            {
                                                "type": "image",
                                                "source": {
                                                    "type": "base64",
                                                    "media_type": img.mime_type,
                                                    "data": img.data,
                                                },
                                            },
                                        )
                                    ]
                                    if r.images
                                    else r.content
                                ),
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
        if thinking == "adaptive":
            # 新模型（Opus/Sonnet 4.6 起、Claude 5 系列）：自适应思考，强度在 output_config 里；不发 budget_tokens
            body["thinking"] = {"type": "adaptive"}
            if effort in ANTHROPIC_EFFORTS:
                body["output_config"] = {"effort": effort}
        elif thinking == "budget" and effort is not None:
            # budget 必须有档位（CLI 层已拒绝缺省）；max 在旧式预算里没有对应档，映射到 high
            budget = _THINKING_BUDGET["high" if effort == "max" else effort]
            body["max_tokens"] = max_output_tokens + budget
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        headers = {
            **({} if self.bearer_only else {"x-api-key": api_key}),
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        return Request(base_url.rstrip("/") + "/v1/messages", headers, body)

    def parse(self, body: dict) -> Turn:
        content = body.get("content")
        if not isinstance(content, list):
            raise _bad("bad_response", _anthropic_usage(body))
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
                        id=_str_id(blk.get("id")),
                        name=str(blk.get("name", "")),
                        args=_loads_args(blk.get("input")),
                    )
                )
            elif kind == "thinking" and isinstance(blk.get("thinking"), str):
                turn.thinking_chars += len(blk["thinking"])
        turn.text = "".join(texts)
        turn.usage = _anthropic_usage(body)
        stop = body.get("stop_reason")
        if stop == "refusal":
            raise _refused("refusal", turn.usage)
        # 截断优先于工具调用：被截断的一轮可能只有前半的"完整"调用，执行它会改出半成品
        turn.stop = (
            "max_tokens"
            if stop == "max_tokens"
            else "tool_use"
            if turn.tool_calls
            else {"end_turn": "end", "stop_sequence": "end"}.get(stop, "other")
        )
        turn.stop_value = clean_stop(stop)
        return turn


# --------------------------------------------------------------------------- openai chat
class OpenAIChatWire:
    """POST `{base}/chat/completions`（兼容网关的最大公约数）。

    * `Authorization: Bearer`；`tools=[{"type": "function", "function": {name, description, parameters}}]`。
    * 系统提示 + 工具定义放在最前且逐字稳定，吃 OpenAI 的自动前缀缓存（不发 `cache_control`，兼容网关会拒收未知字段）。
    * 回复 `choices[0].message`：`content` 文字、`tool_calls[{id, function: {name, arguments(JSON 字符串)}}]`；
      回填为 `{"role": "tool", "tool_call_id", "content"}`。
    * 长度上限字段默认 `max_tokens`（兼容网关通用）；OpenAI 自家 o 系列只认 `max_completion_tokens`，
      由调用方声明（`--max-tokens-field`，将来来自厂商目录），runner 不按模型名猜。
    * 不传推理强度（本阶段只在 responses 上传）。回复里的 `reasoning_content`（DeepSeek 等的思考模式）
      存进 `Turn.native = {"reasoning_content": …}`，回放该 assistant 轮时原样放回同名字段：这类厂商要求
      带工具调用的轮次（以及工具结果之后的那条回复）把思考内容带回，否则整个请求被拒
      （"The `reasoning_content` in the thinking mode must be passed back to the API"）。
      规则：**这一轮存过非空 `reasoning_content` 才放回，否则不加该键**（请求字节不变，拒收多余字段的
      厂商如 Mistral 不受影响）。runner 整个会话只用同一个模型，思考模式是整场会话的属性，不会出现
      「别的轮有、这一轮没有」，所以不做空格占位。
    """

    name = "openai-chat"
    TOKEN_FIELDS = ("max_tokens", "max_completion_tokens")

    def __init__(self, token_field: str = "max_tokens"):
        if token_field not in self.TOKEN_FIELDS:
            raise ValueError(token_field)
        self.token_field = token_field

    def build(
        self,
        *,
        base_url,
        api_key,
        model,
        system,
        tools,
        history,
        effort,
        max_output_tokens,
        thinking="none",
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
                if isinstance(m.turn.native, dict) and m.turn.native.get("reasoning_content"):
                    a["reasoning_content"] = m.turn.native["reasoning_content"]
                msgs.append(a)
            elif m.role == "tool":
                msgs += [
                    {"role": "tool", "tool_call_id": r.call_id, "content": r.content}
                    for r in m.results
                ]
                # Chat's tool messages are text-only. Close every tool call
                # before adding the corresponding images as a user message.
                images = [
                    block
                    for r in m.results
                    for img in r.images
                    for block in (
                        {"type": "text", "text": f"Tool {r.call_id}: {img.label or 'Image'}"},
                        {"type": "image_url", "image_url": {"url": img.url}},
                    )
                ]
                if images:
                    msgs.append({"role": "user", "content": images})
        body: dict[str, Any] = {
            "model": model,
            self.token_field: max_output_tokens,
            "messages": msgs,
        }
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
            raise _bad("bad_response", _chat_usage(body))
        msg = choices[0].get("message")
        if not isinstance(msg, dict):
            raise _bad("bad_response", _chat_usage(body))
        rf = msg.get("refusal")
        if (
            isinstance(rf, str) and rf.strip()
        ):  # content:null + refusal + finish_reason:stop 不是空成功
            raise _refused("refusal", _chat_usage(body))
        turn = Turn(text=msg["content"] if isinstance(msg.get("content"), str) else "")
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") if isinstance(c, dict) else None
            if isinstance(fn, dict):
                turn.tool_calls.append(
                    ToolCall(
                        id=_str_id(c.get("id")),
                        name=str(fn.get("name", "")),
                        args=_loads_args(fn.get("arguments")),
                    )
                )
        rc = msg.get("reasoning_content")
        turn.thinking_chars = len(rc) if isinstance(rc, str) else 0
        if isinstance(rc, str) and rc:
            turn.native = {"reasoning_content": rc}
        turn.usage = _chat_usage(body)
        fin = choices[0].get("finish_reason")
        if fin == "content_filter":
            raise _refused("content_filter", turn.usage)
        # 截断优先于工具调用（同 AnthropicWire）
        turn.stop = (
            "max_tokens"
            if fin == "length"
            else "tool_use"
            if turn.tool_calls
            else {"stop": "end"}.get(fin, "other")
        )
        turn.stop_value = clean_stop(fin)
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
        self,
        *,
        base_url,
        api_key,
        model,
        system,
        tools,
        history,
        effort,
        max_output_tokens,
        thinking="none",
    ) -> Request:
        items: list[Any] = []
        for m in history:
            if m.role == "user":
                items.append({"role": "user", "content": m.text})
            elif m.role == "assistant" and m.turn is not None:
                items += _replay_items(m.turn, "function_call", "arguments")
            elif m.role == "tool":
                items += [
                    {
                        "type": "function_call_output",
                        "call_id": r.call_id,
                        "output": (
                            [{"type": "input_text", "text": r.content}]
                            + [
                                block
                                for img in r.images
                                for block in (
                                    {"type": "input_text", "text": img.label or "Image"},
                                    {"type": "input_image", "image_url": img.url, "detail": "auto"},
                                )
                            ]
                            if r.images
                            else r.content
                        ),
                    }
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
        if effort in RESPONSES_EFFORTS:
            body["reasoning"] = {"effort": effort}
            body["include"] = ["reasoning.encrypted_content"]
        return Request(
            base_url.rstrip("/") + "/responses", {"Authorization": f"Bearer {api_key}"}, body
        )

    def parse(self, body: dict) -> Turn:
        # 顶层 status 先判：2xx 体里 failed / 未完成不是成功（空 output 会被误当作 end）
        usage = _responses_usage(body)  # 先算：拒答 / 失败状态的回复同样计费
        status = body.get("status")
        inc = body.get("incomplete_details")
        truncated = False
        if status == "incomplete":
            reason = inc.get("reason") if isinstance(inc, dict) else None
            if reason == "max_output_tokens":
                truncated = True
            else:
                raise _refused(f"incomplete_{_code(reason)}", usage)
        elif status == "failed":
            err = body.get("error")
            raise _bad(
                f"failed_{_code(err.get('code'))}" if isinstance(err, dict) else "failed", usage
            )
        elif status is not None and status != "completed":
            raise _bad(
                f"status_{_code(status)}", usage
            )  # cancelled / queued / in_progress 等意外状态
        out = body.get("output")
        if not isinstance(out, list):
            raise _bad("bad_response", usage)
        turn = Turn(native=out)
        texts: list[str] = []
        for it in out:
            if not isinstance(it, dict):
                continue
            kind = it.get("type")
            if kind == "message":
                for part in it.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "refusal":
                        raise _refused("refusal", usage)  # 服务端文字不回显
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "output_text"
                        and isinstance(part.get("text"), str)
                    ):
                        texts.append(part["text"])
            elif kind == "function_call":
                turn.tool_calls.append(
                    ToolCall(
                        id=_str_id(it.get("call_id")),
                        name=str(it.get("name", "")),
                        args=_loads_args(it.get("arguments")),
                    )
                )
            elif kind == "reasoning":
                # Compatible endpoints may return plain reasoning in content;
                # summaries alone used to report zero for a full truncated turn.
                turn.thinking_chars += sum(
                    len(part["text"])
                    for field in ("content", "summary")
                    for part in (it.get(field) if isinstance(it.get(field), list) else [])
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
        turn.text = "".join(texts)
        turn.usage = usage
        # 截断优先于工具调用（同 AnthropicWire）
        turn.stop = "max_tokens" if truncated else ("tool_use" if turn.tool_calls else "end")
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
    """通过回 None；否则回短错误码。https，或明文 http 但主机属于本机回环 / 私网（判据只在 `netpolicy`：IP 字面量直接判，
    主机名解析出的**全部**IP 都在放行段才行）；不带用户信息 / 查询 / 片段。"""
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
    if u.scheme == "http" and netpolicy.plain_http_allowed(host):
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
            # 明文 http：判定与连接用同一次解析，直连已校验的 IP（`Host` 头保留原主机名），不读代理设置
            conn, host_header = netpolicy.pinned_connection(url, timeout)
            hdrs = {**hdrs, "Host": host_header}
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
