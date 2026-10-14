#!/usr/bin/env python3
"""汇总真实后端 manifest 热态分段计时；拒绝用不完整数据宣称性能 PASS。"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "tavotto-perf-manifest/1"
KEYS = (
    "manifest_ms",
    "manifest_prepare_ms",
    "manifest_layout_ms",
    "manifest_measure_ms",
    "manifest_final_geometry_ms",
    "canvas_draw_ms",
    "wall_ms",
)


def _valid(value: object) -> bool:
    # Python int 可以远超 float 范围：先比较整数区间，再调用 isfinite，
    # 避免 10**400 之类合法 JSON 整数触发 OverflowError，破坏文档承诺的 rc=2。
    return type(value) in (int, float) and 0 <= value < 120_000 and math.isfinite(value)


def _percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    return round(xs[round((len(xs) - 1) * q)], 3)


def summarize(report: dict, min_samples: int = 9) -> dict:
    """只输出数字与场景序号，用户文件名/路径不进入 Agent 可读取的结果。"""
    if not isinstance(report, dict) or not isinstance(report.get("rows"), list):
        raise ValueError("invalid_benchmark")
    if not 3 <= min_samples <= 100:
        raise ValueError("invalid_min_samples")

    scenarios: list[dict] = []
    complete = bool(report["rows"])
    for i, row in enumerate(report["rows"]):
        samples = row.get("hot_samples") if isinstance(row, dict) else None
        if not isinstance(samples, list):
            complete = False
            scenarios.append({"scenario": i + 1, "status": "missing_samples"})
            continue
        valid = len(samples) >= min_samples and row.get("hot_n") == len(samples)
        metrics: dict[str, list[float]] = {name: [] for name in KEYS}
        for sample in samples:
            if not isinstance(sample, dict) or any(not _valid(sample.get(k)) for k in KEYS):
                valid = False
                continue
            prep = float(sample["manifest_prepare_ms"])
            final = float(sample["manifest_final_geometry_ms"])
            total = float(sample["manifest_ms"])
            layout = float(sample["manifest_layout_ms"])
            measure = float(sample["manifest_measure_ms"])
            if abs((prep + final) - total) > 0.01 or abs(prep - layout - measure) > max(
                2.0, prep * 0.05
            ):
                valid = False
            for k in KEYS:
                metrics[k].append(float(sample[k]))
        valid = valid and len(metrics["manifest_ms"]) == len(samples)
        complete = complete and valid
        stats = {
            name: {
                "p50_ms": _percentile(values, 0.5),
                "p95_ms": _percentile(values, 0.95),
                "max_ms": round(max(values), 3),
            }
            for name, values in metrics.items()
            if values
        }
        scenarios.append(
            {
                "scenario": i + 1,
                "status": "recorded" if valid else "incomplete",
                "sample_count": len(samples),
                "valid_sample_count": len(metrics["manifest_ms"]),
                "phases": stats,
            }
        )
    return {
        "schema": SCHEMA,
        "status": "recorded" if complete else "incomplete",
        "comparison_verdict": "not_checked_without_pinned_same_machine_baseline",
        "coverage": "actual_worker_render_timings",
        "scenarios": scenarios,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Produce anonymized manifest timing report")
    ap.add_argument("raw", type=Path)
    ap.add_argument("--json", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if not args.raw.is_file() or args.raw.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("bad_file")
        report = summarize(json.loads(args.raw.read_text(encoding="utf-8")))
        args.json.parent.mkdir(parents=True, exist_ok=True)
        temp = args.json.with_suffix(args.json.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(args.json)
        print("manifest benchmark: " + report["status"])
        return 0 if report["status"] == "recorded" else 2
    except (OSError, ValueError):
        print("manifest benchmark unavailable: invalid data or file", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
