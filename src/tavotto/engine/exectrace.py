"""Execution Trace —— 跨任务的层级执行追踪：Trace / Span / Event 契约、生命周期与有界本地存储（ADR 0151）。

它是**观察层**，不是业务状态机：任务状态仍归 `ai_tasks` / `ai_history`，工具执行仍归 `fixtools.run_tool`，
诊断快照仍归 `taskdiag`。这里只把那些模块已经产生的事实按父子关系、并行关系与时间记下来，回答
「做了什么、哪里先坏、花了多久、重试过没有、记录全不全」。旧的阶段轨迹 `engine/trace.py`（ADR 0071）
一行不动，经 `observe_stage_trace()` 只读映射成观察点——同一个阶段事实只有它一个判定权威。

三条结构性纪律：

* **生产者白名单投影**：Span 名、kind、状态、错误码只收闭集或 `[a-z][a-z0-9_]` 形状；attributes 只收
  `ATTRIBUTES` 登记过的键，值按键的类型校验（计数 / 毫秒 / 布尔 / 闭集词表）。**任何自由字符串都进不来**——
  不是先存再脱敏，是从入口就没有放字符串的位置。词表由生产者模块用代码里的静态注册表扩充
  （`register_vocabulary`），用户数据不是词表。
* **采集失败不影响业务**：公开方法全部吞异常、只计 `internal_errors`；存储不可写就只留内存；
  `TAVOTTO_NO_EXEC_TRACE=1` 整个关掉（返回同接口的空记录器）。
* **时间不伪造**：同进程耗时只用单调钟（可注入）；墙钟只用于跨进程关联与显示；别的进程报来的只有
  时长（`timing="reported"`），只记了时间点的是 `timing="point"`，量不到的是 `unmeasured`——不把两个
  相邻观察点之间的时间算给前一个阶段。

纯标准库，只在 Flask 父进程里用（worker / bridge 侧不 import）。
"""

from __future__ import annotations

import atexit
import functools
import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path

from . import atomicio
from .procalive import pid_alive

SCHEMA_VERSION = 1
ENV_DISABLE = "TAVOTTO_NO_EXEC_TRACE"

# ---------------------------------------------------------------- 闭集

#: 一次根 Trace 对应的用户任务种类。加种类先加到这里（`tests/test_exectrace.py` 钉闭集）。
OPERATION_KINDS = ("ai_task", "fix_session", "preparation", "export")

#: Span 的 kind——Waterfall 用它选画法（agent 是带泳道的父行，model / tool 是叶子，wait 是斜纹）。
SPAN_KINDS = ("task", "phase", "agent", "model", "tool", "wait", "process", "verify")

#: Span 名闭集：稳定的阶段枚举，永不是文件名 / 工具参数 / Prompt。具体工具、Agent 是谁放在
#: attributes 的闭集词表里（`tool` / `agent`），不进名字。
SPAN_NAMES = (
    "task",
    "accept",
    "plan",
    "agent_run",
    "model_request",
    "tool_call",
    "user_wait",
    "env_install",
    "env_resolve",
    "dependency_diagnosis",
    "script_run",
    "render",
    "receipt",
    "validate",
    "correct",
    "commit",
    "refresh",
    "verify",
    "settle",
    "export",
    "execute",
)

#: 终局状态。`running` 只在快照里出现；`unknown` = 没被合法结束（冻结 / 恢复时补上），永不当成功读。
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_TIMEOUT = "timeout"
STATUS_INTERRUPTED = "interrupted"
STATUS_UNKNOWN = "unknown"
END_STATUSES = (
    STATUS_OK,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_TIMEOUT,
    STATUS_INTERRUPTED,
    STATUS_UNKNOWN,
)
_FAILING = (STATUS_FAILED, STATUS_TIMEOUT, STATUS_INTERRUPTED)

#: 时间的来历：本进程单调钟量的 / 别的进程报来的时长 / 只是时间点 / 区间没量。
TIMING_MEASURED = "measured"
TIMING_REPORTED = "reported"
TIMING_POINT = "point"
TIMING_UNMEASURED = "unmeasured"
TIMINGS = (TIMING_MEASURED, TIMING_REPORTED, TIMING_POINT, TIMING_UNMEASURED)

#: Event 名闭集——时间点上的事实，不是第二套状态机（不从 Event 推导任何状态）。
EVENT_NAMES = (
    "span_started",
    "span_ended",
    "tool_requested",
    "tool_returned",
    "approval_granted",
    "approval_denied",
    "approval_timeout",
    "repair_started",
    "verification_result",
    "cancel_requested",
    "retry_allowed",
    "retry_refused",
    "stage",
    "late_event",
)

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_VOCAB_VALUE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_REF = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_MAX_COUNT = 10**9

#: 词表：attributes 里 `enum:<词表>` 类型的取值范围。初值是本模块知道的；生产者用自己代码里的静态注册表
#: 扩充（PR2：`fixtools.REGISTRY` 的工具名、`ai_agents.AGENT_REGISTRY` 的 agent id……）。
_VOCABULARIES: dict[str, set[str]] = {
    "tool": set(),
    "agent": set(),
    "provider": set(),
    "model": set(),
    "tier": set(),
    "approved_by": {"policy", "user", "blocked", "denied", "timeout"},
    "execution": {"safe", "native"},
    "phase": set(),
    "outcome": set(),
    "usage_source": {"reported", "estimated"},
    "pricing_version": set(),
    "currency": {"USD"},
    "refresh": set(),
}

#: attributes 白名单：键 → 类型（`count` 非负整数 / `ms` 非负毫秒 / `flag` 布尔 / `enum:<词表>`）。
#: 不在表里的键整条丢弃并计数。加键先加到这里（测试钉闭集）。
ATTRIBUTES: dict[str, str] = {
    "tool": "enum:tool",
    "agent": "enum:agent",
    "provider": "enum:provider",
    "model": "enum:model",
    "tier": "enum:tier",
    "approved_by": "enum:approved_by",
    "execution": "enum:execution",
    "phase": "enum:phase",
    "outcome": "enum:outcome",
    "opaque": "flag",
    "exit_ok": "flag",
    "files_changed": "count",
    "turns": "count",
    "retries": "count",
    "items": "count",
    "reported_ms": "ms",
    "changed": "flag",
    "refresh": "enum:refresh",
    "checks_failed": "count",
}

# ---------------------------------------------------------------- 上限

MAX_SPANS = 512
MAX_EVENTS = 1024
MAX_DEDUP_KEYS = 4096
CHECKPOINT_S = 2.0
MAX_RECORD_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
RETENTION_S = 7 * 24 * 3600.0
MAX_LIVE = 64

# ---------------------------------------------------------------- 时钟


class SystemClock:
    """墙钟（秒，跨进程关联 / 显示）与单调钟（秒，同进程耗时）分开给。测试注入假钟。"""

    @staticmethod
    def wall() -> float:
        return time.time()

    @staticmethod
    def mono() -> float:
        return time.monotonic()


def enabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip() not in ("1", "true", "yes")


def new_trace_id() -> str:
    """128 位、来自 `secrets` 的不透明 id（与 OpenTelemetry 的 trace-id 等宽）。"""
    return secrets.token_hex(16)


def register_vocabulary(name: str, values) -> None:
    """生产者用**代码里的静态注册表**扩充词表；形状不对的值不收（只有 id 形状能进）。"""
    if name not in _VOCABULARIES:
        raise ValueError(f"词表不存在: {name!r}")
    for value in values:
        if isinstance(value, str) and _VOCAB_VALUE.fullmatch(value):
            _VOCABULARIES[name].add(value)


def vocabulary(name: str) -> frozenset[str]:
    return frozenset(_VOCABULARIES.get(name, ()))


# ---------------------------------------------------------------- 投影（入口校验）


def _code(value) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _ref(value) -> str | None:
    return value if isinstance(value, str) and _REF.fullmatch(value) else None


def _attr_value(kind: str, value):
    if kind == "count":
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_COUNT:
            return value
        return None
    if kind == "ms":
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            if value != value or value == float("inf"):
                return None
            return round(float(value), 3)
        return None
    if kind == "flag":
        return value if isinstance(value, bool) else None
    if kind.startswith("enum:"):
        vocab = _VOCABULARIES.get(kind[5:], ())
        return value if isinstance(value, str) and value in vocab else None
    return None


def project_attributes(attrs) -> tuple[dict, int]:
    """白名单投影：返回 (收下的, 拒掉的条数)。不认识的键、类型不对的值一律丢，不做任何字符串清洗。"""
    if not isinstance(attrs, dict):
        return {}, (1 if attrs is not None else 0)
    kept: dict = {}
    rejected = 0
    for key, value in attrs.items():
        kind = ATTRIBUTES.get(key) if isinstance(key, str) else None
        clean = _attr_value(kind, value) if kind else None
        if clean is None:
            rejected += 1
            continue
        kept[key] = clean
    return kept, rejected


def project_usage(usage) -> dict | None:
    """Token 与费用：没有可信数据就是 None（不是 0）。数字只收非负整数；来源必须说清（服务端报的 / 估算的）。

    费用只在带币种、定价版本时收，且永远带 `estimated=True`——定价表算出来的不是账单。
    """
    if not isinstance(usage, dict):
        return None
    source = _attr_value("enum:usage_source", usage.get("source"))
    if source is None:
        return None
    out: dict = {"source": source}
    for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
        out[key] = _attr_value("count", usage.get(key))
    cost = usage.get("cost")
    out["cost"] = None
    if isinstance(cost, dict):
        amount = cost.get("amount")
        currency = _attr_value("enum:currency", cost.get("currency"))
        version = _attr_value("enum:pricing_version", cost.get("pricing_version"))
        if (
            isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            and 0 <= amount < 1e6
            and currency
            and version
        ):
            out["cost"] = {
                "amount": round(float(amount), 6),
                "currency": currency,
                "pricing_version": version,
                "estimated": True,
            }
    if all(out[k] is None for k in ("input_tokens", "output_tokens", "cached_input_tokens")):
        return None if out["cost"] is None else out
    return out


# ---------------------------------------------------------------- 记录器


def _never_raises(default=None):
    """采集故障不许传到业务里：吞掉、计数、返回安全的默认值。"""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception:  # noqa: BLE001 —— 观察层绝不让业务失败
                try:
                    self._internal_errors += 1
                except Exception:  # noqa: BLE001
                    pass
                return default

        return inner

    return wrap


class ExecTrace:
    """一次根 Trace。线程安全（自己一把锁，不碰业务的锁）；方法永不抛。"""

    def __init__(
        self,
        operation_kind: str,
        *,
        task_ref: str | None = None,
        session_ref: str | None = None,
        scope: str | None = None,
        clock=None,
        store: TraceStore | None = None,
        trace_id: str | None = None,
    ) -> None:
        if operation_kind not in OPERATION_KINDS:
            raise ValueError(f"operation_kind 不在闭集里: {operation_kind!r}")
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._store = store
        self._internal_errors = 0
        self.trace_id = (
            trace_id
            if isinstance(trace_id, str) and _TRACE_ID.fullmatch(trace_id)
            else new_trace_id()
        )
        self.operation_kind = operation_kind
        self.task_ref = _ref(task_ref)
        self.session_ref = _ref(session_ref)
        self.scope = _ref(scope)
        self.started_at = self._clock.wall()
        self._mono0 = self._clock.mono()
        self._last_offset = 0.0
        self._clock_anomalies = 0
        self._spans: OrderedDict[str, dict] = OrderedDict()
        self._events: list[dict] = []
        self._seq = 0
        self._dedup: OrderedDict[str, None] = OrderedDict()
        self._dropped = 0
        self._rejected = 0
        self._first_failure: dict | None = None
        self._status = STATUS_RUNNING
        self._error_code: str | None = None
        self._ended_at: float | None = None
        self._frozen = False
        self._late = 0
        self._last_checkpoint = -1e18
        self.root_span_id = self._new_span(
            "task", "task", parent=None, attempt=1, attrs=None, offset=0.0
        )
        self._checkpoint(force=True)

    # ---- 时钟 ----
    def _offset_ms(self) -> float:
        """相对 Trace 起点的毫秒（单调钟）。单调钟倒退（注入钟 / 虚拟机暂停恢复）时钉在上次的值、记一次异常。"""
        raw = (self._clock.mono() - self._mono0) * 1000.0
        if raw < self._last_offset:
            self._clock_anomalies += 1
            return self._last_offset
        self._last_offset = raw
        return raw

    # ---- 内部 ----
    def _new_span(self, name, kind, *, parent, attempt, attrs, offset) -> str | None:
        if len(self._spans) >= MAX_SPANS:
            self._dropped += 1
            return None
        span_id = secrets.token_hex(8)
        kept, rejected = project_attributes(attrs)
        self._rejected += rejected
        self._spans[span_id] = {
            "span_id": span_id,
            "parent_span_id": parent,
            "name": name,
            "kind": kind,
            "start_offset_ms": round(offset, 3),
            "end_offset_ms": None,
            "duration_ms": None,
            "status": STATUS_RUNNING,
            "error_code": None,
            "attempt": attempt,
            "timing": TIMING_MEASURED,
            "attributes": kept,
            "usage": None,
        }
        return span_id

    def _push_event(self, name, span_id, attrs, offset) -> int:
        self._seq += 1
        kept, rejected = project_attributes(attrs)
        self._rejected += rejected
        event = {
            "seq": self._seq,
            "name": name,
            "span_id": span_id,
            "at_offset_ms": round(offset, 3),
        }
        if kept:
            event["attributes"] = kept
        if len(self._events) >= MAX_EVENTS:
            # 与旧阶段轨迹同一条：丢中间的（第 2 条），头说明从哪开始、尾说明现在在哪
            del self._events[1]
            self._dropped += 1
        self._events.append(event)
        return self._seq

    def _seen(self, dedup_key) -> bool:
        if dedup_key is None:
            return False
        key = str(dedup_key)[:128]
        if key in self._dedup:
            return True
        self._dedup[key] = None
        if len(self._dedup) > MAX_DEDUP_KEYS:
            self._dedup.popitem(last=False)
        return False

    def _note_failure(self, span: dict) -> None:
        if self._first_failure is None and span["status"] in _FAILING:
            # 第一次坏在哪就是哪：后来的连带失败、重试的失败都不盖掉它
            self._first_failure = {
                "span_id": span["span_id"],
                "name": span["name"],
                "error_code": span["error_code"],
                "attempt": span["attempt"],
            }

    def _late_write(self) -> None:
        self._late += 1

    # ---- 生命周期 ----
    @_never_raises()
    def start_span(
        self,
        name: str,
        kind: str,
        *,
        parent: str | None = None,
        attempt: int = 1,
        attributes: dict | None = None,
    ) -> str | None:
        """开一个 Span，返回 id；冻结后 / 超上限 / 名字不在闭集里返回 None（之后对 None 的操作都是空操作）。"""
        if name not in SPAN_NAMES or kind not in SPAN_KINDS:
            self._rejected += 1
            return None
        with self._lock:
            if self._frozen:
                self._late_write()
                return None
            parent_id = parent if parent in self._spans else self.root_span_id
            if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
                attempt = 1
            span_id = self._new_span(
                name,
                kind,
                parent=parent_id,
                attempt=min(attempt, 1000),
                attrs=attributes,
                offset=self._offset_ms(),
            )
            return span_id

    @_never_raises(default=False)
    def end_span(
        self,
        span_id: str | None,
        status: str = STATUS_OK,
        *,
        error_code: str | None = None,
        attributes: dict | None = None,
        usage: dict | None = None,
        reported_duration_ms: float | None = None,
    ) -> bool:
        """合法结束一次。已结束的再结束返回 False、不改任何字段（第一次的结局是证据）。

        `reported_duration_ms`：别的进程报来的时长（例如 agent_runner 的 `ms`）——Span 的起点由结束时刻
        倒推，`timing="reported"`；报来的比实际观察到的区间长时不信它，保留观察到的。
        """
        if span_id is None or status not in END_STATUSES:
            return False
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                return False
            if self._frozen:
                self._late_write()
                return False
            if span["status"] != STATUS_RUNNING:
                return False
            end = max(self._offset_ms(), span["start_offset_ms"])
            span["end_offset_ms"] = round(end, 3)
            reported = _attr_value("ms", reported_duration_ms)
            observed = end - span["start_offset_ms"]
            if reported is not None and reported <= observed:
                span["start_offset_ms"] = round(end - reported, 3)
                span["timing"] = TIMING_REPORTED
            span["duration_ms"] = round(span["end_offset_ms"] - span["start_offset_ms"], 3)
            span["status"] = status
            span["error_code"] = _code(error_code) if status != STATUS_OK else None
            if status in _FAILING and span["error_code"] is None and error_code is not None:
                self._rejected += 1
            kept, rejected = project_attributes(attributes)
            self._rejected += rejected
            span["attributes"].update(kept)
            span["usage"] = project_usage(usage)
            self._note_failure(span)
        self._checkpoint()
        return True

    @_never_raises()
    def point(
        self,
        name: str,
        kind: str,
        status: str,
        *,
        parent: str | None = None,
        error_code: str | None = None,
        attributes: dict | None = None,
    ) -> str | None:
        """只知道「这一刻发生了」的事实：零宽、`timing="point"`，不推算区间。"""
        span_id = self.start_span(name, kind, parent=parent, attributes=attributes)
        if span_id is None:
            return None
        with self._lock:
            span = self._spans[span_id]
            span["timing"] = TIMING_POINT
        self.end_span(span_id, status, error_code=error_code)
        with self._lock:
            span["end_offset_ms"] = span["start_offset_ms"]
            span["duration_ms"] = 0.0
        return span_id

    @_never_raises()
    def reported(
        self,
        name: str,
        kind: str,
        status: str,
        *,
        duration_ms,
        parent: str | None = None,
        attempt: int = 1,
        error_code: str | None = None,
        attributes: dict | None = None,
        usage: dict | None = None,
    ) -> str | None:
        """事后才知道的区间：别的进程报来「刚才那件事花了 N 毫秒」（agent_runner 的一轮模型请求）。Span 结束在**收到
        的这一刻**，起点倒推 N 毫秒、但不早于父 Span 的起点（不让子越出父）；`timing="reported"`。时长不合法就记成 point。"""
        span_id = self.start_span(name, kind, parent=parent, attempt=attempt, attributes=attributes)
        if span_id is None:
            return None
        self.end_span(span_id, status, error_code=error_code, usage=usage)
        ms = _attr_value("ms", duration_ms)
        with self._lock:
            span = self._spans[span_id]
            end = (
                span["end_offset_ms"]
                if span["end_offset_ms"] is not None
                else span["start_offset_ms"]
            )
            if ms is None:
                span["timing"] = TIMING_POINT
                span["start_offset_ms"] = span["end_offset_ms"] = end
            else:
                floor = self._spans.get(span["parent_span_id"], {}).get("start_offset_ms", 0.0)
                span["start_offset_ms"] = round(max(floor, end - ms), 3)
                span["timing"] = TIMING_REPORTED
            span["duration_ms"] = round(end - span["start_offset_ms"], 3)
        return span_id

    @_never_raises(default=0)
    def event(
        self,
        name: str,
        *,
        span_id: str | None = None,
        attributes: dict | None = None,
        dedup_key: str | None = None,
    ) -> int:
        """记时间点事实，返回序号（0 = 没记：重复送达 / 冻结后迟到 / 名字不在闭集）。"""
        if name not in EVENT_NAMES:
            self._rejected += 1
            return 0
        with self._lock:
            if self._frozen:
                self._late_write()
                return 0
            if self._seen(dedup_key):
                return 0
            target = span_id if span_id in self._spans else self.root_span_id
            return self._push_event(name, target, attributes, self._offset_ms())

    @_never_raises(default=False)
    def finish(
        self, status: str, *, error_code: str | None = None, attributes: dict | None = None
    ) -> bool:
        """冻结终局。还开着的 Span 不会被解释成成功：取消 / 超时随根，其余一律 `unknown`。只生效一次。

        `attributes` 记在根 Span 上：任务的几件**不同**事实（Agent 退出、文件变没变、刷新结局、任务结局）分开放，
        不合并成一个「成功」。
        """
        if status not in END_STATUSES:
            return False
        with self._lock:
            if self._frozen:
                self._late_write()
                return False
            end = self._offset_ms()
            dangling = {
                STATUS_CANCELLED: STATUS_CANCELLED,
                STATUS_TIMEOUT: STATUS_TIMEOUT,
                STATUS_INTERRUPTED: STATUS_INTERRUPTED,
            }.get(status, STATUS_UNKNOWN)
            for span in self._spans.values():
                if span["status"] != STATUS_RUNNING or span["span_id"] == self.root_span_id:
                    continue
                span["status"] = dangling
                span["error_code"] = "unclosed"
                span["end_offset_ms"] = round(max(end, span["start_offset_ms"]), 3)
                span["duration_ms"] = round(span["end_offset_ms"] - span["start_offset_ms"], 3)
                span["timing"] = TIMING_UNMEASURED
            root = self._spans[self.root_span_id]
            kept, rejected = project_attributes(attributes)
            self._rejected += rejected
            root["attributes"].update(kept)
            root["status"] = status
            root["error_code"] = _code(error_code) if status != STATUS_OK else None
            root["end_offset_ms"] = round(end, 3)
            root["duration_ms"] = round(end, 3)
            self._note_failure(root)
            self._status = status
            self._error_code = root["error_code"]
            self._ended_at = self._clock.wall()
            self._frozen = True
        self._checkpoint(force=True)
        return True

    # ---- 读 ----
    @property
    def frozen(self) -> bool:
        return self._frozen

    def bind_scope(self, scope: str | None) -> None:
        """作业先建、后知道属于哪个项目（导出作业的 `project_id`）：只许绑一次，形状不对不绑。"""
        if self.scope is None:
            self.scope = _ref(scope)

    def snapshot(self) -> dict:
        """只读的公开投影（所有字段都是入口校验过的闭集 / 数字 / 不透明 id，可以原样交给前端）。"""
        try:
            with self._lock:
                return self._snapshot_locked()
        except Exception:  # noqa: BLE001
            self._internal_errors += 1
            return {
                "schema_version": SCHEMA_VERSION,
                "trace_id": self.trace_id,
                "status": STATUS_UNKNOWN,
            }

    def _snapshot_locked(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "operation_kind": self.operation_kind,
            "started_at": round(self.started_at, 3),
            "ended_at": None if self._ended_at is None else round(self._ended_at, 3),
            "status": self._status,
            "error_code": self._error_code,
            "root_span_id": self.root_span_id,
            "task_ref": self.task_ref,
            "session_ref": self.session_ref,
            "first_failure": dict(self._first_failure) if self._first_failure else None,
            "truncated": self._dropped > 0,
            "dropped_count": self._dropped,
            "rejected_count": self._rejected,
            "late_count": self._late,
            "clock_anomalies": self._clock_anomalies,
            "internal_errors": self._internal_errors,
            "complete": self._frozen and self._dropped == 0,
            "spans": [dict(s, attributes=dict(s["attributes"])) for s in self._spans.values()],
            "events": [dict(e) for e in self._events],
            "last_seq": self._seq,
        }

    # ---- 落盘 ----
    def _checkpoint(self, *, force: bool = False) -> None:
        store = self._store
        if store is None:
            return
        try:
            now = self._clock.mono()
            if not force and now - self._last_checkpoint < CHECKPOINT_S:
                return
            self._last_checkpoint = now
            store.submit(self)
        except Exception:  # noqa: BLE001
            self._internal_errors += 1


class NullTrace:
    """`TAVOTTO_NO_EXEC_TRACE=1` 时的替身：同一套接口，什么都不记、什么都不写。"""

    trace_id = None
    root_span_id = None
    frozen = False

    def start_span(self, *a, **k):
        return None

    def end_span(self, *a, **k):
        return False

    def point(self, *a, **k):
        return None

    def reported(self, *a, **k):
        return None

    def event(self, *a, **k):
        return 0

    def finish(self, *a, **k):
        return False

    def snapshot(self):
        return None

    def bind_scope(self, scope):
        return None


# ---------------------------------------------------------------- 旧阶段轨迹的只读适配

_STAGE_STATUS = {"ok": STATUS_OK, "failed": STATUS_FAILED, "cancelled": STATUS_CANCELLED}


def observe_stage_trace(trace, payload, *, parent: str | None = None) -> int:
    """把旧 `Trace.to_payload()` 的阶段事件映射成 `stage` 观察点（Event），返回记下的条数。

    只读 phase / outcome / code 三个字段——`facts` 一个都不读（那是 ADR 0071 给公开投影的标量，不是本层的白名单）；
    不造区间：两个相邻阶段之间的时间不算给前一个。旧轨迹仍是阶段事实的唯一判定权威。
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return 0
    from .trace import OUTCOMES, PHASES

    register_vocabulary("phase", PHASES)
    register_vocabulary("outcome", OUTCOMES)
    seen = 0
    for i, ev in enumerate(payload["events"]):
        if not isinstance(ev, dict):
            continue
        attrs = {"phase": ev.get("phase"), "outcome": ev.get("outcome")}
        if trace.event(
            "stage", span_id=parent, attributes=attrs, dedup_key=f"stage:{i}:{ev.get('phase')}"
        ):
            seen += 1
    return seen


# ---------------------------------------------------------------- 有界本地存储


class TraceStore:
    """`data_dir()/cache/exec-trace/`：每条 Trace 一个 JSON 文件（原子替换写）。

    运行中的叫 `<id>.open.json`（节流 checkpoint，带属主 pid / 进程令牌），冻结后换成 `<id>.json`。启动时
    `recover()` 把属主已经不在的 `.open` 收成 `interrupted`（开着的 Span 记 `unknown`）——不会被解释成成功。
    上限：单条 `MAX_RECORD_BYTES`（超了先砍事件中段、再砍 Span 尾、记 truncated）、总量 `MAX_TOTAL_BYTES`、
    保留 `RETENTION_S`；逐出只动冻结的、按结束时间从旧到新，运行中的永不逐出。不可写 / 损坏只计数，不抛。
    """

    _TOKEN = secrets.token_hex(8)

    def __init__(self, root: Path | None = None, *, background: bool = False) -> None:
        self._root = root
        self._lock = threading.Lock()
        self.write_errors = 0
        self.corrupt = 0
        #: 后台写（进程级记录器用）：`submit()` 只把 Trace 记进待写表、立刻返回；写线程每次取**那一刻**的快照写盘。
        #: 业务线程（可能正持着导出全局锁、AI 请求锁、脚本锁）因此永远不做磁盘 I/O——盘慢 / 卡住只拖慢 Trace 自己。
        self._background = background
        self._pending: OrderedDict[str, ExecTrace] = OrderedDict()
        self._pending_lock = threading.Lock()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._writer: threading.Thread | None = None

    def submit(self, trace: ExecTrace) -> None:
        """要求写一次（合并：同一条 Trace 待写多次只写一次最新的）。同步模式直接写（测试 / 工具用）。"""
        if not self._background:
            self.save(trace)
            return
        with self._pending_lock:
            self._pending[trace.trace_id] = trace
            self._pending.move_to_end(trace.trace_id)
            self._idle.clear()
            if self._writer is None or not self._writer.is_alive():
                self._writer = threading.Thread(
                    target=self._drain, name="tavotto-exectrace-writer", daemon=True
                )
                self._writer.start()
        self._wake.set()

    def _drain(self) -> None:
        while True:
            self._wake.wait(timeout=30.0)
            self._wake.clear()
            while True:
                with self._pending_lock:
                    if not self._pending:
                        self._idle.set()
                        break
                    _, trace = self._pending.popitem(last=False)
                self.save(trace)  # 自己吞异常、计 write_errors

    def flush(self, timeout: float = 5.0) -> bool:
        """等待待写表写空（测试 / 退出时用）；超时回 False，不抛。"""
        if not self._background:
            return True
        return self._idle.wait(timeout)

    @property
    def root(self) -> Path:
        if self._root is None:
            from .config import data_path

            self._root = data_path("cache", "exec-trace")
        return self._root

    def _path(self, trace_id: str, *, open_: bool) -> Path:
        return self.root / (f"{trace_id}.open.json" if open_ else f"{trace_id}.json")

    @staticmethod
    def _encode(record: dict) -> bytes:
        data = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        if len(data) <= MAX_RECORD_BYTES:
            return data
        snap = dict(record["trace"], truncated=True)
        events = list(snap.get("events") or [])
        spans = list(snap.get("spans") or [])
        while len(data) > MAX_RECORD_BYTES and (len(events) > 2 or len(spans) > 1):
            if len(events) > 2:
                drop = max(1, (len(events) - 2) // 2)
                del events[1 : 1 + drop]
                snap["dropped_count"] = snap.get("dropped_count", 0) + drop
            else:
                drop = max(1, (len(spans) - 1) // 2)
                del spans[len(spans) - drop :]
                snap["dropped_count"] = snap.get("dropped_count", 0) + drop
            snap["events"], snap["spans"], snap["complete"] = events, spans, False
            data = json.dumps(
                dict(record, trace=snap), ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        return data

    def save(self, trace: ExecTrace) -> None:
        """写一份快照。终局与否以**快照自己的状态**为准，并在写锁里再核一次：一个在 `finish()` 之前截下的运行中
        快照，到写锁时 Trace 已冻结，就丢掉它——不能拿旧的运行中快照盖掉终局（`.open` 由终局那次删掉）。"""
        snap = trace.snapshot()
        final = snap.get("status") != STATUS_RUNNING
        record = {
            "store_version": 1,
            "scope": trace.scope,
            "owner_pid": os.getpid(),
            "owner_token": self._TOKEN,
            "trace": snap,
        }
        try:
            data = self._encode(record)
            with self._lock:
                if not final and trace.frozen:
                    return
                self.root.mkdir(parents=True, exist_ok=True)
                atomicio.write_bytes(self._path(trace.trace_id, open_=not final), data)
                if final:
                    try:
                        self._path(trace.trace_id, open_=True).unlink()
                    except FileNotFoundError:
                        pass
            if final:
                self.prune()
        except Exception:  # noqa: BLE001
            self.write_errors += 1

    def _read(self, path: Path) -> dict | None:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            self.corrupt += 1
            return None
        if not isinstance(record, dict) or not isinstance(record.get("trace"), dict):
            self.corrupt += 1
            return None
        return record

    def load(self, trace_id: str, *, scope: str | None) -> dict | None:
        """按 id 读一份快照；scope（项目的不透明键）对不上就当不存在——猜 id 读不到别的项目的。"""
        if not isinstance(trace_id, str) or not _TRACE_ID.fullmatch(trace_id):
            return None
        for open_ in (False, True):
            record = self._read(self._path(trace_id, open_=open_))
            if record is None:
                continue
            if record.get("scope") != scope:
                return None
            return record["trace"]
        return None

    def _entries(self):
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        out = []
        for name in names:
            if not name.endswith(".json"):
                continue
            path = self.root / name
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((path, name.endswith(".open.json"), st.st_size, st.st_mtime))
        return out

    def recover(self, *, now: float | None = None) -> int:
        """启动恢复：属主不在了的运行中记录收成 interrupted。属主还活着（别的 Tavotto 进程）的不碰。

        一条记录坏了（JSON 合法但结构不对）只把这一条记成 `corrupt`、跳过，后面的照常恢复——永不外抛。
        """
        recovered = 0
        for path, is_open, _size, _mtime in self._entries():
            if not is_open:
                continue
            try:
                outcome = self._recover_one(path)
            except Exception:  # noqa: BLE001 —— 观察层的恢复不让启动失败
                self.write_errors += 1
                continue
            if outcome == "corrupt":
                self.corrupt += 1
            elif outcome == "recovered":
                recovered += 1
        try:
            self.prune(now=now)
        except Exception:  # noqa: BLE001
            self.write_errors += 1
        return recovered

    def _recover_one(self, path: Path) -> str:
        record = self._read(path)
        if record is None:
            return "skipped"
        pid = record.get("owner_pid")
        mine = pid == os.getpid() and record.get("owner_token") == self._TOKEN
        if mine or (isinstance(pid, int) and pid != os.getpid() and pid_alive(pid)):
            return "skipped"
        snap = record["trace"]
        trace_id = snap.get("trace_id")
        spans = snap.get("spans")
        if (
            not isinstance(trace_id, str)
            or not _TRACE_ID.fullmatch(trace_id)
            or not isinstance(spans, list)
            or not all(isinstance(span, dict) for span in spans)
        ):
            return "corrupt"
        for span in spans:
            if span.get("status") == STATUS_RUNNING:
                is_root = span.get("kind") == "task"
                span["status"] = STATUS_INTERRUPTED if is_root else STATUS_UNKNOWN
                span["error_code"] = "interrupted" if is_root else "unclosed"
                span["timing"] = TIMING_UNMEASURED
        snap["status"] = STATUS_INTERRUPTED
        snap["error_code"] = "interrupted"
        snap["complete"] = False
        snap["ended_at"] = None
        if not snap.get("first_failure"):
            snap["first_failure"] = {
                "span_id": snap.get("root_span_id"),
                "name": "task",
                "error_code": "interrupted",
                "attempt": 1,
            }
        record["recovered"] = True
        atomicio.write_bytes(self._path(trace_id, open_=False), self._encode(record))
        path.unlink()
        return "recovered"

    def prune(self, *, now: float | None = None) -> int:
        """过期与总量：只逐出冻结的，最旧的先走；运行中的（`.open`）不在候选里。"""
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            entries = self._entries()
            frozen = sorted((e for e in entries if not e[1]), key=lambda e: e[3])
            total = sum(e[2] for e in entries)
            for path, _open, size, mtime in frozen:
                if now - mtime <= RETENTION_S and total <= MAX_TOTAL_BYTES:
                    break
                try:
                    path.unlink()
                    removed += 1
                    total -= size
                except OSError:
                    self.write_errors += 1
        return removed


# ---------------------------------------------------------------- 进程内入口


class Recorder:
    """进程内的活跃 Trace 表（有界）+ 存储。业务只经 `begin()` 拿 Trace；关掉时拿到 `NullTrace`。"""

    def __init__(self, store: TraceStore | None = None, *, clock=None) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._live: OrderedDict[str, ExecTrace] = OrderedDict()

    def begin(self, operation_kind: str, **kwargs):
        if not enabled():
            return NullTrace()
        try:
            trace = ExecTrace(operation_kind, clock=self._clock, store=self._store, **kwargs)
        except Exception:  # noqa: BLE001 —— 连建都建不起来也不影响业务
            return NullTrace()
        with self._lock:
            self._live[trace.trace_id] = trace
            while len(self._live) > MAX_LIVE:
                # 先挤掉已冻结的；全在跑（有调用方忘了 finish）就挤最旧的——内存表有界，它的 checkpoint 仍在盘上
                victim = next((k for k, t in self._live.items() if t.frozen), None)
                del self._live[victim if victim is not None else next(iter(self._live))]
        return trace

    def get(self, trace_id: str, *, scope: str | None) -> dict | None:
        """活跃的先读内存（最新），没有再读盘；scope 对不上当不存在。"""
        with self._lock:
            trace = self._live.get(trace_id)
        if trace is not None:
            return trace.snapshot() if trace.scope == _ref(scope) else None
        if self._store is None:
            return None
        return self._store.load(trace_id, scope=_ref(scope))


def scope_for(project_key) -> str | None:
    """项目的不透明 scope 键：项目 id / 解析后的根路径 → sha256 前 32 位。Trace 里永不出现路径本身。"""
    if not isinstance(project_key, str) or not project_key:
        return None
    import hashlib

    return hashlib.sha256(project_key.encode("utf-8", "surrogatepass")).hexdigest()[:32]


#: 进程级入口：业务模块只经 `begin()` 拿 Trace。存储根延迟到第一次写才解析（`data_dir()` 在测试里被重定向）。
RECORDER = Recorder(TraceStore(background=True))


def _flush_at_exit() -> None:
    # 正常退出时把最后几次终局写完（最多等 1 秒）；被强杀的进程留下的 `.open` 由下次启动 `recover()` 收成 interrupted
    try:
        if RECORDER._store is not None:
            RECORDER._store.flush(1.0)
    except Exception:  # noqa: BLE001
        pass


atexit.register(_flush_at_exit)


def begin(operation_kind: str, **kwargs):
    """开一条根 Trace；关掉时 / 建不起来时回 `NullTrace`（同一套接口，业务代码不必判空）。"""
    return RECORDER.begin(operation_kind, **kwargs)


def recover_on_startup() -> int:
    """服务启动时调一次：属主已不在的运行中记录收成 interrupted、过期的清掉。永不抛。"""
    if not enabled():
        return 0
    try:
        store = RECORDER._store
        return store.recover() if store is not None else 0
    except Exception:  # noqa: BLE001
        return 0


__all__ = [
    "RECORDER",
    "begin",
    "recover_on_startup",
    "scope_for",
    "ATTRIBUTES",
    "END_STATUSES",
    "EVENT_NAMES",
    "ENV_DISABLE",
    "ExecTrace",
    "NullTrace",
    "OPERATION_KINDS",
    "Recorder",
    "SCHEMA_VERSION",
    "SPAN_KINDS",
    "SPAN_NAMES",
    "SystemClock",
    "TIMINGS",
    "TraceStore",
    "enabled",
    "new_trace_id",
    "observe_stage_trace",
    "project_attributes",
    "project_usage",
    "register_vocabulary",
    "vocabulary",
]
