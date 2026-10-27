"""Execution Trace -> OTLP/JSON（ADR 0151 PR4 §4.8）。判据的主语：`to_otlp_json()` 的返回值（真 ExecTrace 的快照转出来的）。"""

from __future__ import annotations

import json
import re

import pytest

from tavotto.engine import exectrace as xt
from tavotto.engine.exectrace_otel import (
    STATUS_CODE_ERROR,
    STATUS_CODE_OK,
    STATUS_CODE_UNSET,
    to_otlp_json,
    to_otlp_json_with_notes,
)

CANARY = "sk-CANARY-secret-/Users/alice/paper.py-bob@example.com"


class FakeClock:
    def __init__(self) -> None:
        self.w = 1_800_000_000.123
        self.m = 50.0

    def wall(self) -> float:
        return self.w

    def mono(self) -> float:
        return self.m

    def tick(self, ms: float) -> None:
        self.m += ms / 1000.0
        self.w += ms / 1000.0


@pytest.fixture(autouse=True)
def _vocab(monkeypatch):
    monkeypatch.delenv(xt.ENV_DISABLE, raising=False)
    xt.register_vocabulary("tool", ["try_run"])
    xt.register_vocabulary("agent", ["builtin"])
    xt.register_vocabulary("pricing_version", ["p1"])


def _spans(req):
    return req["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _by_name(req, name):
    return [s for s in _spans(req) if s["name"] == name]


def _attr(span, key):
    for item in span["attributes"]:
        if item["key"] == key:
            return next(iter(item["value"].values()))
    return None


def _build(final="ok"):
    clock = FakeClock()
    t = xt.ExecTrace("ai_task", clock=clock, task_ref="abc123def456")
    plan = t.start_span("plan", "phase")
    clock.tick(50)
    t.end_span(plan)
    agent = t.start_span("agent_run", "agent", attributes={"agent": "builtin"})
    m = t.start_span("model_request", "model", parent=agent)
    clock.tick(800)
    t.end_span(
        m,
        usage={
            "source": "reported",
            "input_tokens": 1200,
            "output_tokens": 300,
            "cost": {"amount": 0.01, "currency": "USD", "pricing_version": "p1"},
        },
    )
    t.event("tool_requested", span_id=agent, attributes={"tool": "try_run"})
    tool = t.start_span("tool_call", "tool", parent=agent, attributes={"tool": "try_run"})
    clock.tick(100)
    t.end_span(tool, "failed", error_code="boom_code", attributes={"exit_ok": False})
    t.point("tool_call", "tool", "ok", parent=agent)
    clock.tick(10)
    t.end_span(agent, "cancelled")
    t.start_span("validate", "verify")  # 故意不结束
    if final:
        clock.tick(5)
        t.finish(final)
    return t, clock


def test_ids_shapes_parent_links_and_schema():
    t, _ = _build("ok")
    snap = t.snapshot()
    req = to_otlp_json(snap)
    spans = _spans(req)
    assert len(spans) == len(snap["spans"])
    ids = {s["spanId"] for s in spans}
    assert len(ids) == len(spans)
    for s in spans:
        assert re.fullmatch(r"[0-9a-f]{32}", s["traceId"]) and s["traceId"] == snap["trace_id"]
        assert re.fullmatch(r"[0-9a-f]{16}", s["spanId"])
        assert s["kind"] == 1
        if "parentSpanId" in s:
            assert s["parentSpanId"] in ids
    roots = [s for s in spans if "parentSpanId" not in s]
    assert [r["spanId"] for r in roots] == [snap["root_span_id"]]
    assert req["resourceSpans"][0]["scopeSpans"][0]["scope"]["name"] == "tavotto.exectrace"


def test_timestamps_are_absolute_strings_and_ordered():
    t, _ = _build("ok")
    snap = t.snapshot()
    req = to_otlp_json(snap)
    base = round(snap["started_at"] * 1000) * 1_000_000
    for s in _spans(req):
        assert isinstance(s["startTimeUnixNano"], str)
        start, end = int(s["startTimeUnixNano"]), int(s["endTimeUnixNano"])
        assert base <= start <= end
    root = _by_name(req, "task")[0]
    assert int(root["startTimeUnixNano"]) == base
    # 子 Span 的开始顺序与源偏移一致
    src = {x["span_id"]: x["start_offset_ms"] for x in snap["spans"]}
    got = sorted(_spans(req), key=lambda s: int(s["startTimeUnixNano"]))
    assert [src[s["spanId"]] for s in got] == sorted(src.values())


def test_status_mapping_never_turns_cancelled_or_unknown_into_ok():
    t, _ = _build("cancelled")
    req = to_otlp_json(t.snapshot())
    agent = _by_name(req, "agent_run")[0]
    assert agent["status"]["code"] == STATUS_CODE_UNSET
    assert _attr(agent, "tavotto.status") == "cancelled"
    failed = [s for s in _by_name(req, "tool_call") if _attr(s, "tavotto.status") == "failed"][0]
    assert failed["status"] == {"code": STATUS_CODE_ERROR, "message": "boom_code"}
    assert _attr(failed, "tavotto.error_code") == "boom_code"
    ok = [s for s in _by_name(req, "tool_call") if _attr(s, "tavotto.status") == "ok"][0]
    assert ok["status"]["code"] == STATUS_CODE_OK
    dangling = _by_name(req, "validate")[0]  # 冻结时开着、随根取消
    assert dangling["status"]["code"] == STATUS_CODE_UNSET
    assert _attr(dangling, "tavotto.status") == "cancelled"
    # unknown / interrupted 同样不是 OK
    t2, _ = _build("failed")
    d2 = _by_name(to_otlp_json(t2.snapshot()), "validate")[0]
    assert _attr(d2, "tavotto.status") == "unknown"
    assert d2["status"]["code"] == STATUS_CODE_UNSET
    t3, _ = _build("interrupted")
    d3 = _by_name(to_otlp_json(t3.snapshot()), "validate")[0]
    assert (
        d3["status"]["code"] == STATUS_CODE_UNSET and _attr(d3, "tavotto.status") == "interrupted"
    )


def test_point_span_has_equal_start_and_end():
    t, _ = _build("ok")
    req = to_otlp_json(t.snapshot())
    points = [s for s in _spans(req) if _attr(s, "tavotto.timing") == "point"]
    assert points
    for s in points:
        assert s["startTimeUnixNano"] == s["endTimeUnixNano"]


def test_running_span_omits_end_and_is_marked():
    t, _ = _build(final=None)
    req, notes = to_otlp_json_with_notes(t.snapshot())
    running = _by_name(req, "validate")[0]
    assert "endTimeUnixNano" not in running
    assert _attr(running, "tavotto.running") is True
    assert running["status"]["code"] == STATUS_CODE_UNSET
    assert "running_span_without_end" in notes


def test_attributes_prefixed_usage_and_events():
    t, _ = _build("ok")
    req = to_otlp_json(t.snapshot())
    for s in _spans(req):
        for item in s["attributes"]:
            assert item["key"].startswith(("tavotto.", "gen_ai."))
    model = _by_name(req, "model_request")[0]
    assert model["attributes"][0]["key"].startswith("tavotto.")
    assert _attr(model, "gen_ai.usage.input_tokens") == "1200"
    assert _attr(model, "gen_ai.usage.output_tokens") == "300"
    assert _attr(model, "tavotto.usage.source") == "reported"
    assert _attr(model, "tavotto.usage.cost.estimated") is True
    assert _attr(model, "tavotto.usage.cost.amount") == pytest.approx(0.01)
    tool = [s for s in _by_name(req, "tool_call") if _attr(s, "tavotto.tool")][0]
    assert _attr(tool, "tavotto.tool") == "try_run"
    assert _attr(tool, "tavotto.exit_ok") is False
    agent = _by_name(req, "agent_run")[0]
    assert _attr(agent, "tavotto.agent") == "builtin"
    assert agent["events"][0]["name"] == "tool_requested"
    assert agent["events"][0]["attributes"][0]["key"] == "tavotto.seq"
    root = _by_name(req, "task")[0]
    assert _attr(root, "tavotto.trace.complete") is True
    assert _attr(root, "tavotto.trace.first_failure.error_code") == "boom_code"


def test_no_free_text_canary_and_json_serialisable():
    clock = FakeClock()
    t = xt.ExecTrace("ai_task", clock=clock, task_ref=CANARY)
    s = t.start_span(CANARY, "tool", attributes={"tool": CANARY, "note": CANARY, CANARY: 1})
    t.event(CANARY, span_id=s, attributes={"tool": CANARY})
    t.end_span(
        s, "failed", error_code=CANARY, attributes={"model": CANARY}, usage={"source": CANARY}
    )
    t.finish("failed", error_code=CANARY)
    snap = t.snapshot()
    # 即使快照被篡改塞了自由文本，转换也不带出去
    snap["spans"][0]["attributes"]["evil"] = CANARY
    snap["spans"][0]["attributes"]["tool"] = CANARY
    snap["spans"][0]["error_code"] = CANARY
    text = json.dumps(to_otlp_json(snap))
    assert "CANARY" not in text and "alice" not in text and "example.com" not in text
    assert json.loads(text)["resourceSpans"]


def test_malformed_input_never_raises_and_ids_are_rederived():
    assert to_otlp_json({})["resourceSpans"][0]["scopeSpans"][0]["spans"] == []
    assert to_otlp_json(None)["resourceSpans"]
    snap = {
        "trace_id": "NOT-HEX",
        "started_at": 1.0,
        "root_span_id": "r",
        "spans": [{"span_id": "r", "parent_span_id": None, "name": "task", "start_offset_ms": 0}],
    }
    req, notes = to_otlp_json_with_notes(snap)
    s = _spans(req)[0]
    assert re.fullmatch(r"[0-9a-f]{32}", s["traceId"]) and re.fullmatch(
        r"[0-9a-f]{16}", s["spanId"]
    )
    assert "trace_id_rederived" in notes and "span_id_rederived" in notes


def test_estimated_usage_never_poses_as_standard_genai_usage():
    from tavotto.engine import exectrace, exectrace_otel

    t = exectrace.ExecTrace("ai_task")
    a = t.start_span("model_request", "model")
    t.end_span(a, usage={"source": "estimated", "input_tokens": 10, "output_tokens": 2})
    b = t.start_span("model_request", "model")
    t.end_span(b, usage={"source": "reported", "input_tokens": 7, "output_tokens": 1})
    t.finish("ok")
    spans = exectrace_otel.to_otlp_json(t.snapshot())["resourceSpans"][0]["scopeSpans"][0]["spans"]
    keys = {s["spanId"]: {kv["key"] for kv in s["attributes"]} for s in spans}
    assert "gen_ai.usage.input_tokens" not in keys[a]
    assert "tavotto.usage.estimated_input_tokens" in keys[a]
    assert "gen_ai.usage.input_tokens" in keys[b]
