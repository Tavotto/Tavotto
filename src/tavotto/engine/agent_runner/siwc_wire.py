"""Sign in with ChatGPT（SIWC）专用的 Responses 协议 `siwc-responses`（ADR 0201 §二）。

用户用自己的 ChatGPT 订阅额度调 `POST https://api.openai.com/v1/responses`，令牌是我们自己保存的 OAuth 访问令牌
（本阶段不实现登录，见 `engine/siwc_auth.py`）。与 `wires.OpenAIResponsesWire` 的区别全部来自官方 preview 限制
（https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations ，2026-11-23 读取）：

* 每个请求 `stream: true` + `store: false`；只有 `response.completed` 才算成功，流中断是错误。
* 请求体**白名单**构造（`_ALLOWED_FIELDS`），官方禁用清单 `FORBIDDEN_FIELDS` 一个都不发——包括循环总会传进来的
  `max_output_tokens`。选了思考强度才发 `reasoning.effort` + `include: ["reasoning.encrypted_content"]`（`SIWC_EFFORTS`，2026-11-25 真服务核对），
  回放只保留带 `encrypted_content` 的 `reasoning` 条目（没有的在 `store: false` 下会被拒）。没有 `role: "system"` 条目（系统提示只走 `instructions`）。
* 函数工具须「放进 namespace 或 `additional_tools` 输入条目」：这里包进一个 `namespace`（形状取自通用 function-calling 指南，
  **SIWC 路由上未经真服务验证**；工具打包只在 `_pack_tools` 一处）。
* 错误码闭集（`failure_for_code`）：用量超限 → `quota` 不重试；暂时不可用 → 可重试；不具备资格 → `refused`（**不是 `auth`**，
  官方禁止因此循环 OAuth）；配置类 → `internal`。服务端原文不进事件 / 日志 / 诊断包，只比对闭集码。
* 不参与换协议与字段降级（官方：不得静默切换计费方式）——它不在 `wires.WIRES` 里，`--fallback` 写不出它，
  `fallback.FieldAdjuster` 对它是空操作。

SSE 处理：传输层（`wires.post_json(reader=…)`）边读边用 `compact_sse` 丢掉 `*.delta` 增量帧，只留终态与
`response.output_item.done` 等结构帧（总读入 `MAX_STREAM_BYTES`、留下 `wires.MAX_RESPONSE_BYTES` 各有上限）；
`decode` 把留下的帧折叠成一个最终响应对象，再交给 `OpenAIResponsesWire.parse`。界面不做流式显示（ADR 0147 §六 修订）。

**真服务核对（2026-11-24，维护者本人的 ChatGPT 账号，一次性脚本，令牌只在内存、跑完已撤销；型号 `gpt-6.2-sol`）**：
动态注册（`client_id=dynamic_agent_client` → 回调给 `oaiapp_…`）、授权码 + PKCE 换令牌（`expires_in=3600`，带 `earliest_refresh_at`，
授权范围含 `chatgpt.tokens.use.direct`）、`GET /v1/models`（`models[]` + `slug`）、本协议经 https 真实传输的单轮 / namespace 工具调用 /
回填工具结果的多轮都 200 且以 `response.completed` 收尾；`completed` **带完整 `output`**；namespace 工具被接受，函数调用回来时
`name` 是裸名、另有 `namespace: "tavotto"` 字段（`_bare_name` 只是兜底）；带 `namespace` 字段原样回放的 `function_call` 被接受；撤销刷新令牌 200。
**第二次真服务核对（2026-11-25，维护者本人账号、维护者当面同意；走产品自己的 `siwc_login` 登录实现，临时数据目录，跑完撤销并删除）**：
1455 被占时首次注册换端口成功；ID 令牌对真实 JWKS 的 RS256 验签通过；`/v1/models` 每个型号带 `supported_reasoning_levels`
（low / medium / high / xhigh / max）与 `default_reasoning_level`（响应远超 256 KB——曾因此被当成空清单，已修）；
`reasoning.effort` = low / medium / high / xhigh 实发 200，`minimal` 被拒 `unsupported_value`；xhigh 时回来的 reasoning 条目带 `encrypted_content`；
带思考的工具调用 + 回填结果的多轮 200；刷新令牌**会轮换**，签发后几分钟（远早于 `earliest_refresh_at`）刷新照样 200；https 流到一半取消 1.5 s 内干净结束；撤销 200。
**仍未真服务验证**（不得写成已验证）：`response.failed` / 用量超限等错误码、`response.incomplete` 与 `error` 事件的载荷、
**带 `encrypted_content` 的 reasoning 条目回放**（那一轮服务端没回 reasoning 条目，回放是空验）、`max` 档实发。
"""

from __future__ import annotations

import json
import os
from typing import Any

from .wires import (
    MAX_RESPONSE_BYTES,
    OpenAIResponsesWire,
    Request,
    RunnerFailure,
    Turn,
)

WIRE_NAME = "siwc-responses"
#: 功能开关（只认环境变量；默认关）。父进程与 runner 都问这一处
ENV_FLAG = "TAVOTTO_EXPERIMENTAL_SIWC"
#: SIWC 令牌只许发往这里（官方：`https://api.openai.com/v1`，`POST /v1/responses`）
BASE_URL = "https://api.openai.com/v1"
#: 工具命名空间名（包住我们的函数工具）
NAMESPACE = "tavotto"

#: 官方 preview 限制里「省略」的字段 + 不能用的 `previous_response_id`
FORBIDDEN_FIELDS = (
    "background",
    "conversation",
    "max_output_tokens",
    "max_tool_calls",
    "metadata",
    "moderation",
    "multi_agent",
    "prompt",
    "prompt_cache_retention",
    "safety_identifier",
    "temperature",
    "top_logprobs",
    "top_p",
    "truncation",
    "user",
    "previous_response_id",
)
_ALLOWED_FIELDS = (
    "model",
    "instructions",
    "input",
    "tools",
    "store",
    "stream",
    "reasoning",
    "include",
)
#: SIWC 上能发的思考强度（2026-11-25 真服务核对：`/v1/models` 每个型号的 `supported_reasoning_levels` 给 low / medium / high / xhigh / max，
#: low–xhigh 实发都 200；`minimal` 被拒 `unsupported_value`；`max` 型号清单列了、未实发）。实际给用户的档位 = 这个集合 ∩ 该型号清单里列的
SIWC_EFFORTS = ("low", "medium", "high", "xhigh", "max")

#: 一次流最多读入多少字节（含被丢掉的增量帧）；超了按 `response_too_large` 失败
MAX_STREAM_BYTES = 64 * 1024 * 1024
#: 单帧上限（`response.completed` 带整份输出，可能较大）
MAX_FRAME_BYTES = MAX_RESPONSE_BYTES

_TERMINAL = ("response.completed", "response.failed", "response.incomplete", "error")


def enabled(env: dict[str, str] | None = None) -> bool:
    """`TAVOTTO_EXPERIMENTAL_SIWC=1` 才开；其余（含缺省）一律关。"""
    return ((env if env is not None else os.environ).get(ENV_FLAG) or "").strip() == "1"


# --------------------------------------------------------------------------- 错误码
#: 官方 errors-and-recovery 页的闭集码 → (reason, detail, retryable)
_CODES: dict[str, tuple[str, str, bool]] = {
    "subscription_sharing_usage_limit_exceeded": ("quota", "plan_usage_limit", False),
    "subscription_sharing_usage_unavailable": ("network", "plan_usage_unavailable", True),
    "subscription_sharing_user_unavailable": ("network", "plan_user_unavailable", True),
    "subscription_sharing_user_not_eligible": ("refused", "plan_not_eligible", False),
    "subscription_sharing_unsupported_capability": (
        "internal",
        "plan_unsupported_capability",
        False,
    ),
    "subscription_sharing_route_not_supported": ("internal", "plan_route_not_supported", False),
    "subscription_sharing_invalid_user": ("auth", "plan_invalid_user", False),
    "chatpass_v2_scope_not_authorized": ("internal", "plan_scope_not_authorized", False),
    "chatpass_v2_invalid_authorization_context": (
        "internal",
        "plan_invalid_authorization_context",
        False,
    ),
}
#: 本模块可能产生的全部 detail（闭集；`done.detail` 只会是这些或 `loop` 的通用码）
DETAILS = frozenset(
    {d for _r, d, _x in _CODES.values()}
    | {"stream_incomplete", "response_too_large", "bad_response", "plan_forbidden"}
)


def failure_for_code(code: object) -> RunnerFailure | None:
    """闭集错误码 → `RunnerFailure`；不认得 = None（由调用方按通用规则归类）。"""
    hit = _CODES.get(code) if isinstance(code, str) else None
    if hit is None:
        return None
    reason, detail, retryable = hit
    return RunnerFailure(reason, detail, retryable=retryable)


def _error_code(body: bytes) -> str:
    """`{"error": {"code": …}}` 里的 code；别的形状（含预流的 `{"detail": …}`）= 空串。只用来精确比对闭集码。"""
    try:
        obj = json.loads(body[:8192].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    err = obj.get("error") if isinstance(obj, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code if isinstance(code, str) else ""


# --------------------------------------------------------------------------- SSE
class _Frames:
    """SSE 分帧（WHATWG EventSource 的子集）：`event:` / `data:` 行，空行结束一帧，`:` 开头是注释。"""

    def __init__(self):
        self._buf = bytearray()
        self._event = ""
        self._data: list[bytes] = []
        self._size = 0

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        self._buf += chunk
        out: list[tuple[str, bytes]] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                if len(self._buf) > MAX_FRAME_BYTES:
                    raise RunnerFailure("network", "response_too_large")
                return out
            line = bytes(self._buf[:nl]).rstrip(b"\r")
            del self._buf[: nl + 1]
            self._line(line, out)

    def close(self) -> list[tuple[str, bytes]]:
        out: list[tuple[str, bytes]] = []
        if self._buf:
            self._line(bytes(self._buf).rstrip(b"\r"), out)
            self._buf.clear()
        self._line(b"", out)
        return out

    def _line(self, line: bytes, out: list[tuple[str, bytes]]) -> None:
        if not line:
            if self._data:
                out.append((self._event, b"\n".join(self._data)))
            self._event, self._data, self._size = "", [], 0
            return
        if line.startswith(b":"):
            return
        name, _, value = line.partition(b":")
        if value.startswith(b" "):
            value = value[1:]
        if name == b"event":
            self._event = value.decode("utf-8", errors="replace")
        elif name == b"data":
            self._size += len(value)
            if self._size > MAX_FRAME_BYTES:
                raise RunnerFailure("network", "response_too_large")
            self._data.append(value)


def _frame_type(event: str, data: bytes) -> str:
    """帧的事件类型：有 `event:` 行用它；只有 data 时解析 JSON 取**顶层** `type`（正则会抓到嵌套条目的 type，
    或漏掉排在后面的 type——终态帧认不出来就停不下来，Codex #2132 P2）。解析不了 = 空串（原样保留，交给 `fold`）。"""
    if event:
        return event
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    kind = obj.get("type") if isinstance(obj, dict) else None
    return kind if isinstance(kind, str) else ""


def compact_sse(resp: Any) -> bytes:
    """传输层的读取器（`wires.post_json(reader=…)`，只用于 2xx）：边读边丢增量帧，终态帧之后不再读。
    返回值仍是 SSE 文本（只剩结构帧），由 `decode` 折叠。"""
    frames = _Frames()
    kept = bytearray()
    total = 0

    def keep(items: list[tuple[str, bytes]]) -> bool:
        for event, data in items:
            kind = _frame_type(
                event, data
            )  # 只用来决定丢不丢 / 停不停；原帧原样写回（`fold` 以 JSON 里的 type 为准）
            if kind.endswith(".delta") or data.strip() == b"[DONE]":
                continue
            piece = (
                (b"event: " + event.encode("utf-8") + b"\n" if event else b"")
                + b"".join(b"data: " + line + b"\n" for line in data.split(b"\n"))
                + b"\n"
            )
            if len(kept) + len(piece) > MAX_RESPONSE_BYTES:
                raise RunnerFailure("network", "response_too_large")
            kept.extend(piece)
            if kind in _TERMINAL:
                return True
        return False

    # `read1`：有多少给多少（分块传输时 `read(n)` 会一直等到凑满 n 字节，终态之后也停不下来）
    read = getattr(resp, "read1", None) or resp.read
    while True:
        chunk = read(64 * 1024)
        if not chunk:
            keep(frames.close())
            return bytes(kept)
        total += len(chunk)
        if total > MAX_STREAM_BYTES:
            raise RunnerFailure("network", "response_too_large")
        if keep(frames.feed(chunk)):
            return bytes(kept)


def fold(data: bytes) -> dict:
    """SSE 文本 → 最终响应对象（`OpenAIResponsesWire.parse` 认的形状）。失败 / 没有终态抛 `RunnerFailure`。"""
    frames = _Frames()
    items: list[dict] = []
    terminal: tuple[str, dict] | None = None
    for event, raw in [*frames.feed(data), *frames.close()]:
        if raw.strip() == b"[DONE]":
            continue
        try:
            ev = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise RunnerFailure("network", "bad_response") from None
        if not isinstance(ev, dict):
            raise RunnerFailure("network", "bad_response")
        kind = (
            str(ev.get("type") or "") or event
        )  # JSON 自己的 type 优先（event 行可能缺 / 与之不同）
        if kind == "response.output_item.done" and isinstance(ev.get("item"), dict):
            items.append(ev["item"])
        elif kind in _TERMINAL:
            terminal = (kind, ev)
            break
    if terminal is None:
        # 流断了 / 服务端没发终态：不是成功。工具都在客户端执行，整轮重发没有副作用 → 可重试
        raise RunnerFailure("network", "stream_incomplete", retryable=True)
    kind, ev = terminal
    if kind == "error":
        err = ev.get("error") if isinstance(ev.get("error"), dict) else ev
        raise failure_for_code(err.get("code")) or RunnerFailure("network", "bad_response")
    resp = ev.get("response")
    if not isinstance(resp, dict):
        raise RunnerFailure("network", "bad_response")
    if kind == "response.failed":
        err = resp.get("error") if isinstance(resp.get("error"), dict) else {}
        mapped = failure_for_code(err.get("code"))
        if mapped is not None:
            raise mapped
        resp = {**resp, "status": "failed"}
    else:
        resp = {
            **resp,
            "status": "completed" if kind == "response.completed" else "incomplete",
        }
    out = resp.get("output")
    if not isinstance(out, list) or (not out and items):
        resp["output"] = items  # completed 不带 output（文档未写明）时用累积的条目
    return resp


# --------------------------------------------------------------------------- 协议
def _pack_tools(tools: list[dict]) -> list[dict]:
    """函数工具 → 一个 namespace（官方：「Group function/custom tools in namespaces」）。2026-11-24 真服务核对：被接受。"""
    return [
        {
            "type": "namespace",
            "name": NAMESPACE,
            "description": "Tavotto figure-editing tools.",
            "tools": tools,
        }
    ]


def _bare_name(name: str) -> str:
    for sep in (".", "__", "/"):
        prefix = NAMESPACE + sep
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


class SiwcResponsesWire(OpenAIResponsesWire):
    """`siwc-responses`：见模块说明。请求 / 回放的条目形状复用 `OpenAIResponsesWire`，只换请求体与传输。"""

    name = WIRE_NAME

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
        # max_output_tokens / thinking 不发（官方禁用 / 不适用）；effort 只认 SIWC_EFFORTS（发 reasoning + include 加密思考，见下）
        base = (
            super()
            .build(
                base_url=base_url,
                api_key=api_key,
                model=model,
                system=system,
                tools=tools,
                history=history,
                effort=None,
                max_output_tokens=max_output_tokens,
            )
            .body
        )
        # 回放：`store: false` 下 reasoning 条目只有带 `encrypted_content` 才能放回去（没有的会被拒）——带的留、不带的剥
        items = [
            it
            for it in base["input"]
            if not (
                isinstance(it, dict)
                and it.get("type") == "reasoning"
                and not (effort in SIWC_EFFORTS and it.get("encrypted_content"))
            )
        ]
        body: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "input": items,
            "store": False,
            "stream": True,
        }
        if effort in SIWC_EFFORTS:
            body["reasoning"] = {"effort": effort}
            body["include"] = ["reasoning.encrypted_content"]
        if base.get("tools"):
            body["tools"] = _pack_tools(base["tools"])
        if not set(body) <= set(
            _ALLOWED_FIELDS
        ):  # 白名单是契约：新增字段先改 `_ALLOWED_FIELDS` 与 ADR 0201
            raise RunnerFailure("internal", "bad_request_body")
        return Request(
            base_url.rstrip("/") + "/responses",
            {"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
            body,
            reader=compact_sse,
        )

    def decode(self, data: bytes) -> dict:
        return fold(data)

    def classify(self, status: int, data: bytes) -> RunnerFailure | None:
        """非 2xx：先认闭集码；403 无码是策略 / 地区限制（不是登录问题，不能引导重新登录）；其余交给通用规则（返回 None）。"""
        mapped = failure_for_code(_error_code(data))
        if mapped is not None:
            return mapped
        if status == 403:
            return RunnerFailure("refused", "plan_forbidden")
        return None

    def parse(self, body: dict) -> Turn:
        turn = super().parse(body)
        for call in turn.tool_calls:
            call.name = _bare_name(call.name)
        return turn


def runner_args_ok(*, base_url: str, auth: str, fallback: list, effort, thinking: str) -> bool:
    """runner 起跑前对 `--wire siwc-responses` 的硬约束：开关开着、只发 Bearer、令牌只去官方地址、不换协议、
    思考强度只许 `SIWC_EFFORTS`、不带 anthropic 式 thinking。"""
    return (
        enabled()
        and auth == "bearer"
        and base_url.rstrip("/") == BASE_URL
        and not fallback
        and effort in (None, *SIWC_EFFORTS)
        and thinking == "none"
    )
