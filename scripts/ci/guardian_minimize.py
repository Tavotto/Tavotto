#!/usr/bin/env python3
"""CG-03 bounded execution minimizer for explicitly reviewed reductions.

Every accepted reduction must execute a native truth control and the real V2
authenticated Tavotto path. AST deletion/compilation alone never accepts a step.
The CLI executes only source hashes approved in a committed review plan; otherwise
it emits pending_review/not_run. This environment has no unknown-code sandbox.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import compat_guardian as cg
import guardian_variants as gv


def failure_key(proof: dict) -> str:
    return cg.canonical(proof["signature"])


def accepts(original: dict, candidate: dict) -> bool:
    """Evidence subject: BOTH actual executions of THIS candidate, same launch facts."""
    return (
        original.get("status") == "failed"
        and candidate.get("status") == "failed"
        and candidate.get("native_correct") is True
        and candidate.get("comparable") is True
        and (candidate.get("native_executions") or 0) > 0
        and (candidate.get("tavotto_executions") or 0) > 0
        and candidate.get("environment") == original.get("environment")
        and candidate.get("evidence_sha256")
        and failure_key(candidate) == failure_key(original)
    ) is True


def deletion_candidates(source: bytes):
    """Delete one top-level statement; imports/helpers/data are never moved."""
    tree = ast.parse(source.decode())
    for index in range(len(tree.body)):
        reduced = ast.Module(body=tree.body[:index] + tree.body[index + 1:], type_ignores=tree.type_ignores)
        code = (ast.unparse(ast.fix_missing_locations(reduced)) + "\n").encode()
        if code.strip() and code != source:
            yield index, code


def minimize(files: dict[str, bytes], script: str, oracle, approved: set[str], *, attempts: int, seconds: float) -> dict:
    if attempts < 1 or seconds <= 0:
        raise cg.EvidenceError("positive minimization budgets required")
    gv.static_check(files)
    start = time.monotonic()
    original = oracle(files, seconds)
    history = []
    current = dict(files)
    if not accepts(original, original):
        return {"status": "not_run", "reason": "original native truth / same-failure premise not established", "original": original, "history": history}
    tried = 0
    pending = []
    while tried < attempts and time.monotonic() - start < seconds:
        changed = False
        for index, code in deletion_candidates(current[script]):
            if tried >= attempts or time.monotonic() - start >= seconds:
                break
            candidate = {**current, script: code}
            content = cg.canonical({k:cg.digest(v) for k,v in sorted(candidate.items())})
            if content not in approved:
                pending.append({"content_sha256": content, "removed_statement": index, "status": "pending_review"})
                continue  # no unknown script execution without review + exact content identity
            tried += 1
            proof = oracle(candidate, max(0.001, seconds - (time.monotonic() - start)))
            kept = accepts(original, proof)
            history.append({"content_sha256": content, "removed_statement": index, "accepted": kept, "proof": proof})
            if kept:
                current = candidate
                changed = True
                break
        if not changed:
            break
    return {"status": "minimized" if any(h["accepted"] for h in history) else "not_reduced",
            "original": original, "history": history, "pending_review": pending,
            "attempts": tried, "duration_s": time.monotonic()-start,
            "files": {k:cg.digest(v) for k,v in sorted(current.items())},
            "source": current[script].decode(), "file_relationships_preserved": sorted(current) == sorted(files)}


def diagnose(proof: dict) -> dict:
    if proof.get("native_correct") is not True:
        return {"category": "fixture_or_environment", "confidence": "observed", "product_defect": False}
    if not proof.get("comparable"):
        return {"category": "incomparable_environment", "confidence": "observed", "product_defect": False}
    if proof.get("status") != "failed":
        return {"category": "not_reproduced", "confidence": "observed", "product_defect": False}
    signature = proof["signature"]
    codes = signature.get("codes", [])
    category = "safe_file_semantics" if "safe_file_operation_blocked" in codes else "unclassified"
    return {"category": category, "stage": signature["stage"], "confidence": "hypothesis",
            "product_defect": None, "evidence_sha256": proof.get("evidence_sha256"),
            "reason": "same stage/code is a failure family, not a proven shared root cause"}


def cluster(proofs: list[dict]) -> dict:
    """Only explicit common causal evidence can form a confirmed root-cause cluster."""
    confirmed = {}
    hypotheses = []
    for proof in proofs:
        cause = proof.get("confirmed_cause") or {}
        if accepts(proof, proof) and cause.get("id") and cause.get("evidence_sha256"):
            key = cg.canonical({"id":cause["id"], "evidence":cause["evidence_sha256"],
                                "environment":proof["environment"]})
            confirmed.setdefault(key, []).append(proof["source_sha256"])
        else:
            hypotheses.append({"source_sha256": proof.get("source_sha256"), "diagnosis": diagnose(proof)})
    return {"confirmed": confirmed, "hypotheses": hypotheses,
            "traceback_alone_merges": False}


def _probe(request: dict) -> dict:
    """Real native and Tavotto probes; no synthetic success fallback."""
    from support import compatbench_v2 as cb
    import compat_matrix as cm

    work = Path(request["work"])
    evidence = Path(request["evidence"])
    proof = {"status": "unknown", "native_correct": False, "comparable": False,
             "native_executions": None, "tavotto_executions": None,
             "source_sha256": request["content_sha256"], "environment": None,
             "signature": {}, "evidence_sha256": None}
    missing = cg.preflight()
    if missing:
        return {**proof, "status":"environment_unavailable", "missing":missing}
    cb.FIXTURES = work / "reviewed"
    native_work = work / "native"
    native_work.mkdir()
    project = cb.stage("case", native_work)
    os.environ["CG_EXEC_LOG"] = str(work / "native.jsonl")
    native = cm.run_driver(sys.executable, "native", {
        "script":str(project / "script.py"), "project":str(project), "entry":"__main__",
        "engine_dir":str(cm.ENGINE_DIR), "out_dir":str(evidence / "native"), "observe_semantics":True,
    })
    titles = sorted(t for sem in native.get("semantics", {}).values() for t in sem["titles"] if t)
    proof["native_correct"] = cg._native_status(native) == "passed" and titles == sorted(request["truth_titles"])
    proof["native"] = native
    if not proof["native_correct"]:
        return {**proof, "status":cg._native_status(native) if not native.get("ok") else "invalid_fixture"}
    os.environ["CG_EXEC_LOG"] = str(work / "tavotto.jsonl")
    tavotto_work = work / "tavotto"
    tavotto_work.mkdir()
    scenario = cb.Scenario(id="case", scenes=("reviewed-reduction",), title="reviewed reduction",
                           failure_mode="preserve original signature", expected_strategy="observe safe API",
                           ideal_exec_count=1, ideal_exec_count_max=1, ideal_outcome="figure")
    obs = cb.observe(scenario, tavotto_work, env_overrides={"PYTHONPATH":os.environ["PYTHONPATH"]})
    cb.assert_no_execution_before_run(obs)
    proof["tavotto"] = obs.to_json()
    launches = {}
    for role in ("native", "tavotto"):
        log = work / f"{role}.jsonl"
        records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        launches[role] = records
        proof[f"{role}_executions"] = len(records) if records else None
        if records:
            (evidence / f"{role}.jsonl").write_text(log.read_text())
    envs = {r["environment"]["fingerprint"] for records in launches.values() for r in records}
    proof["environment"] = next(iter(envs)) if len(envs) == 1 else None
    proof["comparable"] = all(launches.values()) and len(envs) == 1 and proof["tavotto_executions"] == obs.exec_count
    failed = obs.figures_valid < 1 or sorted(obs.titles) != titles
    if failed:
        stage = "execute" if obs.final_phase == "failed" else "capture" if obs.figures_valid < 1 else "semantic"
        proof["signature"] = {"stage":stage, "codes":sorted(obs.problem_codes), "phase":obs.final_phase, "error_type":obs.error_type}
    proof["status"] = "failed" if failed and proof["comparable"] else "not_reproduced" if not failed else "unknown"
    proof["evidence_sha256"] = cg.canonical({"native":native, "tavotto":obs.to_json(), "launches":launches})
    return proof


def load_plan(path: Path) -> dict:
    relative = path.resolve().relative_to(cg.ROOT).as_posix()
    blob = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=cg.ROOT)
    if cg.contained(cg.ROOT, relative).read_bytes() != blob:
        raise cg.EvidenceError("reduction review plan differs from committed HEAD")
    plan = json.loads(blob)
    if plan["schema"] != 1 or plan["suite"] != "compat_v2":
        raise cg.EvidenceError("unknown minimizer plan")
    cg.fixture_files(plan["suite"], cg.registered(plan["suite"])[plan["case"]])
    return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--_probe", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args._probe:
            request = json.loads(args._probe.read_text())
            plan = load_plan(Path(request["plan"]))
            if plan["review"]["status"] != "approved" or request["content_sha256"] not in plan["review"]["approved_content_hashes"]:
                raise cg.EvidenceError("unreviewed reduction cannot execute")
            files = {p.relative_to(Path(request["work"]) / "reviewed/case").as_posix():p.read_bytes()
                     for p in (Path(request["work"]) / "reviewed/case").rglob("*") if p.is_file()}
            if cg.canonical({k:cg.digest(v) for k,v in sorted(files.items())}) != request["content_sha256"]:
                raise cg.EvidenceError("reviewed reduction content changed")
            result = _probe(request)
            (Path(request["evidence"]) / "probe.json").write_text(json.dumps(result, indent=2))
            return 0
        if not args.plan or not args.out:
            parser.error("--plan and --out required")
        plan = load_plan(args.plan)
        if plan["review"]["status"] != "approved":
            print(json.dumps({"status":"not_run", "reason":"review plan not approved; AC-06 unverified"}))
            return 3
        _parent, files, script = gv.parent_files(plan["suite"], plan["case"])
        approved = set(plan["review"]["approved_content_hashes"])
        def oracle(candidate, remaining):
            content = cg.canonical({k:cg.digest(v) for k,v in sorted(candidate.items())})
            if content not in approved:
                return {"status":"not_run", "reason":"source not reviewed"}
            dest = args.out.resolve() / content
            dest.mkdir(parents=True, exist_ok=False)
            with tempfile.TemporaryDirectory(prefix="cg-reduction-") as tmp:
                work = Path(tmp)
                for rel, data in candidate.items():
                    target = cg.contained(work / "reviewed/case", rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                request = {"work":str(work), "evidence":str(dest), "plan":str(args.plan.resolve()),
                           "content_sha256":content, "truth_titles":plan["truth_titles"]}
                request_path = work / "request.json"
                request_path.write_text(json.dumps(request))
                hashes = [cg.digest(data) for rel,data in candidate.items() if rel == "script.py"]
                cg.launch_adapter([sys.executable, str(Path(__file__).resolve()), "--_probe", str(request_path)],
                                  work=work, evidence=dest, env=cg.isolated_env(work, work / "adapter.jsonl", hashes), timeout=remaining)
                return json.loads((dest / "probe.json").read_text())
        result = minimize(files, script, oracle, approved, attempts=args.attempts, seconds=args.seconds)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "minimized" else 3
    except (cg.EvidenceError, ValueError, KeyError, OSError) as exc:
        print(f"minimizer refused: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
