"""循环：模型 ↔ 工具，带预算、重试、上下文压缩、账本恒等式与取消。**失败一律不抛**：结局写进最后的 `done` 事件。

结束条件（`done.reason`，闭集 + 一个兜底）：

* `completed`   模型给出最终文字、没有工具调用，或显式最终校验被工具确认通过（`ok=true`）；
* `max_turns`   轮数用尽；`budget` token / 秒数用尽，或一轮输出被截断（`detail=output_truncated`）；
* `tool_error_loop` 同一工具、同一参数的同一错误码连续 3 次；
* `auth` 401 / 403 / 没密钥；`quota` 402、额度耗尽、429 重试用尽；
* `refused` 服务端过滤 / 拒答（chat `finish_reason=content_filter`、anthropic `stop_reason=refusal`、responses `incomplete` 且原因不是
  `max_output_tokens`；`detail` 是短代码）；
* `network` 传输失败、5xx 重试用尽、不可重试的 4xx、回复体不合法（`detail` 是短代码）；
* `cancelled` 收到 SIGTERM / Ctrl-Break；
* `internal` 兜底（本模块自己的缺陷；`detail` 只有异常类型名）。

**预算恒等式**（借 Harness²，ADR 0138 §六）：每轮的 input / output token 与耗时记进 `Ledger.turns`，同时累加进
独立维护的合计；结束时核对「合计 == 各轮之和」，不等则 `done.ledger_mismatch=true`（不抛）。

**事件不带内容**：`tool` 事件只有工具名、成败与一句话摘要；`thinking` 只有字符数；`text` 是模型对用户说的话（产品输出本身）；
文件内容、提示词、密钥、服务端错误体都不进事件，也不进 stderr。
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import tools as toolmod
from .tools import Limits, RunEnvelope, TaskToolset
from .wires import (
    Msg,
    RunnerFailure,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
    Wire,
    post_json,
    truncated_continuation,
)

REASONS = (
    "completed",
    "max_turns",
    "budget",
    "tool_error_loop",
    "auth",
    "quota",
    "refused",
    "network",
    "cancelled",
    "internal",
)

RECOVERY_GUIDANCE = """Execution and recovery loop:
- Treat tool results as observations. If a call fails, use its reported schema and current state to
  correct the next call; do not repeat an identical failing call or guess that it succeeded.
- After a validation error, correct the candidate and validate again within the available check budget.
  An installation or file write alone does not prove that the user's requested run succeeds.
- Prefer one concrete tool action at a time over planning the entire implementation in one response.
  If a response is interrupted, none of its tool calls ran. Retained reasoning is context only;
  earlier successful operations remain valid. Continue from their actual results.
- Recovery never grants permission, bypasses a confirmation, or extends the task's budget.
"""

TRUNCATION_FEEDBACK = (
    "Tavotto runtime: the previous response reached its output limit. NONE of its tool calls ran. "
    "Earlier completed tool operations remain current; do not repeat their side effects. "
    "Use retained progress when available, then take one focused tool action. "
    "Do not restart the entire plan or treat interrupted output as a successful write. "
)


# --------------------------------------------------------------------------- 账本
@dataclass
class Ledger:
    """每轮的账与独立累加的合计。`check()` 核对恒等式。"""

    turns: list[tuple[int, int, int]] = field(default_factory=list)  # (in, out, ms)
    total_in: int = 0
    total_out: int = 0
    total_ms: int = 0
    cached_in: int = 0

    def add(self, usage: Usage, ms: int) -> None:
        self.turns.append((usage.input_tokens, usage.output_tokens, ms))
        self.total_in += usage.input_tokens
        self.total_out += usage.output_tokens
        self.total_ms += ms
        self.cached_in += usage.cached_input_tokens

    def check(self) -> bool:
        return (
            self.total_in == sum(t[0] for t in self.turns)
            and self.total_out == sum(t[1] for t in self.turns)
            and self.total_ms == sum(t[2] for t in self.turns)
        )


# --------------------------------------------------------------------------- 模型客户端
class HttpModel:
    """三种线上协议的客户端：构造请求、发送、分类失败、指数退避重试（429 / 5xx / 传输失败）。"""

    def __init__(
        self,
        wire: Wire,
        *,
        base_url: str,
        api_key: str,
        model: str,
        effort: str | None = None,
        thinking: str = "none",
        request_timeout: float = 120.0,
        max_attempts: int = 4,
        retry_base: float = 1.0,
        post: Callable = post_json,
    ):
        self.wire, self.base_url, self.model, self.effort = wire, base_url, model, effort
        self.thinking = thinking
        self._key = api_key
        self.request_timeout, self.max_attempts, self.retry_base = (
            request_timeout,
            max_attempts,
            retry_base,
        )
        self._post = post

    def __repr__(self) -> str:  # 密钥不进 repr / 日志
        return f"HttpModel({self.wire.name}, model={self.model!r})"

    def truncated_continuation(self, turn: Turn) -> Turn | None:
        return truncated_continuation(self.wire.name, turn)

    def release_recovery_state(self, history: list[Msg]) -> int:
        # A successful tool action now records concrete progress. Do not charge
        # every later request for the long interrupted reasoning that led to it.
        # Manual Anthropic thinking requires its original signed prefix.
        if self.wire.name == "anthropic" and self.thinking == "budget":
            return 0
        count = sum(m.recovery_state for m in history)
        if count:
            history[:] = [m for m in history if not m.recovery_state]
            self._invalidate_thinking_signatures(history)
        return count

    def _invalidate_thinking_signatures(self, history: list[Msg]) -> None:
        if self.wire.name == "anthropic":
            for msg in history:
                if msg.turn is not None and isinstance(msg.turn.native, list):
                    msg.turn.native = [
                        block
                        for block in msg.turn.native
                        if block.get("type") not in ("thinking", "redacted_thinking")
                    ]

    def compress_history(self, history: list[Msg]) -> int:
        # Manual thinking requires its last signed block during tool use. Keep
        # that conversation append-only; the existing total budget still holds.
        if self.wire.name == "anthropic" and self.thinking == "budget":
            return 0
        count = compress(history)
        if count:
            # A thinking signature is bound to the preceding conversation.
            # Editing old tool results invalidates every later signed block.
            # Some endpoints return signed/redacted blocks even when no thinking
            # option was requested. Omit stale blocks in both modes; manual
            # budget mode remains append-only above. Tool calls/results stay
            # paired and the requested thinking mode and effort stay unchanged.
            self._invalidate_thinking_signatures(history)
        return count

    def complete(
        self,
        *,
        system: str,
        tools: list[ToolSpec],
        history: list[Msg],
        max_output_tokens: int,
        cancel: threading.Event,
        deadline: float,
    ) -> Turn:
        req = self.wire.build(
            base_url=self.base_url,
            api_key=self._key,
            model=self.model,
            system=system,
            tools=tools,
            history=history,
            effort=self.effort,
            thinking=self.thinking,
            max_output_tokens=max_output_tokens,
        )
        last: RunnerFailure | None = None
        for attempt in range(self.max_attempts):
            if cancel.is_set():
                raise RunnerFailure("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:  # 总时限到了：不再发请求
                raise RunnerFailure("budget", "time")
            timeout = min(self.request_timeout, remaining)  # 单次请求也不越过总时限
            retry_after = None
            try:
                status, headers, data = _cancellable(
                    lambda: self._post(req.url, req.headers, req.body, timeout=timeout),
                    cancel,
                    deadline,
                )
            except RunnerFailure as exc:
                if not exc.retryable:
                    raise
                last = exc
            else:
                if 200 <= status < 300:
                    try:
                        body = json.loads(data.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise RunnerFailure("network", "bad_response") from None
                    if not isinstance(body, dict):
                        raise RunnerFailure("network", "bad_response")
                    return self.wire.parse(body)
                last = classify_status(status, data)
                if not last.retryable:
                    raise last
                retry_after = _retry_after(headers)
            if attempt + 1 < self.max_attempts:
                delay = min(
                    retry_after if retry_after is not None else self.retry_base * (2**attempt), 30.0
                )
                left = deadline - time.monotonic()
                if delay >= left:
                    # 等完就到总时限了：直接按时限停下。不先睡到时限再查——Windows 上 Event.wait 会早醒几毫秒，
                    # 醒来时「还没到时限」于是又发一次请求（合并组 windows 腿实测）
                    raise RunnerFailure("budget", "time")
                if cancel.wait(delay):
                    raise RunnerFailure("cancelled")
        assert last is not None
        # 重试用尽：429 → quota（被限流），其余 → network
        if last.detail == "http_429":
            raise RunnerFailure("quota", "rate_limited")
        raise RunnerFailure(last.reason, last.detail)


def classify_status(status: int, body: bytes) -> RunnerFailure:
    """非 2xx 的分类。**不把服务端错误体放进任何地方**（可能回显提示词）；只用它判断是不是额度问题。"""
    if status in (401, 403):
        return RunnerFailure("auth", f"http_{status}")
    if status == 402:
        return RunnerFailure("quota", "http_402")
    if status == 429:
        low = body[:4096].lower()
        if any(k in low for k in (b"insufficient_quota", b"billing", b"credit balance", b"quota")):
            return RunnerFailure("quota", "insufficient_quota")
        return RunnerFailure("network", "http_429", retryable=True)
    if status == 408 or status >= 500:  # 408 = 请求超时，可重试；409 是冲突，重发不会变
        return RunnerFailure("network", f"http_{status}", retryable=True)
    if 300 <= status < 400:
        return RunnerFailure("network", "redirect")
    return RunnerFailure("network", f"http_{status}")


def _retry_after(headers: dict[str, str], now: float | None = None) -> float | None:
    """Retry-After 的两种标准写法（RFC 9110 §10.2.3）：秒数，或 HTTP-date。→ 还要等多少秒；看不懂 → None。
    合法的大值照常返回；调用方统一 min(…, 30, 剩余时间) 封顶。"""
    raw = (headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None or when.tzinfo is None:
            return None
        v = when.timestamp() - (time.time() if now is None else now)
        v = max(v, 0.0)
    return v if 0 <= v < float("inf") else None


def _cancellable(fn: Callable, cancel: threading.Event, deadline: float | None = None):
    """在线程里跑阻塞的网络调用，主线程每 50 ms 看一次取消与总时限（socket 超时管不住「慢慢吐字节」）。
    取消 / 到点时放弃那个线程（守护线程，进程随后退出），在途响应丢弃。"""
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=work, daemon=True, name="agent-http").start()
    while not done.wait(0.05):
        if cancel.is_set():
            raise RunnerFailure("cancelled")
        if deadline is not None and time.monotonic() >= deadline:
            raise RunnerFailure("budget", "time")
    if "error" in box:
        raise box["error"]
    return box["value"]


class DemoModel:
    """确定性 Demo 协议（`--wire demo`，借 Harness² 的 Demo 适配器）：按剧本回答，不联网，端到端测试用。

    剧本 JSON：`{"turns": [{"expect": [{"name": "read_file", "summary_contains": "read", "is_error": false}],
    "text": "…", "tool_calls": [{"name": "edit_file", "args": {…}}], "usage": {"input": 100, "output": 20}}]}`。
    `expect` 描述「到这一轮为止收到的上一批工具结果的摘要」；对不上 → `network/demo_mismatch`。"""

    def __init__(self, script: dict):
        self.turns = script.get("turns") if isinstance(script, dict) else None
        if not isinstance(self.turns, list):
            raise ValueError("demo script: `turns` must be a list")
        self.i = 0

    @classmethod
    def from_file(cls, path: str) -> DemoModel:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def complete(self, *, history, cancel, **_ignored) -> Turn:
        if self.i >= len(self.turns):
            raise RunnerFailure("network", "demo_script_exhausted")
        step, self.i = self.turns[self.i], self.i + 1
        last_tool = next((m for m in reversed(history) if m.role == "tool"), None)
        expect = step.get("expect") or []
        got = last_tool.results if (last_tool and expect) else []
        if expect and len(got) != len(expect):
            raise RunnerFailure("network", "demo_mismatch")
        for want, r in zip(expect, got):
            ok = (
                want.get("name", r.name) == r.name
                and want.get("summary_contains", "") in r.summary
                and want.get("is_error", r.is_error) == r.is_error
            )
            if not ok:
                raise RunnerFailure("network", "demo_mismatch")
        usage = step.get("usage") or {}
        calls = [
            ToolCall(f"demo_{self.i}_{k}", str(c.get("name", "")), c.get("args", {}))
            for k, c in enumerate(step.get("tool_calls") or [])
        ]
        return Turn(
            text=step.get("text", ""),
            tool_calls=calls,
            usage=Usage(int(usage.get("input", 0)), int(usage.get("output", 0))),
            stop="tool_use" if calls else "end",
        )


# --------------------------------------------------------------------------- 结果与循环
@dataclass
class RunResult:
    ok: bool
    reason: str
    detail: str = ""
    turns: int = 0
    changed: bool = False
    ledger: Ledger = field(default_factory=Ledger)
    ledger_mismatch: bool = False
    #: 至少一轮服务端没报用量、按字符数估算记账
    usage_estimated: bool = False
    compressions: int = 0
    output_recoveries: int = 0
    protocol_recoveries: int = 0
    recovery_state_releases: int = 0
    elapsed_ms: int = 0


def _tok(text: str) -> int:
    """一段文字的保守 token 估计：ASCII 每 3 字符算 1；非 ASCII（中日韩、emoji…）每个字符算 1——
    它们在各家分词器里常常一个字就是一个甚至多个 token，按 /3 会严重低估。"""
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    return (len(text) - non_ascii) // 3 + non_ascii


def estimate_tokens(system: str, tools: list[ToolSpec], history: list[Msg]) -> int:
    """粗估（偏高估，宁可早压缩）：见 `_tok`。"""
    n = _tok(system) + sum(_tok(t.description) + _tok(json.dumps(t.schema)) for t in tools)
    for m in history:
        n += _tok(m.text)
        if m.turn:
            n += _tok(m.turn.text) + sum(
                _tok(json.dumps(c.args or {}, ensure_ascii=False)) for c in m.turn.tool_calls
            )
            if m.turn.native is not None:
                # 回放时原样带回的原生内容（签名 thinking、加密 reasoning）也占上下文
                n += _tok(json.dumps(m.turn.native, ensure_ascii=False, default=str))
        n += sum(_tok(r.content) for r in m.results)
        # Conservative fallback when the endpoint omits usage. Images must not
        # disappear from the context/total budget just because they aren't text.
        n += sum(img.estimated_tokens for r in m.results for img in r.images)
    return n


def _accountable(usage: Usage, request_tokens: int, turn: Turn | None) -> Usage:
    """服务端没报 / 报了非法计数时，按字符数保守估算（偏高估）记账——缺 usage 不许让预算失效。"""
    if usage.reported:
        return usage
    out = 0
    if turn is not None:
        out = (
            _tok(turn.text)
            + turn.thinking_chars // 3
            + sum(_tok(json.dumps(c.args or {}, ensure_ascii=False)) for c in turn.tool_calls)
            + 1
        )
    return Usage(
        input_tokens=max(usage.input_tokens, request_tokens),
        output_tokens=max(usage.output_tokens, out),
        cached_input_tokens=0,
        reported=False,
    )


def compress(history: list[Msg]) -> int:
    """把早先的 read_file 结果换成「已读过 X」摘要（最近一批工具结果原样留着）。回压缩了几条。"""
    tool_msgs = [m for m in history if m.role == "tool"]
    n = 0
    for m in tool_msgs[:-1]:
        for r in m.results:
            if r.compact is not None and (r.content != r.compact or r.images):
                r.content = r.compact
                r.images = []
                n += 1
    return n


def run(
    model,
    toolset: TaskToolset,
    envelope: RunEnvelope,
    emit: Callable[[dict], None],
    cancel: threading.Event | None = None,
) -> RunResult:
    """跑一次，必发恰好一条 `done`，从不抛。"""
    cancel = cancel or threading.Event()
    t0 = time.monotonic()
    res = RunResult(ok=False, reason="internal")
    try:
        _run(model, toolset, envelope, emit, cancel, t0, res)
    except Exception as exc:  # noqa: BLE001 - 兜底：不抛，只留类型名
        res.ok, res.reason, res.detail = False, "internal", type(exc).__name__
    res.elapsed_ms = int((time.monotonic() - t0) * 1000)
    try:
        res.ledger_mismatch = not res.ledger.check()
    except Exception:  # noqa: BLE001
        res.ledger_mismatch = True
    led = res.ledger
    emit(
        {
            "type": "done",
            "ok": res.ok,
            "reason": res.reason,
            "detail": res.detail,
            "turns": res.turns,
            "changed": res.changed,
            "usage": {
                "input_tokens": led.total_in,
                "output_tokens": led.total_out,
                "cached_input_tokens": led.cached_in,
            },
            "elapsed_ms": res.elapsed_ms,
            "ledger_mismatch": res.ledger_mismatch,
            "usage_estimated": res.usage_estimated,
            "compressions": res.compressions,
            "output_recoveries": res.output_recoveries,
            "protocol_recoveries": res.protocol_recoveries,
            "recovery_state_releases": res.recovery_state_releases,
        }
    )
    return res


def _finish(res: RunResult, ok: bool, reason: str, detail: str = "") -> None:
    res.ok, res.reason, res.detail = ok, reason, detail


def _run(model, toolset, envelope, emit, cancel, t0, res: RunResult) -> None:
    limits: Limits = toolset.budget(envelope)
    try:
        tools = toolset.tools(envelope)
    except ValueError as exc:
        return _finish(res, False, "internal", getattr(exc, "code", "bad_target"))
    by_name = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    system = toolset.system_prompt(envelope) + "\n\n" + RECOVERY_GUIDANCE
    history: list[Msg] = [Msg("user", text=toolset.first_message(envelope))]
    deadline = t0 + limits.max_seconds
    led = res.ledger
    streak_key, streak = None, 0
    budget_notice_level = 0
    truncated_streak = 0
    invalid_identity_streak = 0

    while True:
        if cancel.is_set():
            return _finish(res, False, "cancelled")
        if res.turns >= limits.max_turns:
            return _finish(res, False, "max_turns")
        if time.monotonic() >= deadline or led.total_in + led.total_out >= limits.max_total_tokens:
            return _finish(
                res, False, "budget", "time" if time.monotonic() >= deadline else "tokens"
            )
        hint = getattr(toolset, "budget_hint", "")
        if hint:
            used = led.total_in + led.total_out
            spent = max(
                used / limits.max_total_tokens,
                res.turns / limits.max_turns,
                (time.monotonic() - t0) / limits.max_seconds,
            )
            level = 2 if spent >= 0.75 else 1 if spent >= 0.5 else 0
            if level > budget_notice_level:
                budget_notice_level = level
                history.append(
                    Msg(
                        "user",
                        text=(
                            "Tavotto runtime budget: "
                            f"{limits.max_turns - res.turns} model requests, "
                            f"{limits.max_total_tokens - used} cumulative tokens and "
                            f"{max(0, int(deadline - time.monotonic()))} seconds remain. " + hint
                        ),
                    )
                )
        if estimate_tokens(system, specs, history) > limits.compress_at_tokens:
            res.compressions += getattr(model, "compress_history", compress)(history)

        request_tokens = estimate_tokens(system, specs, history)
        started = time.monotonic()
        try:
            turn: Turn = model.complete(
                system=system,
                tools=specs,
                history=history,
                max_output_tokens=limits.max_output_tokens,
                cancel=cancel,
                deadline=deadline,
            )
        except RunnerFailure as exc:
            if exc.usage is not None:
                # 2xx 但终止于拒答 / 失败状态：这次请求照样计费，先入账再结束
                exc.usage = _accountable(exc.usage, request_tokens, None)
                res.usage_estimated |= not exc.usage.reported
                res.turns += 1
                turn_ms = int((time.monotonic() - started) * 1000)
                led.add(exc.usage, turn_ms)
                emit(
                    {
                        "type": "usage",
                        "turn": res.turns,
                        "input_tokens": exc.usage.input_tokens,
                        "output_tokens": exc.usage.output_tokens,
                        "cached_input_tokens": exc.usage.cached_input_tokens,
                        "ms": turn_ms,
                        "reported": exc.usage.reported,
                        "ok": False,
                    }
                )
            return _finish(res, False, exc.reason, exc.detail)
        turn.usage = _accountable(turn.usage, request_tokens, turn)
        res.usage_estimated |= not turn.usage.reported
        res.turns += 1
        turn_ms = int((time.monotonic() - started) * 1000)
        led.add(turn.usage, turn_ms)
        emit(
            {
                "type": "usage",
                "turn": res.turns,
                "input_tokens": turn.usage.input_tokens,
                "output_tokens": turn.usage.output_tokens,
                "cached_input_tokens": turn.usage.cached_input_tokens,
                # 执行追踪（ADR 0151）：这一轮请求的时长与用量是否服务端报的（否 = 字符数保守估算）
                "ms": turn_ms,
                "reported": turn.usage.reported,
            }
        )
        # 每轮记账后立刻复查：超额的回复不接受、其工具不执行。终止答复（end 且无工具调用）恰好用满上限
        # 仍算完成（`>` 才拒）；要开下一轮 / 要执行工具的回复等于上限也拦（`>=`）。
        terminal = not turn.tool_calls and turn.stop == "end"
        used = led.total_in + led.total_out
        # 被截断的一轮先判截断（结局 output_truncated），不被累计用量盖成 tokens
        if turn.stop != "max_tokens" and (
            used > limits.max_total_tokens or (not terminal and used >= limits.max_total_tokens)
        ):
            return _finish(res, False, "budget", "tokens")
        if cancel.is_set():  # 请求期间被取消，或上面的 usage 输出遇到 BrokenPipe
            return _finish(res, False, "cancelled")
        if turn.thinking_chars:
            emit({"type": "thinking", "chars": turn.thinking_chars})
        # 被截断的半截回答、以及停止原因不合法（未知 / 声称调工具却没有可用调用）的那一轮，都不把文字给用户看
        identity_error = _call_identity_error(turn.tool_calls)
        rejected_stop = (
            turn.stop == "max_tokens"
            or identity_error
            or (not turn.tool_calls and turn.stop != "end")
        )
        if turn.text and not rejected_stop:
            emit({"type": "text", "text": turn.text})
        if cancel.is_set():
            return _finish(res, False, "cancelled")
        if turn.stop == "max_tokens":  # 被截断的一轮：带不带工具调用都一个不执行
            truncated_streak += 1
            hint = getattr(toolset, "truncation_hint", "")
            if (
                truncated_streak < limits.same_error_limit
                and used < limits.max_total_tokens
                and res.turns < limits.max_turns
                and time.monotonic() < deadline
            ):
                # Interrupted calls/text never run or reach the user. The wire
                # may retain replayable reasoning so the next request need not
                # spend the same output budget starting over from nothing.
                res.output_recoveries += 1
                continue_turn = getattr(model, "truncated_continuation", lambda turn: None)(turn)
                if continue_turn is not None:
                    history.append(Msg("assistant", turn=continue_turn, recovery_state=True))
                history.append(Msg("user", text=TRUNCATION_FEEDBACK + hint))
                continue
            return _finish(res, False, "budget", "output_truncated")
        truncated_streak = 0
        if identity_error:
            invalid_identity_streak += 1
            if invalid_identity_streak >= limits.same_error_limit:
                return _finish(res, False, "network", identity_error)
            res.protocol_recoveries += 1
            history.append(
                Msg(
                    "user",
                    text=(
                        "Tavotto runtime rejected the entire response because tool call identities were missing "
                        "or duplicated. No tool calls ran and no file changed. Reissue the next intended call "
                        "using the advertised tool schema; each call must have a nonempty, unique identity."
                    ),
                )
            )
            continue
        invalid_identity_streak = 0
        history.append(Msg("assistant", turn=turn))
        if not turn.tool_calls:
            if turn.stop != "end":  # 未知停止原因 / 声称要调工具却没有可用调用：不是完成
                return _finish(res, False, "network", f"bad_stop_{turn.stop_value or turn.stop}")
            return _finish(res, True, "completed")

        results: list[ToolResult] = []
        for call in turn.tool_calls:
            if cancel.is_set():  # 每个工具调用前都查：已取消就不再写文件
                return _finish(res, False, "cancelled")
            r = _execute(by_name, call)
            results.append(r)
            res.changed = res.changed or r.mutated
            emit(
                {
                    "type": "tool",
                    "name": call.name if call.name in by_name else "unknown",
                    "ok": not r.is_error,
                    "summary": r.summary,
                }
            )
            if cancel.is_set():
                return _finish(res, False, "cancelled")
            if r.completed and not r.is_error:
                # The user-facing result still passes through the normal
                # redacting emitter. Do not make another model request, or
                # execute any later calls that could change the checked bytes.
                if time.monotonic() >= deadline:
                    return _finish(res, False, "budget", "time")
                if r.completion_text:
                    emit({"type": "text", "text": r.completion_text})
                if cancel.is_set():
                    return _finish(res, False, "cancelled")
                return _finish(res, True, "completed")
            # Distinct argument corrections may share an error code. Stop a
            # repeated failed action, not three genuine attempts to repair it.
            args_digest = hashlib.sha256(
                json.dumps(call.args, sort_keys=True, ensure_ascii=True).encode()
            ).digest()
            key = (call.name, r.code, args_digest) if r.is_error else None
            streak = streak + 1 if key is not None and key == streak_key else (1 if key else 0)
            streak_key = key
            if streak >= limits.same_error_limit:
                history.append(Msg("tool", results=results))
                return _finish(res, False, "tool_error_loop", r.code)
        history.append(Msg("tool", results=results))
        if any(not r.is_error for r in results):
            res.recovery_state_releases += getattr(
                model, "release_recovery_state", lambda history: 0
            )(history)


def _call_identity_error(calls: list[ToolCall]) -> str:
    if any(not (isinstance(call.id, str) and call.id.strip()) for call in calls):
        return "tool_call_without_id"
    if len({call.id for call in calls}) != len(calls):
        return "duplicate_tool_call_id"
    return ""


def _execute(by_name: dict[str, toolmod.Tool], call: ToolCall) -> ToolResult:
    tool = by_name.get(call.name)
    if tool is None:
        return ToolResult(
            call.id,
            call.name,
            f"Unknown tool. Available tools: {', '.join(sorted(by_name))}.",
            is_error=True,
            code="unknown_tool",
            summary="error: unknown_tool",
        )
    if call.args is None:
        return ToolResult(
            call.id,
            call.name,
            "Tool arguments were not a valid JSON object. " + _argument_hint(tool, None),
            is_error=True,
            code="bad_arguments",
            summary="error: bad_arguments",
        )
    try:
        r = tool.handler(call.args)
    except Exception as exc:  # noqa: BLE001 - 工具实现的缺陷也不抛出循环；只报类型名
        r = ToolResult(
            call.id,
            call.name,
            f"The tool failed unexpectedly ({type(exc).__name__}).",
            is_error=True,
            code="tool_crashed",
            summary="error: tool_crashed",
        )
    r.call_id, r.name = call.id, call.name
    if r.code == "bad_arguments" and not r.mutated:
        # Handlers remain the only validation authority. Explain their
        # rejection using the advertised schema, without guessing unknown keys or
        # echoing model-supplied keys/values (which may contain private text).
        r.content += " " + _argument_hint(tool, call.args)
    return r


def _argument_hint(tool: toolmod.Tool, args: dict | None) -> str:
    props = tool.schema.get("properties", {})
    required = tool.schema.get("required", [])
    missing = [key for key in required if args is None or key not in args]
    types = {key: spec.get("type", "value") for key, spec in props.items()}
    prefix = f"Missing required keys: {', '.join(missing)}. " if missing else ""
    return (
        prefix
        + "Accepted keys and types: "
        + json.dumps(types, ensure_ascii=True)
        + ". Use these exact parameter names and actual values; do not rename keys. "
        "Resend one corrected call. This rejected call made no changes."
    )
