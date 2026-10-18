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
import shutil
import subprocess
import sys
import tempfile
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
        if len(valid) != len(values):
            invalid_scenarios = True
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


def failed_run_summary(include_drag: bool) -> dict:
    """浏览器还没产出有效 run.json 就失败时，也必须给 Agent 一个明确状态。"""
    return {
        "schema": SCHEMA,
        "status": "failed",
        "comparison_verdict": "not_checked",
        "coverage": "browser_webview_proxy_only",
        "scenarios": {},
        "suites": {
            "symbol_ui": "failed",
            "canvas_drag": "not_started" if include_drag else "not_requested",
        },
        "not_covered": ["native_window_drag", "gpu_compositor", "macos_windowserver"],
    }


def compose_run_summary(symbol: dict, drag_status: str) -> dict:
    """Agent 所读的 summary.json 必须反映整次任务，不能因第一段绿掩盖第二段失败。"""
    if drag_status not in ("not_requested", "not_started", "pending", "ok", "failed"):
        raise ValueError("invalid_drag_status")
    symbol_status = symbol.get("status")
    if symbol_status in ("pending", "failed"):
        whole = symbol_status
    elif symbol_status != "ok":
        whole = "incomplete"
    elif drag_status == "failed":
        whole = "failed"
    elif drag_status in ("pending", "not_started"):
        whole = "pending"
    elif symbol.get("regression_status") == "pending":
        whole = "pending"
    else:
        whole = "ok"
    return {
        **symbol,
        "status": whole,
        "suites": {"symbol_ui": symbol_status, "canvas_drag": drag_status},
    }


def comparison_run_summary(symbol: dict, drag_status: str, comparison: dict) -> dict:
    """比较未通过时，整个 run 不得继续显示 ok / not_checked。"""
    verdict = comparison.get("status")
    if verdict not in ("pass", "regression", "incomparable"):
        raise ValueError("invalid_comparison_verdict")
    result = compose_run_summary({**symbol, "regression_status": verdict}, drag_status)
    result["comparison"] = comparison
    if result["status"] == "ok" and verdict != "pass":
        result["status"] = verdict
    return result


def compare(current: dict, baseline: dict, regression_pct: float = DEFAULT_REGRESSION_PCT) -> dict:
    now, old = summarize(current), summarize(baseline)
    if now["status"] != "ok" or old["status"] != "ok":
        status, rows = "incomparable", []
    elif now["environment"] != old["environment"]:
        status, rows = "incomparable", []
    elif current["iterations"] != baseline["iterations"]:
        # 场景每轮都新增对象，不同 repeat 不只是样本数不同，也是画布负载不同。
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
    """原子替换报告；随机独占临时文件避免误写同名 .tmp 历史采样。

    曾使用固定 <output>.tmp：如果该路径恰好是 baseline 或其软/硬链接，
    写报告就会先截断只读输入。mkstemp 使用 O_EXCL，写完关闭后再 replace。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            json.dump(value, writer, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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
        f.write(f"整体测试结果：**{result['status']}**；原生窗口合成未覆盖。\n\n")
        suites = result.get("suites") or {}
        if suites:
            f.write(
                f"子测试：符号 **{suites.get('symbol_ui')}**；"
                f"画布拖动 **{suites.get('canvas_drag')}**。\n\n"
            )
        f.write("| 场景 | 样本数 | P50 ms | P95 ms | 最大 ms |\n|---|---:|---:|---:|---:|\n")
        for k, v in sorted(result["scenarios"].items()):
            f.write(f"| {k} | {v['count']} | {v['p50_ms']} | {v['p95_ms']} | {v['max_ms']} |\n")


def _option_path(argv: list[str], flag: str) -> Path | None:
    """解析失败后的保底提取：只看明确的 --summary / --json / --out，不猜未知选项。"""
    for i, token in enumerate(argv):
        # argparse 标准 -- 终止符后面全是位置数据：后面的 --out 等绝不能误删文件。
        if token == "--":
            break
        if token.startswith(flag + "="):
            value = token[len(flag) + 1 :]
            return Path(value) if value else None
        if token == flag and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            return Path(argv[i + 1])
    return None


_PATH_OPTIONS = ("--out", "--summary", "--baseline", "--json")


def _path_options_ambiguous(argv: list[str]) -> bool:
    """路径类选项出现超过一次（含 --opt=val 与 --opt val 混用）即视为有歧义。

    手写扫描取第一次出现，argparse 取最后一次；重复时二者会指向不同文件。
    只数 `--` 之前的 token，与 _option_path 的终止符语义一致。
    """
    head = argv[: argv.index("--")] if "--" in argv else argv
    for flag in _PATH_OPTIONS:
        hits = sum(1 for t in head if t == flag or t.startswith(flag + "="))
        if hits > 1:
            return True
    return False


class _SingleUse(argparse.Action):
    """路径类选项只允许出现一次，正常解析与失败后的恢复扫描语义一致。"""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, f"_seen_{self.dest}", False):
            parser.error(f"{option_string} may be given only once")
        setattr(namespace, f"_seen_{self.dest}", True)
        setattr(namespace, self.dest, values)


def _same_report_path(a: Path, b: Path) -> bool:
    """正规化路径 + 硬链接同 inode：两者不能作为独立的输入和输出。"""
    x, y = a.resolve(), b.resolve()
    if x == y:
        return True
    try:
        return x.samefile(y)
    except FileNotFoundError:
        return False


def _protected_parse_positionals(argv: list[str]) -> list[Path]:
    """从错误的 CLI 中保守提取只读位置输入，不能假设 argv[1:3]。

    argparse 允许选项在位置参数前/中/后出现，--regression-pct 的非法值也
    会占一个 token。只跳过已知带值选项；不确定的裸 token 一律当输入保护。
    这样宁可留下旧输出让本次返回 rc=2，也绝不抹掉用户的 current/baseline。
    """
    if not argv or argv[0] not in ("compare", "summarize"):
        return []
    value_options = (
        {"--json", "--summary", "--regression-pct"} if argv[0] == "compare" else {"--json"}
    )
    protected: list[Path] = []
    i = 1
    positional_mode = False
    while i < len(argv):
        arg = argv[i]
        if arg == "--" and not positional_mode:
            positional_mode = True
            i += 1
            continue
        if not positional_mode and arg in value_options:
            i += 2
            continue
        if not positional_mode and any(arg.startswith(key + "=") for key in value_options):
            i += 1
            continue
        if positional_mode or not arg.startswith("-"):
            protected.append(Path(arg))
        i += 1
    return protected


_FINDINGS_KEYS = {"report", "machine", "grades", "headroom", "findings", "segments"}


def _is_tool_artifact(path: Path, kind: str) -> bool:
    """只有能证明是本工具产物才允许清理；读不出来 / 不认识的一律当用户数据。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if kind == "run":
        return isinstance(data, dict) and data.get("schema") == SCHEMA
    if kind == "drag":
        return isinstance(data, dict) and data.get("schema") == "tavotto-perf-probe/1"
    if kind == "comparison":
        return isinstance(data, dict) and data.get("schema") == COMPARE_SCHEMA
    # perf_report.py --json 写出的是无 schema 的列表，按其固定键集判断。
    return (
        isinstance(data, list)
        and bool(data)
        and all(isinstance(row, dict) and _FINDINGS_KEYS <= row.keys() for row in data)
    )


def _clear_run_outputs(
    output: Path,
    summary: Path | None,
    baseline: Path | None,
    owned_output_only: bool = False,
) -> None:
    """清理本工具的旧派生证据；run 与参数解析失败共用同一规则。

    owned_output_only：解析失败路径下 --out 不一定是本次要写的文件，
    只有它自己是本工具产物才删（本工具产物的判定唯一出处是 _is_tool_artifact）。
    """
    output = output.resolve()
    if not owned_output_only or (output.is_file() and _is_tool_artifact(output, "run")):
        output.unlink(missing_ok=True)
    for stale, kind in (
        (output.with_name("drag.json"), "drag"),
        (output.with_name("drag-findings.json"), "findings"),
        (output.with_suffix(".comparison.json"), "comparison"),
    ):
        if (baseline is not None and _same_report_path(stale, baseline)) or (
            summary is not None and _same_report_path(stale, summary)
        ):
            continue
        if stale.is_file() and _is_tool_artifact(stale, kind):
            stale.unlink(missing_ok=True)


def _run_io_collision(
    output: Path, summary: Path | None, baseline: Path | None, include_drag: bool
) -> bool:
    """跑测试前检查输出/sidecar/历史基线的所有别名（包括硬链接）。"""
    writable = [output]
    if include_drag:
        writable += [output.with_name("drag.json"), output.with_name("drag-findings.json")]
    if baseline is not None:
        writable.append(output.with_suffix(".comparison.json"))
    if summary is not None:
        writable.append(summary)
    for i, candidate in enumerate(writable):
        if baseline is not None and _same_report_path(candidate, baseline):
            return True
        if any(_same_report_path(candidate, other) for other in writable[:i]):
            return True
    return False


def _flag_before_terminator(argv: list[str], flag: str) -> bool:
    """与 argparse 一致：`--` 之后全是位置数据，不再当作选项。"""
    head = argv[: argv.index("--")] if "--" in argv else argv
    return flag in head


def _invalidate_parse_error(argv: list[str]) -> None:
    """argparse 先于运行态抛 SystemExit 时，也必须作废指定位置的旧成功结论。

    主语是 Agent 交付的文件，而不是命令行的错误退出码；没有参数时不写默认路径。
    """
    if not argv:
        return
    if _path_options_ambiguous(argv):
        # 恢复扫描与 argparse 对重复选项的取值不同：无法证明删的是哪个文件，就都不动。
        return
    command = argv[0]
    json_path = _option_path(argv, "--json")
    summary_path = _option_path(argv, "--summary")
    try:
        if command == "run":
            output = _option_path(argv, "--out")
            if output is not None:
                output = output.resolve()
            baseline = _option_path(argv, "--baseline")
            # 在解析失败时也要遵守基线只读原则；之前直接 unlink 会把
            # --out 与 --baseline 同一路径的历史采样永久删除。
            collision = output is not None and _run_io_collision(
                output, summary_path, baseline, _flag_before_terminator(argv, "--include-drag")
            )
            if output is not None and not collision:
                # 解析失败时 --out 的内容没人确认过：连主报告也只清本工具产物。
                _clear_run_outputs(output, summary_path, baseline, owned_output_only=True)
            if summary_path is not None and not (
                baseline is not None and _same_report_path(summary_path, baseline)
            ):
                # summary 与主报告/派生文件撞名时，也禁止在解析失败后误写数据。
                outputs = [output] if output is not None else []
                if output is not None and _flag_before_terminator(argv, "--include-drag"):
                    outputs.extend(
                        [output.with_name("drag.json"), output.with_name("drag-findings.json")]
                    )
                if output is not None and baseline is not None:
                    outputs.append(output.with_suffix(".comparison.json"))
                if all(not _same_report_path(summary_path, candidate) for candidate in outputs):
                    failure = failed_run_summary(_flag_before_terminator(argv, "--include-drag"))
                    if baseline is not None:
                        failure["regression_status"] = "failed"
                    _write(summary_path, failure)
        elif command in ("compare", "summarize"):
            failed = {
                "schema": COMPARE_SCHEMA if command == "compare" else SCHEMA,
                "status": "failed",
                "reason": "invalid_command_arguments",
            }
            if command == "compare":
                failed["rows"] = []
            # 不能按 argv[1:3] 读取：argparse 允许 option 和 positional
            # 交错，也要保护那些出现在非法 --regression-pct 后面的基线。
            protected = _protected_parse_positionals(argv)

            def can_write(path: Path | None) -> bool:
                return path is not None and not any(
                    _same_report_path(path, source) for source in protected
                )

            if can_write(json_path):
                _write(json_path, failed)
            if (
                command == "compare"
                and can_write(summary_path)
                and (json_path is None or not _same_report_path(summary_path, json_path))
            ):
                previous = {}
                if summary_path.is_file():
                    try:
                        previous = json.loads(summary_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                if not isinstance(previous, dict) or previous.get("schema") != SCHEMA:
                    previous = {"schema": SCHEMA}
                _write(
                    summary_path,
                    {
                        **previous,
                        "status": "failed",
                        "regression_status": "failed",
                        "comparison": failed,
                    },
                )
    except OSError:
        # 输出目录无权限时命令仍失败，不得因报告写入失败变成退出 0。
        pass


def main(argv: list[str] | None = None) -> int:
    # 禁用唯一前缀的长选项缩写：错误路径扫描只认完整名称，
    # 解析器与失败报告清理器必须共享同一套 CLI 语法。
    p = argparse.ArgumentParser(
        description="Agent-friendly Tavotto performance CLI", allow_abbrev=False
    )
    commands = p.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="run real UI interactions automatically", allow_abbrev=False
    )
    run.add_argument("--browser", choices=("chromium", "webkit"), default="chromium")
    run.add_argument("--repeat", type=int, default=16)
    run.add_argument(
        "--out", action=_SingleUse, type=Path, default=Path("web/test-results/perf-agent/run.json")
    )
    run.add_argument("--baseline", action=_SingleUse, type=Path)
    run.add_argument(
        "--include-drag", action="store_true", help="also capture the existing ADR 0075 drag probe"
    )
    run.add_argument("--summary", action=_SingleUse, type=Path)
    run.add_argument("--regression-pct", type=float, default=DEFAULT_REGRESSION_PCT)
    report = commands.add_parser("summarize", allow_abbrev=False)
    report.add_argument("report", type=Path)
    report.add_argument("--json", action=_SingleUse, type=Path)
    comp = commands.add_parser("compare", allow_abbrev=False)
    comp.add_argument("current", type=Path)
    comp.add_argument("baseline", type=Path)
    comp.add_argument("--json", action=_SingleUse, type=Path)
    comp.add_argument(
        "--summary",
        action=_SingleUse,
        type=Path,
        help="update the completed run summary with verdict",
    )
    comp.add_argument("--regression-pct", type=float, default=DEFAULT_REGRESSION_PCT)
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = p.parse_args(raw_argv)
    except SystemExit as exc:
        # --help 不是失败，不能删除前次结果。类型错误/未知选项则不能留旧的 pass。
        if exc.code == 0:
            return 0
        _invalidate_parse_error(raw_argv)
        return 2

    result = None
    comparison_summary = None
    drag_status = "not_started" if args.command == "run" and args.include_drag else "not_requested"
    command_failed = False

    def save_suite_summary() -> None:
        if result is None:
            return
        whole = compose_run_summary(result, drag_status)
        if command_failed:
            whole["status"] = "failed"
        if args.summary:
            _write(args.summary, whole)
        if whole["status"] != "pending" and not args.baseline:
            _append_gha_summary(whole)

    try:
        if args.command == "run":
            result = failed_run_summary(args.include_drag)
            output = args.out.resolve()
            # 所有派生 sidecar 和基线一起检查；尤其 out=drag.json 时不能先
            # 成功完成符号阶段又被后续的 drag_path.unlink() 错误清掉原结果。
            if _run_io_collision(output, args.summary, args.baseline, args.include_drag):
                write_targets = [output]
                if args.include_drag:
                    write_targets += [
                        output.with_name("drag.json"),
                        output.with_name("drag-findings.json"),
                    ]
                if args.baseline is not None:
                    write_targets.append(output.with_suffix(".comparison.json"))
                summary_safe = (
                    args.summary is not None
                    and all(not _same_report_path(args.summary, path) for path in write_targets)
                    and (
                        args.baseline is None or not _same_report_path(args.summary, args.baseline)
                    )
                )
                if summary_safe:
                    failed = failed_run_summary(args.include_drag)
                    if args.baseline is not None:
                        failed["regression_status"] = "failed"
                    _write(args.summary, failed)
                print("error: output_or_sidecar_aliases_readonly_data", file=sys.stderr)
                return 2
            result["status"] = "pending"
            if args.baseline is not None:
                # 请求了比较就必须有裁决：启动期的任何终态都要把它收成 failed。
                result["regression_status"] = "pending"
            # 先作废上次的成功摘要；启动失败/超时也必须交出本次任务的明确结论。
            save_suite_summary()
            # 即使参数是合法浮点/整型、但超出允许区间，也必须先作废旧原始报告。
            # 否则 --repeat 2 --out old-run.json 会留下上一轮成功采样。
            # 性能基线是只读证据；即使通过符号链接/硬链接别名，
            # 也不许先 unlink 再将本次报告当成自己的历史基线，伪造 PASS。
            if args.baseline is not None and _same_report_path(output, args.baseline):
                # 基线不可写，但本次请求比较失败必须给 Agent 明确 failed 裁决。
                result["regression_status"] = "failed"
                raise ValueError("baseline_aliases_run_output")
            _clear_run_outputs(output, args.summary, args.baseline)
            if not 3 <= args.repeat <= 60 or not 1 <= args.regression_pct <= 200:
                raise ValueError("invalid_run_parameters")
            output.parent.mkdir(parents=True, exist_ok=True)
            pnpm = shutil.which("pnpm")
            if pnpm is None:
                result["status"] = "failed"
                if args.baseline is not None:
                    result["regression_status"] = "failed"
                save_suite_summary()
                print("error: perf_agent_pnpm_unavailable", file=sys.stderr)
                return 2
            env = {
                **os.environ,
                "TAVOTTO_PYTHON": os.environ.get("TAVOTTO_PYTHON") or sys.executable,
                "TAVOTTO_PERF_AGENT_OUTPUT": str(output),
                "TAVOTTO_PERF_AGENT_REPEATS": str(args.repeat),
                "TAVOTTO_NO_TELEMETRY": "1",
                "TAVOTTO_NO_DIAG_UPLOAD": "1",
                "TAVOTTO_NO_UPDATE_CHECK": "1",
            }
            proc = subprocess.run(
                [
                    pnpm,
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
                result["status"] = "failed"
                if args.baseline is not None:
                    result["regression_status"] = "failed"
                save_suite_summary()
                print("error: perf_agent_browser_run_failed", file=sys.stderr)
                return 2
            result = summarize(load(output))
            if args.baseline:
                result["regression_status"] = "pending"
            _print_summary(result)
            save_suite_summary()
            if result["status"] != "ok":
                if args.baseline is not None:
                    # 未完整取得符号样本时比较根本不应启动，
                    # 因而不能留下一个永久 pending 的 regression_status。
                    result["regression_status"] = "failed"
                    save_suite_summary()
                return 2
            if args.include_drag:
                # 只有符号套件完整成功、实际准备启动拖动时才标 pending；
                # 早期失败时保持 not_started，避免将终态报告伪装成运行中。
                drag_status = "pending"
                save_suite_summary()
                # 使用仓库既有 E2E：真实拖动 + 两轮合成输入，严格收尾且不改原图。
                # 其报告由原有浏览器测试产生，禁止为了测性能另写一个假的拖动协议。
                drag_path = output.with_name("drag.json")
                findings_path = output.with_name("drag-findings.json")
                drag_path.unlink(missing_ok=True)
                findings_path.unlink(missing_ok=True)
                drag_env = {**env, "TAVOTTO_PERF_REPORT_OUT": str(drag_path)}
                try:
                    drag_run = subprocess.run(
                        [
                            pnpm,
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
                except subprocess.TimeoutExpired:
                    drag_status = "failed"
                    if args.baseline is not None:
                        result["regression_status"] = "failed"
                    save_suite_summary()
                    print("error: perf_agent_drag_probe_timeout", file=sys.stderr)
                    return 2
                if drag_run.returncode != 0 or not drag_path.is_file():
                    drag_status = "failed"
                    if args.baseline is not None:
                        result["regression_status"] = "failed"
                    save_suite_summary()
                    print("error: perf_agent_drag_probe_failed", file=sys.stderr)
                    return 2
                try:
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
                except subprocess.TimeoutExpired:
                    drag_status = "failed"
                    if args.baseline is not None:
                        result["regression_status"] = "failed"
                    save_suite_summary()
                    print("error: perf_agent_drag_analysis_timeout", file=sys.stderr)
                    return 2
                if analysis.returncode != 0 or not findings_path.is_file():
                    drag_status = "failed"
                    if args.baseline is not None:
                        result["regression_status"] = "failed"
                    save_suite_summary()
                    print("error: perf_agent_drag_analysis_failed", file=sys.stderr)
                    return 2
                drag_status = "ok"
                save_suite_summary()
                print(f"Drag probe: recorded and analyzed (browser={args.browser})")
            if args.baseline:
                assessment = compare(load(output), load(args.baseline), args.regression_pct)
                _write(output.with_suffix(".comparison.json"), assessment)
                whole = comparison_run_summary(result, drag_status, assessment)
                if args.summary:
                    _write(args.summary, whole)
                _append_gha_summary(whole)
                print(f"Baseline verdict: {assessment['status']}")
                return 0 if assessment["status"] == "pass" else 3
            print("Baseline verdict: not_checked (no baseline provided)")
            return 0
        if args.command == "summarize":
            if args.json is not None and _same_report_path(args.json, args.report):
                raise ValueError("summarize_output_aliases_input")
            result = summarize(load(args.report))
            _print_summary(result)
            if args.json:
                _write(args.json, result)
            return 0 if result["status"] == "ok" else 2
        if args.summary:
            previous = json.loads(args.summary.read_text(encoding="utf-8"))
            suites = previous.get("suites") if isinstance(previous, dict) else None
            if (
                not isinstance(previous, dict)
                or previous.get("schema") != SCHEMA
                or previous.get("status") != "ok"
                or not isinstance(suites, dict)
                or suites.get("symbol_ui") != "ok"
                or suites.get("canvas_drag") not in ("ok", "not_requested")
            ):
                raise ValueError("cannot_update_incomplete_summary")
            comparison_summary = previous
            # 摘要必须来自 current 这一次 run：环境与各场景统计逐项一致才允许写入比较裁决，
            # 否则 A 对基线的结论会被嵌进 B 的测量，一份文档混着两次 run。
            # current 读不出来仍按既有约定作废摘要；而「摘要属于别的 run」时那份摘要
            # 并非本次调用的产物，必须原样不动，所以先放弃对它的写入再拒绝。
            current_summary = summarize(load(args.current))
            if (
                previous.get("environment") != current_summary["environment"]
                or previous.get("scenarios") != current_summary["scenarios"]
            ):
                comparison_summary = None
                raise ValueError("summary_not_from_current_run")
        # current/baseline 是只读历史证据。输出名无论是同路径、软链接
        # 还是硬链接同 inode，都绝不能先被 _write 替换。
        if _same_report_path(args.current, args.baseline):
            # 自己跟自己比必然 pass，是空门禁；与 run 路径同一判据（resolve + samefile）。
            raise ValueError("comparison_inputs_alias")
        if args.json is not None and (
            _same_report_path(args.json, args.current)
            or _same_report_path(args.json, args.baseline)
        ):
            raise ValueError("comparison_output_aliases_input")
        if args.summary is not None and (
            _same_report_path(args.summary, args.current)
            or _same_report_path(args.summary, args.baseline)
            or (args.json is not None and _same_report_path(args.summary, args.json))
        ):
            raise ValueError("comparison_summary_aliases_input")
        if not 1 <= args.regression_pct <= 200:
            raise ValueError("invalid_regression_pct")
        result = compare(load(args.current), load(args.baseline), args.regression_pct)
        print(f"Comparison: {result['status']}")
        if args.json:
            _write(args.json, result)
        if comparison_summary is not None:
            _write(
                args.summary,
                comparison_run_summary(
                    comparison_summary, comparison_summary["suites"]["canvas_drag"], result
                ),
            )
        return 0 if result["status"] == "pass" else 3
    except (OSError, json.JSONDecodeError, ValueError, subprocess.TimeoutExpired) as exc:
        if args.command == "run" and result is not None:
            if result["status"] == "pending":
                result["status"] = "failed"
            elif drag_status == "pending":
                drag_status = "failed"
            command_failed = True
            if result.get("regression_status") == "pending":
                result["regression_status"] = "failed"
            try:
                save_suite_summary()
            except OSError:
                pass  # 摘要目录不可写时仍以失败码交出，不能再盖住原始失败。
        elif args.command in ("summarize", "compare"):
            # summarize/compare 的失败也必须作废旧机器结论。
            failed = (
                {"schema": COMPARE_SCHEMA, "rows": []}
                if args.command == "compare"
                else summarize({"environment": {}, "scenarios": []})
            )
            failed.update(status="failed", reason="invalid_or_unavailable_benchmark")
            try:
                if args.json and not (
                    (
                        args.command == "compare"
                        and (
                            _same_report_path(args.json, args.current)
                            or _same_report_path(args.json, args.baseline)
                        )
                    )
                    or (args.command == "summarize" and _same_report_path(args.json, args.report))
                ):
                    _write(args.json, failed)
                if comparison_summary is not None and not (
                    _same_report_path(args.summary, args.current)
                    or _same_report_path(args.summary, args.baseline)
                    or (args.json is not None and _same_report_path(args.summary, args.json))
                ):
                    _write(
                        args.summary,
                        {
                            **comparison_summary,
                            "status": "failed",
                            "regression_status": "failed",
                            "comparison": failed,
                        },
                    )
            except OSError:
                pass
        # 不把用户路径或操作数据写进控制台。
        print(f"error: {type(exc).__name__} (invalid or unavailable benchmark)", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
