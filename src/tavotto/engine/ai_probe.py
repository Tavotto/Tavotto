"""保存连接时的协议探测：对一个 (地址, Key, 型号) 真发一条带工具定义的最小对话请求，看哪种线上协议通。

起因（TVD-HN4B）：用户自填的中转站（new-api 一类）「能列型号、跑不了任务」——型号清单只证明地址通了，不证明对话协议对。
这里照 magpie 的 Detect 做：每种协议发一条最小请求，通的就记下来，之后内置循环用那一种。

**判据**：通过 = 回复体解析成合法的 `Turn`（复用 `agent_runner.wires` 里该协议的 `parse`），**不是只看 2xx**——
200 的网页、200 的错误对象都判失败。请求的构造与传输同样复用 `wires`（`Wire.build` + `post_json`：不新增出站点、不新增 opener）。

**结果是闭集**（`REASONS`），响应体原文只在本模块内被读来分类，**不进返回值、日志、诊断包**；Key 只在请求头里。

**预算**：每条请求 ≤ `PER_REQUEST_S`，整次探测 ≤ `TOTAL_S`；同一主机连不上（传输层失败）就不再试剩下的。
为省额度与时间，按优先级排好序、**第一个通过的就停**（等价于「都通时按优先级选」）：Claude 型号先试 anthropic；
OpenAI 自家型号（api.openai.com + gpt / o 系列）先试 responses；其余先试 chat（与 magpie `usable()` 一致）；
调用方明确给了协议（`preferred`）就先试它，它通过就尊重用户。

纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from . import ai_providers
from .agent_runner import wires

#: 每种协议的结论（闭集）。`ok` 之外的都是失败原因。
REASON_OK = "ok"
REASONS = (
    REASON_OK,
    "unauthorized",  # 401 / 403：Key 被拒
    "quota",  # 402 / 429 / 文字说余额不足：Key 认，但这次用不了
    "no_model",  # 型号不存在 / 没有可用渠道
    "wrong_protocol",  # 404 / 405：这个地址上没有这套对话接口
    "network",  # 连不上 / 超时 / 证书
    "web_page",  # 回的是网页（地址少了 /v1，或指到了中转站首页）
    "bad_reply",  # 回的不是这套协议该有的样子
)
#: 全部失败时挑「最该告诉用户的那一条」的顺序：越靠前越能说明「地址通了、问题在哪」
_PRIMARY_ORDER = (
    "unauthorized",
    "quota",
    "no_model",
    "web_page",
    "bad_reply",
    "wrong_protocol",
    "network",
)

PER_REQUEST_S = 15.0
TOTAL_S = 30.0
MAX_TOKENS = 16
_PROBE_SYSTEM = "Reply with the single word OK."
_PROBE_PROMPT = "ping"

_MODEL_GONE = (
    "not found",
    "does not exist",
    "not exist",
    "unknown model",
    "invalid model",
    "unsupported model",
    "not supported",
    "no available",
    "model_not_found",
)
_NO_CHANNEL = ("no available channel", "无可用渠道", "no channel available")
_QUOTA_WORDS = ("quota", "insufficient", "balance", "credit", "billing", "余额", "额度")
_OPENAI_HOST = "api.openai.com"
_OPENAI_MODEL = re.compile(r"^(gpt-|chatgpt-|o\d)", re.IGNORECASE)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _openai_root(base: str) -> list[str]:
    """OpenAI 系协议在地址后直接拼 `/responses` / `/chat/completions`：用户给的原样在前，不带 `/v1` 再补一个换算版
    （与 `ai_catalog.api_root_fix` 同一结论：中转站首页回网页、`/v1` 才是 API 根）。"""
    base = base.rstrip("/")
    return [base] if base.lower().endswith("/v1") else [base, base + "/v1"]


def _anthropic_root(base: str) -> list[str]:
    """Anthropic 系自己拼 `/v1/messages`：用户给的原样在前，带 `/v1` 的再补一个去掉 `/v1` 的换算版。"""
    base = base.rstrip("/")
    if base.lower().endswith("/v1"):
        return [base, base[: -len("/v1")]]
    return [base]


def candidates(protocol: str, base_url: str) -> list[str]:
    return _anthropic_root(base_url) if protocol == "anthropic" else _openai_root(base_url)


def order(base_url: str, model: str, preferred: str | None = None) -> list[str]:
    """试的顺序（见模块说明）；`preferred` 排最前。"""
    m = (model or "").strip()
    if "claude" in m.lower():
        base = ["anthropic", "openai-chat", "openai-responses"]
    elif _host(base_url) == _OPENAI_HOST and _OPENAI_MODEL.match(m):
        base = ["openai-responses", "openai-chat", "anthropic"]
    else:
        base = ["openai-chat", "openai-responses", "anthropic"]
    if preferred in ai_providers.PROTOCOLS:
        base = [preferred, *[p for p in base if p != preferred]]
    return base


def _err_text(obj: object) -> str:
    """错误体里能分类用的那几个字段，小写后只在本函数调用方的比较里用；**不返回给任何人**。"""
    if not isinstance(obj, dict):
        return ""
    parts: list[str] = []
    err = obj.get("error")
    for src in (err, obj):
        if isinstance(src, dict):
            parts += [str(src.get(k) or "") for k in ("message", "code", "type")]
        elif isinstance(src, str):
            parts.append(src)
    if isinstance(obj.get("message"), str):
        parts.append(obj["message"])
    return " ".join(parts).lower()[:2000]


def _tool():
    return wires.ToolSpec(
        "ping", "A no-op tool.", {"type": "object", "properties": {}, "additionalProperties": False}
    )


def _build(protocol: str, base: str, key: str, model: str, token_field: str):
    wire = (
        wires.OpenAIChatWire(token_field=token_field)
        if protocol == "openai-chat"
        else wires.WIRES[protocol]
    )
    req = wire.build(
        base_url=base,
        api_key=key,
        model=model,
        system=_PROBE_SYSTEM,
        tools=[_tool()],
        history=[wires.Msg("user", _PROBE_PROMPT)],
        effort=None,
        max_output_tokens=MAX_TOKENS,
        thinking="none",
    )
    return wire, req


def classify(wire, status: int, data: bytes) -> tuple[str, bool]:
    """一次响应 → (结论, 要不要换 `max_completion_tokens` 重发)。`data` 只在这里被读。"""
    html = data.lstrip()[:1] == b"<"
    try:
        obj = json.loads(data.decode("utf-8")) if not html else None
    except (ValueError, UnicodeDecodeError):
        obj = None
    if 200 <= status < 300:
        if html:
            return "web_page", False
        if not isinstance(obj, dict):
            return "bad_reply", False
        try:
            wire.parse(obj)
        except wires.RunnerFailure:
            return "bad_reply", False
        return REASON_OK, False
    if 300 <= status < 400:
        return "web_page", False  # 跳去登录页 / 首页（不跟随重定向）
    text = _err_text(obj)
    if status >= 500:
        # 5xx 是暂时性的（服务商抖动 / 过载）：没结论，不落盘。唯一例外：中转站的「没有可用渠道」503 = 这个地址上没有这套协议的渠道
        if status == 503 and any(w in text for w in _NO_CHANNEL):
            return "wrong_protocol", False
        return "network", False
    if status in (404, 405) and html:
        return "wrong_protocol", False
    if html:
        return ("unauthorized" if status == 401 else "web_page"), False
    if status == 400 and "max_completion_tokens" in text:
        return "bad_reply", True
    if any(w in text for w in _QUOTA_WORDS) and status in (400, 402, 403, 429):
        return "quota", False
    if status in (401, 403):
        return "unauthorized", False
    if status in (402, 429):
        return "quota", False
    if any(w in text for w in _MODEL_GONE) and "model" in text:
        return "no_model", False
    if status in (404, 405, 410):
        return "wrong_protocol", False
    return "bad_reply", False


class _Budget:
    def __init__(self, total_s: float, per_request_s: float):
        self.end = time.monotonic() + total_s
        self.per = per_request_s

    def slice(self) -> float | None:
        left = self.end - time.monotonic()
        return None if left < 1.0 else min(self.per, left)


def _attempt(protocol, base, key, model, budget: _Budget) -> tuple[str, str, bool]:
    """对一个具体地址发探测 → (结论, token_field, 传输层失败)；`token_field` 只对 chat 有意义。
    传输层失败 = 连不上 / 超时 / 证书（同主机不必再试）；5xx 也归 `network` 但主机是通的，不算。"""
    fields = (
        ("max_tokens", "max_completion_tokens") if protocol == "openai-chat" else ("max_tokens",)
    )
    used = fields[0]
    for field in fields:
        used = field
        timeout = budget.slice()
        if timeout is None:
            return "network", used, True
        wire, req = _build(protocol, base, key, model, field)
        try:
            status, _h, data = wires.post_json(req.url, req.headers, req.body, timeout=timeout)
        except wires.RunnerFailure:
            return "network", used, True
        reason, retry = classify(wire, status, data)
        if not retry:
            return reason, used, False
    return "bad_reply", used, False


def detect(
    base_url: str,
    api_key: str,
    model: str,
    *,
    preferred: str | None = None,
    protocols: tuple[str, ...] | None = None,
    root_fix: bool = True,
    total_s: float = TOTAL_S,
    per_request_s: float = PER_REQUEST_S,
) -> dict:
    """探测 → `{protocol, base_url, token_field, reasons, reason, conclusive}`。永不抛。

    * `protocol` / `base_url`：通过的协议与它对应的 API 根（OpenAI 系带 `/v1`、Anthropic 系不带）；全不通 = None。
    * `token_field`：chat 的长度上限字段（`max_tokens` | `max_completion_tokens`），其余 None。
    * `reasons`：**试过的**协议 → 结论（早停，没试的不在里面）；`reason`：全不通时最该告诉用户的那一条，通了 = None。
    * `conclusive`：False = 一条结论都没拿到（缺型号 / 全是网络失败）——调用方不要据此给连接记「不可用」。
    * `protocols`：只核这几种（目录里的已知厂商只核自己那种）；`root_fix=False`：只试地址原样，不换算。"""
    base = (base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    model = (model or "").strip()
    empty = {
        "protocol": None,
        "base_url": None,
        "token_field": None,
        "reasons": {},
        "reason": "no_model" if not model else "network",
        "conclusive": False,
    }
    if not base or not key or not model:
        return empty
    seq = [p for p in order(base, model, preferred) if protocols is None or p in protocols]
    budget = _Budget(total_s, per_request_s)
    reasons: dict[str, str] = {}
    known_root: dict[
        str, str
    ] = {}  # 家族 → 已确认的根（第一个协议摸出 `/v1` 后，后面的协议直接用）
    host_dead = False
    for proto in seq:
        if host_dead:
            reasons[proto] = "network"
            continue
        family = "anthropic" if proto == "anthropic" else "openai"
        cands = [known_root[family]] if family in known_root else candidates(proto, base)
        if not root_fix:
            cands = cands[:1]
        reason, field, root, transport = "network", "max_tokens", cands[0], False
        for cand in cands:
            root = cand
            reason, field, transport = _attempt(proto, cand, key, model, budget)
            if reason not in ("web_page", "wrong_protocol"):
                break
        reasons[proto] = reason
        if reason == REASON_OK:
            return {
                "protocol": proto,
                "base_url": root,
                "token_field": field if proto == "openai-chat" else None,
                "reasons": reasons,
                "reason": None,
                "conclusive": True,
            }
        if transport:
            host_dead = True
        elif reason not in ("web_page", "wrong_protocol", "network"):
            known_root[family] = root  # 这个根上有真 API 在答（哪怕拒了）：同族协议别再换根
    seen = [r for r in reasons.values() if r != "network"]
    primary = next((r for r in _PRIMARY_ORDER if r in reasons.values()), "network")
    return {
        "protocol": None,
        "base_url": None,
        "token_field": None,
        "reasons": reasons,
        "reason": primary,
        "conclusive": bool(seen),
    }


def fingerprint(base_url: str, model: str, protocol: str, token_field: str | None = None) -> dict:
    """探测结论的指纹（落盘用，`ai_providers._clean` 只在地址与型号都没变时保留）。"""
    out = {"base_url": base_url, "model": model, "protocol": protocol}
    if protocol == "openai-chat" and token_field in ("max_tokens", "max_completion_tokens"):
        out["token_field"] = token_field
    return out
