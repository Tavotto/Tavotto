"""Bounded application report projection shared by observation and verification."""

from __future__ import annotations

import json

from ..agent_runner.wires import ToolResult
from .base import FixContext, err, ok, shadow_note

REPORT_MAX_CHARS = 8000


def _tail(text: str, n: int = 40) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _precis(value, depth: int = 0):
    if isinstance(value, str):
        return _tail(value, 40)[-1200:] if len(value) > 1200 else value
    if isinstance(value, list):
        return [_precis(v, depth + 1) for v in value[-12:]]
    if isinstance(value, dict) and depth < 4:
        return {str(k): _precis(v, depth + 1) for k, v in list(value.items())[:30]}
    return value if isinstance(value, bool | int | float | type(None)) else str(value)[:200]


_REPORT_KEYS = (
    "phase",
    "outcome",
    "code",
    "error",
    "error_tail",
    "attempt_history",
    "remedy",
    "dependency",
    "facts",
    "no_figure_hint",
)


def project_run_report(report: dict | None, ctx: FixContext) -> ToolResult:
    """The same bounded projection for read_run_report and application try_run."""
    if not isinstance(report, dict):
        return err("no_report", "There is no preparation session report right now.")
    # The failing current attempt and its execution context precede history.
    # Otherwise a long history can consume the cap before the actual error.
    picked = {
        k: _precis(report[k])
        for k in ("phase", "outcome", "facts")
        if report.get(k) not in (None, "", [], {})
    }
    result = report.get("result") or {}
    error = result.get("error") if isinstance(result, dict) else None
    error = error or report.get("error")
    # Keep package provenance in the same projection used by observation and the
    # real application trial. Historical failures must not replace the current one.
    diagnostic = error or report.get("error_tail") or report.get("attempt_history")
    err_text = (
        diagnostic
        if isinstance(diagnostic, str)
        else json.dumps(diagnostic, ensure_ascii=False, default=str)
    )
    note = shadow_note(ctx, err_text).removeprefix("\nproject_package_shadow: ")
    if note:
        picked = {"project_package_shadow": note, **picked}
    if isinstance(error, dict):
        picked["error"] = {
            k: _precis(error[k]) for k in ("code", "message", "module") if error.get(k)
        }
    elif error:
        picked["error"] = _precis(error)
    plan = report.get("plan") or {}
    if isinstance(plan, dict):
        if plan.get("launch_context"):
            picked["launch_context"] = _precis(plan["launch_context"])
        decision = plan.get("workdir_decision")
        if isinstance(decision, dict):
            picked["workdir_decision"] = {
                k: _precis(decision[k])
                for k in ("mode", "decided", "implied_by", "needs_confirmation", "confirmation")
                if k in decision
            }
    for item in report.get("requirements") or []:
        # 数据失联（ADR 0106）：脚本要的那串和脚本里别处写着、此刻也找不到的路径——`link_data_file` 的 `requested` 就取自这里
        if isinstance(item, dict) and item.get("kind") == "input_location":
            if isinstance(item.get("payload"), dict):
                picked["missing_input"] = _precis(item["payload"])
            break
    if isinstance(error, dict) and error.get("traceback"):
        picked["error_tail"] = _precis(error["traceback"])
    for key in _REPORT_KEYS:
        if key not in picked and report.get(key) not in (None, "", [], {}):
            picked[key] = _precis(report[key])
    text = json.dumps(picked, ensure_ascii=False, indent=1, default=str)
    if len(text) > REPORT_MAX_CHARS:
        text = text[:REPORT_MAX_CHARS] + "\n[truncated]"
    return ok(text, f"report with {len(picked)} sections")
