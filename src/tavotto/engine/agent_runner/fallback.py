"""第一轮换协议与非必需字段降级的判据（纯函数 + 一个小状态类，不联网、不写事件）。

**为什么有这个模块**：用户自填的第三方接口多是 new-api / one-api 类中转，经常「能列型号、跑不了任务」——
同一个地址上 chat 通、responses 不通（或反过来），或者不认我们带的 `store` / `include` / `max_tokens`。
`store: false` 是故意的（提示词里有用户未发表的脚本），**绝不自动剥**：responses 上 `store` 被点名拒绝 = 这个地址不适合 responses，
当作「协议不对」信号（`store_rejected`），有备选才换（chat 没有 store 字段），没备选照常失败。
这里只回答两个问题，且判据都是**闭集**：

1. 第一次模型请求的失败是不是「协议不对」（`protocol_mismatch` / `store_rejected`）→ 换下一个协议重发；
2. 400 / 422 是不是明确点名了我们发的某个**非必需**字段不被接受（`FieldAdjuster.consider`）→ 剥掉 / 换名后重发一次。

**响应体原文只在内存里匹配**：不进异常、日志、事件、诊断包；对外只有闭集结果（换没换、剥了哪个字段）。
401 / 403 / 429 / 一般 5xx 与任意其他 400 一律不换、不剥——它们是密钥、额度、服务故障或值非法，换协议或改请求只会
把真问题藏起来（ADR 0147 修订 2026-11-15）。
"""

from __future__ import annotations

import json
import re
from typing import Any

#: 4xx 里一律认「这个地址不认这个协议路径」的状态码（路径不存在 / 方法不允许）
WRONG_PATH_STATUSES = frozenset({404, 405})
#: 带下面短语时也认「协议不对」的状态码（new-api / one-api 把不匹配渠道、上游无该渠道也报成 400 / 503）
PHRASE_STATUSES = frozenset({400, 404, 503})
#: 小写、逐字子串匹配。出处：magpie `internal/catalog/catalog.go` 的 WrongEndpoint / NoChannel 两张表。
#: 想加短语必须同步 ADR 0147 修订记录与 `tests/test_agent_runner_fallback.py` 的参数表。
WRONG_PROTOCOL_PHRASES = (
    "not supported in /v1/chat/completions",
    "not supported in the v1/chat/completions",
    "not supported in /v1/responses",
    "only supported in /v1/responses",
    "use /v1/responses",
    "use /v1/chat/completions",
    "unsupported_api_for_model",
    "does not support this protocol",
    "is not supported for format",
    "not a chat model",
    "no available channel",
    "无可用渠道",
    "没有可用渠道",
    "convert_request_failed",
    "not implemented",
)

#: 降级可能剥 / 换的字段名（闭集；`done.dropped_fields` 只会出现这些）
DROPPABLE_FIELDS = ("include", "max_output_tokens", "max_tokens", "max_completion_tokens")
#: 各协议里「可剥」的非必需字段（chat 的长度字段是换名，不在这里）
_DROP_CANDIDATES = {"openai-responses": ("include", "max_output_tokens")}
_TOKEN_FIELDS = ("max_tokens", "max_completion_tokens")

#: 点名之后必须带的否定措辞之一（值非法 / 超限不算：它们没有这些词）
_NEGATIVE = (
    "unsupported",
    "not supported",
    "unknown",
    "unrecognized",
    "not permitted",
    "not allowed",
)
#: 出现即判「值的问题」，不是「字段不被接受」
_VALUE_PROBLEM = ("unsupported value", "invalid value", "invalid_value", "out of range")

_BODY_PEEK = 8192


def _leaves(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.append(str(k))
            _leaves(v, out)
    elif isinstance(node, list):
        for v in node:
            _leaves(v, out)


def _peek(body: bytes) -> tuple[str, frozenset[str]]:
    """体的前 8 KiB → (小写文本, 小写的字符串叶子集合)。

    JSON 体先解码再把所有字符串（键与值）用换行拼起来：真实服务多半回 `\\u65e0\\u53ef…` 这种转义、值里的引号也被
    转义成 `\\"`，不还原就匹配不上中文短语与「被引号包住的字段名」。叶子集合用于 `param: "store"` /
    `loc: ["body", "include"]` 这类「字段名单独成一个字符串」的写法。非 JSON（或被截断的 JSON）退回原文、叶子为空。"""
    text = body[:_BODY_PEEK].decode("utf-8", errors="replace")
    if text.lstrip()[:1] in ("{", "["):
        try:
            leaves: list[str] = []
            _leaves(json.loads(text), leaves)
            return "\n".join(leaves).lower(), frozenset(x.lower() for x in leaves)
        except ValueError:
            pass
    return text.lower(), frozenset()


def protocol_mismatch(status: int, body: bytes) -> bool:
    """这次失败是不是「协议不对」。404 / 405 直接算；400 / 404 / 503 要体里有闭集短语之一。"""
    if status in WRONG_PATH_STATUSES:
        return True
    if status not in PHRASE_STATUSES:
        return False
    low, _ = _peek(body)
    return any(p in low for p in WRONG_PROTOCOL_PHRASES)


def store_rejected(wire_name: str, status: int, body: bytes) -> bool:
    """responses 上 `store` 被点名拒绝：不剥（隐私），当作「协议不对」，由调用方决定有没有备选可换。"""
    return wire_name == "openai-responses" and names_field(status, body, "store")


def names_field(status: int, body: bytes, field: str) -> bool:
    """400 / 422 的体里是不是明确点名 `field` 为不被接受。

    点名 = 被引号 / 反引号包住，或字段名本身带下划线（`max_tokens` 这种不会是普通英文词）；`store` / `include`
    是普通英文词，只认带引号的写法，免得「store」出现在别的句子里被误剥。"""
    if status not in (400, 422):
        return False
    low, leaves = _peek(body)
    if any(v in low for v in _VALUE_PROBLEM) or not any(n in low for n in _NEGATIVE):
        return False
    if field in leaves:
        return True
    if any(q + field + q in low for q in ('"', "'", "`")):
        return True
    return "_" in field and re.search(rf"(?<![a-z0-9_]){field}(?![a-z0-9_])", low) is not None


class FieldAdjuster:
    """一个协议上的字段降级状态：同一字段只尝试一次，跨轮保持（后面每轮请求都套同样的调整）。"""

    def __init__(self, wire_name: str):
        self.wire = wire_name
        self.dropped: list[str] = []
        #: chat 长度字段已经换过名（只换一次）
        self.swapped = False

    def consider(self, status: int, body: bytes, sent: dict[str, Any]) -> bool:
        """`sent` = 刚发的请求体。有了新调整回 True（调用方重建请求体重发）；没有回 False。"""
        changed = False
        if self.wire == "openai-chat":
            current = next((f for f in _TOKEN_FIELDS if f in sent), None)
            if current and not self.swapped and names_field(status, body, current):
                self.swapped = True
                self.dropped.append(current)
                changed = True
            return changed
        for f in _DROP_CANDIDATES.get(self.wire, ()):
            if f in sent and f not in self.dropped and names_field(status, body, f):
                self.dropped.append(f)
                changed = True
        return changed

    def apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """把已记下的调整套到刚构造的请求体上（原地改，返回同一个对象）。"""
        for f in self.dropped:
            if self.wire == "openai-chat":
                continue
            body.pop(f, None)
        if self.wire == "openai-chat" and self.swapped:
            current = next((f for f in _TOKEN_FIELDS if f in body), None)
            if current:
                other = _TOKEN_FIELDS[1 - _TOKEN_FIELDS.index(current)]
                body[other] = body.pop(current)
        if self.wire == "openai-responses" and "include" in self.dropped:
            # 没有 `include: reasoning.encrypted_content`，回放里的 reasoning 条目没有加密内容，
            # `store=false` 下会被拒（第二轮才炸，那时工具已经执行了）：一并剥掉
            items = body.get("input")
            if isinstance(items, list):
                body["input"] = [
                    it
                    for it in items
                    if not (isinstance(it, dict) and it.get("type") == "reasoning")
                ]
        return body

    @staticmethod
    def token_field(body: dict[str, Any]) -> str:
        return next((f for f in _TOKEN_FIELDS if f in body), "")
