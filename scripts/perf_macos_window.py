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
import plistlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePath

# Windows 被重定向的子进程按本地代码页编码中文，必须同时固定 stdout / stderr。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "tavotto-macos-window-probe/1"
COMPARE_SCHEMA = "tavotto-macos-window-comparison/1"
STAGES = ("idle", "drag", "resize", "complex")
ACTIVE_STAGES = ("drag", "resize", "complex")


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    cpu_seconds: float
    rss_kib: int
    command: str


def parse_cpu_time(text: str) -> float:
    """ps 的累计 CPU 时间 [dd-][hh:]mm:ss[.cc] -> 秒；无法解析抛 ValueError。"""
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        days = int(day_text)
    parts = text.replace(",", ".").split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(text)
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return days * 86400 + seconds


def parse_ps(stdout: str) -> list[Process]:
    """PS 的 comm 可能有空格；只在内存里解析，不把命令名放进报告。

    第三列是累计 CPU 时间而不是 %cpu：macOS 的 %cpu 是带衰减的长窗口均值，
    不是本次采样区间的占用，区间占用由相邻两次累计时间的差分得到。
    """
    result: list[Process] = []
    for line in stdout.splitlines():
        fields = line.strip().split(None, 4)
        if len(fields) != 5:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
            cpu = parse_cpu_time(fields[2])
            rss = int(fields[3])
        except (ValueError, OverflowError):
            continue
        if pid <= 0 or ppid < 0 or cpu < 0 or rss < 0:
            continue
        result.append(Process(pid, ppid, cpu, rss, fields[4]))
    return result


def classify(processes: list[Process]) -> dict:
    """仅以 Tavotto 二进制的精确 basename 找 root；不关联其他应用的 WebKit。"""
    by_pid = {p.pid: p for p in processes}
    named = {p.pid for p in processes if Path(p.command).name == "Tavotto"}
    # 打包的 sidecar 也叫 Tavotto，但它是主进程的后代；只有父进程不是 Tavotto 的
    # 才算顶层根。孤儿 sidecar 或第二个实例各自是一棵独立的树，root_count 会 > 1。
    roots = {p.pid for p in processes if p.pid in named and p.ppid not in named}
    family = set(roots)
    changed = True
    while changed:
        changed = False
        for p in processes:
            if p.pid not in family and p.ppid in family:
                family.add(p.pid)
                changed = True
    members = [by_pid[pid] for pid in family]
    servers = [p for p in processes if Path(p.command).name == "WindowServer"]
    return {
        "root_count": len(roots),
        "family_processes": len(members),
        # 下面两个以 pid 为键的累计 CPU 时间只在内存里用于差分，record 不写进报告。
        "_roots": frozenset(roots),
        "_root_command": min((by_pid[pid].command for pid in roots), default=None),
        "_family_cpu_s": {p.pid: p.cpu_seconds for p in members},
        "_windowserver_cpu_s": {
            p.pid: p.cpu_seconds for p in processes if Path(p.command).name == "WindowServer"
        },
        # 按进程相加的 RSS 可能重复计算共享页，不是物理占用的真实总和。
        "family_rss_sum_mib": round(sum(p.rss_kib for p in members) / 1024, 2),
        # WindowServer 为系统全局值，不可归因于 Tavotto。
        "windowserver_present": bool(servers),
    }


def sample_ps() -> dict:
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,time=,rss=,comm="],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return classify(parse_ps(output.stdout))


def interval_cpu_pct(
    previous: dict[int, float], current: dict[int, float], wall_seconds: float
) -> float | None:
    """相邻两次累计 CPU 时间之差 / 墙钟间隔。

    ps 看不到已退出的进程，它退出前在本区间内用掉的 CPU 原理上补不回来，
    所以上一样本有、这一样本没有的 pid 出现时，本区间记 None（未知），不记偏小的值。
    新出现的 pid 取其全部累计时间：它出生在本区间内，累计时间就是区间内用掉的。
    """
    if wall_seconds <= 0 or any(pid not in current for pid in previous):
        return None
    used = sum(max(0.0, t - previous.get(pid, 0.0)) for pid, t in current.items())
    return round(used / wall_seconds * 100, 2)


def _run_text(cmd: list[str], timeout: float = 10) -> str | None:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if isinstance(done.stdout, str) else None


def info_plist_for_executable(exe: PurePath) -> PurePath | None:
    """<x>.app/Contents/MacOS/<exe> -> <x>.app/Contents/Info.plist；按父目录名找，不依赖分隔符。"""
    macos = exe.parent
    contents = macos.parent
    if macos.name == "MacOS" and contents.name == "Contents":
        return contents / "Info.plist"
    return None


def collect_environment(root_command: str | None) -> dict:
    """采集会影响可比性的非标识性环境事实；取不到的字段为 None（compare 视为不满足）。

    只记机型标识符、供电状态、低电量模式、各显示器的分辨率/刷新率字符串、Tavotto 版本。
    不记主机名、序列号、显示器名称或任何路径。
    """
    model = (_run_text(["sysctl", "-n", "hw.model"]) or "").strip() or None
    batt = _run_text(["pmset", "-g", "batt"])
    power_source = None
    if batt is not None:
        if "'AC Power'" in batt:
            power_source = "ac"
        elif "'Battery Power'" in batt:
            power_source = "battery"
    settings = _run_text(["pmset", "-g"])
    low_power = None
    if settings is not None:
        found = re.search(r"lowpowermode\s+(\d)", settings)
        # 没有这一行 = 本机不支持或未开启，视为 False；pmset 本身失败才是未知。
        low_power = found.group(1) == "1" if found else False
    displays = None
    raw = _run_text(["system_profiler", "SPDisplaysDataType", "-json"], timeout=30)
    if raw is not None:
        try:
            gpus = json.loads(raw).get("SPDisplaysDataType", [])
            displays = sorted(
                str(d["_spdisplays_resolution"])
                for gpu in gpus
                for d in gpu.get("spdisplays_ndrvs", [])
                if isinstance(d, dict) and d.get("_spdisplays_resolution")
            )
        except (ValueError, AttributeError, TypeError):
            displays = None
    version = None
    plist = info_plist_for_executable(Path(root_command)) if root_command else None
    if plist is not None:
        try:
            value = plistlib.loads(Path(plist).read_bytes()).get("CFBundleShortVersionString")
            version = value if isinstance(value, str) and value else None
        except (OSError, ValueError, plistlib.InvalidFileException):
            version = None
    return {
        "hw_model": model,
        "power_source": power_source,
        "low_power_mode": low_power,
        "displays": displays,
        "tavotto_version": version,
    }


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
    out["valid_family_cpu_samples"] = sum(
        1
        for s in report["samples"]
        if isinstance(s, dict)
        and isinstance(s.get("family_cpu_pct"), (int, float))
        and not isinstance(s["family_cpu_pct"], bool)
    )
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


def _valid_interval(report: dict) -> float | None:
    value = report.get("sample_interval_seconds")
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return float(value) if ok else None


def _single_family_throughout(report: dict) -> bool:
    """每个样本都必须是 dict 且带整数 root_count == 1：缺字段/类型不对/0（丢失）/>1（多树）都不满足。"""
    return all(
        isinstance(s, dict)
        and isinstance(s.get("root_count"), int)
        and not isinstance(s["root_count"], bool)
        and s["root_count"] == 1
        for s in report["samples"]
    )


ENVIRONMENT_FIELDS = ("hw_model", "power_source", "low_power_mode", "displays", "tavotto_version")


def _same_known_environment(idle: dict, drag: dict) -> bool:
    """两边都必须完整记录每个环境字段（None = 未知 = 不满足，即使两边都未知）且逐项相等。"""
    envs = (idle.get("environment"), drag.get("environment"))
    if not all(isinstance(e, dict) for e in envs):
        return False
    return all(e.get(field) is not None for e in envs for field in ENVIRONMENT_FIELDS) and all(
        envs[0][field] == envs[1][field] for field in ENVIRONMENT_FIELDS
    )


def failed_conditions(idle: dict, drag: dict, a: dict, b: dict) -> list[str]:
    """compare 的全部可比条件集中在这一张表；加条件只改这里（并同步 docs 清单）。"""
    conditions = (
        ("stages_idle_vs_active", a["stage"] == "idle" and b["stage"] in ACTIVE_STAGES),
        ("same_arch", _nonempty_str(idle.get("arch")) and idle.get("arch") == drag.get("arch")),
        (
            "same_macos_version",
            _nonempty_str(idle.get("macos_version"))
            and idle.get("macos_version") == drag.get("macos_version"),
        ),
        (
            "same_sample_interval",
            _valid_interval(idle) is not None and _valid_interval(idle) == _valid_interval(drag),
        ),
        ("same_capture_environment", _same_known_environment(idle, drag)),
        ("at_least_5_samples_each", a["count"] >= 5 and b["count"] >= 5),
        (
            "at_least_5_valid_cpu_samples_each",
            a["valid_family_cpu_samples"] >= 5 and b["valid_family_cpu_samples"] >= 5,
        ),
        (
            "single_tavotto_family_throughout",
            _single_family_throughout(idle) and _single_family_throughout(drag),
        ),
    )
    return [name for name, ok in conditions if not ok]


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def compare_reports(idle: dict, drag: dict) -> dict:
    """Agent 机器可读诊断：仅资源代理，无 GPU/frame 呈现证据，绝不返回 pass。"""
    a, b = summarize(idle), summarize(drag)
    failed = failed_conditions(idle, drag, a, b)
    comparable = not failed
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
        "failed_conditions": failed,
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
    first_roots: frozenset[int] | None = None
    first_command: str | None = None
    previous: dict | None = None
    previous_at = start
    while time.monotonic() - start < seconds:
        row = sample_ps()
        now = time.monotonic()
        if first_roots is None:
            first_roots = row["_roots"]
            first_command = row["_root_command"]
        if row["root_count"] != 1:
            raise ValueError(
                f"need exactly one top-level Tavotto process tree, found {row['root_count']}; "
                "no report was saved"
            )
        if row["_roots"] != first_roots:
            raise ValueError(
                "Tavotto process missing or restarted during capture; no report was saved"
            )
        cpu_pct = windowserver_pct = None
        exited = 0
        if previous is not None:
            exited = len(set(previous["_family_cpu_s"]) - set(row["_family_cpu_s"]))
            wall = now - previous_at
            cpu_pct = interval_cpu_pct(previous["_family_cpu_s"], row["_family_cpu_s"], wall)
            if row["windowserver_present"]:
                windowserver_pct = interval_cpu_pct(
                    previous["_windowserver_cpu_s"], row["_windowserver_cpu_s"], wall
                )
        samples.append(
            {
                "root_count": row["root_count"],
                "family_processes": row["family_processes"],
                "family_exited": exited,
                "family_cpu_pct": cpu_pct,
                "family_rss_sum_mib": row["family_rss_sum_mib"],
                "windowserver_global_cpu_pct": windowserver_pct,
                "elapsed_ms": round((now - start) * 1000, 1),
            }
        )
        previous, previous_at = row, now
        time.sleep(interval)
    if not samples:
        raise ValueError("no samples collected; no report was saved")
    return {
        "schema": SCHEMA,
        "stage": stage,
        "macos_version": platform.mac_ver()[0],
        "arch": platform.machine(),
        "requested_seconds": seconds,
        "sample_interval_seconds": interval,
        "environment": collect_environment(first_command),
        "samples": samples,
        "limits": [
            "rss_is_sum_with_possible_shared_pages",
            "windowserver_is_system_wide_not_app_attribution",
            "webkit_webcontent_may_not_be_descendant",
            "no_frame_or_gpu_presentation_measurement",
            "cpu_is_cputime_delta_between_polls_10ms_resolution",
            "cpu_unknown_when_a_descendant_exits_short_lived_children_invisible",
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
    compare.add_argument(
        "--json", type=Path, help="numeric-only machine-readable diagnostic result"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            output = record(args.stage, args.seconds, args.interval)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
            print(f"Saved {args.output.name}: {len(output['samples'])} numeric-only samples")
        else:
            reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
            if len(reports) != 2:
                raise ValueError("compare needs exactly two reports: idle then active")
            for source in reports:
                print(json.dumps(summarize(source), ensure_ascii=False))
            result = compare_reports(*reports)
            print(f"Comparison status: {result['status']} (never pass)")
            if result["failed_conditions"]:
                print("Failed conditions: " + ", ".join(result["failed_conditions"]))
            else:
                print("delta_p95: " + json.dumps(result["delta_p95"]))
            if args.json:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                tmp = args.json.with_suffix(args.json.suffix + ".tmp")
                tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(args.json)
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
