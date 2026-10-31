"""Read-only analysis of Execution Trace files (ADR 0151, PR4 sections 4.4-4.5).

Pure stdlib. Reads `<data_dir>/cache/exec-trace/*.json` (frozen) and `*.open.json` (running) and turns
them into one row per trace plus aggregate comparison statistics. It never writes into the trace
directory, never starts a model request, never imports `tavotto`.

Hard rules (each one has a test in `tests/test_exectrace_analysis.py`):

* A Trace observes; it does not judge. The `verdict` and `verification` columns come ONLY from the
  guardian / bench results passed in. Without them they are `null`, whatever the trace `status` says.
* Case ids come from the guardian / bench results, never from the trace. taf_bench rows record
  `task_refs: [session id]` (run it with `--exec-trace-out`); compat_guardian records nothing yet,
  so the linkage is explicit: a result entry lists `trace_ids` and/or `task_refs`
  (the trace's opaque `task_ref` / `session_ref`; for taf_bench the session id is both).
  A trace no entry claims has `case_id = null`.
* Error class = the guardian taxonomy category when the results supply one for the case, else the
  trace's `first_failure.error_code`. Grouping by class is grouping by evidence-backed stable code;
  the same code is NOT the same root cause (the output says so).
* Every mean / p50 / p95 carries `n`; below MIN_SAMPLES the value is `null` with
  `insufficient_samples: true`. Nothing here claims statistical significance.

Guardian-results file (`--guardian-results`) accepts, in one JSON object, any of:

* `{"cases": [...]}`  - a CompatBench report (`compat_guardian.py`): entries use `id`, `status`,
  `failure_category.category`.
* `{"rows": [...]}`   - a taf_bench report: entries use `id` + `variant`, `outcome`, `figure_verdict`.
* Both kinds of entry may add `trace_ids: [...]` / `task_refs: [...]` (the linkage) and
  `verification` (a short string).

Usage:
    python scripts/ci/exectrace_analysis.py --traces DIR [--guardian-results FILE]
        [--baseline FILE] --out FILE
`--baseline` is a previous output of this script; the output then also carries per-metric deltas.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Windows: a redirected stdout/stderr falls back to the locale code page; pin both to UTF-8 so a report
# or diagnostic with non-ASCII text never raises UnicodeEncodeError in a CI step that otherwise succeeded.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "tavotto.exectrace-analysis/1"
#: Fewer samples than this and a statistic is `null` + `insufficient_samples: true`.
MIN_SAMPLES = 5
NOTE_SAME_CODE = "Rows sharing an error class share an evidence-backed stable code, not necessarily a root cause."
_BENCH = None


def _bench_final_ok(row: dict) -> bool:
    """`scripts/dev/taf_bench.py::final_ok` 按文件路径载入（它顶层只 import 标准库）；判据只有那一份。"""
    global _BENCH
    if _BENCH is None:
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "dev" / "taf_bench.py"
        spec = importlib.util.spec_from_file_location("_tavotto_taf_bench", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _BENCH = mod
    return bool(_BENCH.final_ok(row))


NOTE_NO_VERDICT = "Verdict and verification come only from guardian/bench results; a trace status is never a verdict."

#: Source statuses that count as a pass. Everything else with a known status counts as not passed.
GUARDIAN_PASS = ("passed", "control_met")


# ---------------------------------------------------------------- loading


def load_traces(directory) -> tuple[list[dict], int]:
    """Return `(records, corrupt_count)`. A frozen record wins over a leftover `.open` of the same trace."""
    root = Path(directory)
    records: dict[str, tuple[bool, dict]] = {}
    corrupt = 0
    if not root.is_dir():
        return [], 0
    for path in sorted(root.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            trace = rec["trace"]
            tid = trace["trace_id"]
            if not isinstance(tid, str) or not isinstance(trace["spans"], list):
                raise ValueError("shape")
        except (OSError, ValueError, KeyError, TypeError):
            corrupt += 1
            continue
        frozen = not path.name.endswith(".open.json")
        prev = records.get(tid)
        if prev is None or (frozen and not prev[0]):
            records[tid] = (frozen, rec)
    out = [r for _, r in records.values()]
    out.sort(key=lambda r: (r["trace"].get("started_at") or 0, r["trace"]["trace_id"]))
    return out, corrupt


# ---------------------------------------------------------------- guardian / bench input


def _case_entries(results) -> list[dict]:
    """Normalise guardian + bench entries to {case_id, verdict, passed, error_class, verification, links}."""
    entries: list[dict] = []
    if not isinstance(results, dict):
        return entries
    for c in results.get("cases") or []:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            continue
        status = c.get("status")
        fc = c.get("failure_category")
        entries.append(
            {
                "case_id": c["id"],
                "verdict": status if isinstance(status, str) else None,
                "passed": (status in GUARDIAN_PASS) if isinstance(status, str) else None,
                "error_class": fc.get("category") if isinstance(fc, dict) else None,
                "verification": c.get("verification")
                if isinstance(c.get("verification"), str)
                else None,
                "raw": c,
            }
        )
    for r in results.get("rows") or []:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            continue
        outcome = r.get("outcome")
        fv = r.get("figure_verdict")
        variant = r.get("variant")
        entries.append(
            {
                "case_id": f"{r['id']}/{variant}" if variant else r["id"],
                "verdict": outcome if isinstance(outcome, str) else None,
                # 通过与否只用 taf_bench 自己的判据（含目标断言 goal），不在这里另写一份（Codex #1457）
                "passed": _bench_final_ok(r) if isinstance(outcome, str) else None,
                "error_class": None,
                "verification": fv if isinstance(fv, str) else None,
                "raw": r,
            }
        )
    return entries


def _link_index(entries: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for e in entries:
        raw = e["raw"]
        for key in ("trace_ids", "task_refs"):
            for ref in raw.get(key) or []:
                if isinstance(ref, str):
                    index.setdefault(f"{key}:{ref}", e)
    return index


# ---------------------------------------------------------------- rows


def _duration_ms(trace: dict, spans: list[dict]) -> float | None:
    root = next((s for s in spans if s.get("span_id") == trace.get("root_span_id")), None)
    if root and isinstance(root.get("duration_ms"), (int, float)):
        return float(root["duration_ms"])
    a, b = trace.get("started_at"), trace.get("ended_at")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a:
        return round((b - a) * 1000.0, 3)
    return None


def _completeness(rec: dict, trace: dict) -> str:
    if rec.get("recovered") or trace.get("status") == "interrupted":
        return "interrupted"
    if trace.get("truncated"):
        return "truncated"
    if trace.get("complete") is True and trace.get("status") != "running":
        return "complete"
    return "incomplete"


def _reported_tokens(spans: list[dict]) -> int | None:
    total, seen = 0, False
    for s in spans:
        u = s.get("usage")
        if not isinstance(u, dict) or u.get("source") != "reported":
            continue
        for key in ("input_tokens", "output_tokens"):
            v = u.get(key)
            if isinstance(v, int) and not isinstance(v, bool):
                total += v
                seen = True
    return total if seen else None


def _stage_ms(spans: list[dict]) -> dict[str, float]:
    """Per span name, summed duration of spans that really have one (point / unmeasured are skipped)."""
    out: dict[str, float] = {}
    for s in spans:
        if s.get("timing") not in ("measured", "reported"):
            continue
        d = s.get("duration_ms")
        if isinstance(d, (int, float)) and not isinstance(d, bool) and s.get("name") != "task":
            out[s["name"]] = round(out.get(s["name"], 0.0) + float(d), 3)
    return out


def case_rows(traces: list[dict], guardian_results=None) -> list[dict]:
    """One row per trace. `traces` are the records from `load_traces`."""
    index = _link_index(_case_entries(guardian_results))
    rows = []
    for rec in traces:
        trace = rec["trace"]
        spans = [s for s in trace.get("spans") or [] if isinstance(s, dict)]
        ff = trace.get("first_failure") if isinstance(trace.get("first_failure"), dict) else None
        entry = None
        for key in (
            f"trace_ids:{trace.get('trace_id')}",
            f"task_refs:{trace.get('task_ref')}",
            f"task_refs:{trace.get('session_ref')}",
        ):
            if key in index:
                entry = index[key]
                break
        agent_attempts = [
            s["attempt"]
            for s in spans
            if s.get("name") == "agent_run" and isinstance(s.get("attempt"), int)
        ]
        trace_class = ff.get("error_code") if ff else None
        guardian_class = entry["error_class"] if entry else None
        env_kind = next(
            (
                s["attributes"]["execution"]
                for s in spans
                if isinstance(s.get("attributes"), dict) and s["attributes"].get("execution")
            ),
            None,
        )
        rows.append(
            {
                "case_id": entry["case_id"] if entry else None,
                "trace_id": trace.get("trace_id"),
                "operation_kind": trace.get("operation_kind"),
                "environment_kind": env_kind,
                "failure_stage": ff.get("name") if ff else None,
                "error_class": guardian_class or trace_class,
                "error_class_source": (
                    "guardian" if guardian_class else ("trace" if trace_class else None)
                ),
                "attempts": max(agent_attempts) if agent_attempts else None,
                "agent_runs": len(agent_attempts),
                "duration_ms": _duration_ms(trace, spans),
                "verdict": entry["verdict"] if entry else None,
                "verdict_pass": entry["passed"] if entry else None,
                "verification": entry["verification"] if entry else None,
                "completeness": _completeness(rec, trace),
                "tool_calls": sum(1 for s in spans if s.get("name") == "tool_call"),
                "model_requests": sum(1 for s in spans if s.get("name") == "model_request"),
                "reported_tokens": _reported_tokens(spans),
                "stage_ms": _stage_ms(spans),
            }
        )
    return rows


# ---------------------------------------------------------------- statistics


def _pct(sorted_vals: list[float], q: float) -> float:
    k = max(0, math.ceil(q * len(sorted_vals)) - 1)
    return sorted_vals[k]


def dist(values) -> dict:
    """`{n, mean, p50, p95, insufficient_samples}`; stats are null below MIN_SAMPLES."""
    vals = sorted(
        float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)
    )
    n = len(vals)
    if n < MIN_SAMPLES:
        return {"n": n, "mean": None, "p50": None, "p95": None, "insufficient_samples": True}
    return {
        "n": n,
        "mean": round(sum(vals) / n, 3),
        "p50": round(_pct(vals, 0.5), 3),
        "p95": round(_pct(vals, 0.95), 3),
        "insufficient_samples": False,
    }


def rate(hits: int, n: int) -> dict:
    short = n < MIN_SAMPLES
    return {
        "n": n,
        "k": hits,
        "rate": None if short else round(hits / n, 4),
        "insufficient_samples": short,
    }


def aggregate(rows: list[dict]) -> dict:
    judged = [r for r in rows if r["verdict_pass"] is not None]
    first_pass_pool = [r for r in judged if r["attempts"] is not None]
    fix = [r for r in judged if r["operation_kind"] == "fix_session"]
    failed = [r for r in judged if r["verdict_pass"] is False and r["error_class"]]
    class_counts: dict[str, int] = {}
    for r in rows:
        if r["error_class"]:
            class_counts[r["error_class"]] = class_counts.get(r["error_class"], 0) + 1
    repeated = sum(
        1 for r in failed if sum(1 for q in failed if q["error_class"] == r["error_class"]) > 1
    )
    stage_names = sorted({k for r in rows for k in r["stage_ms"]})
    token_rows = [r for r in rows if r["reported_tokens"] is not None]
    return {
        "rows": len(rows),
        "first_pass_rate": rate(
            sum(1 for r in first_pass_pool if r["verdict_pass"] and r["attempts"] == 1),
            len(first_pass_pool),
        ),
        "final_pass_rate": rate(sum(1 for r in judged if r["verdict_pass"]), len(judged)),
        "auto_repair_success_rate": rate(sum(1 for r in fix if r["verdict_pass"]), len(fix)),
        "retry_counts": dist([r["attempts"] - 1 for r in rows if r["attempts"] is not None]),
        "task_duration_ms": dist([r["duration_ms"] for r in rows]),
        "stage_duration_ms": {
            n: dist([r["stage_ms"][n] for r in rows if n in r["stage_ms"]]) for n in stage_names
        },
        "tool_calls": dist([r["tool_calls"] for r in rows]),
        "repeated_failure_ratio": {
            **rate(repeated, len(failed)),
            "note": NOTE_SAME_CODE,
        },
        "error_class_frequencies": dict(sorted(class_counts.items())),
        "missing_verification_ratio": rate(
            sum(1 for r in rows if r["verification"] is None), len(rows)
        ),
        "verifiable_tokens": {
            "rows_with_reported_tokens": len(token_rows),
            "total": sum(r["reported_tokens"] for r in token_rows) if token_rows else None,
            "per_row": dist([r["reported_tokens"] for r in token_rows]),
        },
    }


def _flat(agg: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in (
        "first_pass_rate",
        "final_pass_rate",
        "auto_repair_success_rate",
        "repeated_failure_ratio",
        "missing_verification_ratio",
    ):
        out[f"{key}.rate"] = agg[key]["rate"]
    for key in ("retry_counts", "task_duration_ms", "tool_calls"):
        for stat in ("mean", "p50", "p95"):
            out[f"{key}.{stat}"] = agg[key][stat]
    for name, d in agg["stage_duration_ms"].items():
        for stat in ("mean", "p50", "p95"):
            out[f"stage_duration_ms.{name}.{stat}"] = d[stat]
    return out


def compare(rows_a: list[dict], rows_b: list[dict] | None = None) -> dict:
    """Aggregate group A; with `rows_b` (baseline) also B and per-metric delta `a - b`.

    A delta is null when either side is null (insufficient samples). No significance is claimed.
    """
    a = aggregate(rows_a)
    out = {"current": a, "notes": [NOTE_SAME_CODE, NOTE_NO_VERDICT]}
    if rows_b is not None:
        b = aggregate(rows_b)
        fa, fb = _flat(a), _flat(b)
        out["baseline"] = b
        out["delta"] = {
            k: (None if fa[k] is None or fb.get(k) is None else round(fa[k] - fb[k], 4))
            for k in sorted(fa)
        }
        out["notes"].append("Deltas are descriptive; no significance test is performed.")
    return out


# ---------------------------------------------------------------- output


def build_report(traces_dir, guardian_results=None, baseline=None) -> dict:
    records, corrupt = load_traces(traces_dir)
    rows = case_rows(records, guardian_results)
    base_rows = None
    if isinstance(baseline, dict) and baseline.get("schema") == SCHEMA:
        base_rows = [r for r in baseline.get("rows") or [] if isinstance(r, dict)]
        for r in base_rows:  # tolerate older rows: fill the keys aggregate() reads
            r.setdefault("stage_ms", {})
    return {
        "schema": SCHEMA,
        "min_samples": MIN_SAMPLES,
        "traces_read": len(records),
        "corrupt_files": corrupt,
        "guardian_results_supplied": guardian_results is not None,
        "rows": rows,
        **compare(rows, base_rows),
    }


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--traces", required=True, help="directory with the exec-trace JSON files")
    ap.add_argument("--guardian-results", help="guardian / taf_bench report JSON (verdict source)")
    ap.add_argument("--baseline", help="previous output of this script to compare against")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        results = _load_json(args.guardian_results) if args.guardian_results else None
        baseline = _load_json(args.baseline) if args.baseline else None
    except (OSError, ValueError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2
    report = build_report(args.traces, results, baseline)
    Path(args.out).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
