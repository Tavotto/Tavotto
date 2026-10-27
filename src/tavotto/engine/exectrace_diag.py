"""执行追踪的诊断面（ADR 0151 PR4）：对外导出的白名单投影 + 基于 Trace 的故障归因摘要。

两条边界：

* **本地 Trace ≠ 对外导出**。本地快照已经是入口白名单校验过的，但离开本机（诊断包 / 用户导出的单份诊断）再走一遍
  **独立的**投影：逐字段按形状重新校验、不认识的字段一律丢，不递归复制 `snapshot`（与 `taskdiag` 同一条纪律）。不导出
  `task_ref` / `session_ref`（本机任务引用）与 scope；只留 `trace_id`。
* **归因只说证据说得出的话**。「最早失败的阶段」「最长耗时的阶段」「最终失败的任务」是三件事，分开给；无法确定根因就是
  `unknown`，绝不从「最慢」推出根因，也不生成貌似合理的解释。

不采集、不探测、不重跑：只读调用方给的快照。Task Diagnostic（`taskdiag`）仍是任务诊断的权威，这里是与它并列的时序证据。
纯标准库，Flask 父进程侧。
"""

from __future__ import annotations

import json
import re

from . import exectrace

DOCUMENT_SCHEMA = "tavotto.exec-trace-diagnostic"
DOCUMENT_VERSION = 1
#: 诊断包里放几条（失败在前）与整节的字节上限（超了从尾部丢、记 truncated）
BUNDLE_TRACES = 8
MAX_SECTION_BYTES = 192 * 1024
MAX_CASCADE = 8

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_FAILING = (exectrace.STATUS_FAILED, exectrace.STATUS_TIMEOUT, exectrace.STATUS_INTERRUPTED)
_TRACE_STATUSES = (exectrace.STATUS_RUNNING, *exectrace.END_STATUSES)
#: 导出时保留的 Event 名（时间点证据）；其余（span_started 之类的冗余）不出门
_EXPORT_EVENTS = {
    "stage",
    "approval_granted",
    "approval_denied",
    "approval_timeout",
    "cancel_requested",
    "retry_allowed",
    "retry_refused",
    "verification_result",
}


# ---------------------------------------------------------------- 形状校验（出门的第二道）


def _code(v):
    return v if isinstance(v, str) and _CODE.fullmatch(v) else None


def _closed(v, allowed):
    return v if v in allowed else None


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15:
        return round(float(v), 3)
    return None


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10**9 else None


def _id16(v):
    return v if isinstance(v, str) and _HEX16.fullmatch(v) else None


def _attrs(attrs) -> dict:
    """attributes 按**导出这一刻**的白名单与词表重新校验（`exectrace.project_attributes`，同一份判据）。"""
    kept, _ = exectrace.project_attributes(attrs if isinstance(attrs, dict) else {})
    return kept


def _usage(u):
    return exectrace.project_usage(u) if isinstance(u, dict) else None


def _span(s: dict) -> dict | None:
    sid = _id16(s.get("span_id"))
    if sid is None:
        return None
    return {
        "span_id": sid,
        "parent_span_id": _id16(s.get("parent_span_id")),
        "name": _closed(s.get("name"), exectrace.SPAN_NAMES),
        "kind": _closed(s.get("kind"), exectrace.SPAN_KINDS),
        "start_offset_ms": _num(s.get("start_offset_ms")),
        "end_offset_ms": _num(s.get("end_offset_ms")),
        "duration_ms": _num(s.get("duration_ms")),
        "status": _closed(s.get("status"), _TRACE_STATUSES),
        "error_code": _code(s.get("error_code")),
        "attempt": _count(s.get("attempt")),
        "timing": _closed(s.get("timing"), exectrace.TIMINGS),
        "attributes": _attrs(s.get("attributes")),
        "usage": _usage(s.get("usage")),
    }


def _event(e: dict) -> dict | None:
    name = e.get("name")
    if name not in _EXPORT_EVENTS:
        return None
    return {
        "seq": _count(e.get("seq")),
        "name": name,
        "span_id": _id16(e.get("span_id")),
        "at_offset_ms": _num(e.get("at_offset_ms")),
        "attributes": _attrs(e.get("attributes")),
    }


def export_projection(snapshot: dict) -> dict | None:
    """一份快照 → 可以离开本机的投影（逐字段重校验，不认识的丢）。形状不对回 None。"""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("spans"), list):
        return None
    trace_id = snapshot.get("trace_id")
    if not (isinstance(trace_id, str) and _HEX32.fullmatch(trace_id)):
        return None
    spans = [p for p in (_span(s) for s in snapshot["spans"] if isinstance(s, dict)) if p]
    events = [
        p for p in (_event(e) for e in snapshot.get("events") or [] if isinstance(e, dict)) if p
    ]
    ff = snapshot.get("first_failure") if isinstance(snapshot.get("first_failure"), dict) else None
    return {
        "schema_version": _count(snapshot.get("schema_version")),
        "trace_id": trace_id,
        "operation_kind": _closed(snapshot.get("operation_kind"), exectrace.OPERATION_KINDS),
        "started_at": _num(snapshot.get("started_at")),
        "ended_at": _num(snapshot.get("ended_at")),
        "status": _closed(snapshot.get("status"), _TRACE_STATUSES),
        "error_code": _code(snapshot.get("error_code")),
        "root_span_id": _id16(snapshot.get("root_span_id")),
        "first_failure": (
            {
                "span_id": _id16(ff.get("span_id")),
                "name": _closed(ff.get("name"), exectrace.SPAN_NAMES),
                "error_code": _code(ff.get("error_code")),
                "attempt": _count(ff.get("attempt")),
            }
            if ff
            else None
        ),
        "truncated": snapshot.get("truncated") is True,
        "dropped_count": _count(snapshot.get("dropped_count")) or 0,
        "complete": snapshot.get("complete") is True,
        "recovered": snapshot.get("status") == exectrace.STATUS_INTERRUPTED,
        "spans": spans,
        "events": events,
    }


# ---------------------------------------------------------------- 故障归因


def attribution(snapshot: dict) -> dict:
    """基于 Trace 的结构化诊断摘要。每个结论只来自快照里的事实；说不出的给 `unknown` / None。"""
    spans = [s for s in snapshot.get("spans") or [] if isinstance(s, dict)]
    root_id = snapshot.get("root_span_id")
    status = snapshot.get("status")
    non_root = [s for s in spans if s.get("span_id") != root_id]
    ff = snapshot.get("first_failure") if isinstance(snapshot.get("first_failure"), dict) else None

    failed_task = status in _FAILING
    # ① 最早失败的阶段（根因候选）：只在任务真的失败时给。任务成功了，途中自愈的失败（Agent 调错工具又改正）不是「起源」，
    #    单列在 `recovered`（真实 Codex 验证发现：修好的会话被归因到一次被策略挡下的读取）
    if not failed_task:
        origin = None
    elif ff and ff.get("span_id") != root_id:
        origin = {
            "stage": _closed(ff.get("name"), exectrace.SPAN_NAMES),
            "error_code": _code(ff.get("error_code")),
            "attempt": _count(ff.get("attempt")),
        }
    else:
        origin = {
            "stage": "unknown",
            "error_code": _code(snapshot.get("error_code")),
            "attempt": None,
        }

    # ② 后续连带失败：第一次之后的其他失败（不含根；根的失败是它们的结果）
    failing = [s for s in non_root if s.get("status") in _FAILING]
    first_id = ff.get("span_id") if ff else None
    others = [
        {
            "stage": _closed(s.get("name"), exectrace.SPAN_NAMES),
            "error_code": _code(s.get("error_code")),
        }
        for s in failing
        if s.get("span_id") != first_id or not failed_task
    ][:MAX_CASCADE]
    cascade = others if failed_task else []
    recovered = [] if failed_task else others
    blocked = sum(
        1
        for s in non_root
        if s.get("name") == "tool_call"
        and (s.get("attributes") or {}).get("approved_by") == "blocked"
    )

    # ③ 最长耗时的阶段：单独给，明确它不是根因；算的是**自身**耗时——等用户点卡片的时间（子 user_wait）扣掉，
    #    不把用户在想的那几秒算成工具慢（真实 Codex 验证发现）
    waited: dict[str, float] = {}
    for s in non_root:
        if s.get("name") == "user_wait" and _num(s.get("duration_ms")):
            pid = s.get("parent_span_id")
            waited[pid] = waited.get(pid, 0.0) + s["duration_ms"]
    own = {
        s.get("span_id"): s["duration_ms"] - waited.get(s.get("span_id"), 0.0)
        for s in non_root
        if s.get("kind") not in ("agent", "wait")
        and s.get("timing") in ("measured", "reported")
        and _num(s.get("duration_ms"))
    }
    slowest_id = max(own, key=own.get, default=None)
    slowest = next((s for s in non_root if s.get("span_id") == slowest_id), None)

    names = [s.get("name") for s in spans]
    corrections = names.count("correct")
    changes = sum(
        1
        for s in non_root
        if s.get("name") == "tool_call"
        and (s.get("attributes") or {}).get("tier") in ("tavotto", "listed", "user_env")
        and s.get("status") in (exectrace.STATUS_OK, *_FAILING)
    )
    runs = [s for s in non_root if s.get("name") == "agent_run"]
    retried = sum(1 for s in non_root if (_count(s.get("attempt")) or 1) > 1)
    tool_counts: dict[str, int] = {}
    for s in non_root:
        tool = (s.get("attributes") or {}).get("tool")
        if s.get("name") == "tool_call" and isinstance(tool, str):
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
    repeated = {k: v for k, v in sorted(tool_counts.items()) if v > 1}
    codes = [s.get("error_code") or "" for s in spans] + [snapshot.get("error_code") or ""]
    waits = [s for s in non_root if s.get("name") == "user_wait"]

    # ④ 最终是否由真实证据验证。阶段 Span 的 ok 只说明那一段走完了，**不是**检查通过（Codex #1457）：
    #   AI 任务只认 Task Core 的结局（`completed` = 必需检查全部 verified；其余一律说不出 = unknown）；
    #   准备 / 运行只认真实执行区间 `script_run` 的终局；别的没有执行证据 = unknown。
    root_outcome = next(
        ((s.get("attributes") or {}).get("outcome") for s in spans if s.get("span_id") == root_id),
        None,
    )
    runs_real = [s for s in non_root if s.get("name") == "script_run"]
    if snapshot.get("operation_kind") == "ai_task":
        verified = "verified" if root_outcome == "completed" else "unknown"
    elif snapshot.get("operation_kind") == "fix_session":
        # 修复会话交 `fixed` 之前，产品要求本会话最后一次改动之后的应用执行证据（ADR 0149 修订 2026-10-25）：
        # 结局 fixed 就是那道门过了；其余结局说不出 = unknown（AI 自述或副本运行不算）
        verified = "verified" if root_outcome == "fixed" else "unknown"
    elif runs_real and runs_real[-1].get("status") in (exectrace.STATUS_OK, *_FAILING):
        verified = "verified" if runs_real[-1].get("status") == exectrace.STATUS_OK else "failed"
    else:
        verified = "unknown"

    return {
        "final_status": _closed(status, _TRACE_STATUSES),
        "final_error_code": _code(snapshot.get("error_code")),
        "origin": origin,
        "cascade": cascade,
        "slowest": (
            {
                "stage": _closed(slowest.get("name"), exectrace.SPAN_NAMES),
                "duration_ms": _num(own[slowest_id]),
                "is_cause": False,
            }
            if slowest
            else None
        ),
        "recovered": recovered,
        "blocked_calls": blocked,
        "repair_attempted": corrections > 0
        or changes > 0
        or snapshot.get("operation_kind") == "fix_session",
        # AI 改图：修正轮数；智能修复：动了环境的工具调用数（tavotto / listed / user_env 档）
        "repair_attempts": corrections + changes,
        "agent_runs": len(runs),
        "retried_spans": retried,
        "repeated_tool_calls": repeated,
        "budget_exhausted": any("budget" in c for c in codes),
        "user_refused": any(
            (w.get("attributes") or {}).get("approved_by") == "denied" for w in waits
        ),
        "timed_out": status == exectrace.STATUS_TIMEOUT
        or any(s.get("status") == exectrace.STATUS_TIMEOUT for s in spans),
        "verified_by_execution": verified,
        "complete": snapshot.get("complete") is True,
        "truncated": snapshot.get("truncated") is True,
        "interrupted": status == exectrace.STATUS_INTERRUPTED,
    }


def document(snapshot: dict) -> dict | None:
    """用户主动导出的单份执行追踪诊断：投影 + 归因 + 一句「不完整」的明说。"""
    proj = export_projection(snapshot)
    if proj is None:
        return None
    return {
        "schema": DOCUMENT_SCHEMA,
        "version": DOCUMENT_VERSION,
        "note": (
            None
            if proj["complete"]
            else (
                "interrupted"
                if proj["recovered"]
                else "truncated"
                if proj["truncated"]
                else "incomplete"
            )
        ),
        "attribution": attribution(proj),
        "trace": proj,
    }


def bundle_section(snapshots: list[dict], *, limit: int = BUNDLE_TRACES) -> dict:
    """诊断包的 `exec-traces.json`：最近几条（调用方已按失败在前排好）的导出文档，整节有字节上限。"""
    docs = [d for d in (document(s) for s in snapshots[:limit]) if d is not None]
    truncated = len(snapshots) > limit
    while docs and len(json.dumps(docs, ensure_ascii=False)) > MAX_SECTION_BYTES:
        docs.pop()
        truncated = True
    return {
        "schema": DOCUMENT_SCHEMA,
        "version": DOCUMENT_VERSION,
        "truncated": truncated,
        "traces": docs,
    }


__all__ = [
    "BUNDLE_TRACES",
    "DOCUMENT_SCHEMA",
    "DOCUMENT_VERSION",
    "attribution",
    "bundle_section",
    "document",
    "export_projection",
]
