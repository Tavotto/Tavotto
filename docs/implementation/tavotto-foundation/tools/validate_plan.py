#!/usr/bin/env python3
"""Validate this implementation specification, NOT Tavotto software or CI results.

Usage: python tools/validate_plan.py /path/to/Tavotto_Unified_Implementation_Pack
Only Python standard library is used. No subprocess, network, or product code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

GATES = ["CI fast gate", "CI integration gate", "CodeQL gate"]
ENROLLMENTS = {"planned", "observing", "enforced", "later"}
RESULTS = {"not_run", "pass", "fail", "infrastructure_error", "not_applicable"}
REQUIRED_POLICY = {
    "existing_required_gates_preserved": True,
    "all_future_requirements_block_merge": False,
    "enforced_missing_result": "fail",
    "unknown_optional_blocks_regular_export": False,
    "unknown_mandatory_can_claim_verified": False,
    "product_failure_auto_retry": False,
    "global_pymupdf_runtime_ban_begins": "U10",
    "root_license_change": False,
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def contained_file(root: Path, name: object) -> Path | None:
    if not isinstance(name, str) or not name:
        return None
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def validate_data(
    root: Path,
    plan: dict,
    registry: dict,
    sources: list,
    phase_map: dict,
    *,
    check_hashes: bool = True,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict) or not isinstance(registry, dict):
        return ["plan and registry must be JSON objects"]
    if plan.get("nature") != "implementation_spec_not_product_validation":
        errors.append("plan must not claim product validation")
    if plan.get("stable_gate_contexts") != GATES:
        errors.append("three stable gate contexts changed")
    policy = plan.get("policy", {})
    for key, value in REQUIRED_POLICY.items():
        if policy.get(key) != value:
            errors.append(f"policy mismatch: {key}")

    stages = plan.get("stages", [])
    if not isinstance(stages, list):
        return errors + ["stages must be an array"]
    ids = [s.get("id") for s in stages if isinstance(s, dict)]
    expected_stages = {f"U{i:02}" for i in range(12)} | {"X01", "X02", "X03"}
    if set(ids) != expected_stages or len(ids) != len(set(ids)) or len(stages) != len(ids):
        errors.append("stage ID set incomplete or duplicated")
    stage_by_id = {s.get("id"): s for s in stages if isinstance(s, dict)}
    milestone_owner: dict[str, str] = {}
    for s in stages:
        if not isinstance(s, dict):
            continue
        sid = s.get("id", "")
        if contained_file(root, s.get("document")) is None:
            errors.append(f"missing stage document: {sid}")
        for m in s.get("milestones", []):
            if not isinstance(m, str) or not m.startswith(str(sid) + ".") or m in milestone_owner:
                errors.append(f"invalid/duplicate milestone: {m}")
            else:
                milestone_owner[m] = sid
    graph: dict[str, list[str]] = {sid: [] for sid in stage_by_id}
    for s in stages:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        for m in s.get("dependencies", []):
            if m not in milestone_owner:
                errors.append(f"unknown dependency {sid}: {m}")
            else:
                graph[sid].append(milestone_owner[m])
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            errors.append(f"cyclic stage dependency at {node}")
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for sid in graph:
        visit(sid)
    # Guard the two explicit decouplings in this revision.
    if any(str(x).startswith("U05.") for x in stage_by_id.get("U04", {}).get("dependencies", [])):
        errors.append("joint dependency work must not wait for private Python provisioning")
    if "U02.render_spike" not in stage_by_id.get("U06", {}).get("dependencies", []):
        errors.append("render implementation lacks its own feasibility milestone")
    for s in stages:
        if not isinstance(s, dict):
            continue
        for own, dependencies in s.get("milestone_dependencies", {}).items():
            if milestone_owner.get(own) != s.get("id"):
                errors.append(f"unknown owned submilestone: {own}")
            for dep in dependencies:
                if dep not in milestone_owner:
                    errors.append(f"unknown submilestone dependency: {dep}")
    # This graph follows milestones, including extra requirements for managed join.
    milestone_graph = {}
    for s in stages:
        if isinstance(s, dict):
            for own in s.get("milestones", []):
                milestone_graph[own] = list(s.get("dependencies", [])) + list(
                    s.get("milestone_dependencies", {}).get(own, [])
                )
    seen_m, active_m = set(), set()

    def visit_m(node):
        if node in active_m:
            errors.append(f"cyclic milestone dependency at {node}")
            return
        if node in seen_m:
            return
        active_m.add(node)
        for dep in milestone_graph.get(node, []):
            visit_m(dep)
        active_m.remove(node)
        seen_m.add(node)

    for node in milestone_graph:
        visit_m(node)
    existing = milestone_graph.get("U09.existing_env_join", [])
    if any(d.startswith("U05.") for d in existing):
        errors.append("existing environment join must not wait for private provisioning")
    if "U09.managed_env_join" not in milestone_graph.get("U11.release_qualification", []):
        errors.append("combined core qualification must include managed environment join")
    if not isinstance(phase_map, dict) or set(phase_map) != (
        {f"R{i:02}" for i in range(17)}
        | {"E01", "E02"}
        | {f"CP{i:02}" for i in range(9)}
        | {f"CP08-{x}" for x in "ABCDE"}
    ):
        errors.append("legacy phase map is incomplete")
    else:
        for key, targets in phase_map.items():
            if not targets or any(t not in stage_by_id for t in targets):
                errors.append(f"invalid phase mapping: {key}")

    raw_sources: dict[str, Any] = {}
    if not isinstance(sources, list):
        return errors + ["sources must be an array"]
    source_ids: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            errors.append("malformed source entry")
            continue
        source_id = src.get("id")
        if source_id in source_ids:
            errors.append(f"duplicate source: {source_id}")
        source_ids.add(source_id)
        p = contained_file(root, src.get("archived_path"))
        if p is None:
            errors.append(f"missing source archive: {source_id}")
            continue
        data = p.read_bytes()
        if check_hashes and (
            hashlib.sha256(data).hexdigest() != src.get("sha256") or len(data) != src.get("bytes")
        ):
            errors.append(f"source integrity mismatch: {source_id}")
        if src.get("authority") != "historical_reference_only":
            errors.append(f"old source cannot become parallel authority: {source_id}")
        if source_id in {
            "rendercore_requirements",
            "compatibility_requirements",
            "firstopen_cases",
        }:
            try:
                raw_sources[source_id] = json.loads(data)
            except (UnicodeDecodeError, ValueError):
                errors.append(f"invalid archived JSON: {source_id}")
    try:
        originals = {
            **{
                x["id"]: ("rendercore_requirements", x, "requirement")
                for x in raw_sources["rendercore_requirements"]["requirements"]
            },
            **{
                x["id"]: ("compatibility_requirements", x, "requirement")
                for x in raw_sources["compatibility_requirements"]["items"]
            },
            **{
                x["id"]: ("firstopen_cases", x, "scenario")
                for x in raw_sources["firstopen_cases"]["cases"]
            },
        }
    except (KeyError, TypeError):
        return errors + ["original requirement/scenario sources unavailable"]
    entries = registry.get("entries", [])
    if not isinstance(entries, list):
        return errors + ["registry entries must be an array"]
    entry_ids = [e.get("id") for e in entries if isinstance(e, dict)]
    if len(entry_ids) != len(entries) or len(entry_ids) != len(set(entry_ids)):
        errors.append("malformed or duplicated source mapping")
    if set(entry_ids) != set(originals):
        errors.append("220-source traceability set is incomplete or contains unknown entries")
    if (
        registry.get("original_counts")
        != {
            "rendercore_requirements": 114,
            "compatibility_requirements": 74,
            "firstopen_scenarios": 32,
        }
        or len(originals) != 220
    ):
        errors.append("source counts mismatch")
    for e in entries:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if eid not in originals:
            continue
        source_id, original, kind = originals[eid]
        if (
            e.get("source_id") != source_id
            or e.get("kind") != kind
            or e.get("original") != original
        ):
            errors.append(f"original source text/identity altered: {eid}")
        stage = e.get("implementation_stage")
        if stage not in stage_by_id or e.get("stage_document") != stage_by_id.get(stage, {}).get(
            "document"
        ):
            errors.append(f"bad stage ownership: {eid}")
        if (
            not e.get("current_interpretation")
            or not e.get("merge_rule")
            or not e.get("qualification_rule")
        ):
            errors.append(f"missing active contract: {eid}")
        for other in e.get("follow_up_stages", []):
            if other not in stage_by_id:
                errors.append(f"unknown follow-up stage: {eid}")
        if e.get("enrollment") not in ENROLLMENTS or e.get("execution_status") not in RESULTS:
            errors.append(f"unknown enrollment/result: {eid}")
        if e.get("program_scope") == "later" and (
            not str(stage).startswith("X") or e.get("enrollment") != "later"
        ):
            errors.append(f"later scope inconsistent: {eid}")
        if e.get("execution_status") == "pass" and not e.get("evidence"):
            errors.append(f"unsupported product pass: {eid}")
        if e.get("enrollment") == "enforced":
            qualification = e.get("promotion", {})
            if not all(
                qualification.get(k)
                for k in ("capability", "target_scope", "lane", "negative_evidence", "approval")
            ):
                errors.append(f"enforced without promotion contract: {eid}")
    if plan.get("new_default_capabilities_enabled"):
        errors.append("unexecuted specification cannot enable product defaults")
    if (
        any(e.get("execution_status") != "not_run" for e in entries)
        and registry.get("product_validation_status") == "not_run"
    ):
        errors.append("registry aggregate execution status contradicts entries")
    return errors


def document_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if "archive" in path.relative_to(root).parts or path.name == "ALL_PROMPTS.md":
            continue
        text = path.read_text(encoding="utf-8")
        if len(re.findall(r"^```", text, re.M)) % 2:
            errors.append(f"unclosed code fence: {path.relative_to(root)}")
        # Local markdown links only. Code examples in fenced blocks are not link targets.
        visible = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in re.findall(r"\]\(([^)]+)\)", visible):
            if re.match(r"^[a-zA-Z]+:", target) or target.startswith("#"):
                continue
            rel = target.split("#", 1)[0]
            if rel and not (path.parent / rel).exists():
                errors.append(f"broken local link: {path.relative_to(root)} -> {target}")
    return errors


def validate(root: Path) -> dict:
    try:
        errors = validate_data(
            root,
            read_json(root / "plan.json"),
            read_json(root / "registry.json"),
            read_json(root / "sources_manifest.json"),
            read_json(root / "phase_mapping.json"),
        )
        errors.extend(document_errors(root))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors = [f"validation input error: {type(exc).__name__}: {exc}"]
    return {
        "kind": "implementation_plan_integrity_only",
        "ok": not errors,
        "errors": errors,
        "product_tests_executed": False,
        "product_qualification": "not_run",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    result = validate(Path(args.root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
