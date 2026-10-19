"""Guardian failure taxonomy: a closed category set plus an explicit code mapping.

Categories come from the maintainer brief (section 7.1). The mapping is a table
from codes that already exist elsewhere (CompatBench stages, native-driver codes,
V2 session problem codes, runner conditions) to a category. A code that is not in
the table is reported as `unknown` with the raw code kept: guessing a root cause
is worse than admitting none, and clustering on a wrong guess hides real families.
"""

from __future__ import annotations

CATEGORIES = (
    "environment_resolution",
    "dependency_provider",
    "dependency_version",
    "argument_or_input",
    "cwd_or_data_path",
    "safe_execution_divergence",
    "figure_capture",
    "partial_capture",
    "product_entry_reachability",
    "manifest_semantics",
    "editing_capability",
    "replay_inconsistency",
    "export_or_fidelity",
    "timeout_or_process_crash",
    "invalid_fixture",
    "unknown",
)

#: CompatBench stage that failed first -> category. Only meaningful when the native
#: reference passed (otherwise the case is `invalid_fixture` before any stage counts).
STAGE_TO_CATEGORY = {
    "discover": "product_entry_reachability",
    "execute": "safe_execution_divergence",
    "capture": "figure_capture",
    "open": "manifest_semantics",
    "semantic": "manifest_semantics",
    "edit": "editing_capability",
    "replay": "replay_inconsistency",
    "export": "export_or_fidelity",
    "fidelity": "export_or_fidelity",
}

#: Existing machine codes -> category. Keep low-level codes visible next to the category.
CODE_TO_CATEGORY = {
    # native reference driver (scripts/ci/compat_driver.py, compat_matrix.run_driver)
    "native_script_error": "invalid_fixture",
    "native_render_failed": "invalid_fixture",
    "driver_timeout": "timeout_or_process_crash",
    "driver_crashed": "timeout_or_process_crash",
    "driver_no_output": "unknown",
    "driver_bad_output": "unknown",
    # runner / adapter conditions
    "adapter_timeout": "timeout_or_process_crash",
    "runner_crash": "unknown",
    "missing_prerequisites": "environment_resolution",
    "precondition_unmet": "environment_resolution",
    "module_not_found": "dependency_provider",
    # session / worker problem codes seen by the V2 harness and engine routes
    "safe_file_operation_blocked": "safe_execution_divergence",
    "no_figures_captured": "figure_capture",
    "multi_figure_contract": "partial_capture",
    "missing_dependency": "dependency_provider",
    "dependency_ambiguous": "dependency_provider",
    "dependency_version_conflict": "dependency_version",
    "input_not_found": "cwd_or_data_path",
    "argument_required": "argument_or_input",
}


def category_of_code(code: str | None) -> str:
    return CODE_TO_CATEGORY.get(code or "", "unknown")


def category_of_stage(stage: str | None) -> str:
    return STAGE_TO_CATEGORY.get(stage or "", "unknown")


def attribute(row: dict) -> dict:
    """Return {"category", "source_code", "basis"} for a case row.

    `category` is None only for rows that have no failure to attribute
    (passed / expected_limitation / partial_coverage / not_reproduced). Everything else
    gets a category, `unknown` included, so a non-green row is never unlabeled.
    """
    status = row["status"]
    if status in (
        "passed",
        "expected_limitation",
        "partial_coverage",
        "not_reproduced",
        "control_met",
    ):
        return {
            "category": None,
            "source_code": None,
            "basis": f"no failure to attribute ({status})",
        }
    raw = row.get("raw") or {}
    native = row.get("native") or {}
    result = native.get("result") or {}
    if "runner_error" in raw:
        return {
            "category": "unknown",
            "source_code": "runner_crash",
            "basis": "runner crashed or produced no terminal evidence; never attributed to the product",
        }
    if "adapter_timeout" in raw:
        return {
            "category": "timeout_or_process_crash",
            "source_code": "adapter_timeout",
            "basis": "adapter exceeded its time budget or was killed",
        }
    if "missing_prerequisites" in raw or "precondition_unmet" in raw:
        key = "missing_prerequisites" if "missing_prerequisites" in raw else "precondition_unmet"
        return {
            "category": "environment_resolution",
            "source_code": key,
            "basis": "required module/font/interpreter state absent before any product code ran",
        }
    if native.get("status") == "invalid_fixture":
        return {
            "category": "invalid_fixture",
            "source_code": result.get("code") or "native_failed",
            "basis": "the native reference itself failed or produced nothing; not a Tavotto defect",
        }
    if native.get("status") == "environment_unavailable":
        tb = str(result.get("traceback", ""))
        code = "module_not_found" if "ModuleNotFoundError" in tb else result.get("code")
        return {
            "category": category_of_code(code) if code else "environment_resolution",
            "source_code": code,
            "basis": "native reference could not run for an environment reason",
        }
    if status == "failed":
        for name, cell in row["stages"].items():
            if cell["status"] == "failed":
                figures = row.get("figures") or {}
                have, want = figures.get("tavotto_captured"), figures.get("native")
                if (
                    name in ("capture", "open")
                    and type(have) is int
                    and type(want) is int
                    and 0 < have < want
                ):
                    return {
                        "category": "partial_capture",
                        "source_code": f"stage:{name}",
                        "basis": f"Tavotto captured {have} of the {want} figures native produced",
                    }
                return {
                    "category": category_of_stage(name),
                    "source_code": f"stage:{name}",
                    "basis": "first stage that failed while the native reference passed",
                }
        if (raw.get("routes") or {}) and any(
            isinstance(r, dict) and r.get("status") == "fail" for r in raw["routes"].values()
        ):
            return {
                "category": "product_entry_reachability",
                "source_code": "route",
                "basis": "a declared product route failed",
            }
    return {
        "category": "unknown",
        "source_code": None,
        "basis": f"status {status} without a mapped failure code",
    }
