#!/usr/bin/env python3
"""Static S0 cohort verification and fixed-denominator Guardian report projection.

No script execution or dependency installation occurs here. The measured subject
is each frozen development project's engine task in one explicitly selected
Guardian run, not a desktop user journey or the scientific population at large.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath

import compat_corpus as cc
import compat_guardian as cg
import compat_guardian_summary as summary
import compat_research_sources as sources

# Windows runners fall back to the code page when stdout/stderr are pipes; pin both
# so evidence errors and Chinese diagnostics reach the parent (same as build_frontend.py).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

COHORT = cc.COMPAT_DIR / "research-cohort.json"
SNAPSHOT = Path("tests/compat/research-baseline.json")
SNAPSHOT_EVIDENCE = Path("docs/implementation/script-compat/s0-evidence/baseline")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_relative(path: str, *, directory: bool = False) -> None:
    """Frozen staging names are canonical POSIX paths on every runner platform."""
    if directory and path == ".":
        return
    if (
        not isinstance(path, str)
        or not path
        or "\\" in path
        or PurePosixPath(path).is_absolute()
        or PureWindowsPath(path).drive
        or PurePosixPath(path).as_posix() != path
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise cg.EvidenceError("frozen staging path must be canonical and project-relative")


#: Tavotto's own RenderCore dependencies (process boundary): they belong to the Tavotto venv, never
#: to the interpreter that runs a research script or the worker.
RENDER_PACKAGES = ("uharfbuzz", "pikepdf", "pypdfium2")


def renderer_packages(python: str) -> list[str]:
    """RenderCore packages visible to `python` (isolated mode), by import spec and by distribution."""
    probe = (
        "import importlib.metadata as m, importlib.util as u, sys;"
        "print(*[n for n in sys.argv[1:] if u.find_spec(n) is not None or any("
        "(d.metadata['Name'] or '').lower() == n for d in m.distributions())])"
    )
    done = subprocess.run(
        [python, "-I", "-c", probe, *RENDER_PACKAGES], capture_output=True, text=True, check=True
    )
    return done.stdout.split()


def check_project_contract(project: dict, case: dict, root: Path) -> None:
    """Bind the executed case and install inputs to this project's frozen intent."""
    if case.get("assets"):
        raise cg.EvidenceError("research assets must be in the frozen upstream inventory")
    mapping = project["upstream_path_map"]
    for destination, source in mapping.items():
        project_relative(destination)
        if source not in project["files"]:
            raise cg.EvidenceError("upstream path mapping lacks frozen source inventory")
    entry = project["entry"]
    project_relative(entry)
    if (
        case.get("script_dest") != entry
        or mapping.get(entry) != "tests/compat/" + case["script"]
        or case.get("entry") != "__main__"
    ):
        raise cg.EvidenceError("research entry differs from frozen launch")
    for destination, source in case.get("extra_files", {}).items():
        if mapping.get(destination) != "tests/compat/" + source:
            raise cg.EvidenceError("runtime destination differs from frozen upstream mapping")
    launch = project["launch"]
    if set(launch) != {"cwd", "argv"}:
        raise cg.EvidenceError("frozen launch requires cwd and argv")
    project_relative(launch["cwd"], directory=True)
    if not isinstance(launch["argv"], list) or any(
        not isinstance(arg, str) for arg in launch["argv"]
    ):
        raise cg.EvidenceError("frozen launch argv must be a list of strings")
    if case.get("reference_invocation") != launch:
        raise cg.EvidenceError("reference invocation differs from frozen launch")
    directories = project["project_directories"]
    for directory in directories:
        project_relative(directory, directory=True)
    if case.get("project_directories") != directories:
        raise cg.EvidenceError("prepared directories differ from frozen launch")
    tasks = project["tasks"]
    if len(tasks) != 1:
        raise cg.EvidenceError("each S0 case must bind exactly one frozen task")
    task = tasks[0]
    figures = project["expected_figures_static"]
    index = task["target_figure_index"]
    if (
        type(figures) is not int
        or figures < 1
        or type(index) is not int
        or not 0 <= index < figures
        or case.get("expected_figures") != figures
    ):
        raise cg.EvidenceError("figure expectation differs from frozen task")
    mutation = task["mutation"]
    if (
        case.get("stem") != task["target_stem"]
        or case.get("mutations") != [mutation]
        or case.get("semantic_expectations", {}).get("editable")
        != [[mutation["gid"], mutation["prop"]]]
        or case.get("export_formats") != task["exports"]
        or task["required_stages"] != list(cc.STAGES)
        or any(case.get("expected", {}).get(stage, True) is not True for stage in cc.STAGES)
    ):
        raise cg.EvidenceError("manifest task differs from frozen edit/export contract")
    recipe = project["environment_recipe"]
    expected_path = f"tests/compat/research-environments/{project['case_id']}.txt"
    if (
        recipe["path"] != expected_path
        or not re.fullmatch(r"[0-9a-f]{64}", recipe["sha256"])
        or file_digest(cg.contained(root, expected_path)) != recipe["sha256"]
    ):
        raise cg.EvidenceError("scientific environment recipe differs from frozen input")
    if type(project["requires_tex"]) is not bool:
        raise cg.EvidenceError("requires_tex must freeze a boolean prerequisite")
    for destination, source in project["source_package_files"].items():
        project_relative(destination)
        if mapping.get(destination) != source or source not in project["files"]:
            raise cg.EvidenceError("source package mapping differs from frozen upstream inventory")
    helper = project.get("dependency_helper_integrity")
    if helper:
        source = mapping.get(helper["archived_path"])
        if (
            source is None
            or project["files"].get(source) != helper["sha256"]
            or helper["module"].replace(".", "/") + ".py" != helper["archived_path"]
        ):
            raise cg.EvidenceError("scientific helper identity differs from frozen upstream source")


def load_cases(root: Path = cc.REPO) -> list[dict]:
    data = json.loads((root / sources.MANIFEST_REL).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("cases"), list):
        raise cg.EvidenceError("invalid research task registry")
    cases = data["cases"]
    if len({case["id"] for case in cases}) != len(cases):
        raise cg.EvidenceError("duplicate research case")
    return cases


def load_cohort(path: Path = COHORT, root: Path = cc.REPO, *, verify_files=False) -> dict:
    cohort = json.loads(path.read_text(encoding="utf-8"))
    if cohort.get("schema") != "tavotto-research-cohort/1":
        raise cg.EvidenceError("unknown research cohort schema")
    runtime_lock_hash = cohort.get("runtime_lock_sha256")
    if not isinstance(runtime_lock_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", runtime_lock_hash
    ):
        raise cg.EvidenceError("missing or invalid frozen runtime lock commitment")
    runtime_lock = cg.contained(root, "packaging/runtime-lock.json")
    if not runtime_lock.is_file() or file_digest(runtime_lock) != runtime_lock_hash:
        raise cg.EvidenceError("runtime lock differs from frozen cohort commitment")
    development, holdout = cohort["development"], cohort["holdout"]
    if len(development) != 16 or len(holdout) != 8:
        raise cg.EvidenceError("S0 cohort must preserve 16 development and 8 sealed projects")
    ids = [p["project_id"] for p in development + holdout]
    if len(set(ids)) != 24:
        raise cg.EvidenceError("duplicate project identity")
    repos = [p["upstream"]["repository"].lower() for p in development]
    if len(set(repos)) != 16:
        raise cg.EvidenceError("multiple scripts from one upstream are not independent projects")
    manifest_cases = load_cases(root)
    cases = {c["id"]: c for c in manifest_cases}
    enrolled = {c["id"] for c in manifest_cases if c.get("research_project_id")}
    if enrolled != {p["case_id"] for p in development}:
        raise cg.EvidenceError("executable research manifest differs from the development cohort")
    for p in development:
        try:
            sources.validate_project(p)
        except sources.SourceError as exc:
            raise cg.EvidenceError(str(exc)) from exc
        case = cases[p["case_id"]]
        if case.get("research_project_id") != p["project_id"]:
            raise cg.EvidenceError("project/case identity mismatch")
        if not re.fullmatch(r"[0-9a-f]{40}", p["upstream"]["commit"]):
            raise cg.EvidenceError("upstream revision must be immutable")
        paths = {"tests/compat/" + case["script"]}
        paths.update("tests/compat/" + f for f in case.get("extra_files", {}).values())
        if paths != set(p["staged_files"]) or not paths <= set(p["files"]):
            raise cg.EvidenceError(f"{p['project_id']}: staged inputs differ from frozen inventory")
        for name, expected in p["files"].items():
            if verify_files and file_digest(sources.source_file(case, name, root=root)) != expected:
                raise cg.EvidenceError(f"{p['project_id']}: source bytes changed: {name}")
        if not p["tasks"] or not p["license_files"]:
            raise cg.EvidenceError("tasks and license evidence must be frozen before execution")
        if not set(p["license_files"]) <= set(p["files"]):
            raise cg.EvidenceError("license evidence missing from frozen inventory")
        check_project_contract(p, case, root)
    for p in holdout:
        if set(p) - {
            "project_id",
            "domain",
            "sample_kind",
            "source_sha256",
            "task_sha256",
            "package_sha256",
            "license_verified",
            "inputs_available",
            "status",
        }:
            raise cg.EvidenceError("sealed holdout record exposes non-opaque metadata")
        if p["project_id"] in cases or p["status"] != "sealed_not_run":
            raise cg.EvidenceError("holdout leaked into executable development corpus")
        for key in ("source_sha256", "task_sha256", "package_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", p[key]):
                raise cg.EvidenceError("missing holdout commitment")
    return cohort


def project_reports(cohort: dict, reports: list[dict], expected_sha: str) -> dict:
    """Project verified Guardian rows; missing rows remain visible and never pass."""
    projects = {p["case_id"]: p for p in cohort["development"]}
    rows = {}
    report_sources = []
    for report in reports:
        if report["source"]["commit"] != expected_sha or report["source"]["dirty"]:
            raise cg.EvidenceError("report is not from the selected clean enrollment commit")
        if report["suite"] != "compat_engine":
            raise cg.EvidenceError("S0 engine projection requires compat_engine evidence")
        report_sources.append({"run_id": report["run_id"], "source": report["source"]})
        for row in report["cases"]:
            cid = row["id"]
            if cid not in projects or cid in rows:
                raise cg.EvidenceError(f"unexpected or duplicate report case: {cid}")
            rows[cid] = row
    projected = []
    for cid, p in projects.items():
        row = rows.get(cid)
        if row is None:
            projected.append(
                {
                    "project_id": p["project_id"],
                    "case_id": cid,
                    "domain": p["domain"],
                    "status": "not_run",
                    "eligible": False,
                    "stages": {},
                    "failure_category": "unknown",
                    "source_code": "report_missing",
                    "environment_fingerprint": None,
                    "requires_tex": p["requires_tex"],
                }
            )
            continue
        native = row["native"]
        eligible = cg.native_eligible(row)
        failure = row["failure_category"]
        cells = {s: row["stages"][s]["status"] for s in cc.STAGES}
        item = {
            "project_id": p["project_id"],
            "case_id": cid,
            "domain": p["domain"],
            "sample_kind": p["sample_kind"],
            "status": row["status"],
            "eligible": eligible,
            "native_status": native["status"],
            "figures": row["figures"],
            "executions": row["executions"],
            "stages": cells,
            "failure_category": failure["category"],
            "source_code": failure["source_code"],
            "environment_fingerprint": row["environment_before"]["fingerprint"],
            "unverified": row["unverified"],
            "requires_tex": p["requires_tex"],
        }
        projected.append(item)
    data = {
        "schema": "tavotto-research-baseline/1",
        "cohort_id": cohort["cohort_id"],
        "product_baseline_sha": cohort["product_baseline_sha"],
        "enrollment_sha": expected_sha,
        "n_raw": len(projects),
        "holdout_sealed_not_run": len(cohort["holdout"]),
        "missing_cases": sorted(set(projects) - set(rows)),
        "projects": projected,
        "reports": report_sources,
        "unverified": [
            "Convenience pilot, not a representative user success rate.",
            "Engine adapter only: desktop/CLI/native routes and document persistence not covered.",
            "Aggregation checks report structure/proof; complete evidence validation occurs in the producing hosted job.",
            "Different projects may use different prerequisite environments; compare a project only to its own frozen environment.",
            "Taxonomy clusters identify observed failure layers; detailed causal diagnosis needs the original evidence.",
        ],
    }
    refresh_accounting(data)
    return data


def refresh_accounting(data: dict) -> None:
    """Every aggregate describes the same final, prerequisite-qualified rows."""
    qualified = [p for p in data["projects"] if p["eligible"]]
    data["n_eligible"] = len(qualified)
    data["status_counts"] = dict(Counter(p["status"] for p in data["projects"]))
    data["stages_fixed_denominator"] = {
        stage: {
            "passed": sum(p["stages"].get(stage) == "passed" for p in qualified),
            "denominator": len(qualified),
        }
        for stage in cc.STAGES
    }
    clusters = defaultdict(list)
    for project in data["projects"]:
        if project["status"] == "passed":
            continue
        if project["source_code"] == "report_missing":
            key = "not_run:report_missing"
        else:
            scope = (
                "product"
                if project["eligible"] and project["status"] == "failed"
                else "prerequisite_or_evidence"
            )
            key = f"{scope}:{project['failure_category']}:{project['source_code']}"
        clusters[key].append(project["project_id"])
    data["clusters"] = dict(sorted(clusters.items()))


def check_setup_receipts(data: dict, paths: list[Path]) -> None:
    """A passing observation cannot qualify an unsuccessfully prepared recipe."""
    receipts = {}
    expected = {p["case_id"] for p in data["projects"]}
    for path in paths:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        cid = receipt["case_id"]
        if cid not in expected:
            raise cg.EvidenceError("setup receipt case is outside the frozen development set")
        if receipt["source_sha"] != data["enrollment_sha"] or cid in receipts:
            raise cg.EvidenceError("setup receipt has wrong SHA or duplicate case")
        receipts[cid] = receipt
    data["missing_setup_receipts"] = sorted(expected - set(receipts))
    for p in data["projects"]:
        receipt = receipts.get(p["case_id"])
        outcomes = receipt["steps"] if receipt is not None else {}
        good = all(outcomes.get(k) == "success" for k in ("core", "fonts", "scientific"))
        tex = outcomes.get("tex")
        good = good and (tex == "success" or (not p["requires_tex"] and tex == "skipped"))
        p["setup"] = receipt
        if not good:
            p["eligible"] = False
            p["status"] = "not_run"
            p["failure_category"] = "environment_resolution"
            p["source_code"] = (
                "setup_receipt_missing" if receipt is None else "environment_setup_failed"
            )
    refresh_accounting(data)


def verify_snapshot(
    path: Path = SNAPSHOT, root: Path = cc.REPO, *, evidence_directory=None
) -> dict:
    """Read and reproduce a saved observation; this never runs or writes anything."""

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise cg.EvidenceError(f"duplicate research snapshot key: {key}")
            result[key] = value
        return result

    path = Path(path)
    if not path.is_absolute():
        path = root / path
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise cg.EvidenceError(f"research snapshot is missing or invalid: {path}") from exc
    if not isinstance(snapshot, dict):
        raise cg.EvidenceError("research snapshot must be an object")
    evidence = snapshot.get("evidence")
    required_keys = {"reports", "setup_receipts", "sha256"}
    if not isinstance(evidence, dict) or not (
        required_keys <= set(evidence) <= required_keys | {"inventories"}
    ):
        raise cg.EvidenceError("research snapshot requires explicit evidence references and hashes")
    inventories = evidence.get("inventories", {})
    if not isinstance(inventories, dict) or any(
        not isinstance(entry, dict)
        or set(entry) != {"research", "tavotto"}
        or any(not isinstance(name, str) for name in entry.values())
        for entry in inventories.values()
    ):
        raise cg.EvidenceError("snapshot inventories must map a receipt to its two package lists")
    references = []
    for kind in ("reports", "setup_receipts"):
        names = evidence[kind]
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise cg.EvidenceError("snapshot evidence references must be lists of relative paths")
        references.extend(names)
    if set(inventories) - set(evidence["setup_receipts"]):
        raise cg.EvidenceError("snapshot inventories name a receipt outside the evidence")
    references.extend(name for entry in inventories.values() for name in entry.values())
    hashes = evidence["sha256"]
    if (
        len(set(references)) != len(references)
        or not isinstance(hashes, dict)
        or set(hashes) != set(references)
    ):
        raise cg.EvidenceError(
            "snapshot evidence must have unique references and exact hash coverage"
        )
    archive = Path(evidence_directory) if evidence_directory is not None else root
    evidence_root = (archive / SNAPSHOT_EVIDENCE).resolve()
    files = {}
    for name in references:
        project_relative(name)
        source = cg.contained(archive, name)
        if (
            not Path(name).is_relative_to(SNAPSHOT_EVIDENCE)
            or not source.resolve().is_relative_to(evidence_root)
            or not source.is_file()
        ):
            raise cg.EvidenceError(
                "snapshot evidence must be a file in the baseline evidence directory"
            )
        expected = hashes[name]
        if (
            not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or file_digest(source) != expected
        ):
            raise cg.EvidenceError(f"snapshot evidence hash differs: {name}")
        files[name] = source
    check_package_inventories(evidence, files)
    enrollment_sha = snapshot.get("enrollment_sha")
    if not isinstance(enrollment_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", enrollment_sha):
        raise cg.EvidenceError("research snapshot requires an immutable enrollment SHA")
    cohort = load_cohort(root / "tests/compat/research-cohort.json", root=root)
    reports = [summary.load_report(files[name]) for name in evidence["reports"]]
    recomputed = project_reports(cohort, reports, enrollment_sha)
    check_setup_receipts(recomputed, [files[name] for name in evidence["setup_receipts"]])
    recomputed["evidence"] = evidence
    # JSON comparison preserves number/bool distinctions that Python dict equality loses.
    if json.dumps(snapshot, sort_keys=True) != json.dumps(recomputed, sort_keys=True):
        raise cg.EvidenceError("research snapshot differs from recomputed evidence")
    return recomputed


#: receipt field -> inventory role. Receipts from before the two-venv split carry only the first.
INVENTORY_FIELDS = (("installed_sha256", "research"), ("tavotto_installed_sha256", "tavotto"))


def check_package_inventories(evidence: dict, files: dict) -> None:
    """Each receipt's recorded package-list hashes must match archived lists, byte for byte.

    A receipt that records `tavotto_installed_sha256` (two-venv format) must come with both lists;
    older receipts record only the scientific list and may omit the archive. Recorded but absent,
    listed but unrecorded, or different bytes all fail.
    """
    inventories = evidence.get("inventories", {})
    for name in evidence["setup_receipts"]:
        receipt = json.loads(files[name].read_text(encoding="utf-8"))
        recorded = {role: receipt.get(field) for field, role in INVENTORY_FIELDS}
        listed = inventories.get(name)
        if listed is None:
            if recorded["tavotto"] is not None:
                raise cg.EvidenceError(f"receipt package lists are not archived: {name}")
            continue
        for role, path in listed.items():
            if recorded[role] is None or file_digest(files[path]) != recorded[role]:
                raise cg.EvidenceError(f"receipt package list differs from archive: {name} {role}")


def markdown(data: dict) -> str:
    lines = [
        "# S0 开发集基线",
        "",
        f"产品基线 `{data['product_baseline_sha']}`；登记/测量提交 `{data['enrollment_sha']}`。",
        f"固定开发项目 {data['n_raw']}；有可信可比原生 Figure 的项目 {data['n_eligible']}；"
        f"封存留出 {data['holdout_sealed_not_run']}（未运行）。",
        "",
        "| 项目 | 领域 | 原生 | Guardian 结论 | 类别 / 原始码 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in data["projects"]:
        lines.append(
            f"| {row['project_id']} | {row['domain']} | {row.get('native_status', 'not_run')} "
            f"| {row['status']} | {row['failure_category']} / {row['source_code']} |"
        )
    lines += ["", "## 固定分母阶段统计", ""]
    for name, value in data["stages_fixed_denominator"].items():
        lines.append(f"- {name}: {value['passed']} / {value['denominator']}")
    lines += ["", "## 待分诊分组", ""]
    for name, ids in data["clusters"].items():
        lines.append(f"- `{name}` ({len(ids)}): {', '.join(ids)}")
    lines += ["", "## 证据边界", ""] + ["- " + s for s in data["unverified"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "verify",
            "verify-snapshot",
            "matrix",
            "summarize",
            "stage-dependency",
            "verify-environment",
            "verify-science-environment",
            "fetch",
        ),
    )
    parser.add_argument("reports", nargs="*")
    parser.add_argument("--expected-sha")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument("--case")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--setup-receipts", type=Path, nargs="*", default=[])
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--evidence-directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "verify-snapshot":
        if args.evidence_directory is None:
            verify_snapshot(args.snapshot)
        else:
            verify_snapshot(args.snapshot, evidence_directory=args.evidence_directory)
        print("research snapshot exactly reproduces its referenced Guardian evidence")
        return 0
    if args.command == "verify-science-environment":
        if args.python is None:
            parser.error("verify-science-environment requires --python")
        try:
            present = renderer_packages(str(args.python))
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"science interpreter cannot be inspected: {type(exc).__name__}")
            return 1
        if present:
            print("science environment contains Tavotto render packages: " + ", ".join(present))
            return 1
        print("science environment has none of the Tavotto render packages")
        return 0
    cohort = load_cohort()
    selected = [p for p in cohort["development"] if args.case is None or p["case_id"] == args.case]
    if not selected:
        parser.error("--case must name a registered development project")
    if args.command == "fetch":
        cache = sources.cache_root(args.cache)
        for project in selected:
            print(json.dumps(sources.fetch_project(project, cache)), flush=True)
        return 0
    if args.command == "verify-environment":
        project = next((p for p in cohort["development"] if p["case_id"] == args.case), None)
        if project is None:
            parser.error("verify-environment requires a registered --case")
        helper = project.get("dependency_helper_integrity")
        if helper:
            parts = helper["module"].split(".")
            # The helper lives in the scientific interpreter, not in this (Tavotto) one.
            located = cg.science_distribution_file(parts[0], "/".join(parts) + ".py")
            path = located
            if file_digest(Path(path)) != helper["sha256"]:
                raise cg.EvidenceError(
                    "installed scientific helper differs from frozen upstream source"
                )
        print("scientific helper source identity verified (when applicable)")
        return 0
    if args.command == "stage-dependency":
        project = next((p for p in cohort["development"] if p["case_id"] == args.case), None)
        if project is None or args.out is None:
            parser.error("stage-dependency requires a registered --case and --out")
        target = args.out.resolve()
        if target.is_relative_to(cc.REPO.resolve()) or target.exists():
            parser.error("dependency staging must be a new directory outside the checkout")
        target.mkdir(parents=True)
        for dest, source in project.get("source_package_files", {}).items():
            path = cg.contained(target, dest)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(sources.verified_bytes(project, source, sources.cache_root()))
        print("frozen dependency sources staged; nothing executed")
        return 0
    if args.command == "verify":
        print("frozen registration metadata verified; no upstream files downloaded or executed")
        return 0
    if args.command == "matrix":
        print(
            json.dumps(
                {
                    "include": [
                        {
                            "case_id": p["case_id"],
                            "tex": p.get("requires_tex", False),
                            "source_package": bool(p.get("source_package_files")),
                        }
                        for p in selected
                    ]
                }
            )
        )
        return 0
    if not args.expected_sha:
        parser.error("summarize requires --expected-sha (the actual enrollment commit)")
    data = project_reports(
        cohort, [summary.load_report(Path(p)) for p in args.reports], args.expected_sha
    )
    check_setup_receipts(data, args.setup_receipts)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown(data), encoding="utf-8")
    print(markdown(data))
    return 3 if data["missing_cases"] or data["missing_setup_receipts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
