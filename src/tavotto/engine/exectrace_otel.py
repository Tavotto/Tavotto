"""Execution Trace -> OTLP/JSON 转换（ADR 0151 PR4 §4.8）。纯标准库，只在 Flask 父进程；只有函数，**没有**网络导出、没有 collector。

目标格式：OTLP/JSON `ExportTraceServiceRequest`，对应 opentelemetry-proto v1.x 的 `collector/trace/v1` + `trace/v1`
（字段名 lowerCamelCase；64 位整数写成十进制字符串；枚举写整数——proto3 JSON 映射规定发送方写整数，接收方两种都认）。
GenAI 语义约定：`gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` 是 OTel GenAI semantic conventions（1.27 起由
`prompt_tokens` / `completion_tokens` 改名而来，稳定性仍是 Development）里登记的名字。本沙箱没有联网核对，名字来自该约定
的已发布版本；若上游再改名只需改 `_GENAI_INPUT` / `_GENAI_OUTPUT` 两个常量。其余用量事实（来源、缓存、费用）一律
放 `tavotto.usage.*`，约定里没有对应物。

字段映射：

| Tavotto | OTLP |
| --- | --- |
| trace_id（32 hex） / span_id（16 hex） / parent_span_id | traceId / spanId / parentSpanId（原样；形状不对就改用 sha256 稳定派生并记 notes） |
| started_at + 偏移 | startTimeUnixNano / endTimeUnixNano（整数毫秒 + 偏移纳秒，不经浮点秒） |
| name | name（闭集阶段名） |
| kind | 一律 SPAN_KIND_INTERNAL(1)；Tavotto 的 kind 放 `tavotto.span.kind` |
| status ok | STATUS_CODE_OK(1) |
| status failed / timeout | STATUS_CODE_ERROR(2)，message = error_code（code 形状，非自由文本） |
| status cancelled / unknown / interrupted / running | STATUS_CODE_UNSET(0)，原值放 `tavotto.status`（**不**映成 OK） |
| error_code / attempt / timing | `tavotto.error_code` / `tavotto.attempt` / `tavotto.timing` |
| attributes | `tavotto.<键>`（白名单已在源头校验；这里再只放 bool/int/float 与短 code 形状字符串） |
| usage | `gen_ai.usage.input_tokens` / `output_tokens` + `tavotto.usage.*` |
| events | Span.events（timeUnixNano、name、`tavotto.seq` 与事件 attributes），挂在其 span_id 上 |
| Trace 级事实 | 根 Span 的 `tavotto.trace.*` 与 resource 的 `tavotto.*` |

不支持 / 有损（同样逐条返回在 `notes`）：
* 运行中 Span：省略 endTimeUnixNano（proto 里 0 = 未结束），加 `tavotto.running=true`；
* `timing=reported/unmeasured/point` 的起点是倒推 / 推断的，只在 `tavotto.timing` 里说明，OTLP 没有对应概念；
* 点 Span（start==end）导出为零宽 Span，不转 Span Event；
* `first_failure`、`truncated`、`dropped_count`、`rejected_count`、`late_count`、`clock_anomalies`、`internal_errors`、
  `complete` 只在根 Span 的属性里，OTLP 没有等价物；被丢的中间事件无法表示；
* 墙钟只有毫秒精度（`started_at` 已取整）；偏移是单调钟，跨进程对齐不保证；
* `links`、`traceState`、`flags`、`droppedAttributesCount` 不输出；Span.kind 不区分 CLIENT/SERVER；
* `interrupted` 在 OTel 里读不出「错误」——按维护者要求不改 Tavotto 的状态语义，读 `tavotto.status`。
"""

from __future__ import annotations

import hashlib
import re

SCOPE_NAME = "tavotto.exectrace"

SPAN_KIND_INTERNAL = 1
STATUS_CODE_UNSET = 0
STATUS_CODE_OK = 1
STATUS_CODE_ERROR = 2

_GENAI_INPUT = "gen_ai.usage.input_tokens"
_GENAI_OUTPUT = "gen_ai.usage.output_tokens"

_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_STR = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")


def _kv(key: str, value) -> dict | None:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return {"key": key, "value": {"doubleValue": value}}
    if isinstance(value, str) and _SAFE_STR.fullmatch(value):
        return {"key": key, "value": {"stringValue": value}}
    return None  # None / 容器 / 自由字符串：不导出


def _attrs(pairs) -> list[dict]:
    out = []
    for key, value in pairs:
        item = _kv(key, value)
        if item is not None:
            out.append(item)
    return out


def _derive(raw, width: int, note_label: str, notes: list[str]) -> str:
    digest = hashlib.sha256(f"{note_label}:{raw}".encode("utf-8", "replace")).hexdigest()
    notes.append(f"{note_label}_id_rederived")
    return digest[:width]


def _id(raw, pattern, width, label, notes) -> str:
    if isinstance(raw, str) and pattern.fullmatch(raw) and set(raw) != {"0"}:
        return raw
    return _derive(raw, width, label, notes)


def _ns(started_ms: int, offset_ms) -> int:
    return started_ms * 1_000_000 + int(round(float(offset_ms) * 1_000_000))


def _status(span: dict) -> dict:
    status = span.get("status")
    if status == "ok":
        return {"code": STATUS_CODE_OK}
    if status in ("failed", "timeout"):
        out = {"code": STATUS_CODE_ERROR}
        code = span.get("error_code")
        if isinstance(code, str) and _KEY.fullmatch(code):
            out["message"] = code
        return out
    return {"code": STATUS_CODE_UNSET}


def _span_attributes(span: dict) -> list[dict]:
    pairs: list = [
        ("tavotto.span.kind", span.get("kind")),
        ("tavotto.status", span.get("status")),
        ("tavotto.timing", span.get("timing")),
        ("tavotto.attempt", span.get("attempt")),
        ("tavotto.error_code", span.get("error_code")),
    ]
    if span.get("status") == "running":
        pairs.append(("tavotto.running", True))
    attrs = span.get("attributes")
    for key, value in sorted(attrs.items()) if isinstance(attrs, dict) else ():
        if isinstance(key, str) and _KEY.fullmatch(key):
            pairs.append((f"tavotto.{key}", value))
    usage = span.get("usage")
    if isinstance(usage, dict):
        # 标准的 gen_ai 用量只给服务端报的；字符数估算的不冒充标准字段，放 tavotto.usage.estimated_*
        reported = usage.get("source") == "reported"
        prefix = "" if reported else "tavotto.usage.estimated_"
        pairs += [
            (_GENAI_INPUT if reported else prefix + "input_tokens", usage.get("input_tokens")),
            (_GENAI_OUTPUT if reported else prefix + "output_tokens", usage.get("output_tokens")),
            ("tavotto.usage.source", usage.get("source")),
            ("tavotto.usage.cached_input_tokens", usage.get("cached_input_tokens")),
        ]
        cost = usage.get("cost")
        if isinstance(cost, dict):
            pairs += [
                ("tavotto.usage.cost.amount", cost.get("amount")),
                ("tavotto.usage.cost.currency", cost.get("currency")),
                ("tavotto.usage.cost.pricing_version", cost.get("pricing_version")),
                ("tavotto.usage.cost.estimated", cost.get("estimated")),
            ]
    return _attrs(pairs)


def to_otlp_json(snapshot: dict) -> dict:
    """一条 Trace 快照（`ExecTrace.snapshot()` / 落盘记录里的 `trace`）-> `ExportTraceServiceRequest`（JSON 可序列化的 dict）。

    纯函数、缺字段按未知处理、不抛；返回值严格是 OTLP 形状。有损项的 `notes` 经 `to_otlp_json_with_notes` 取。
    """
    return to_otlp_json_with_notes(snapshot)[0]


def to_otlp_json_with_notes(snapshot: dict) -> tuple[dict, list[str]]:
    notes: list[str] = []
    snap = snapshot if isinstance(snapshot, dict) else {}
    trace_id = _id(snap.get("trace_id"), _TRACE_ID, 32, "trace", notes)
    started = snap.get("started_at")
    started_ms = int(round(float(started) * 1000)) if isinstance(started, (int, float)) else 0
    if not started_ms:
        notes.append("started_at_missing")

    id_map: dict[str, str] = {}
    spans_in = [s for s in (snap.get("spans") or []) if isinstance(s, dict)]
    for s in spans_in:
        raw = s.get("span_id")
        id_map[raw] = _id(raw, _SPAN_ID, 16, "span", notes)

    root_id = snap.get("root_span_id")
    spans_out = []
    events_by_span: dict[str, list[dict]] = {}
    for ev in snap.get("events") or []:
        if not isinstance(ev, dict):
            continue
        target = ev.get("span_id") if ev.get("span_id") in id_map else root_id
        pairs = [("tavotto.seq", ev.get("seq"))]
        eattrs = ev.get("attributes")
        for key, value in sorted(eattrs.items()) if isinstance(eattrs, dict) else ():
            if isinstance(key, str) and _KEY.fullmatch(key):
                pairs.append((f"tavotto.{key}", value))
        name = ev.get("name") if isinstance(ev.get("name"), str) else "event"
        events_by_span.setdefault(target, []).append(
            {
                "timeUnixNano": str(_ns(started_ms, ev.get("at_offset_ms") or 0)),
                "name": name,
                "attributes": _attrs(pairs),
            }
        )

    for s in spans_in:
        start = _ns(started_ms, s.get("start_offset_ms") or 0)
        end_off = s.get("end_offset_ms")
        out = {
            "traceId": trace_id,
            "spanId": id_map[s.get("span_id")],
            "name": s.get("name") if isinstance(s.get("name"), str) else "span",
            "kind": SPAN_KIND_INTERNAL,
            "startTimeUnixNano": str(start),
            "attributes": _span_attributes(s),
            "status": _status(s),
        }
        parent = s.get("parent_span_id")
        if parent in id_map:
            out["parentSpanId"] = id_map[parent]
        if end_off is not None:
            out["endTimeUnixNano"] = str(max(start, _ns(started_ms, end_off)))
        else:
            notes.append("running_span_without_end")
        evs = events_by_span.get(s.get("span_id"))
        if evs:
            out["events"] = evs
        if s.get("span_id") == root_id:
            trace_pairs = [
                ("tavotto.trace.truncated", snap.get("truncated")),
                ("tavotto.trace.complete", snap.get("complete")),
                ("tavotto.trace.dropped_count", snap.get("dropped_count")),
                ("tavotto.trace.rejected_count", snap.get("rejected_count")),
                ("tavotto.trace.late_count", snap.get("late_count")),
                ("tavotto.trace.clock_anomalies", snap.get("clock_anomalies")),
                ("tavotto.trace.internal_errors", snap.get("internal_errors")),
            ]
            ff = snap.get("first_failure")
            if isinstance(ff, dict):
                trace_pairs += [
                    ("tavotto.trace.first_failure.name", ff.get("name")),
                    ("tavotto.trace.first_failure.error_code", ff.get("error_code")),
                    ("tavotto.trace.first_failure.attempt", ff.get("attempt")),
                ]
            out["attributes"] += _attrs(trace_pairs)
        spans_out.append(out)

    resource = _attrs(
        [
            ("service.name", "tavotto"),
            ("tavotto.trace.schema_version", snap.get("schema_version")),
            ("tavotto.operation_kind", snap.get("operation_kind")),
        ]
    )
    if snap.get("truncated"):
        notes.append("trace_truncated_dropped_middle_not_representable")
    request = {
        "resourceSpans": [
            {
                "resource": {"attributes": resource},
                "scopeSpans": [{"scope": {"name": SCOPE_NAME}, "spans": spans_out}],
            }
        ]
    }
    return request, sorted(set(notes))


__all__ = ["to_otlp_json", "to_otlp_json_with_notes", "SPAN_KIND_INTERNAL"]
