"""无工具、单轮、只要一份结构化结果的模型调用（ADR 0178 修订 2026-11-19「接模型的地基」）。

给「从数据到图」这类**一问一答**的模型调用用：把系统提示 + 一段用户内容发给用户当前连着的 API 连接，回复必须是一个 JSON 对象，
过调用方给的校验函数才算数。**永不抛到调用方**——任何一种失败都落成闭集原因码（`REASONS`），调用方据此静默退回规则结果。

挂在哪一层：不另写 HTTP。请求构造 / 三种线上协议 / 出站传输 / 状态码分类 / 非必需字段降级 / 首轮换协议，全部复用内置执行器的
`agent_runner.loop.HttpModel`（经 `fixsession.build_model` 拿到），只是 `tools=[]`、`max_attempts=1`（传输失败不重发：本模块唯一
的重试是「回复不是合法 JSON」那一次）。出站只许 https / 回环 http、证书校验走 `tlstrust`、不跟随重定向，这些约束在 `wires.post_json`
里本来就在，本模块不碰 opener。

连接支持：
* API Key / 自填地址 / 账号登录（OAuth）这类内置执行器能直接用的连接（`ai_providers.builtin_conn`）：支持。
* 订阅登录（Codex / Claude Code CLI）：**暂不支持**，返回 `unsupported_connection`。它们没有干净的「无工具单次调用」——CLI 自带系统提示、
  工具与用户配置，Tavotto 没有给它们的单轮入口。适配方向见 ADR 0178 修订节与 `docs/rules/backend/ai-agent-bridge.md`。

隐私：本模块**不写日志、不发遥测**；失败的 `detail` 只是短代码（HTTP 状态、异常类型名、`not_json` 之类），不含响应原文、提示词、
地址或密钥；`StructuredResult` 里没有提示词与回复原文（成功时只有已过校验的 `value`）。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .agent_runner.wires import Msg, RunnerFailure, Usage, post_json

#: 失败的闭集原因。调用方只按这些码决定「退回规则结果」，不展示给用户。
#: * no_connection          没有任何连接（或运维开关关了内置执行器）
#: * unsupported_connection  只有订阅登录（Codex / Claude Code CLI）这类没有单次调用方式的连接
#: * over_budget             输入超过上限（没发请求）、输出被截断（撞到最长输出），或 `max_requests` 到顶
#: * timeout                 超过总时限（含两次尝试）或网络层读超时
#: * refused                 服务端拒答 / 内容过滤
#: * invalid_output          回复不是合法 JSON（已重试一次）、没有文字、或没过校验函数
#: * auth / quota            密钥被拒 / 额度或限流
#: * transport               其余一切：网络错误、非 2xx、回复体不是预期形状、本模块内部异常
REASONS = (
    "no_connection",
    "unsupported_connection",
    "over_budget",
    "timeout",
    "refused",
    "invalid_output",
    "auth",
    "quota",
    "transport",
)

#: 第二次尝试时追加的一句固定文字（不含任何用户内容）。
RETRY_NOTICE = (
    "上一条回复不是合法的 JSON 对象。请只输出一个 JSON 对象，不要解释文字，不要 Markdown 围栏。"
)


@dataclass(frozen=True)
class Limits:
    """一次调用的上限。`max_input_chars` 数系统提示 + 用户内容的字符；`timeout_s` 是**总**时限（两次尝试共用）；
    `max_retries` 只在「回复不是合法 JSON」时用，上限 1。"""

    max_input_chars: int = 60_000
    max_output_tokens: int = 3_000
    timeout_s: float = 60.0
    max_retries: int = 1

    def __post_init__(self) -> None:
        if self.max_input_chars < 1 or self.max_output_tokens < 1 or self.timeout_s <= 0:
            raise ValueError("limits must be positive")
        if not 0 <= self.max_retries <= 1:
            raise ValueError("max_retries must be 0 or 1")


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class StructuredResult:
    """`ok` 时 `value` 是**已过校验**的 dict、`reason` 为空；失败时 `value` 是 None、`reason` 在 `REASONS` 里。
    用量跨两次尝试累加（失败的那次若服务端报了用量也算，照样计费）。`cost_usd` 只在服务端报了用量、且定价表认得这家官方端点 / 型号时有，
    自填地址与别的模型是 None（不是 0）。"""

    ok: bool
    value: dict | None = None
    reason: str = ""
    detail: str = ""
    attempts: int = 0
    #: 实际发出的 HTTP 请求数（在传输层数）。一次尝试里换协议 / 字段降级的恢复路径会多发请求，所以 `requests ≥ attempts`
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    usage_reported: bool = True
    elapsed_ms: int = 0
    cost_usd: float | None = None
    wire: str = ""
    model: str = ""


# --------------------------------------------------------------------------- 失败归类
_TIMEOUT_MARK = "timeout"


def map_failure(exc: RunnerFailure) -> tuple[str, str]:
    """内置执行器的 `RunnerFailure` → `(本模块原因码, 短 detail)`。对 `loop.REASONS` 的每一个值都有结局（测试逐项核）。

    两种超时要认全：总时限到了是 `budget/time`；套接字读超时是 `network` + 异常类型名（`TimeoutError`）。"""
    reason, detail = exc.reason, exc.detail
    if reason == "refused":
        return "refused", detail
    if reason == "auth":
        return "auth", detail
    if reason == "quota":
        return "quota", detail
    if reason == "budget":
        if detail == "requests":
            return "over_budget", detail
        return "timeout", detail or "time"
    if reason == "network" and _TIMEOUT_MARK in detail.lower():
        return "timeout", detail
    return "transport", detail or reason


# --------------------------------------------------------------------------- 回复解析
def _reject_constant(_name: str):
    raise ValueError("non-finite number")


def parse_json_object(text: str) -> dict | None:
    """回复文字 → dict；不是合法 JSON 对象 = None。容忍两种常见包装：一对 Markdown 围栏、JSON 前后的几句话（从第一个 `{` 起解析一个对象）。
    不接受 NaN / Infinity。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
        t = t.strip()
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    try:
        obj = decoder.decode(t)
        return obj if isinstance(obj, dict) else None
    except (ValueError, RecursionError):
        pass
    start = t.find("{")
    if start < 0:
        return None
    try:
        obj, _end = decoder.raw_decode(t[start:])
    except (ValueError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


# --------------------------------------------------------------------------- 连接
#: 联网刷新账号登录令牌（`ai_oauth.HTTP_TIMEOUT_S` = 30 s）/ 核 OpenAI 系地址少写 `/v1`（最多两次各 5 s）所需的剩余预算。
#: 不够就**不刷新**：这两步没法传入时限，放进线程超时后会留下继续写 provider 存储的半截副作用，所以只在预算够它跑完时才做。
REFRESH_NEEDS_S = 35.0


def resolve_connection(budget_s: float | None = None) -> tuple[dict | None, str, str]:
    """用户当前连着的、内置执行器能直接用的连接 → `(builtin_conn 形状 | None, 原因码, detail)`。

    「当前」= 连接视图里第一个可用的 Key / 账号登录连接（`ai_connections.first_key_connection`，旧版选中的接口排在前，与 AI 修复同一口径）。
    没有时分两种：能经 MCP 接法修复的 CLI（订阅登录）在用 → `unsupported_connection`；什么都没有 → `no_connection`。

    **时限**：挑连接本身不联网（`refresh=False`）。只对挑中的**那一条**记录、且只在 `budget_s`（剩余预算，默认 `DEFAULT_LIMITS.timeout_s`）
    ≥ `REFRESH_NEEDS_S` 时才联网刷新（账号登录令牌 `ensure_fresh` / 地址 `ensure_api_root`）；预算不够就用现有记录，令牌真过期了请求会得到 `auth`、
    调用方退回规则结果。不会顺序刷新多条连接。令牌刷不动（需重新登录）→ `no_connection` / `needs_login`。"""
    from . import ai_connections, ai_oauth, ai_providers, fixsession

    budget = DEFAULT_LIMITS.timeout_s if budget_s is None else budget_s
    if ai_connections.builtin_forced() is False:
        return None, "no_connection", "builtin_disabled"
    conn = ai_connections.first_key_connection(refresh=False)
    if conn is None:
        if fixsession.cli_agent() is not None:
            return None, "unsupported_connection", "cli_only"
        return None, "no_connection", "none"
    if budget >= REFRESH_NEEDS_S:
        rec = ai_providers.get(conn.get("provider_id"))
        if rec is not None:
            try:
                if rec.get("auth") == "oauth":
                    rec = ai_oauth.ensure_fresh(rec) or rec
                else:
                    rec = ai_providers.ensure_api_root(rec) or rec
            except ai_oauth.NeedsLogin:
                return None, "no_connection", "needs_login"
            fresh = ai_providers.builtin_conn(rec, conn.get("model"))
            if fresh is not None:
                conn = fresh
    return conn, "", ""


def _effort_kwargs(conn: dict, effort: str | None) -> dict:
    """想要的强度 → `HttpModel` 的 `effort` / `thinking`（经 `builtin_effort_args` 核过才有）；任何一步不确定 = 空（不发）。"""
    if conn.get(
        "effort"
    ):  # 只有手动基准的临时连接带（存储里的连接没有这个键）；协议层仍按自己认的取值过滤
        return {"effort": conn["effort"]}
    if not effort:
        return {}
    try:
        from . import ai_providers

        rec = ai_providers.get(conn.get("provider_id"))
        if rec is None:
            return {}
        args = ai_providers.builtin_effort_args(rec, effort, conn.get("model"))
    except Exception:  # noqa: BLE001 - 查不到强度支持 = 不发
        return {}
    out: dict = {}
    if "--effort" in args:
        out["effort"] = args[args.index("--effort") + 1]
    if "--thinking" in args:
        out["thinking"] = args[args.index("--thinking") + 1]
    return out


def _cost(conn: dict, usage: Usage, reported: bool) -> float | None:
    if not reported:
        return None
    from . import ai_pricing

    vendor = ai_pricing.vendor_of(conn)
    # `cost_usd` 只收美元：人民币计价的站点（智谱 / MiniMax 国内站）当没有价，不把人民币数字当美元累加，也不换算。
    if ai_pricing.currency_of(vendor, conn.get("model")) not in (None, "USD"):
        return None
    return ai_pricing.estimate(
        vendor,
        conn.get("model"),
        input_tokens=usage.input_tokens,
        cached=usage.cached_input_tokens,
        output=usage.output_tokens,
    )


# --------------------------------------------------------------------------- 调用
def call_structured(
    *,
    system: str,
    user: str,
    validate: Callable[[dict], dict],
    limits: Limits = DEFAULT_LIMITS,
    conn: dict | None = None,
    post: Callable | None = None,
    max_requests: int | None = None,
    effort: str | None = None,
) -> StructuredResult:
    """发一次无工具的单轮请求，要回一个过了 `validate` 的 dict。**永不抛**（`Exception` 一律落成 `transport`）。

    * `validate(obj) -> dict`：收一个 JSON 对象，合格就返回（可以是规整后的新 dict），不合格抛 `ValueError`（任何异常都算不合格）。
      形状不合格**不重试**（重发大概率还是同一个错）。
    * `conn`：`builtin_conn` 形状的连接；不给就 `resolve_connection()`。`post`：注入的传输函数（`wires.post_json` 的签名），只给测试用。
    * 上限：输入字符超限不发请求（`over_budget`）；输出被截断（撞到最长输出）→ `over_budget`、不重试；总时限（`limits.timeout_s`）两次尝试共用。
    * `max_requests`：实际 HTTP 请求数的硬上限（传输层数，含换协议 / 字段降级的恢复请求）；到顶不再发，回 `over_budget` / `requests`。
    * `effort`：想要的思考强度（`low` 等）。只有 `ai_providers.builtin_effort_args` 对「这个地址 + 这个型号」确认支持的才发（官方端点、目录核过的型号），
      其余（第三方网关、不认的型号、不支持的协议）一个字段都不发——不发对方不认的字段，也就不需要靠 400 再降级。
    * 重试：只在回复不是合法 JSON（含没有文字）时，最多 `limits.max_retries`（≤ 1）次；传输失败 / 拒答 / 鉴权失败都不重发。
    """
    t0 = time.monotonic()
    try:
        return _call(system, user, validate, limits, conn, post, t0, max_requests, effort)
    except Exception:  # noqa: BLE001 - 契约：永不抛；原因里不放异常消息（可能含提示词 / 地址）
        return StructuredResult(False, reason="transport", detail="internal", elapsed_ms=_ms(t0))


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _call(
    system: str,
    user: str,
    validate: Callable[[dict], dict],
    limits: Limits,
    conn: dict | None,
    post: Callable | None,
    t0: float,
    max_requests: int | None,
    effort: str | None = None,
) -> StructuredResult:
    if len(system) + len(user) > limits.max_input_chars:
        return StructuredResult(False, reason="over_budget", detail="input", elapsed_ms=_ms(t0))
    if conn is None:
        conn, why, detail = resolve_connection(limits.timeout_s)
        if conn is None:
            return StructuredResult(False, reason=why, detail=detail, elapsed_ms=_ms(t0))
        if time.monotonic() - t0 >= limits.timeout_s:  # 解析连接已经用完总时限：不发请求
            return StructuredResult(False, reason="timeout", detail="resolve", elapsed_ms=_ms(t0))
    from . import fixsession

    sent = [
        0
    ]  # 传输层的真实请求数（`HttpModel.complete` 在换协议 / 字段降级时一次尝试可发多个请求）
    inner = post if post is not None else post_json

    def counted(url, headers, body, *, timeout, max_bytes=None):
        if max_requests is not None and sent[0] >= max_requests:
            raise RunnerFailure("budget", "requests")
        sent[0] += 1
        if max_bytes is None:
            return inner(url, headers, body, timeout=timeout)
        return inner(url, headers, body, timeout=timeout, max_bytes=max_bytes)

    kwargs: dict = {
        "request_timeout": limits.timeout_s,
        "max_attempts": 1,
        "retry_base": 0.0,
        "post": counted,
        **_effort_kwargs(conn, effort),
    }
    model, _redactor = fixsession.build_model(conn, **kwargs)
    deadline = t0 + limits.timeout_s  # 总时限从调用开始算：解析连接（可能联网刷新令牌）也在里面
    history = [Msg("user", user)]
    total = Usage()
    reported = True
    attempts = 0
    max_attempts = 1 + limits.max_retries
    max_chars = (
        limits.max_output_tokens * 8
    )  # 一个 token 至多 8 个字符的宽松上界：防不守 max_tokens 的端点回一大坨

    def usage_fields() -> dict:
        return {
            "attempts": attempts,
            "requests": sent[0],
            "input_tokens": total.input_tokens,
            "output_tokens": total.output_tokens,
            "cached_input_tokens": total.cached_input_tokens,
            "usage_reported": reported,
            "elapsed_ms": _ms(t0),
            "cost_usd": _cost(conn, total, reported),
            "wire": model.wire.name,
            "model": str(conn.get("model") or ""),
        }

    def add(u: Usage | None) -> None:
        nonlocal reported
        if u is None:
            return
        total.input_tokens += u.input_tokens
        total.output_tokens += u.output_tokens
        total.cached_input_tokens += u.cached_input_tokens
        reported = reported and u.reported

    def fail(reason: str, detail: str) -> StructuredResult:
        return StructuredResult(False, reason=reason, detail=detail, **usage_fields())

    while attempts < max_attempts:
        attempts += 1
        try:
            turn = model.complete(
                system=system,
                tools=[],
                history=history,
                max_output_tokens=limits.max_output_tokens,
                cancel=threading.Event(),
                deadline=deadline,
            )
        except RunnerFailure as exc:
            add(exc.usage)
            reason, detail = map_failure(exc)
            return fail(reason, detail)
        add(turn.usage)
        if turn.stop == "max_tokens":
            return fail("over_budget", "output")
        if turn.stop == "tool_use":
            return fail("invalid_output", "tool_call")
        if len(turn.text) > max_chars:
            return fail("over_budget", "output")
        obj = parse_json_object(turn.text)
        if obj is None:
            if attempts < max_attempts:
                history = [*history, Msg("assistant", turn=turn), Msg("user", RETRY_NOTICE)]
                continue
            return fail("invalid_output", "not_json")
        try:
            value = validate(obj)
        except Exception:  # noqa: BLE001 - 校验函数说「不合格」的方式不限
            return fail("invalid_output", "shape")
        if not isinstance(value, dict):
            return fail("invalid_output", "shape")
        return StructuredResult(True, value=value, **usage_fields())
    return fail("invalid_output", "not_json")  # 不可达（循环内必返回或继续）；兜底给闭集结局
