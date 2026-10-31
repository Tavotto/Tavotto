"""CG-03 `minimize`: bounded delta debugging over one explore finding.

What is reduced: the AST of a CG-02 candidate (statement and block level), plus data /
helper files nothing references. What may run: only reductions of a candidate that is
re-derived from (parent, chain, seed); a reduced script is a deterministic subset of that
candidate (nothing is ever added), so it has the candidate's, hence the parent's, trust
level. Execution is CG-02's isolated run path; no installs, no authorization endpoints.

A reduction step is accepted only if the *same* failure is reproduced for the *same*
reason (`accept`): native still runs correctly with the same expectation, Tavotto shows the
same difference with the same stable code, the attributed stage and family are unchanged,
and the trial did not trade the defect for a new syntax error or a missing package.
Anything else is rejected and recorded. When the run or time budget is exhausted the
outcome is `minimization_failed` and the original fixture is what is kept.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import compat_explore as ex
import compat_explore_transforms as XT
import compat_guardian as cg
import compat_triage as TG

SCHEMA = "tavotto-guardian-minimization/1"
PROTECTED_KINDS = (ast.FunctionDef, ast.ClassDef)


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, max_runs: int, max_seconds: float):
        if not (type(max_runs) is int and max_runs > 0) or not (0 < max_seconds < float("inf")):
            raise cg.EvidenceError("budgets must be finite and positive")
        self.max_runs, self.max_seconds = max_runs, max_seconds
        self.runs, self.started = 0, time.monotonic()

    def spend(self) -> None:
        if self.runs >= self.max_runs or time.monotonic() - self.started > self.max_seconds:
            raise BudgetExceeded(f"budget exhausted after {self.runs} run(s)")
        self.runs += 1

    def remaining(self) -> float:
        """Wall-clock budget left; an in-flight trial must not outlive it."""
        return max(0.0, self.max_seconds - (time.monotonic() - self.started))

    @property
    def seconds(self) -> float:
        return round(time.monotonic() - self.started, 3)


# ---------------------------------------------------------------- failure signature / acceptance


def signature(record: dict, chain: list[str], rules: list[dict]) -> dict:
    """The reproducible shape of a finding, derived from one run record."""
    judgment = record["judgment"]
    nat = record.get("native") or {}
    sig = {
        "outcome": judgment["outcome"],
        "kinds": sorted(d["kind"] for d in judgment["differences"]),
        "native_exit": nat.get("exit_status"),
        "native_expect": {
            "shown": (nat.get("figures_shown") or 0) > 0,
            "saved": (nat.get("savefig_calls") or 0) > 0,
            "multi_writer": bool(nat.get("savefig_last_writer")),
            # several distinct figures written to one path: what makes a same-path finding real
            "overwrite": bool(nat.get("same_path_distinct_figures")),
            "figures": (nat.get("figure_count") or 0) > 0,
        },
        "tavotto_error": ((record.get("tavotto") or {}).get("error") or {}).get("code"),
    }
    if judgment["outcome"] == "difference_found" and judgment["differences"]:
        row = {
            "outcome": judgment["outcome"],
            "chain": chain,
            "evidence": ex.evidence_summary(record),
        }
        attr = TG.attribute(TG.explore_facts(row, judgment["differences"][0]), "explore", rules)
        sig["stage"], sig["family"] = attr["stage"], attr["family"]
    return sig


def accept(original: dict, trial: dict) -> tuple[bool, str]:
    """Is `trial` the same failure for the same reason? Returns (accepted, reason)."""
    if trial["outcome"] != "difference_found":
        return False, f"failure disappeared (outcome {trial['outcome']})"
    if trial["native_exit"] != 0:
        return False, "native no longer runs cleanly: the reduction broke the script, not Tavotto"
    if trial["native_expect"] != original["native_expect"]:
        return False, "native expectation changed"
    if trial["tavotto_error"] != original["tavotto_error"]:
        return (
            False,
            f"different Tavotto error ({original['tavotto_error']} -> {trial['tavotto_error']})",
        )
    if trial["kinds"] != original["kinds"]:
        return False, f"different difference kinds ({original['kinds']} -> {trial['kinds']})"
    if (trial.get("stage"), trial.get("family")) != (original.get("stage"), original.get("family")):
        return False, "attributed stage/family changed"
    return True, "same failure, same reason"


# ---------------------------------------------------------------- reduction machinery


def units_of(source: str, entry: str) -> list[str]:
    """Removable statement addresses; the entry function and the __main__ guard stay."""
    tree = ast.parse(source)
    wanted = set(XT.statement_addresses(tree))
    protected = set()

    def mark(node, prefix):
        for fld in ("body", "orelse", "finalbody"):
            for i, child in enumerate(getattr(node, fld, None) or []):
                addr = f"{prefix}/{fld}{i}"
                if isinstance(child, ast.FunctionDef) and child.name == entry:
                    protected.add(addr)
                if isinstance(child, ast.If) and "__name__" in ast.unparse(child.test):
                    protected.add(addr)
                if isinstance(child, ast.stmt):
                    mark(child, addr)

    mark(tree, "")
    return [a for a in sorted(wanted, key=lambda a: (a.count("/"), a)) if a not in protected]


def ddmin(units: list[str], test, budget: Budget) -> list[str]:
    """Zeller's ddmin over the set of statements to KEEP; `test(kept)` is True if the failure
    is still the same failure. Every call is a re-verification; results are cached."""
    cache: dict[frozenset, bool] = {}

    def check(kept: list[str]) -> bool:
        key = frozenset(kept)
        if key not in cache:
            cache[key] = test(kept)
        return cache[key]

    n = 2
    while len(units) >= 2:
        size = max(1, len(units) // n)
        chunks = [units[i : i + size] for i in range(0, len(units), size)]
        progress = False
        for chunk in chunks:
            if len(chunk) < len(units) and check(chunk):
                units, n, progress = chunk, 2, True
                break
        if not progress:
            for chunk in chunks:
                complement = [u for u in units if u not in chunk]
                if complement and check(complement):
                    units, n, progress = complement, max(n - 1, 2), True
                    break
        if not progress:
            if n >= len(units):
                break
            n = min(len(units), n * 2)
    if len(units) == 1 and check([]):
        units = []
    return units


def reduction_for(kept: list[str], all_units: list[str], unwrap=(), drop_files=()) -> dict:
    keep = set(kept)
    return {
        "delete": sorted(set(all_units) - keep),
        "unwrap": sorted(unwrap),
        "drop_files": sorted(drop_files),
    }


def referenced_files(source: str, files: list[str], script: str) -> set[str]:
    """Files the script plausibly needs: named in a string literal or imported as a module."""
    tree = ast.parse(source)
    words = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            words.add(node.value)
        elif isinstance(node, ast.Import):
            words |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            words.add(node.module.split(".")[0])
    keep = set()
    for rel in files:
        stem = Path(rel).stem
        if any(Path(rel).name in w or stem == w for w in words):
            keep.add(rel)
    return keep


def measure(files: dict[str, bytes], script: str) -> dict:
    source = files[script].decode()
    return {
        "statements": len(XT.statement_addresses(ast.parse(source))),
        "lines": len([ln for ln in source.splitlines() if ln.strip()]),
        "bytes": len(source.encode()),
        "files": len(files),
    }


# ---------------------------------------------------------------- the search


class Result(dict):
    pass


def minimize_candidate(
    manifest: dict, parent: XT.Parent, oracle, rules: list[dict], budget: Budget, repeat: int = 2
) -> Result:
    """`oracle(reduction)` runs one trial and returns a run record (see compat_explore)."""
    chain = [s["name"] for s in manifest["chain"]]
    files0 = XT.build(parent, tuple(chain), manifest["seed"])[1]
    source0 = files0[parent.script].decode()
    all_units = units_of(source0, manifest["entry"])
    trials: list[dict] = []
    seen_sources: dict[str, tuple[bool, str]] = {}
    result = Result(status="minimization_failed", before=measure(files0, parent.script))

    def run_trial(reduction: dict, label: str) -> tuple[bool, str, dict | None]:
        try:
            _, trial_files = XT.build_reduced(parent, tuple(chain), manifest["seed"], reduction)
        except (cg.EvidenceError, SyntaxError) as exc:
            verdict = (False, f"invalid reduction: {str(exc)[:120]}", None)
            trials.append(
                {"label": label, "executed": False, "accepted": False, "reason": verdict[1]}
            )
            return verdict
        plan = XT.execution_plan(trial_files, parent.executable)
        if plan["status"] != "executable":
            verdict = (False, "static-only after reduction", None)
            trials.append(
                {"label": label, "executed": False, "accepted": False, "reason": verdict[1]}
            )
            return verdict
        digest = cg.canonical({k: cg.digest(v) for k, v in trial_files.items()})
        if digest in seen_sources:
            return (*seen_sources[digest], None)
        budget.spend()
        record = oracle(reduction, f"t{budget.runs}")
        trial_sig = signature(record, chain, rules)
        ok, reason = accept(original_sig, trial_sig)
        seen_sources[digest] = (ok, reason)
        trials.append(
            {
                "label": label,
                "executed": True,
                "accepted": ok,
                "reason": reason,
                "removed": len(reduction["delete"]),
                "files_dropped": len(reduction["drop_files"]),
            }
        )
        return ok, reason, record

    base_record: dict | None = None

    def close() -> Result:
        """Common tail for every outcome: write_outcome needs files/after/reduction/runs/seconds."""
        result.update(trials=trials, runs=budget.runs, seconds=budget.seconds)
        if result["status"] != "minimized":
            # Fall back to the original script AND the evidence of the run that reproduced it:
            # the reduced candidate's record/reproducibility describe a different script.
            result.update(
                files=files0,
                after=result["before"],
                reduction=None,
                final_record=base_record,
                reproducibility=None,
            )
        return result

    try:
        # 0. the original must reproduce the finding, or there is nothing to minimize
        budget.spend()
        base = oracle(reduction_for(all_units, all_units), "base")
        original_sig = signature(base, chain, rules)
        base_record = base
        if original_sig["outcome"] != "difference_found" or original_sig["native_exit"] != 0:
            result.update(
                status="not_reproduced",
                signature=original_sig,
                trials=trials,
                reproduced_base=False,
            )
            return close()
        result["signature"] = original_sig
        # 1. statements and blocks
        kept = ddmin(
            all_units, lambda k: run_trial(reduction_for(k, all_units), f"keep{len(k)}")[0], budget
        )
        reduction = reduction_for(kept, all_units)
        # 2. unwrap blocks that survived
        unwrap: list[str] = []
        for addr in _unwrappable(source0, set(kept)):
            candidate = reduction_for(kept, all_units, unwrap=[*unwrap, addr])
            if run_trial(candidate, f"unwrap{addr}")[0]:
                unwrap.append(addr)
        reduction = reduction_for(kept, all_units, unwrap=unwrap)
        # 3. files nothing references
        reduced_source = XT.apply_reduction(source0, reduction)
        extra = sorted(set(files0) - {parent.script})
        unused = sorted(set(extra) - referenced_files(reduced_source, extra, parent.script))
        if unused and run_trial({**reduction, "drop_files": unused}, "drop-unreferenced-files")[0]:
            reduction = {**reduction, "drop_files": unused}
        # 4. reproducibility of the final result
        _, final_files = XT.build_reduced(parent, tuple(chain), manifest["seed"], reduction)
        confirmed = 1
        total = 1
        final_record = None
        for i in range(max(0, repeat) + 1):
            budget.spend()
            record = oracle(reduction, f"confirm{i}")
            final_record = final_record or record
            total += 1
            if accept(original_sig, signature(record, chain, rules))[0]:
                confirmed += 1
        result.update(
            status="minimized",
            reduction=reduction,
            files=final_files,
            after=measure(final_files, parent.script),
            reproducibility={"confirmed": confirmed - 1, "total": total - 1},
            final_record=final_record,
        )
        if result["reproducibility"]["confirmed"] != result["reproducibility"]["total"]:
            result["status"] = "minimization_failed"
            result["note"] = "the reduced script did not reproduce the failure every time"
    except BudgetExceeded as exc:
        result.update(status="minimization_failed", note=str(exc))
    return close()


def _unwrappable(source: str, kept: set[str]) -> list[str]:
    tree = ast.parse(source)
    out = []

    def walk(node, prefix):
        for fld in ("body", "orelse", "finalbody"):
            for i, child in enumerate(getattr(node, fld, None) or []):
                addr = f"{prefix}/{fld}{i}"
                if isinstance(child, ast.stmt):
                    if addr in kept and isinstance(child, XT._UNWRAPPABLE):
                        out.append(addr)
                    walk(child, addr)

    walk(tree, "")
    return out


# ---------------------------------------------------------------- CLI orchestration


def environment_identity() -> dict:
    facts = cg.environment()["facts"]
    return {
        "python": str(facts["python"]).split()[0],
        "platform": facts["platform"],
        "machine": facts["machine"],
        "key_distributions": facts["key_distributions"],
        "fingerprint": cg.environment()["fingerprint"],
    }


def commands(script: str, entry: str) -> dict:
    return {
        "native": f"cd <dir> && python {script}"
        if entry == "__main__"
        else f'cd <dir> && python -c "import {Path(script).stem} as m; m.{entry}()"',
        "tavotto": "python scripts/ci/compat_guardian.py minimize --replay <dir>/repro.json --out <scratch dir>",
    }


def write_outcome(
    result: Result,
    manifest: dict,
    parent: XT.Parent,
    out: Path,
    rules: list[dict],
    budget_spec: dict,
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    minimal = out / "minimal"
    for rel, data in result["files"].items():
        dest = cg.contained(minimal, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    chain = [s["name"] for s in manifest["chain"]]
    record = result.get("final_record")
    summary = ex.evidence_summary(record) if record else None
    attribution = None
    if record and record["judgment"]["differences"]:
        row = {
            "outcome": "difference_found",
            "chain": chain,
            "evidence": ex.evidence_summary(record),
        }
        attribution = TG.attribute(
            TG.explore_facts(row, record["judgment"]["differences"][0]), "explore", rules
        )
    doc = {
        "schema": SCHEMA,
        "status": result["status"],
        "note": result.get("note"),
        "candidate": {
            "candidate_id": manifest["candidate_id"],
            "parent": {"suite": manifest["parent"]["suite"], "case": manifest["parent"]["case"]},
            "chain": chain,
            "seed": manifest["seed"],
            "entry": manifest["entry"],
        },
        "reduction": result.get("reduction"),
        "script": parent.script,
        "files": {rel: cg.digest(d) for rel, d in sorted(result["files"].items())},
        "script_text": result["files"][parent.script].decode(),
        "commands": commands(parent.script, manifest["entry"]),
        "environment": environment_identity(),
        "signature": result.get("signature"),
        "native_behavior": summary and summary["native"],
        "tavotto_behavior": summary and summary["tavotto"],
        "reproducibility": result.get("reproducibility"),
        "before_after": {"before": result["before"], "after": result["after"]},
        "attribution": attribution,
        "budget": {**budget_spec, "runs": result.get("runs"), "seconds": result.get("seconds")},
        "trials": result["trials"],
        "trust": {
            "level": "parent",
            "basis": "deterministic subset (deletions/unwraps only) of a candidate re-derived from a committed parent",
        },
        "unverified": [
            "reproduction is for this interpreter, platform and Tavotto revision only",
            "a minimal script shows what triggers the finding, not that the root cause is confirmed",
            "not in the corpus and not a regression test until a maintainer adds it",
        ],
    }
    path = out / "repro.json"
    tmp = out / "repro.json.tmp"
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def find_candidate(report_path: Path, candidate_id: str) -> tuple[dict, dict]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    run_dir = report_path.resolve().parent.parent
    listing = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    # candidates.json lists everything the generator produced (also what --limit/--candidates
    # skipped); only the report says what was executed and found.
    rows = [r for r in report.get("candidates", []) if r.get("candidate_id") == candidate_id]
    if len(rows) != 1:
        raise cg.EvidenceError(f"candidate {candidate_id} was not observed in {report_path}")
    if rows[0].get("outcome") != "difference_found":
        raise cg.EvidenceError(
            f"candidate {candidate_id} is not a finding in {report_path} "
            f"(outcome {rows[0].get('outcome')!r}); only difference_found is minimized"
        )
    for manifest in listing["candidates"]:
        if manifest["candidate_id"] == candidate_id:
            return manifest, report
    raise cg.EvidenceError(f"candidate {candidate_id} is not in {run_dir / 'candidates.json'}")


def trial_timeout(per_trial: float, budget: Budget) -> float:
    """Per-trial timeout capped by the wall-clock budget left (floor avoids a 0 timeout)."""
    return min(per_trial, max(0.5, budget.remaining()))


def run(args) -> int:
    rules = TG.load_rules()
    out = Path(args.out).resolve()
    if out.is_relative_to(cg.ROOT):
        raise cg.EvidenceError("--out must be outside the source worktree")
    # minimize 与 replay 都会执行派生候选：执行 / 取证代码必须是 HEAD 那份（与 explore 同一道核对，
    # Codex #1136 r4312326669）
    dirty = ex.dirty_execution_code()
    if dirty:
        raise cg.EvidenceError(
            f"execution code under {ex.EXECUTION_CODE}/ differs from reviewed HEAD: {dirty}; "
            "commit it before minimizing or replaying"
        )
    if getattr(args, "replay", None):
        return replay(Path(args.replay), out, rules, args.timeout)
    if not args.report or not args.candidate:
        raise cg.EvidenceError("minimize needs --report and --candidate (or --replay)")
    initial = cg.source_state()
    manifest, _report = find_candidate(Path(args.report), args.candidate)
    parent = XT.load_parent(manifest["parent"]["suite"], manifest["parent"]["case"])
    # re-derive: the report only names the candidate, it cannot supply its code
    derived, _ = XT.build(parent, tuple(s["name"] for s in manifest["chain"]), manifest["seed"])
    if derived["candidate_id"] != manifest["candidate_id"]:
        raise cg.EvidenceError(
            "candidate does not follow from (parent, chain, seed) under this generator"
        )
    if derived["execution_plan"]["status"] != "executable":
        raise cg.EvidenceError("static-only candidates are never executed or minimized")
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)

    def oracle(reduction, tag):
        # never let one trial run past the total wall-clock budget (floor avoids a 0 timeout)
        timeout = trial_timeout(args.timeout, budget)
        return ex.run_one(derived, derived["seed"], work, timeout, reduction or None, tag)

    budget = Budget(args.max_runs, args.max_seconds)
    result = minimize_candidate(derived, parent, oracle, rules, budget, args.repeat)
    if cg.source_state() != initial:
        raise cg.EvidenceError("source content changed during minimize; no result published")
    path = write_outcome(
        result,
        derived,
        parent,
        out,
        rules,
        {"max_runs": args.max_runs, "max_seconds": args.max_seconds},
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    print(doc["script_text"])
    print(
        json.dumps(
            {
                "repro": str(path),
                "status": result["status"],
                "before": result["before"],
                "after": result["after"],
                "runs": result.get("runs"),
                "reproducibility": result.get("reproducibility"),
            }
        )
    )
    return cg.EXIT_PASSED if result["status"] == "minimized" else cg.EXIT_UNKNOWN


def replay(repro_path: Path, out: Path, rules: list[dict], timeout: float) -> int:
    """Re-run a recorded minimal reproduction and compare with its recorded signature."""
    doc = json.loads(repro_path.read_text(encoding="utf-8"))
    cand = doc["candidate"]
    parent = XT.load_parent(cand["parent"]["suite"], cand["parent"]["case"])
    derived, _ = XT.build(parent, tuple(cand["chain"]), cand["seed"])
    if derived["candidate_id"] != cand["candidate_id"]:
        raise cg.EvidenceError("recorded candidate does not follow from (parent, chain, seed)")
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    record = ex.run_one(derived, derived["seed"], work, timeout, doc["reduction"] or None, "replay")
    sig = signature(record, cand["chain"], rules)
    same = accept(doc["signature"], sig)
    print(json.dumps({"replay": str(repro_path), "reproduced": same[0], "reason": same[1]}))
    return cg.EXIT_PASSED if same[0] else cg.EXIT_UNKNOWN
