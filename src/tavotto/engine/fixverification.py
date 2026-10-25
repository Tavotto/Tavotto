"""Repair verification consumes preparation actions; it never executes scripts itself.

The application supplies project-bound check/claim/read/cancel callbacks. A single
reviewed run action is the only action this adapter may claim. Dependency installs,
run intents, environment choices and cwd grants remain separate user decisions.
"""

from __future__ import annotations

import json
import threading
import time

from .interpreterpath import same_python
from .prepsession import (
    OUTCOME_NO_FIGURE,
    OUTCOME_SUCCEEDED,
    PHASE_COMPLETED,
    PHASE_PARTIAL,
)


def evidence(report: dict | None) -> dict | None:
    """Verify a successful current application run, including a no-figure handoff."""
    if not isinstance(report, dict) or not (report.get("facts") or {}).get("execution_finished"):
        return None
    # A finished attempt can still need dependencies, fail registration or capture
    # only some figures. Consume the preparation service's authoritative result;
    # no editable figures is the intentional successful-script handoff to conversion.
    result = (report.get("phase"), (report.get("outcome") or {}).get("kind"))
    if result not in (
        (PHASE_COMPLETED, OUTCOME_SUCCEEDED),
        (PHASE_PARTIAL, OUTCOME_NO_FIGURE),
    ):
        return None
    provider = report.get("provider") or {}
    if not provider.get("attempt_id") or not provider.get("plan_id"):
        return None
    return {
        "session_id": report.get("session_id"),
        "config_revision": report.get("config_revision"),
        "plan_id": provider["plan_id"],
        "attempt_id": provider["attempt_id"],
    }


def run_preparation(
    *, python: str, timeout: float, cancel, approve, check, selected_python, claim, read, stop
) -> dict:
    """Recheck → review the exact action → claim it → await its own final report.

    `read/stop(action_id)` must enforce action ownership, including any attempts
    the preparation service itself owns for that action. No action is inferred or
    self-approved from a missing requirement.
    """
    report = check()
    plan = report.get("plan") or {}
    selected = selected_python()
    if not isinstance(selected, str) or not same_python(python, selected):
        return {"code": "environment_not_selected", "report": report}
    action = next((a for a in report.get("actions", []) if a.get("kind") == "run"), None)
    if action is None:
        return {"code": "application_not_runnable", "report": report}
    launch = plan.get("launch_context") or {}
    decision = plan.get("workdir_decision") or {}
    details = json.dumps(
        {"execution": launch, "workdir": decision, "impact": action.get("impact")},
        ensure_ascii=False,
        default=str,
    )
    allowed = approve(
        f"Verify the repair by running the real script in {launch.get('profile', 'current')} mode "
        f"(working directory: {launch.get('cwd_origin', decision.get('mode', 'current'))}; "
        f"writes: {launch.get('write_mode', 'according to project settings')})? "
        "This includes any native retry permitted by the project policy, with access to your files. "
        "Tavotto will register captured figures.",
        details,
    )
    if not allowed:
        return {"code": "needs_approval", "report": report}
    cancelled = cancel or threading.Event()
    if cancelled.is_set():
        return {"code": "cancelled", "report": report}
    action_id = action["id"]
    if not claim(action_id, report["config_revision"]):
        # Another consumer claimed the reviewed action; this repair owns no run.
        return {"code": "application_run_not_claimed", "report": report}
    deadline = time.monotonic() + timeout
    while True:
        report = read(action_id)
        proof = evidence(report)
        if proof is not None:
            return {"code": "", "report": report, "verification": proof}
        if report.get("phase") not in (
            "running",
            "awaiting_runtime_input",
            "preparing_environment",
        ):
            return {"code": "application_run_failed", "report": report}
        if cancelled.is_set() or time.monotonic() >= deadline:
            stop(action_id)
            return {"code": "cancelled" if cancelled.is_set() else "timeout", "report": report}
        cancelled.wait(min(0.1, max(0.0, deadline - time.monotonic())))
