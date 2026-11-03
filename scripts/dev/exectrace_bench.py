"""Execution Trace 性能预算基准（ADR 0151 PR4 §4.7）。开发侧工具，不进产品；纯标准库。

比较「追踪开」与「追踪关」（`TAVOTTO_NO_EXEC_TRACE=1` -> `exectrace.begin` 回 NullTrace）：

1. micro：每对 `start_span`+`end_span`、每次 `event` 的开销（N 次 x R 轮，报每轮均值的中位数 / p95，单位 µs）；
2. 代表性任务：约 200 Span / 120 Event 的假 AI 任务，夹少量模拟工作，总耗时开 vs 关（>= 10 轮）；
3. 内存：一条 Trace 顶到 MAX_SPANS / MAX_EVENTS 的 tracemalloc 峰值；
4. 落盘：每条 Trace 写多少字节（临时 TraceStore，后台线程 + flush），以及顶到上限时的 truncated / 单条上限；
5. 事件洪水：10 万个 Event 的耗时与 dropped_count。

数据目录是临时目录（`TAVOTTO_DATA_DIR`），不碰用户真实数据。**数字只对本机本次运行有效**（解释器、CPU、负载、磁盘都会变），
只拿来比「开 vs 关」与看量级，不是跨机器的承诺。

用法：python scripts/dev/exectrace_bench.py [--quick] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "tavotto.exectrace-bench/1"
ENV = "TAVOTTO_NO_EXEC_TRACE"
WORK_US = 100.0

PROFILES = {
    "full": {"micro_n": 10_000, "micro_r": 7, "task_r": 12, "flood": 100_000, "task_spans": 200},
    "quick": {"micro_n": 300, "micro_r": 5, "task_r": 10, "flood": 3_000, "task_spans": 20},
}


def _setup(tmp: Path):
    os.environ["TAVOTTO_DATA_DIR"] = str(tmp / "data")
    sys.path.insert(0, str(ROOT / "src"))
    from tavotto.engine import exectrace

    return exectrace


def _set_mode(on: bool) -> None:
    if on:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = "1"


def _stats(samples_us: list[float]) -> dict:
    s = sorted(samples_us)
    p95 = s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]
    return {"median_us": round(statistics.median(s), 3), "p95_us": round(p95, 3), "repeats": len(s)}


def _micro(xt, n: int, r: int, on: bool) -> dict:
    """每轮 n 次操作，分块（每块一条新 Trace，避开 Span / Event 上限；建 Trace 不计时），报每次操作的平均微秒。"""
    _set_mode(on)
    chunk = min(n, xt.MAX_SPANS - 8)
    span_runs, event_runs = [], []
    for _ in range(r):
        for op, runs, size in (
            ("span", span_runs, chunk),
            ("event", event_runs, min(n, xt.MAX_EVENTS - 8)),
        ):
            spent, done = 0.0, 0
            while done < n:
                k = min(size, n - done)
                t = xt.begin("ai_task")
                t0 = time.perf_counter()
                if op == "span":
                    for _ in range(k):
                        t.end_span(t.start_span("tool_call", "tool"))
                else:
                    for _ in range(k):
                        t.event("stage")
                spent += time.perf_counter() - t0
                done += k
            runs.append(spent / n * 1e6)
    return {"n_per_repeat": n, "span_pair": _stats(span_runs), "event": _stats(event_runs)}


def _spin(us: float) -> None:
    end = time.perf_counter() + us / 1e6
    while time.perf_counter() < end:
        pass


def _fake_task(xt, spans: int, events: int) -> None:
    t = xt.begin("ai_task", task_ref="bench0000task")
    phases = ("plan", "execute", "validate", "commit")
    per = max(1, spans // len(phases))
    for ph in phases:
        p = t.start_span(ph, "phase")
        for i in range(per):
            s = t.start_span("tool_call", "tool", parent=p, attempt=1 + i % 3)
            _spin(
                WORK_US
            )  # 模拟工作（真实工具调用是毫秒到秒级，这里取很短的 100 µs，对开销比例是偏严的）
            if i % max(1, per * len(phases) // max(1, events)) == 0:
                t.event("stage", span_id=s)
            t.end_span(s, "ok", attributes={"exit_ok": True})
        t.end_span(p)
    for _ in range(events):
        t.event("stage")
    t.finish("ok")


def _task(xt, spans: int, r: int) -> dict:
    events = max(1, spans * 120 // 200)
    out = {}
    for label, on in (("on", True), ("off", False)):
        _set_mode(on)
        _fake_task(xt, spans, events)  # 预热
        runs = []
        for _ in range(r):
            t0 = time.perf_counter()
            _fake_task(xt, spans, events)
            runs.append((time.perf_counter() - t0) * 1e3)
        out[label] = {
            "median_ms": round(statistics.median(runs), 3),
            "p95_ms": round(sorted(runs)[min(len(runs) - 1, math.ceil(0.95 * len(runs)) - 1)], 3),
            "repeats": r,
        }
    off, on_ = out["off"]["median_ms"], out["on"]["median_ms"]
    out["spans"], out["events"] = spans, events
    out["overhead_ms_median"] = round(on_ - off, 3)
    out["work_us_per_span"] = WORK_US
    out["overhead_us_per_span"] = round((on_ - off) * 1000 / max(1, spans), 3)
    out["overhead_pct_median"] = round((on_ - off) / off * 100, 2) if off else None
    return out


def _memory(xt) -> dict:
    _set_mode(True)
    tracemalloc.start()
    t = xt.begin("ai_task")
    for i in range(xt.MAX_SPANS):
        sid = t.start_span("tool_call", "tool", attributes={"exit_ok": True})
        t.end_span(sid)
        t.event("stage", span_id=sid)
    for _ in range(xt.MAX_EVENTS):
        t.event("stage")
    t.finish("ok")
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    snap = t.snapshot()
    return {
        "peak_bytes": peak,
        "retained_bytes": cur,
        "spans": len(snap["spans"]),
        "events": len(snap["events"]),
        "truncated": snap["truncated"],
    }


def _storage(xt, tmp: Path) -> dict:
    _set_mode(True)
    out = {}
    for label, nspans, nevents in (
        ("typical", 40, 60),
        ("at_caps", xt.MAX_SPANS, xt.MAX_EVENTS * 2),
    ):
        root = tmp / f"store-{label}"
        store = xt.TraceStore(root, background=True)
        t = xt.ExecTrace("ai_task", store=store)
        for i in range(nspans - 1):
            sid = t.start_span("tool_call", "tool", attributes={"exit_ok": True, "items": i})
            t.end_span(sid, "ok", reported_duration_ms=1.5)
        for _ in range(nevents):
            t.event("stage", attributes={"items": 1})
        t.finish("ok")
        flushed = store.flush(10.0)
        files = [p for p in root.glob("*.json")]
        size = sum(p.stat().st_size for p in files)
        snap = t.snapshot()
        out[label] = {
            "flushed": flushed,
            "files": len(files),
            "bytes_on_disk": size,
            "within_max_record_bytes": size <= xt.MAX_RECORD_BYTES,
            "memory_truncated": snap["truncated"],
            "memory_dropped_count": snap["dropped_count"],
            "store_write_errors": store.write_errors,
        }
        if files:
            rec = json.loads(files[0].read_text(encoding="utf-8"))["trace"]
            out[label]["disk_truncated"] = rec.get("truncated")
            out[label]["disk_complete"] = rec.get("complete")
    return out


def _flood(xt, n: int) -> dict:
    _set_mode(True)
    t = xt.begin("ai_task")
    t0 = time.perf_counter()
    for _ in range(n):
        t.event("stage")
    dt = time.perf_counter() - t0
    snap = t.snapshot()
    return {
        "events_sent": n,
        "seconds": round(dt, 4),
        "events_per_s": round(n / dt) if dt else None,
        "dropped_count": snap["dropped_count"],
        "kept_events": len(snap["events"]),
        "truncated": snap["truncated"],
    }


def _off_writes_nothing(xt, tmp: Path) -> dict:
    """关闭时：begin 回 NullTrace、数据目录下不出现 exec-trace 目录 / 文件。"""
    _set_mode(False)
    t = xt.begin("ai_task")
    t.start_span("plan", "phase")
    t.finish("ok")
    xt.RECORDER._store.flush(2.0)
    d = tmp / "data" / "cache" / "exec-trace"
    files = list(d.glob("*")) if d.exists() else []
    return {"null_trace": type(t).__name__ == "NullTrace", "files_written": len(files)}


def run(profile: str) -> dict:
    cfg = PROFILES[profile]
    with tempfile.TemporaryDirectory(prefix="tavotto-xtbench-", ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        xt = _setup(tmp)
        t0 = time.perf_counter()
        report = {
            "schema": SCHEMA,
            "profile": profile,
            "note": "numbers are environment-specific (interpreter, CPU, load, disk); use for on-vs-off ratios and orders of magnitude only",
            "machine": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.system(),
                "machine": platform.machine(),
                "cpu_count": os.cpu_count(),
            },
            "limits": {
                "max_spans": xt.MAX_SPANS,
                "max_events": xt.MAX_EVENTS,
                "max_record_bytes": xt.MAX_RECORD_BYTES,
            },
        }
        report["off_check"] = _off_writes_nothing(xt, tmp)
        report["micro"] = {
            "on": _micro(xt, cfg["micro_n"], cfg["micro_r"], True),
            "off": _micro(xt, cfg["micro_n"], cfg["micro_r"], False),
        }
        report["task"] = _task(xt, cfg["task_spans"], cfg["task_r"])
        report["memory"] = _memory(xt)
        report["storage"] = _storage(xt, tmp)
        report["flood"] = _flood(xt, cfg["flood"])
        report["total_seconds"] = round(time.perf_counter() - t0, 2)
        xt.RECORDER._store.flush(10.0)  # 后台写线程写空再清临时目录
        _set_mode(True)
        return report


def _print(rep: dict) -> None:
    m, k, mem, fl = rep["micro"], rep["task"], rep["memory"], rep["flood"]
    print(
        f"[{rep['schema']}] profile={rep['profile']} python={rep['machine']['python']} {rep['machine']['platform']}"
    )
    print("NOTE: " + rep["note"])
    print(f"{'op':<22}{'ON med/p95 us':>20}{'OFF med/p95 us':>20}")
    for op, label in (("span_pair", "start+end span"), ("event", "event")):
        a, b = m["on"][op], m["off"][op]
        print(
            f"{label:<22}{a['median_us']:>10.2f}/{a['p95_us']:<9.2f}{b['median_us']:>10.2f}/{b['p95_us']:<9.2f}"
        )
    print(
        f"task ({k['spans']} spans/{k['events']} events) ON {k['on']['median_ms']} ms  OFF {k['off']['median_ms']} ms"
        f"  overhead {k['overhead_ms_median']} ms ({k['overhead_pct_median']}%)"
    )
    print(
        f"memory at caps: peak {mem['peak_bytes'] / 1024:.0f} KiB, retained {mem['retained_bytes'] / 1024:.0f} KiB"
    )
    for name, s in rep["storage"].items():
        print(
            f"storage {name}: {s['bytes_on_disk']} B on disk, truncated={s['memory_truncated']}, <=cap={s['within_max_record_bytes']}"
        )
    print(
        f"flood {fl['events_sent']} events: {fl['seconds']} s, dropped {fl['dropped_count']}, kept {fl['kept_events']}"
    )
    print(f"off mode: {rep['off_check']}  total {rep['total_seconds']} s")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="tiny profile (for tests)")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    rep = run("quick" if args.quick else "full")
    _print(rep)
    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
