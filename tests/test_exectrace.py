"""Execution Trace Core（ADR 0151）：契约、生命周期、白名单投影、有界存储与恢复。

判据的主语：`engine/exectrace.py` 在 Flask 父进程里给出的**公开投影**（`snapshot()`）与**落盘记录**
（`data_dir()/cache/exec-trace/` 下的文件字节）。时钟一律注入，不 sleep。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

from tavotto.engine import exectrace as xt, trace as stagetrace


class FakeClock:
    def __init__(self, wall: float = 1_800_000_000.0) -> None:
        self.w = wall
        self.m = 100.0

    def wall(self) -> float:
        return self.w

    def mono(self) -> float:
        return self.m

    def tick(self, ms: float) -> None:
        self.m += ms / 1000.0
        self.w += ms / 1000.0


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv(xt.ENV_DISABLE, raising=False)
    xt.register_vocabulary("tool", ["try_run", "inspect_environment", "install_packages"])
    xt.register_vocabulary("agent", ["builtin", "codex", "claude"])


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(tmp_path):
    return xt.TraceStore(tmp_path / "exec-trace")


def _span(snap, span_id):
    return next(s for s in snap["spans"] if s["span_id"] == span_id)


def _fake_task(clock, store=None, **kw):
    """一个假的「改图」任务：plan → agent_run（里面两次 model_request + 一次 tool_call）→ validate → commit。"""
    t = xt.ExecTrace("ai_task", clock=clock, store=store, task_ref="abc123def456", **kw)
    plan = t.start_span("plan", "phase")
    clock.tick(50)
    t.end_span(plan)
    agent = t.start_span("agent_run", "agent", attributes={"agent": "builtin"})
    m1 = t.start_span("model_request", "model", parent=agent)
    clock.tick(800)
    t.end_span(
        m1,
        usage={
            "source": "reported",
            "input_tokens": 1200,
            "output_tokens": 300,
            "cached_input_tokens": 1000,
        },
    )
    tool = t.start_span("tool_call", "tool", parent=agent, attributes={"tool": "try_run"})
    clock.tick(1200)
    t.end_span(tool, attributes={"exit_ok": True})
    m2 = t.start_span("model_request", "model", parent=agent)
    clock.tick(400)
    t.end_span(m2)
    t.end_span(agent, attributes={"turns": 2})
    val = t.start_span("validate", "verify")
    clock.tick(30)
    t.end_span(val)
    com = t.start_span("commit", "phase")
    clock.tick(20)
    t.end_span(com, attributes={"files_changed": 1})
    t.finish(xt.STATUS_OK)
    return t, dict(plan=plan, agent=agent, m1=m1, tool=tool, m2=m2, val=val, com=com)


# ---------------------------------------------------------------- 1–3 生命周期、嵌套、并行


def test_a_fake_task_produces_a_hierarchical_trace_with_real_offsets(clock, store):
    t, ids = _fake_task(clock, store)
    snap = t.snapshot()
    assert snap["schema_version"] == xt.SCHEMA_VERSION
    assert len(snap["trace_id"]) == 32 and snap["status"] == "ok" and snap["complete"] is True
    root = _span(snap, snap["root_span_id"])
    assert root["parent_span_id"] is None and root["kind"] == "task"
    assert _span(snap, ids["m1"])["parent_span_id"] == ids["agent"]
    assert _span(snap, ids["tool"])["parent_span_id"] == ids["agent"]
    assert _span(snap, ids["plan"])["parent_span_id"] == snap["root_span_id"]
    tool = _span(snap, ids["tool"])
    assert (tool["start_offset_ms"], tool["end_offset_ms"], tool["duration_ms"]) == (
        850.0,
        2050.0,
        1200.0,
    )
    assert root["duration_ms"] == 2500.0
    assert _span(snap, ids["m1"])["usage"] == {
        "source": "reported",
        "input_tokens": 1200,
        "output_tokens": 300,
        "cached_input_tokens": 1000,
        "cost": None,
    }
    # 没报用量的那次是 None，不是 0
    assert _span(snap, ids["m2"])["usage"] is None
    # 存下来、读回来是同一份
    assert store.load(snap["trace_id"], scope=None) == snap


def test_parallel_spans_overlap_and_keep_their_own_timing(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    a = t.start_span("tool_call", "tool", attributes={"tool": "try_run"})
    clock.tick(100)
    b = t.start_span("tool_call", "tool", attributes={"tool": "inspect_environment"})
    clock.tick(100)
    t.end_span(a)
    clock.tick(300)
    t.end_span(b, xt.STATUS_FAILED, error_code="probe_failed")
    t.finish(xt.STATUS_FAILED, error_code="task_failed")
    snap = t.snapshot()
    sa, sb = _span(snap, a), _span(snap, b)
    assert sa["start_offset_ms"] < sb["start_offset_ms"] < sa["end_offset_ms"] < sb["end_offset_ms"]
    assert (sa["status"], sb["status"], sb["error_code"]) == ("ok", "failed", "probe_failed")
    # 根因是那次工具失败，不是后来根 Span 的终局
    assert snap["first_failure"]["span_id"] == b


# ---------------------------------------------------------------- 4–6 重复结束、重试、取消 / 超时 / 中断


def test_a_span_ends_once_and_the_second_end_changes_nothing(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    s = t.start_span("tool_call", "tool")
    clock.tick(10)
    assert t.end_span(s, xt.STATUS_FAILED, error_code="tool_crashed") is True
    clock.tick(500)
    assert t.end_span(s, xt.STATUS_OK) is False
    span = _span(t.snapshot(), s)
    assert (span["status"], span["error_code"], span["duration_ms"]) == (
        "failed",
        "tool_crashed",
        10.0,
    )


def test_a_retry_is_a_separate_attempt_and_never_overwrites_the_first_failure(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    first = t.start_span("model_request", "model", attempt=1)
    clock.tick(30_000)
    t.end_span(first, xt.STATUS_TIMEOUT, error_code="model_timeout")
    t.event("retry_allowed", span_id=first)
    second = t.start_span("model_request", "model", attempt=2)
    clock.tick(900)
    t.end_span(second)
    t.finish(xt.STATUS_OK)
    snap = t.snapshot()
    a, b = _span(snap, first), _span(snap, second)
    assert (a["attempt"], a["status"], b["attempt"], b["status"]) == (1, "timeout", 2, "ok")
    assert snap["first_failure"] == {
        "span_id": first,
        "name": "model_request",
        "error_code": "model_timeout",
        "attempt": 1,
    }
    # 父的成功不证明子的成功：根是 ok，第一次尝试仍是 timeout
    assert snap["status"] == "ok"


@pytest.mark.parametrize(
    ("final", "dangling"),
    [("cancelled", "cancelled"), ("timeout", "timeout"), ("ok", "unknown"), ("failed", "unknown")],
)
def test_spans_left_open_at_freeze_are_never_read_as_success(clock, final, dangling):
    t = xt.ExecTrace("fix_session", clock=clock)
    agent = t.start_span("agent_run", "agent")
    wait = t.start_span("user_wait", "wait", parent=agent)
    clock.tick(5000)
    t.finish(final)
    snap = t.snapshot()
    for sid in (agent, wait):
        span = _span(snap, sid)
        assert span["status"] == dangling and span["status"] != "ok"
        assert span["error_code"] == "unclosed" and span["timing"] == "unmeasured"


def test_late_writes_after_freeze_are_counted_not_recorded(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    s = t.start_span("tool_call", "tool")
    t.finish(xt.STATUS_CANCELLED)
    before = t.snapshot()
    assert t.end_span(s, xt.STATUS_OK) is False
    assert t.start_span("tool_call", "tool") is None
    assert t.event("tool_returned") == 0
    assert t.finish(xt.STATUS_OK) is False
    after = t.snapshot()
    assert after["late_count"] == 4
    assert {k: v for k, v in after.items() if k != "late_count"} == {
        k: v for k, v in before.items() if k != "late_count"
    }


# ---------------------------------------------------------------- 7–8 跨线程、乱序 / 重复 / 时钟


def test_events_from_many_threads_get_unique_monotonic_sequence_numbers(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    barrier = threading.Barrier(8)

    def work(n):
        barrier.wait()
        for i in range(100):
            t.event("tool_requested", dedup_key=f"{n}:{i}")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    snap = t.snapshot()
    seqs = [e["seq"] for e in snap["events"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 800 == snap["last_seq"]


def test_duplicate_delivery_is_recorded_once(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    assert t.event("tool_returned", dedup_key="call-7:end") == 1
    assert t.event("tool_returned", dedup_key="call-7:end") == 0
    assert len(t.snapshot()["events"]) == 1


def test_out_of_order_and_unknown_input_is_rejected_without_raising(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    assert t.end_span("nope", xt.STATUS_OK) is False  # 结束一个从没开过的
    assert t.end_span(None) is False
    assert t.start_span("rm -rf /", "tool") is None  # 名字不在闭集
    assert t.start_span("tool_call", "weird") is None
    s = t.start_span("tool_call", "tool", parent="not-a-span")  # 父不存在 → 挂到根
    assert _span(t.snapshot(), s)["parent_span_id"] == t.root_span_id
    assert t.end_span(s, "maybe") is False  # 状态不在闭集
    assert t.snapshot()["rejected_count"] >= 2


def test_a_monotonic_clock_going_backwards_never_yields_negative_durations(clock):
    t = xt.ExecTrace("ai_task", clock=clock)
    s = t.start_span("tool_call", "tool")
    clock.tick(100)
    clock.m -= 5.0  # 单调钟倒退（注入钟 / 休眠恢复）
    clock.w -= 3600  # 墙钟跳一小时也不影响耗时
    t.end_span(s)
    snap = t.snapshot()
    span = _span(snap, s)
    assert span["duration_ms"] >= 0 and span["end_offset_ms"] >= span["start_offset_ms"]
    assert snap["clock_anomalies"] == 1


def test_a_reported_duration_from_another_process_is_marked_and_never_stretches_the_observed_window(
    clock,
):
    t = xt.ExecTrace("ai_task", clock=clock)
    a = t.start_span("model_request", "model")
    clock.tick(1000)
    t.end_span(a, reported_duration_ms=700)
    b = t.start_span("model_request", "model")
    clock.tick(100)
    t.end_span(b, reported_duration_ms=99_999)  # 报得比观察到的还长：不信
    snap = t.snapshot()
    sa, sb = _span(snap, a), _span(snap, b)
    assert (sa["timing"], sa["duration_ms"], sa["end_offset_ms"]) == ("reported", 700.0, 1000.0)
    assert (sb["timing"], sb["duration_ms"]) == ("measured", 100.0)


def test_a_point_observation_is_zero_width_and_says_so(clock):
    t = xt.ExecTrace("preparation", clock=clock)
    clock.tick(40)
    p = t.point("receipt", "phase", xt.STATUS_OK)
    span = _span(t.snapshot(), p)
    assert (span["timing"], span["duration_ms"], span["start_offset_ms"]) == ("point", 0.0, 40.0)


# ---------------------------------------------------------------- 9 容量


def test_span_and_event_caps_truncate_honestly(clock, monkeypatch):
    monkeypatch.setattr(xt, "MAX_SPANS", 5)
    monkeypatch.setattr(xt, "MAX_EVENTS", 4)
    t = xt.ExecTrace("ai_task", clock=clock)
    ids = [t.start_span("tool_call", "tool") for _ in range(6)]
    assert ids[:4] != [None] * 4 and ids[4:] == [None, None]  # 根占一个
    for i in range(6):
        t.event("tool_requested", dedup_key=str(i))
    t.finish(xt.STATUS_OK)
    snap = t.snapshot()
    assert snap["truncated"] is True and snap["complete"] is False
    assert snap["dropped_count"] == 2 + 2
    assert [e["seq"] for e in snap["events"]] == [1, 4, 5, 6]  # 头尾留着


def test_a_record_over_the_byte_cap_is_cut_and_marked(clock, store, monkeypatch):
    monkeypatch.setattr(xt, "MAX_RECORD_BYTES", 6000)
    t = xt.ExecTrace("ai_task", clock=clock, store=store)
    for i in range(60):
        t.event("tool_requested", dedup_key=str(i))
        t.end_span(t.start_span("tool_call", "tool"))
    t.finish(xt.STATUS_OK)
    raw = (store.root / f"{t.trace_id}.json").read_bytes()
    assert len(raw) <= 6000
    saved = json.loads(raw)["trace"]
    assert saved["truncated"] is True and saved["complete"] is False
    assert saved["spans"][0]["span_id"] == t.root_span_id


def test_store_eviction_only_touches_frozen_records_oldest_first(clock, store, monkeypatch):
    running = xt.ExecTrace("ai_task", clock=clock, store=store)
    done = []
    for _ in range(3):
        d = xt.ExecTrace("ai_task", clock=clock, store=store)
        d.finish(xt.STATUS_OK)
        done.append(d)
    for i, d in enumerate(done):
        p = store.root / f"{d.trace_id}.json"
        os.utime(p, (1000 + i, 1000 + i))
    os.utime(store.root / f"{running.trace_id}.open.json", (1, 1))  # 最旧的是运行中的
    monkeypatch.setattr(xt, "MAX_TOTAL_BYTES", 1)
    store.prune(now=2000)
    left = sorted(p.name for p in store.root.iterdir())
    assert left == [f"{running.trace_id}.open.json"]


def test_retention_expires_frozen_records(clock, store):
    t = xt.ExecTrace("ai_task", clock=clock, store=store)
    t.finish(xt.STATUS_OK)
    path = store.root / f"{t.trace_id}.json"
    mtime = path.stat().st_mtime
    assert store.prune(now=mtime + xt.RETENTION_S - 1) == 0 and path.exists()
    assert store.prune(now=mtime + xt.RETENTION_S + 1) == 1 and not path.exists()


# ---------------------------------------------------------------- 10 启动恢复与旧数据


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_a_running_record_whose_owner_died_is_recovered_as_interrupted(clock, store):
    t = xt.ExecTrace("ai_task", clock=clock, store=store)
    agent = t.start_span("agent_run", "agent")
    store.save(t)
    path = store.root / f"{t.trace_id}.open.json"
    record = json.loads(path.read_text())
    record["owner_pid"], record["owner_token"] = _dead_pid(), "someone-else"
    path.write_text(json.dumps(record))
    assert store.recover() == 1
    assert not path.exists()
    snap = store.load(t.trace_id, scope=None)
    assert snap["status"] == "interrupted" and snap["complete"] is False
    assert _span(snap, agent)["status"] == "unknown"
    assert _span(snap, snap["root_span_id"])["status"] == "interrupted"
    assert snap["first_failure"]["error_code"] == "interrupted"


def test_recovery_leaves_records_owned_by_this_process_or_a_live_one(clock, store):
    mine = xt.ExecTrace("ai_task", clock=clock, store=store)
    assert store.recover() == 0
    assert (store.root / f"{mine.trace_id}.open.json").exists()


def test_the_legacy_stage_trace_maps_to_point_events_without_reading_facts(clock):
    legacy = stagetrace.Trace()
    legacy.mark("plan", sha="0123456789abcdef", path_hint="/Users/alice/secret.csv")
    legacy.mark("check")
    legacy.fail("execute", "script_crashed")
    t = xt.ExecTrace("preparation", clock=clock)
    assert xt.observe_stage_trace(t, legacy.to_payload()) == 3
    assert xt.observe_stage_trace(t, legacy.to_payload()) == 0  # 重复送达去重
    snap = t.snapshot()
    stages = [e["attributes"] for e in snap["events"] if e["name"] == "stage"]
    assert stages == [
        {"phase": "plan", "outcome": "ok"},
        {"phase": "check", "outcome": "ok"},
        {"phase": "execute", "outcome": "failed"},
    ]
    assert "alice" not in json.dumps(snap) and "0123456789abcdef" not in json.dumps(snap)
    # 不造区间：只有根 Span，没有凭相邻时间戳算出来的阶段 Span
    assert len(snap["spans"]) == 1
    # 旧轨迹本身不受影响
    assert legacy.to_payload()["failed_phase"] == "execute"


# ---------------------------------------------------------------- 11 隐私 Canary

_CANARIES = [
    "sk-ant-CANARYKEY0123456789",
    "alice.canary@example.org",
    "/Users/alice/Experiments/raw_spectra.csv",
    "C:\\Users\\alice\\thesis\\fig3.py",
    "Please make the error bars thicker CANARY_PROMPT",
    "SAMPLE-ID-HeLa-CANARY-42",
    "AUTHORIZATION: Bearer canary",
]


def test_canary_strings_never_reach_the_public_projection_or_the_disk(clock, store):
    xt.register_vocabulary("tool", _CANARIES)  # 就算生产者把自由文本当词表塞，形状也不让进
    t = xt.ExecTrace(
        "ai_task", clock=clock, store=store, task_ref=_CANARIES[2], session_ref=_CANARIES[1]
    )
    for c in _CANARIES:
        s = t.start_span("tool_call", "tool", attributes={"tool": c, c: 1, "items": c, "opaque": c})
        t.end_span(
            s,
            xt.STATUS_FAILED,
            error_code=c,
            attributes={"note": c},
            usage={
                "source": c,
                "input_tokens": c,
                "cost": {"amount": 1, "currency": c, "pricing_version": c},
            },
        )
        t.event("tool_returned", attributes={"tool": c, "path": c}, dedup_key=c)
        assert t.start_span(c, "tool") is None
    t.finish(xt.STATUS_FAILED, error_code=_CANARIES[4])
    blobs = [json.dumps(t.snapshot(), ensure_ascii=False)]
    blobs += [p.read_text(encoding="utf-8") for p in store.root.iterdir()]
    for blob in blobs:
        for c in _CANARIES:
            for fragment in (c, "alice", "CANARY", "canary", "HeLa"):
                assert fragment not in blob, f"{fragment!r} 泄漏进了 {blob[:120]}…"
    snap = t.snapshot()
    assert snap["task_ref"] is None and snap["session_ref"] is None
    assert snap["rejected_count"] > 0


def test_attributes_and_vocabularies_are_closed_sets():
    assert set(xt.ATTRIBUTES) == {
        "tool",
        "agent",
        "provider",
        "model",
        "tier",
        "approved_by",
        "execution",
        "phase",
        "outcome",
        "opaque",
        "exit_ok",
        "files_changed",
        "turns",
        "retries",
        "items",
        "reported_ms",
        "changed",
        "refresh",
        "checks_failed",
    }
    with pytest.raises(ValueError):
        xt.register_vocabulary("prompt", ["x"])
    assert xt.project_attributes({"turns": True}) == ({}, 1)  # bool 不是计数
    assert xt.project_attributes({"reported_ms": float("nan")}) == ({}, 1)


def test_cost_is_only_kept_with_currency_and_pricing_version_and_is_always_an_estimate():
    xt.register_vocabulary("pricing_version", ["2026-10-27"])
    u = xt.project_usage(
        {"source": "reported", "input_tokens": 10, "cost": {"amount": 0.01, "currency": "USD"}}
    )
    assert u["cost"] is None
    u = xt.project_usage(
        {
            "source": "estimated",
            "output_tokens": 5,
            "cost": {
                "amount": 0.0123456789,
                "currency": "USD",
                "pricing_version": "2026-10-27",
                "estimated": False,
            },
        }
    )
    assert u["cost"] == {
        "amount": 0.012346,
        "currency": "USD",
        "pricing_version": "2026-10-27",
        "estimated": True,
    }
    assert u["input_tokens"] is None and u["source"] == "estimated"
    assert xt.project_usage({"input_tokens": 3}) is None  # 来源不明 = 不可信


# ---------------------------------------------------------------- 12 存储不可写 / 损坏


def test_an_unwritable_store_never_breaks_the_traced_task(clock, tmp_path):
    blocker = tmp_path / "exec-trace"
    blocker.write_text("not a directory")
    store = xt.TraceStore(blocker)
    t, _ = _fake_task(clock, store)
    assert t.snapshot()["status"] == "ok"
    assert store.write_errors >= 2
    assert store.recover() == 0 and store.prune() == 0


def test_corrupt_records_are_skipped_and_counted(clock, store):
    t = xt.ExecTrace("ai_task", clock=clock, store=store)
    t.finish(xt.STATUS_OK)
    (store.root / f"{t.trace_id}.json").write_text("{half a record")
    (store.root / f"{'f' * 32}.open.json").write_text("[]")
    assert store.load(t.trace_id, scope=None) is None
    assert store.recover() == 0
    assert store.corrupt >= 2


def test_internal_faults_are_swallowed_and_counted(clock, monkeypatch):
    t = xt.ExecTrace("ai_task", clock=clock)

    def boom(*a, **k):
        raise RuntimeError("collector bug")

    monkeypatch.setattr(xt, "project_attributes", boom)
    assert t.start_span("tool_call", "tool") is None
    assert t.event("tool_requested") == 0
    assert t.snapshot()["internal_errors"] == 2


# ---------------------------------------------------------------- 项目隔离


def test_a_trace_from_another_scope_reads_as_absent(clock, store):
    rec = xt.Recorder(store, clock=clock)
    t = rec.begin("ai_task", scope="projA")
    t.finish(xt.STATUS_OK)
    assert rec.get(t.trace_id, scope="projA")["trace_id"] == t.trace_id
    assert rec.get(t.trace_id, scope="projB") is None
    assert store.load(t.trace_id, scope="projB") is None
    assert store.load("../../etc/passwd", scope="projA") is None


# ---------------------------------------------------------------- 13 旧 Trace 契约不变


def test_the_legacy_stage_trace_payload_shape_is_untouched():
    payload = stagetrace.Trace().to_payload()
    assert set(payload) == {
        "trace_version",
        "events",
        "limit",
        "truncated",
        "dropped",
        "current_phase",
        "failed_phase",
        "failed_code",
    }
    assert payload["trace_version"] == 1


# ---------------------------------------------------------------- 14 关闭时零副作用、热路径不同步写盘


def test_disabled_tracing_is_a_null_object_with_no_side_effects(clock, store, monkeypatch):
    monkeypatch.setenv(xt.ENV_DISABLE, "1")
    rec = xt.Recorder(store, clock=clock)
    t = rec.begin("ai_task")
    assert isinstance(t, xt.NullTrace)
    s = t.start_span("tool_call", "tool")
    assert s is None and t.end_span(s) is False and t.event("tool_returned") == 0
    assert t.finish(xt.STATUS_OK) is False and t.snapshot() is None
    assert not store.root.exists()


def test_span_churn_checkpoints_at_most_once_per_interval(clock):
    saves = []

    class CountingStore:
        def submit(self, trace):
            saves.append(trace.frozen)

    t = xt.ExecTrace("ai_task", clock=clock, store=CountingStore())
    for _ in range(500):
        t.end_span(t.start_span("tool_call", "tool"))
        clock.tick(1)  # 500 个 Span、共 0.5 秒
    t.finish(xt.STATUS_OK)
    # begin 一次 + 节流后一次 + finish 一次
    assert saves.count(False) <= 2 and saves[-1] is True


def test_operation_kinds_span_names_and_statuses_are_closed_sets():
    assert xt.OPERATION_KINDS == ("ai_task", "fix_session", "preparation", "export")
    with pytest.raises(ValueError):
        xt.ExecTrace("anything")
    assert "running" not in xt.END_STATUSES and "unknown" in xt.END_STATUSES
    assert set(xt.TIMINGS) == {"measured", "reported", "point", "unmeasured"}


# ---------------------------------------------------------------- Codex #1449 两条


def test_recovery_skips_a_structurally_malformed_record_and_still_recovers_the_rest(clock, store):
    """P1：JSON 合法但结构不对的 `.open` 只记 corrupt、跳过；后面那条属主已死的照常恢复，不外抛。"""
    store.root.mkdir(parents=True)
    (store.root / f"{'a' * 32}.open.json").write_text(
        json.dumps({"owner_pid": -1, "trace": {"spans": [1]}})
    )
    (store.root / f"{'b' * 32}.open.json").write_text(
        json.dumps({"owner_pid": -1, "trace": {"trace_id": 7}})
    )
    t = xt.ExecTrace("ai_task", clock=clock, store=store)
    path = store.root / f"{t.trace_id}.open.json"
    record = json.loads(path.read_text())
    record["owner_pid"], record["owner_token"] = _dead_pid(), "someone-else"
    path.write_text(json.dumps(record))
    assert store.recover() == 1
    assert store.corrupt == 2
    assert store.load(t.trace_id, scope=None)["status"] == "interrupted"


def test_a_checkpoint_captured_before_finish_never_overwrites_the_final_record(clock, store):
    """P2：一次 checkpoint 在 `finish()` 之前截下运行中快照、在终局写完之后才写——不许盖掉终局。"""
    t = xt.ExecTrace("ai_task", clock=clock, store=store)
    real = xt.ExecTrace.snapshot
    state = {"raced": False}

    def racing_snapshot(self):
        snap = real(self)
        if not state["raced"] and snap["status"] == "running":
            state["raced"] = True
            self.finish(xt.STATUS_OK)  # 另一个线程在这一刻冻结并写完终局
        return snap

    t.snapshot = racing_snapshot.__get__(t)
    store.save(t)  # 拿着旧的运行中快照来写
    assert state["raced"]
    saved = store.load(t.trace_id, scope=None)
    assert saved["status"] == "ok"
    assert not (store.root / f"{t.trace_id}.open.json").exists()
