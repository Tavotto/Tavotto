#!/usr/bin/env python3
"""Tavotto 交互性能 Agent CLI：后台运行/汇总/同机基线对照（stdlib）。

Agent 可直接执行：
  python scripts/perf_agent.py run --browser chromium --repeat 16 --out perf-run.json
  python scripts/perf_agent.py summarize perf-run.json --json summary.json
  python scripts/perf_agent.py compare perf-run.json baseline.json --json comparison.json

不基于未验证的硬件绝对阈值放行。不同平台/浏览器/分辨率或样本不足返回
incomparable/no_baseline，绝不伪装成 pass。窗口拖影不在本自动化浏览器覆盖范围。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "tavotto-perf-agent/1"
COMPARE_SCHEMA = "tavotto-perf-comparison/1"
REPO = Path(__file__).resolve().parents[1]
ALLOWED_SCENARIOS = {
    "dialog.presets_open",
    "symbol.alpha",
    "symbol.superscript_2",
    "symbol.micron",
    "preset.dimension",
}
MIN_SAMPLES = 10
# 第二次 Playwright 执行必须使用独立结果目录，不能清掉第一次的 run.json！
DRAG_E2E_OUTPUT = "test-results-perf-drag"
# 不同 SHA 可以比较，但机器与浏览器完整版本必须匹配。
ENV_FIELDS = (
    "platform",
    "arch",
    "browser",
    "browser_major",
    "browser_version",
    "viewport_w",
    "viewport_h",
    "dpr",
    "headless",
    "os_release",
    "cpu_model",
    "cpu_count",
    "total_mem_gib",
)
DEFAULT_REGRESSION_PCT = 25.0
DEFAULT_ABSOLUTE_DELTA_MS = 15.0


def _percentile(samples: list[float], q: float) -> float:
    s = sorted(samples)
    return round(s[round((len(s) - 1) * q)], 2)


def load(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("report_missing_or_too_large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("invalid_report_schema")
    if not isinstance(data.get("environment"), dict) or not isinstance(data.get("scenarios"), list):
        raise ValueError("invalid_report_structure")
    return data


def summarize(data: dict) -> dict:
    env = data["environment"]
    # 白名单字段，不会把测试机的路径、进程命令、用户名、页面数据写出。
    environment = {k: env.get(k) for k in ENV_FIELDS}
    strings = ("platform", "arch", "browser", "browser_version", "os_release", "cpu_model")
    positive = ("browser_major", "viewport_w", "viewport_h", "dpr", "cpu_count", "total_mem_gib")
    env_ok = (
        all(isinstance(environment[k], str) and 0 < len(environment[k]) <= 256 for k in strings)
        and environment["browser"] in ("chromium", "webkit")
        and isinstance(environment["headless"], bool)
        and all(
            isinstance(environment[k], (int, float))
            and not isinstance(environment[k], bool)
            and 0 < environment[k] < 100_000
            for k in positive
        )
    )
    rows = {}
    invalid_scenarios = False
    for raw in data["scenarios"]:
        if not isinstance(raw, dict):
            invalid_scenarios = True
            continue
        name = raw.get("id")
        if not isinstance(name, str) or name not in ALLOWED_SCENARIOS or name in rows:
            invalid_scenarios = True
            continue
        values = raw.get("two_raf_ms")
        if not isinstance(values, list):
            invalid_scenarios = True
            continue
        valid = [
            float(x)
            for x in values
            if isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x < 120_000
        ]
        # 无完整样本意味着一次操作没测完；不能在剩下的「成功样本」上报绿。
        requested = data.get("iterations")
        per_round = 4 if name == "dialog.presets_open" else 1
        count_expected = requested * per_round if type(requested) is int else None
        rows[name] = {
            "count": len(valid),
            "expected_count": count_expected,
            "p50_ms": _percentile(valid, 0.50) if valid else None,
            "p95_ms": _percentile(valid, 0.95) if valid else None,
            "max_ms": round(max(valid), 2) if valid else None,
        }
    expected = data.get("iterations")
    complete = (
        isinstance(expected, int)
        and not isinstance(expected, bool)
        and expected >= 1
        and env_ok
        and not invalid_scenarios
        and set(rows) == ALLOWED_SCENARIOS
        and all(x["count"] == x["expected_count"] for x in rows.values())
    )
    return {
        "schema": SCHEMA,
        "status": "ok" if complete else "incomplete",
        "coverage": "browser_webview_proxy_only",
        "regression_status": "not_checked_without_baseline",
        "not_covered": ["native_window_drag", "gpu_compositor", "macos_windowserver"],
        "environment": environment,
        "scenarios": rows,
    }


def compare(current: dict, baseline: dict, regression_pct: float = DEFAULT_REGRESSION_PCT) -> dict:
    now, old = summarize(current), summarize(baseline)
    if now["status"] != "ok" or old["status"] != "ok":
        status, rows = "incomparable", []
    elif now["environment"] != old["environment"]:
        status, rows = "incomparable", []
    elif any(
        now["scenarios"][key]["count"] < MIN_SAMPLES or old["scenarios"][key]["count"] < MIN_SAMPLES
        for key in ALLOWED_SCENARIOS
    ):
        status, rows = "incomparable", []
    else:
        rows = []
        for key in sorted(ALLOWED_SCENARIOS):
            c = now["scenarios"][key]["p95_ms"]
            b = old["scenarios"][key]["p95_ms"]
            if c is None or b is None or b <= 0:
                return {
                    "schema": COMPARE_SCHEMA,
                    "status": "incomparable",
                    "rows": [],
                    "reason": "invalid_baseline_p95",
                }
            delta_ms = round(c - b, 2)
            delta_pct = round(delta_ms / b * 100, 2)
            regression = delta_pct > regression_pct and delta_ms >= DEFAULT_ABSOLUTE_DELTA_MS
            rows.append(
                {
                    "id": key,
                    "baseline_p95_ms": b,
                    "current_p95_ms": c,
                    "delta_ms": delta_ms,
                    "delta_pct": delta_pct,
                    "verdict": "regression" if regression else "ok",
                }
            )
        status = "regression" if any(x["verdict"] == "regression" for x in rows) else "pass"
    return {
        "schema": COMPARE_SCHEMA,
        "status": status,
        "reason": "environment_or_sample_mismatch" if status == "incomparable" else None,
        "regression_threshold_pct": regression_pct,
        "minimum_absolute_delta_ms": DEFAULT_ABSOLUTE_DELTA_MS,
        "minimum_samples": MIN_SAMPLES,
        "rows": rows,
    }


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _print_summary(result: dict) -> None:
    print(f"Status: {result['status']} (browser/WebView proxy; native window NOT tested)")
    for key, row in sorted(result["scenarios"].items()):
        print(
            f"  {key}: {row['count']} samples, p50={row['p50_ms']}ms, "
            f"p95={row['p95_ms']}ms, max={row['max_ms']}ms"
        )


def _append_gha_summary(result: dict) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as f:
        f.write("\n## Tavotto Performance Agent · 浏览器交互\n\n")
        f.write(f"结果：**{result['status']}**；原生窗口合成未覆盖。\n\n")
        f.write("| 场景 | 样本数 | P50 ms | P95 ms | 最大 ms |\n|---|---:|---:|---:|---:|\n")
        for k, v in sorted(result["scenarios"].items()):
            f.write(f"| {k} | {v['count']} | {v['p50_ms']} | {v['p95_ms']} | {v['max_ms']} |\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Agent-friendly Tavotto performance CLI")
    commands = p.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run real UI interactions automatically")
    run.add_argument("--browser", choices=("chromium", "webkit"), default="chromium")
    run.add_argument("--repeat", type=int, default=16)
    run.add_argument("--out", type=Path, default=Path("web/test-results/perf-agent/run.json"))
    run.add_argument("--baseline", type=Path)
    run.add_argument(
        "--include-drag", action="store_true", help="also capture the existing ADR 0075 drag probe"
    )
    run.add_argument("--summary", type=Path)
    run.add_argument("--regression-pct", type=float, default=DEFAULT_REGRESSION_PCT)
    report = commands.add_parser("summarize")
    report.add_argument("report", type=Path)
    report.add_argument("--json", type=Path)
    comp = commands.add_parser("compare")
    comp.add_argument("current", type=Path)
    comp.add_argument("baseline", type=Path)
    comp.add_argument("--json", type=Path)
    comp.add_argument("--regression-pct", type=float, default=DEFAULT_REGRESSION_PCT)
    args = p.parse_args(argv)

    try:
        if args.command == "run":
            if not 3 <= args.repeat <= 60 or not 1 <= args.regression_pct <= 200:
                raise ValueError("invalid_run_parameters")
            output = args.out.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.unlink(missing_ok=True)  # 不许把上一轮成功报告误当作本轮通过
            env = {
                **os.environ,
                "TAVOTTO_PERF_AGENT_OUTPUT": str(output),
                "TAVOTTO_PERF_AGENT_REPEATS": str(args.repeat),
                "TAVOTTO_NO_TELEMETRY": "1",
                "TAVOTTO_NO_DIAG_UPLOAD": "1",
            }
            proc = subprocess.run(
                [
                    "pnpm",
                    "exec",
                    "playwright",
                    "test",
                    "--config",
                    "playwright.perf.config.ts",
                    "--project",
                    args.browser,
                ],
                cwd=REPO / "web",
                env=env,
                check=False,
                timeout=1800,
            )
            if proc.returncode != 0 or not output.is_file():
                print("error: perf_agent_browser_run_failed", file=sys.stderr)
                return 2
            result = summarize(load(output))
            _print_summary(result)
            _append_gha_summary(result)
            if args.summary:
                _write(args.summary, result)
            if result["status"] != "ok":
                return 2
            if args.include_drag:
                # 使用仓库既有 E2E：真实拖动 + 两轮合成输入，严格收尾且不改原图。
                # 其报告由原有浏览器测试产生，禁止为了测性能另写一个假的拖动协议。
                drag_path = output.with_name("drag.json")
                findings_path = output.with_name("drag-findings.json")
                drag_path.unlink(missing_ok=True)
                findings_path.unlink(missing_ok=True)
                drag_env = {**env, "TAVOTTO_PERF_REPORT_OUT": str(drag_path)}
                drag_run = subprocess.run(
                    [
                        "pnpm",
                        "exec",
                        "playwright",
                        "test",
                        "--config",
                        "playwright.config.ts",
                        "--project",
                        args.browser,
                        "--output",
                        DRAG_E2E_OUTPUT,
                        "--grep",
                        "性能探针：录制真实拖动",
                        "e2e/perf-probe.spec.ts",
                    ],
                    cwd=REPO / "web",
                    env=drag_env,
                    check=False,
                    timeout=900,
                )
                if drag_run.returncode != 0 or not drag_path.is_file():
                    print("error: perf_agent_drag_probe_failed", file=sys.stderr)
                    return 2
                analysis = subprocess.run(
                    [
                        sys.executable,
                        str(REPO / "scripts" / "perf_report.py"),
                        str(drag_path),
                        "--json",
                        str(findings_path),
                    ],
                    cwd=REPO,
                    env=drag_env,
                    check=False,
                    timeout=120,
                )
                if analysis.returncode != 0 or not findings_path.is_file():
                    print("error: perf_agent_drag_analysis_failed", file=sys.stderr)
                    return 2
                print(f"Drag probe: recorded and analyzed (browser={args.browser})")
            if args.baseline:
                assessment = compare(load(output), load(args.baseline), args.regression_pct)
                _write(output.with_suffix(".comparison.json"), assessment)
                print(f"Baseline verdict: {assessment['status']}")
                return 0 if assessment["status"] == "pass" else 3
            print("Baseline verdict: not_checked (no baseline provided)")
            return 0
        if args.command == "summarize":
            result = summarize(load(args.report))
            _print_summary(result)
            if args.json:
                _write(args.json, result)
            return 0 if result["status"] == "ok" else 2
        if not 1 <= args.regression_pct <= 200:
            raise ValueError("invalid_regression_pct")
        result = compare(load(args.current), load(args.baseline), args.regression_pct)
        print(f"Comparison: {result['status']}")
        if args.json:
            _write(args.json, result)
        return 0 if result["status"] == "pass" else 3
    except (OSError, json.JSONDecodeError, ValueError, subprocess.TimeoutExpired) as exc:
        # 不把用户路径或操作数据写进控制台。
        print(f"error: {type(exc).__name__} (invalid or unavailable benchmark)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
