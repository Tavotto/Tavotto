#!/usr/bin/env python3
"""CG-03 reductions of exact reviewed graphs; evidence validation, not attestation.

Each acceptance reads retained native/V2 bytes, counters, reviewed Figure truth,
source/plan identity and the same concrete failing criterion. No unknown scripts,
installer, new worker, automatic root-cause confirmation or product-defect claim.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import compat_guardian as cg
import guardian_variants as gv


def graph(files):
    return {k: cg.digest(v) for k, v in sorted(files.items())}


@dataclass(frozen=True)
class Packet:
    root: Path
    refs: list[dict]


@dataclass(frozen=True)
class _VerifiedProof:
    status: str
    source_sha256: str
    source: dict
    plan_sha256: str
    native_correct: bool
    comparable: bool
    native_executions: int | None
    tavotto_executions: int | None
    environment: str | None
    signature: dict
    evidence_sha256: str
    evidence: list[dict]
    evidence_root: str


def truth_figures(truth):
    figures = truth.get("figures")
    if not isinstance(figures, dict) or not figures:
        raise cg.EvidenceError("reviewed per-Figure truth required")
    for stem, titles in figures.items():
        if (not isinstance(stem, str) or not stem or not isinstance(titles, list)
                or any(not isinstance(t, str) for t in titles)):
            raise cg.EvidenceError("invalid per-Figure truth")
    return figures


def signature(native, obs):
    """Keep exact criterion delta, including missing or untitled Figures."""
    stems = sorted(native["stems"])
    titles = sorted(t for v in native["semantics"].values() for t in v["titles"] if t)
    if not obs.get("ran"):
        stage, delta = "execute", {"ran": obs.get("ran"), "phase": obs.get("final_phase")}
    elif (obs.get("figures_captured") != len(stems) or obs.get("figures_valid") != len(stems)
          or sorted(obs.get("captured_stems", [])) != stems
          or obs.get("failed_figures") or obs.get("render_errors")):
        stage, delta = "capture", {
            "expected_stems": stems, "actual_stems": sorted(obs.get("captured_stems", [])),
            "expected_count": len(stems), "captured": obs.get("figures_captured"),
            "valid": obs.get("figures_valid"), "failed": obs.get("failed_figures"),
            "render_errors": obs.get("render_errors")}
        if (not obs.get("failed_figures") and not obs.get("render_errors")
                and (obs.get("error_type") or (not obs.get("figures_captured")
                     and obs.get("final_phase") in ("failed", "action_required")))):
            stage = "execute"
    elif sorted(obs.get("titles", [])) != titles:
        stage, delta = "semantic", {"expected": titles, "actual": sorted(obs.get("titles", []))}
    elif obs.get("final_phase") != "completed" or obs.get("problem_codes"):
        stage, delta = "execute", {"expected_phase": "completed", "actual_phase": obs.get("final_phase")}
    else:
        return {}
    return {"stage": stage, "criterion_delta": delta, "phase": obs.get("final_phase"),
            "codes": sorted(obs.get("problem_codes", [])), "error_type": obs.get("error_type")}


def verify_packet(packet, files, *, source, plan_sha256, truth):
    """Only a raw packet can become a proof; dictionary verdicts are rejected."""
    if not isinstance(packet, Packet) or not packet.refs:
        raise cg.EvidenceError("raw packet required")
    if cg.evidence_refs(packet.root) != packet.refs:
        raise cg.EvidenceError("missing/changed/extra raw evidence")
    process = json.loads(cg.contained(packet.root, "process.json").read_text())
    cleanup = process.get("cleanup") or {}
    if (type(process.get("exit_code")) is not int or process["exit_code"] != 0
            or process.get("timed_out") is not False or cleanup.get("group_gone") is not True
            or type(cleanup.get("returncode")) is not int or cleanup["returncode"] != 0):
        raise cg.EvidenceError("interrupted probe or unverified process-tree cleanup")
    inputs = cg.contained(packet.root, "reviewed-inputs")
    retained = {p.relative_to(inputs).as_posix(): cg.contained(inputs, p.relative_to(inputs).as_posix()).read_bytes()
                for p in inputs.rglob("*") if p.is_file()}
    if retained != files:
        raise cg.EvidenceError("retained source/data graph differs from candidate")
    raw = json.loads(cg.contained(packet.root, "reports/probe-raw.json").read_text())
    if (raw.get("schema") != 2 or raw.get("source_before") != source
            or raw.get("source_after") != source or raw.get("plan_sha256") != plan_sha256
            or raw.get("files") != graph(files)):
        raise cg.EvidenceError("candidate, implementation revision or reviewed plan changed")
    if not re.fullmatch(r"[0-9a-f]{32}", str(raw.get("run_id", ""))):
        raise cg.EvidenceError("missing run identity")
    want = truth_figures(truth)
    native = raw.get("native") or {}
    native_status = cg._native_status(native)
    if raw.get("missing_prerequisites") and not native:
        native_status = "environment_unavailable"
    native_correct = (native_status == "passed" and sorted(native.get("stems", [])) == sorted(want)
                      and native.get("semantics") == {s: {"titles": t} for s, t in want.items()})
    launches = {}
    for role in ("native", "tavotto"):
        path = packet.root / f"{role}-execs.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        for item in records:
            if (item.get("source_sha256") != cg.digest(files["script.py"])
                    or type(item.get("pid")) is not int or item["pid"] <= 0):
                raise cg.EvidenceError("counter source does not match candidate")
            env = item.get("environment") or {}
            if not env.get("facts") or env.get("fingerprint") != cg.canonical(env["facts"]):
                raise cg.EvidenceError("unbound actual environment")
        launches[role] = records
    fps = {r["environment"]["fingerprint"] for rows in launches.values() for r in rows}
    obs = raw.get("tavotto") or {}
    fixture = packet.root / "fixture-execs.jsonl"
    fixture_count = len(fixture.read_text().splitlines()) if fixture.exists() else None
    before = raw.get("environment_before") or {}
    checks = obs.get("facts", {}).get("exec_counts_at_checks")
    comparable = (all(launches.values()) and len(fps) == 1
                  and before == raw.get("environment_after")
                  and before.get("fingerprint") == cg.canonical(before.get("facts"))
                  and len(launches["native"]) == 1
                  and len(launches["tavotto"]) == fixture_count == obs.get("exec_count")
                  and bool(checks) and all(v == 0 for v in checks))
    failure = signature(native, obs) if native_correct and obs else {}
    if not native_correct:
        status = native_status if native_status != "passed" else "invalid_fixture"
    elif not comparable:
        status = "unknown"
    elif not failure and len(want) > 1:
        status = "unknown"  # V2 cannot observe per-stem title association; do not claim equivalence.
    else:
        status = "failed" if failure else "not_reproduced"
    return _VerifiedProof(status, cg.canonical(graph(files)), source, plan_sha256, native_correct,
                          bool(comparable), len(launches["native"]) or None,
                          len(launches["tavotto"]) or None, next(iter(fps)) if len(fps) == 1 else None,
                          failure, cg.canonical(packet.refs), packet.refs, str(packet.root))


def accepts(original, candidate, expected_subject):
    return (isinstance(original, _VerifiedProof) and isinstance(candidate, _VerifiedProof)
            and original.status == candidate.status == "failed"
            and candidate.source_sha256 == expected_subject and candidate.source == original.source
            and candidate.plan_sha256 == original.plan_sha256
            and original.native_correct and original.comparable
            and candidate.native_correct and candidate.comparable
            and candidate.native_executions == 1 and (candidate.tavotto_executions or 0) > 0
            and bool(candidate.environment) and candidate.environment == original.environment
            and bool(candidate.signature) and candidate.signature == original.signature)


def deletion_candidates(source):
    tree = ast.parse(source.decode())
    for index in range(len(tree.body)):
        reduced = ast.Module(body=tree.body[:index] + tree.body[index + 1:], type_ignores=tree.type_ignores)
        code = (ast.unparse(ast.fix_missing_locations(reduced)) + "\n").encode()
        if code.strip() and code != source:
            yield index, code


def minimize(files, script, oracle, approved, *, attempts, seconds, source, plan_sha256, truth):
    if type(attempts) is not int or attempts < 1 or not math.isfinite(seconds) or seconds <= 0:
        raise cg.EvidenceError("finite positive candidate/time budgets required")
    gv.static_check(files)
    truth_figures(truth)
    if cg.canonical(graph(files)) not in approved:
        return {"status": "not_run", "reason": "original graph not reviewed", "history": []}
    started = time.monotonic()
    history, pending, calls = [], [], 0

    def observe(candidate):
        nonlocal calls
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise cg.EvidenceError("wall-clock budget exhausted")
        calls += 1
        return verify_packet(oracle(candidate, remaining), candidate, source=source,
                             plan_sha256=plan_sha256, truth=truth)

    try:
        original = observe(files)
    except (cg.EvidenceError, ValueError, OSError) as exc:
        return {"status": "not_run", "reason": str(exc), "history": history, "oracle_calls": calls}
    if not accepts(original, original, cg.canonical(graph(files))):
        return {"status": "not_run", "reason": "original failure/native truth premise not established",
                "original": asdict(original), "history": history, "oracle_calls": calls}
    current = dict(files)
    while len(history) < attempts and time.monotonic() - started < seconds:
        changed = False
        for index, code in deletion_candidates(current[script]):
            if len(history) >= attempts or time.monotonic() - started >= seconds:
                break
            candidate = {**current, script: code}
            subject = cg.canonical(graph(candidate))
            if subject not in approved:
                pending.append({"content_sha256": subject, "removed_statement": index, "status": "pending_review",
                                "script": script, "source": code.decode(), "files": graph(candidate), "execution": "not_run"})
                continue
            try:
                proof = observe(candidate)
                trial = {"proof": asdict(proof), "accepted": accepts(original, proof, subject)}
            except (cg.EvidenceError, ValueError, OSError) as exc:
                trial = {"accepted": False, "status": "unknown", "reason": str(exc)}
            history.append({"content_sha256": subject, "removed_statement": index, **trial})
            if trial["accepted"]:
                current, changed = candidate, True
                break
        if not changed:
            break
    return {"status": "minimized" if any(h["accepted"] for h in history) else "not_reduced",
            "original": asdict(original), "history": history, "pending_review": pending,
            "candidate_attempts": len(history), "oracle_calls": calls,
            "duration_s": time.monotonic() - started, "files": graph(current), "source": current[script].decode(),
            "preserved_relative_names": sorted(current) == sorted(files),
            "unchanged_non_entry_bytes": all(current[k] == v for k, v in files.items() if k != script)}


def diagnose(proof):
    status = proof.get("status", "unknown")
    if proof.get("native_correct") is not True:
        return {"category": "fixture_or_environment", "execution_status": status,
                "product_defect": False if status in ("invalid_fixture", "environment_unavailable") else None}
    if not proof.get("comparable"):
        return {"category": "incomparable_environment", "execution_status": status, "product_defect": None}
    if status == "not_reproduced":
        return {"category": "not_reproduced", "execution_status": status, "product_defect": False}
    if status != "failed":
        return {"category": "unknown", "execution_status": status, "product_defect": None}
    return {"category": "unclassified", "confidence": "hypothesis", "product_defect": None,
            "signature": proof.get("signature"), "evidence_sha256": proof.get("evidence_sha256")}


def cluster(proofs):
    return {"confirmed": {}, "hypotheses": [{"source_sha256": p.get("source_sha256"), "diagnosis": diagnose(p)}
                                          for p in proofs], "traceback_alone_merges": False,
            "reason": "no verified causal-evidence contract; caller assertions cannot confirm roots"}


def load_plan(path):
    relative = path.resolve().relative_to(cg.ROOT).as_posix()
    blob = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=cg.ROOT)
    if cg.contained(cg.ROOT, relative).read_bytes() != blob:
        raise cg.EvidenceError("review plan differs from committed HEAD")
    plan = json.loads(blob)
    if plan["schema"] != 2 or plan["suite"] != "compat_v2":
        raise cg.EvidenceError("unknown minimizer plan")
    cg.fixture_files(plan["suite"], cg.registered(plan["suite"])[plan["case"]])
    truth_figures(plan["truth"])
    return plan, cg.digest(blob)


def _probe(request):
    from support import compatbench_v2 as cb
    import compat_matrix as cm

    plan, plan_sha = load_plan(Path(request["plan"]))
    work, evidence = Path(request["work"]), Path(request["evidence"])
    base = work / "reviewed/case"
    files = {p.relative_to(base).as_posix(): cg.contained(base, p.relative_to(base).as_posix()).read_bytes()
             for p in base.rglob("*") if p.is_file()}
    subject = cg.canonical(graph(files))
    if (plan["review"]["status"] != "approved" or subject != request["content_sha256"]
            or subject not in plan["review"]["approved_content_hashes"]):
        raise cg.EvidenceError("unreviewed graph cannot execute")
    gv.static_check(files)
    raw = {"schema": 2, "run_id": uuid.uuid4().hex, "source_before": cg.source_state(),
           "plan_sha256": plan_sha, "files": graph(files), "native": {}, "tavotto": None,
           "environment_before": cg.environment()}
    try:
        missing = cg.preflight()
        if missing:
            raw["missing_prerequisites"] = missing
            return
        cb.FIXTURES = work / "reviewed"
        native_work = work / "native"
        native_work.mkdir()
        project = cb.stage("case", native_work)
        os.environ["CG_EXEC_LOG"] = str(work / "native-execs.jsonl")
        raw["native"] = cm.run_driver(sys.executable, "native", {
            "script": str(project / "script.py"), "project": str(project), "entry": "__main__",
            "engine_dir": str(cm.ENGINE_DIR), "out_dir": str(evidence / "native"), "observe_semantics": True})
        want = {s: {"titles": t} for s, t in truth_figures(plan["truth"]).items()}
        if cg._native_status(raw["native"]) != "passed" or raw["native"].get("semantics") != want:
            return
        os.environ["CG_EXEC_LOG"] = str(work / "tavotto-execs.jsonl")
        case_work = work / "v2"
        case_work.mkdir()
        scenario = cb.Scenario(id="case", scenes=("reviewed-reduction",), title="reviewed reduction",
                               failure_mode="preserve criterion delta", expected_strategy="observe Safe API",
                               ideal_exec_count=1, ideal_exec_count_max=1, ideal_outcome="figure")
        obs = cb.observe(scenario, case_work, env_overrides={"PYTHONPATH": os.environ["PYTHONPATH"]})
        cb.assert_no_execution_before_run(obs)
        raw["tavotto"] = obs.to_json()
    finally:
        raw["source_after"] = cg.source_state()
        raw["environment_after"] = cg.environment()
        cg.write_report("probe-raw.json", raw, root=evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--_probe", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args._probe:
            _probe(json.loads(args._probe.read_text()))
            return 0
        if not args.plan or not args.out:
            parser.error("--plan and --out required")
        if not math.isfinite(args.seconds) or args.seconds <= 0 or args.attempts < 1:
            raise cg.EvidenceError("finite positive candidate/time budgets required")
        if args.out.resolve().is_relative_to(cg.ROOT):
            raise cg.EvidenceError("outputs must be outside source worktree")
        plan, plan_sha = load_plan(args.plan)
        if plan["review"]["status"] != "approved":
            print(json.dumps({"status": "not_run", "reason": "unapproved plan; AC-06 unverified"}))
            return 3
        source, run_id = cg.source_state(), uuid.uuid4().hex
        output = args.out.resolve() / run_id
        output.mkdir(parents=True)
        _parent, files, script = gv.parent_files(plan["suite"], plan["case"])
        if script != "script.py":
            raise cg.EvidenceError("V2 entry must be script.py")

        def oracle(candidate, remaining):
            dest = output / "evidence" / uuid.uuid4().hex
            dest.mkdir(parents=True)
            with tempfile.TemporaryDirectory(prefix="cg-reduction-") as tmp:
                work = Path(tmp)
                for rel, data in candidate.items():
                    target = cg.contained(work / "reviewed/case", rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    retained = cg.contained(dest / "reviewed-inputs", rel)
                    retained.parent.mkdir(parents=True, exist_ok=True)
                    retained.write_bytes(data)
                request = {"work": str(work), "evidence": str(dest), "plan": str(args.plan.resolve()),
                           "content_sha256": cg.canonical(graph(candidate))}
                path = work / "request.json"
                path.write_text(json.dumps(request))
                try:
                    cg.launch_adapter([sys.executable, str(Path(__file__).resolve()), "--_probe", str(path)],
                                      work=work, evidence=dest, timeout=remaining,
                                      env=cg.isolated_env(work, work / "adapter-execs.jsonl", [cg.digest(candidate[script])]))
                except cg.EvidenceError:
                    pass  # partial packets are retained and fail verification
                finally:
                    for log in work.glob("*-execs.jsonl"):
                        shutil.copy2(log, dest / log.name)
                    fixture = work / "v2/exec_log.jsonl"
                    if fixture.exists():
                        shutil.copy2(fixture, dest / "fixture-execs.jsonl")
            return Packet(dest, cg.evidence_refs(dest))

        result = minimize(files, script, oracle, set(plan["review"]["approved_content_hashes"]),
                          attempts=args.attempts, seconds=args.seconds, source=source,
                          plan_sha256=plan_sha, truth=plan["truth"])
        after = cg.source_state()
        if after != source:
            result = {"status": "unknown", "reason": "source changed during minimization", "partial": result}
        report = {"schema": 2, "run_id": run_id, "source_before": source, "source_after": after,
                  "plan_sha256": plan_sha, "result": result, "evidence": cg.evidence_refs(output / "evidence"),
                  "unverified": ["scientific arrays", "desktop Native", "confirmed root cause", "AC-06 until real evidence"]}
        path = cg.write_report("minimization.json", report, root=output)
        print(json.dumps({"report": str(path), "status": result["status"]}))
        return 0 if result["status"] == "minimized" else 3
    except (cg.EvidenceError, ValueError, KeyError, OSError) as exc:
        print(f"minimizer refused: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
