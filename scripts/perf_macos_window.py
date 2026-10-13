#!/usr/bin/env python3
"""macOS Tavotto 原生窗口移动现场取样（只用标准库）。

测的是 Tavotto 进程家族的 CPU/RSS，以及系统全局 WindowServer CPU。
这些值**不是**窗口呈现延迟、GPU 负载或拖影率；WebKit 子进程可能不在家族内。
真相要与 Instruments 的 Hangs/Time Profiler/Animation Hitches 时间线对照。

用法:
    python scripts/perf_macos_window.py record --stage idle --seconds 12 --output idle.json
    python scripts/perf_macos_window.py record --stage drag --seconds 12 --output drag.json
    python scripts/perf_macos_window.py compare idle.json drag.json
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Windows 被重定向的子进程按本地代码页编码中文，必须同时固定 stdout / stderr。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "tavotto-macos-window-probe/1"
COMPARE_SCHEMA = "tavotto-macos-window-comparison/1"
STAGES = ("idle", "drag", "resize", "complex")


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    cpu: float
    rss_kib: int
    command: str


def parse_ps(stdout: str) -> list[Process]:
    """PS 的 comm 可能有空格；只在内存里解析，不把命令名放进报告。"""
    result: list[Process] = []
    for line in stdout.splitlines():
        fields = line.strip().split(None, 4)
        if len(fields) != 5:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
            cpu = float(fields[2].replace(",", "."))
            rss = int(fields[3])
        except (ValueError, OverflowError):
            continue
        if pid <= 0 or ppid < 0 or cpu < 0 or rss < 0:
            continue
        result.append(Process(pid, ppid, cpu, rss, fields[4]))
    return result


def classify(processes: list[Process]) -> dict[str, float | int | None]:
    """仅以 Tavotto 二进制的精确 basename 找 root；不关联其他应用的 WebKit。"""
    by_pid = {p.pid: p for p in processes}
    roots = {p.pid for p in processes if Path(p.command).name == "Tavotto"}
    family = set(roots)
    changed = True
    while changed:
        changed = False
        for p in processes:
            if p.pid not in family and p.ppid in family:
                family.add(p.pid)
                changed = True
    members = [by_pid[pid] for pid in family]
    servers = [p.cpu for p in processes if Path(p.command).name == "WindowServer"]
    return {
        "root_count": len(roots),
        "family_processes": len(members),
        "family_cpu_pct": round(sum(p.cpu for p in members), 2),
        # 按进程相加的 RSS 可能重复计算共享页，不是物理占用的真实总和。
        "family_rss_sum_mib": round(sum(p.rss_kib for p in members) / 1024, 2),
        # WindowServer 为系统全局值，不可归因于 Tavotto。
        "windowserver_global_cpu_pct": round(sum(servers), 2) if servers else None,
    }


def sample_ps() -> dict[str, float | int | None]:
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pcpu=,rss=,comm="],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return classify(parse_ps(output.stdout))


def quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    values = sorted(xs)
    return round(values[round(q * (len(values) - 1))], 2)


def summarize(report: dict) -> dict:
    if (
        report.get("schema") != SCHEMA
        or report.get("stage") not in STAGES
        or not isinstance(report.get("samples"), list)
    ):
        raise ValueError("invalid Tavotto macOS window probe report")
    out = {"stage": report.get("stage"), "count": len(report["samples"])}
    for field in ("family_cpu_pct", "family_rss_sum_mib", "windowserver_global_cpu_pct"):
        vals = [
            float(s[field])
            for s in report["samples"]
            if isinstance(s, dict)
            and isinstance(s.get(field), (int, float))
            and not isinstance(s[field], bool)
        ]
        out[field] = {"p50": quantile(vals, 0.5), "p95": quantile(vals, 0.95)}
    return out


def compare_reports(idle: dict, drag: dict) -> dict:
    """Agent 机器可读诊断：仅资源代理，无 GPU/frame 呈现证据，绝不返回 pass。"""
    a, b = summarize(idle), summarize(drag)
    comparable = (
        a["stage"] == "idle"
        and b["stage"] in ("drag", "resize", "complex")
        and idle.get("arch") == drag.get("arch")
        and idle.get("macos_version") == drag.get("macos_version")
        and isinstance(idle.get("arch"), str)
        and bool(idle["arch"])
        and isinstance(idle.get("macos_version"), str)
        and bool(idle["macos_version"])
        and a["count"] >= 5
        and b["count"] >= 5
    )
    deltas: dict[str, float | None] = {}
    if comparable:
        for key in ("family_cpu_pct", "family_rss_sum_mib", "windowserver_global_cpu_pct"):
            x = a[key]["p95"]
            y = b[key]["p95"]
            deltas[key] = round(y - x, 2) if x is not None and y is not None else None
    return {
        "schema": COMPARE_SCHEMA,
        "status": "diagnostic_only" if comparable else "incomparable",
        "evidence": "process_resource_proxies_not_frame_or_gpu_presentation",
        "idle": a,
        "active": b,
        "delta_p95": deltas,
        "not_covered": ["actual_window_drag_latency", "gpu_compositor", "display_ghosting"],
    }


def record(stage: str, seconds: float, interval: float) -> dict:
    if sys.platform != "darwin":
        raise ValueError("macOS only: needs real Tavotto.app and WindowServer")
    if seconds < 2 or seconds > 120 or interval < 0.1 or interval > 2:
        raise ValueError("require 2 <= seconds <= 120 and 0.1 <= interval <= 2")
    start = time.monotonic()
    samples = []
    while time.monotonic() - start < seconds:
        row = sample_ps()
        row["elapsed_ms"] = round((time.monotonic() - start) * 1000, 1)
        samples.append(row)
        time.sleep(interval)
    if not any(row["root_count"] > 0 for row in samples):
        raise ValueError("Tavotto process not found; no report was saved")
    return {
        "schema": SCHEMA,
        "stage": stage,
        "macos_version": platform.mac_ver()[0],
        "arch": platform.machine(),
        "requested_seconds": seconds,
        "sample_interval_seconds": interval,
        "samples": samples,
        "limits": [
            "rss_is_sum_with_possible_shared_pages",
            "windowserver_is_system_wide_not_app_attribution",
            "webkit_webcontent_may_not_be_descendant",
            "no_frame_or_gpu_presentation_measurement",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tavotto macOS window diagnostic")
    subs = parser.add_subparsers(dest="command", required=True)
    capture = subs.add_parser("record")
    capture.add_argument("--stage", choices=STAGES, required=True)
    capture.add_argument("--seconds", type=float, default=12)
    capture.add_argument("--interval", type=float, default=0.25)
    capture.add_argument("--output", type=Path, required=True)
    compare = subs.add_parser("compare")
    compare.add_argument("reports", type=Path, nargs="+")
    compare.add_argument("--json", type=Path, help="numeric-only machine-readable diagnostic result")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            output = record(args.stage, args.seconds, args.interval)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
            print(f"Saved {args.output.name}: {len(output['samples'])} numeric-only samples")
        else:
            reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
            for source in reports:
                print(json.dumps(summarize(source), ensure_ascii=False))
            if args.json:
                if len(reports) != 2:
                    raise ValueError("--json needs exactly two reports: idle then active")
                result = compare_reports(*reports)
                args.json.parent.mkdir(parents=True, exist_ok=True)
                tmp = args.json.with_suffix(args.json.suffix + ".tmp")
                tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(args.json)
                print(f"Machine-readable status: {result['status']} (never pass)")
            print(
                "WindowServer CPU is system-wide; a delta cannot prove Tavotto caused dragging ghosts."
            )
            print("Use Instruments and video/high-speed capture for actual presentation evidence.")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Window probe unavailable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
