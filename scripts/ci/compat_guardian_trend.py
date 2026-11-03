"""Guardian trend summary: what was measured, on what, and what may and may not be compared.

Rules this module enforces, because the failure modes are easy to fall into:

* A rate over one case set is never compared with a rate over a different case set. Trends
  are computed on the *fixed set*: cases present in both runs with byte-identical fixtures.
  Added, removed and changed cases are reported separately as scope changes. Removing a hard
  case is a scope change, never an improvement.
* A different environment (platform, Python, key dependency versions) gives no regression
  verdict at all: the observed status changes are listed, the verdict is `incomparable`.
* Metrics that cannot be computed from the inputs say `not_computed` and why.
* Everything here is a rate over curated fixtures. It is not a user success rate, and the
  summary says so; the real-user rate is always `not_computed`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import compat_explore_shards as ES
import compat_guardian as cg
import compat_guardian_summary as SM

SCHEMA = "tavotto-guardian-trend/1"
GOOD = ("passed", "partial_coverage", "expected_limitation", "control_met", "not_reproduced")
POPULATION = "curated corpus fixtures (not real users)"


def classify(path: Path) -> tuple[str, dict]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("schema") == "tavotto-guardian-explore/1":
        return "explore", report
    if report.get("schema_version") == cg.SCHEMA_VERSION and "cases" in report:
        return "run", report
    raise cg.EvidenceError(f"{path}: not a run or explore report")


def _not_computed(reason: str) -> dict:
    return {"status": "not_computed", "reason": reason, "population": POPULATION}


def _ratio(num: int, den: int) -> dict:
    return {
        "status": "computed",
        "numerator": num,
        "denominator": den,
        "value": round(num / den, 4),
        "population": POPULATION,
    }


# ---------------------------------------------------------------- tables from reports


def case_table(runs: list[dict]) -> dict:
    table = {}
    for report in runs:
        for row in report["cases"]:
            stages = {n: c["status"] for n, c in row["stages"].items()}
            table[f"{report['suite']}/{row['id']}"] = {
                "suite": report["suite"],
                "case": row["id"],
                "status": row["status"],
                "fixture": cg.canonical(row["fixture"]["files"]),
                "declared": (row.get("expectation") or {}).get("declared_outcome"),
                # Digest of the declaration itself (outcome + reason + references): a changed
                # declaration rewrites the status without the product behaving differently.
                "declaration_sha256": cg.canonical(
                    {
                        k: v
                        for k, v in (row.get("expectation") or {}).items()
                        if k in ("declared_outcome", "reason", "references")
                    }
                ),
                "stages": stages,
                "native_status": row["native"]["status"],
                "native_figures": row["figures"]["native"],
                "tavotto_executions": row["executions"]["tavotto"],
            }
    return table


def corpus_of(runs: list[dict]) -> dict:
    out = {}
    for report in runs:
        out[report["suite"]] = {
            "corpus_sha256": report["corpus_sha256"],
            "expectations_sha256": report["expectations_sha256"],
            "n_cases": len(report["cases"]),
            "case_ids": sorted(r["id"] for r in report["cases"]),
            "commit": report["source"]["commit"],
            "dirty": report["source"]["dirty"],
        }
    return out


def environment_of(runs: list[dict], explores: list[dict]) -> dict:
    fingerprints, view = set(), {}
    for report in runs:
        for row in report["cases"]:
            env = row["environment_before"]
            if env.get("fingerprint"):
                fingerprints.add(env["fingerprint"])
                facts = env["facts"]
                view = view or {
                    "python": str(facts.get("python", "?")).split()[0],
                    "platform": facts.get("platform", "?"),
                    "machine": facts.get("machine", "?"),
                    "key_distributions": facts.get("key_distributions", {}),
                }
    for report in explores:
        src = report.get("source") or {}
        view = view or {}
        view.setdefault("explore_source_commit", src.get("commit"))
    return {**view, "fingerprints": sorted(fingerprints)}


# ---------------------------------------------------------------- metrics (brief section 12)


def metrics(runs: list[dict], explores: list[dict], families: list[dict]) -> dict:
    out: dict = {}
    by_suite = {r["suite"]: r for r in runs}

    def per_suite(compute):
        return {suite: compute(rep) for suite, rep in sorted(by_suite.items())}

    if not runs:
        for name in (
            "first_figure_success",
            "execution_attempts",
            "semantic_fidelity",
            "editable_export_success",
        ):
            out[name] = _not_computed("no run report was given")
    else:

        def first_figure(rep):
            eligible = [
                r
                for r in rep["cases"]
                if r["native"]["status"] == "passed" and (r["figures"]["native"] or 0) >= 1
            ]
            wins = [
                r
                for r in eligible
                if r["stages"]["capture"]["status"] == "passed"
                and r["stages"]["open"]["status"] == "passed"
            ]
            excluded = len(rep["cases"]) - len(eligible)
            if not eligible:
                return _not_computed("no case had a native reference that produced a figure")
            return {
                **_ratio(len(wins), len(eligible)),
                "excluded_cases": excluded,
                "excluded_reason": "native failed / unavailable or produced no figure (nothing to compare against)",
            }

        def attempts(rep):
            measured = [
                r["executions"]["tavotto"]
                for r in rep["cases"]
                if isinstance(r["executions"]["tavotto"], int)
            ]
            if not measured:
                return _not_computed("no case has an out-of-project execution counter")
            dist: dict[str, int] = {}
            for n in measured:
                dist[str(n)] = dist.get(str(n), 0) + 1
            return {
                "status": "computed",
                "total_executions": sum(measured),
                "cases_measured": len(measured),
                "cases_unmeasured": len(rep["cases"]) - len(measured),
                "mean": round(sum(measured) / len(measured), 3),
                "distribution": dict(sorted(dist.items())),
                "basis": "real script executions (audit counter), not API calls",
                "population": POPULATION,
            }

        def semantic(rep):
            passed = sum(1 for r in rep["cases"] if r["stages"]["semantic"]["status"] == "passed")
            failed = sum(1 for r in rep["cases"] if r["stages"]["semantic"]["status"] == "failed")
            if passed + failed == 0:
                return _not_computed("no case reached an evaluated semantic stage")
            return {
                **_ratio(passed, passed + failed),
                "oracle": "titles / roles / editable fields only; not scientific values",
            }

        def editable_export(rep):
            both = [
                r
                for r in rep["cases"]
                if all(r["stages"][s]["status"] in ("passed", "failed") for s in ("edit", "export"))
            ]
            if not both:
                return _not_computed(
                    "this adapter does not drive edit/replay/export (layers reported not_covered)"
                )
            wins = [
                r
                for r in both
                if all(r["stages"][s]["status"] == "passed" for s in ("edit", "export"))
            ]
            return _ratio(len(wins), len(both))

        out["first_figure_success"] = per_suite(first_figure)
        out["execution_attempts"] = per_suite(attempts)
        out["semantic_fidelity"] = per_suite(semantic)
        out["editable_export_success"] = per_suite(editable_export)
    if not explores:
        out["family_coverage"] = _not_computed("no explore report was given")
    else:
        defined, executed = set(), set()
        for rep in explores:
            defined |= set(rep["coverage"]["by_family"])
            for row in rep["candidates"]:
                if row["outcome"] not in ("static_only", "not_run"):
                    executed |= {
                        fam
                        for fam, info in rep["coverage"]["by_family"].items()
                        if any(step in info["transforms"] for step in row["chain"])
                    }
        out["family_coverage"] = {
            **_ratio(len(executed), len(defined)),
            "meaning": "transform families with at least one executed candidate / families defined",
            "failure_family_records": {
                "recorded": len(families),
                "in_corpus": sum(1 for f in families if f["in_corpus"]),
                "fixed": sum(1 for f in families if f["fix_pr"] is not None),
            },
        }
    out["real_user_success_rate"] = _not_computed(
        "CI has no real-user data; every rate above describes curated fixtures and must not be read as user success"
    )
    return out


# ---------------------------------------------------------------- uncovered scenarios


def axis_table(explores: list[dict]) -> dict:
    """`{axis: {candidates, executed, differences}}` over the explore candidates, sorted by name.

    Data only; the order in which a report *lists* axes is `compat_guardian_summary.AXIS_WEIGHTS`
    and lives in the renderer, so changing a weight can never change a number here.
    """
    table: dict[str, dict[str, int]] = {}
    for rep in explores:
        for row in rep["candidates"]:
            for axis in sorted({v.split(":", 1)[0] for v in row.get("axis_vector", [])}):
                cell = table.setdefault(axis, {"candidates": 0, "executed": 0, "differences": 0})
                cell["candidates"] += 1
                cell["executed"] += row["outcome"] not in ("static_only", "not_run")
                cell["differences"] += row["outcome"] == "difference_found"
    return dict(sorted(table.items()))


def uncovered(runs: list[dict], explores: list[dict], env: dict) -> list[str]:
    out = []
    counts: dict[str, int] = {}
    for rep in runs:
        for stage, by_status in rep["coverage"].items():
            if by_status.get("not_covered"):
                counts[stage] = counts.get(stage, 0) + by_status["not_covered"]
    if counts:
        out.append(
            "layers not driven by an adapter (not_covered): "
            + ", ".join(f"{k} x{v}" for k, v in sorted(counts.items()))
        )
    for rep in explores:
        cov = rep["coverage"]["parents"]
        empty = sorted(
            set(cov["corpus_by_manifest_category"]) - set(cov["used_by_manifest_category"])
        )
        if empty:
            out.append("corpus categories with no explore parent: " + ", ".join(empty))
        static = sum(1 for r in rep["candidates"] if r["outcome"] == "static_only")
        if static:
            out.append(
                f"{static} explore candidate(s) static-only (V2 parents or uncertain sources)"
            )
        idle = sorted(
            set(rep["coverage"]["by_family"])
            - {
                fam
                for fam, info in rep["coverage"]["by_family"].items()
                if any(
                    step in info["transforms"]
                    for row in rep["candidates"]
                    if row["outcome"] not in ("static_only", "not_run")
                    for step in row["chain"]
                )
            }
        )
        if idle:
            out.append("transform families with no executed candidate this run: " + ", ".join(idle))
    out.append("desktop (Native) route and real WebView2/WKWebView: not exercised")
    out.append(
        f"single platform: {env.get('platform', 'unknown')}; other OS / Python / matplotlib versions unverified"
    )
    return out


# ---------------------------------------------------------------- previous vs current


def compare(previous: dict | None, current: dict, required_suites: tuple[str, ...] = ()) -> dict:
    if not previous:
        return {
            "status": "no_previous",
            "reason": "no previous summary was provided: trend, added and removed cases are not computed",
        }
    # A suite that produced no report is a missing observation, not a removal.
    missing = {
        "current": sorted(set(required_suites) - set(current.get("corpus", {}))),
        "previous": sorted(set(required_suites) - set(previous.get("corpus", {}))),
    }
    if missing["current"] or missing["previous"]:
        return {
            "status": "not_computed",
            "reason": (
                "an expected suite has no report ("
                + ", ".join(f"{side}: {names}" for side, names in missing.items() if names)
                + "): absent cases are missing observations, so no added/removed/regression is computed"
            ),
            "missing_suites": missing,
        }
    prev_cases, cur_cases = previous["cases"], current["cases"]
    shared = sorted(set(prev_cases) & set(cur_cases))
    same_fixture = [k for k in shared if prev_cases[k]["fixture"] == cur_cases[k]["fixture"]]
    # A status that follows from a rewritten declaration is scope, not behaviour. A previous
    # summary without the digest cannot prove the declaration is unchanged, so it is excluded too.
    fixed = [
        k
        for k in same_fixture
        if prev_cases[k].get("declaration_sha256") == cur_cases[k]["declaration_sha256"]
    ]
    changed = [k for k in shared if k not in same_fixture]
    declaration_changed = [k for k in same_fixture if k not in fixed]
    added = sorted(set(cur_cases) - set(prev_cases))
    removed = sorted(set(prev_cases) - set(cur_cases))
    corpus_changed = {
        suite: previous["corpus"].get(suite, {}).get("corpus_sha256") != info["corpus_sha256"]
        for suite, info in current["corpus"].items()
    }
    prev_env, cur_env = previous.get("environment", {}), current["environment"]
    env_diff = sorted(
        k
        for k in ("python", "platform", "machine", "key_distributions")
        if prev_env.get(k) != cur_env.get(k)
    )
    comparable = not env_diff
    transitions = []
    for key in fixed:
        before, after = prev_cases[key]["status"], cur_cases[key]["status"]
        if before == after:
            continue
        kind = (
            "regression"
            if before in GOOD and after == "failed"
            else "improvement"
            if before == "failed" and after in GOOD
            # an endpoint without a conclusion (unknown, not_run, environment_unavailable,
            # invalid_fixture): the fixed set cannot be called regression-free
            else "inconclusive"
            if not {before, after} <= {*GOOD, "failed"}
            else "changed"
        )
        transitions.append({"case": key, "from": before, "to": after, "kind": kind})
    hard_removed = [
        k for k in removed if prev_cases[k]["status"] not in ("passed", "partial_coverage")
    ]
    verdict = (
        "incomparable"
        if not comparable
        else "inconclusive"
        if not shared
        else (
            "regression"
            if any(t["kind"] == "regression" for t in transitions)
            else "inconclusive"
            if any(t["kind"] == "inconclusive" for t in transitions)
            else "no_regression"
        )
    )
    return {
        "status": "compared",
        "fixed_set": {
            "size": len(fixed),
            "environment_comparable": comparable,
            "environment_differences": env_diff,
            "verdict": verdict,
            "note": (
                "no regression verdict: the environment differs, so status changes below are observations"
                if not comparable
                else "no case exists on both sides (e.g. an explore-only weekly report): nothing was compared"
                if not shared
                else "same case bytes, same environment"
            ),
            "transitions": transitions,
        },
        "scope_changes": {
            "corpus_version_changed": corpus_changed,
            "added": added,
            "removed": removed,
            "fixture_changed": changed,
            "declaration_changed": declaration_changed,
            "removed_non_passing": hard_removed,
            "denominator": {"previous": len(prev_cases), "current": len(cur_cases)},
            "note": "scope changes are not regressions and not improvements; removing a hard case never counts as one",
            "aggregate_rates_compared": False,
        },
    }


# ---------------------------------------------------------------- assemble + render


def build(
    run_paths: list[Path],
    explore_paths: list[Path],
    previous: dict | None,
    families: list[dict],
    required_suites: tuple[str, ...] = (),
    shards: dict | None = None,
) -> dict:
    runs = [classify(p)[1] for p in run_paths]
    explores = [classify(p)[1] for p in explore_paths]
    env = environment_of(runs, explores)
    # CG-09: the shards of one sweep (already checked by the caller) are measured as one report
    whole = [ES.merge_reports(explores)] if shards else explores
    summary = {
        "schema": SCHEMA,
        "sources": [
            {
                "kind": "run",
                "run_id": r["run_id"],
                "suite": r["suite"],
                "commit": r["source"]["commit"],
            }
            for r in runs
        ]
        + [
            {
                "kind": "explore",
                "run_id": e["run_id"],
                "seed": e["seed"],
                "commit": e["source"]["commit"],
            }
            for e in explores
        ],
        "corpus": corpus_of(runs),
        "sample": {
            "run_cases": {r["suite"]: len(r["cases"]) for r in runs},
            "explore_candidates_executed": sum(
                1
                for e in explores
                for c in e["candidates"]
                if c["outcome"] not in ("static_only", "not_run")
            ),
            "explore_candidates_total": sum(len(e["candidates"]) for e in explores),
            "denominator_note": "every attempted case is in the denominator; non-green cases are never dropped",
        },
        "environment": env,
        "metrics": metrics(runs, whole, families),
        "explore": [
            {
                "run_id": e["run_id"],
                "seed": e["seed"],
                "outcomes": e["outcome_counts"],
                "known_gaps": {k: v["found"] for k, v in e["known_gaps"].items()},
            }
            for e in explores
        ],
        "uncovered": uncovered(runs, whole, env),
        "explore_axes": axis_table(whole),
        "cases": case_table(runs),
        **({"explore_shards": shards} if shards else {}),
        "notes": [
            "All rates are over curated fixtures; they are not user success rates.",
            "Rates are never compared across different case sets or corpus versions; trends use the fixed set only.",
        ],
    }
    prior = (previous or {}).get("trend", previous)
    summary["comparison"] = compare(
        prior if prior and "cases" in prior else None, summary, tuple(required_suites)
    )
    summary["digest"] = hashlib.sha256(
        json.dumps({k: v for k, v in summary.items() if k != "digest"}, sort_keys=True).encode()
    ).hexdigest()
    return summary


def render_markdown(summary: dict) -> str:
    lines = ["## Compatibility Guardian trend", ""]
    env = summary["environment"]
    lines += [
        f"- environment: python {env.get('python', '?')}, {env.get('platform', '?')}, {env.get('machine', '?')}; "
        f"{len(env['fingerprints'])} fingerprint(s)",
        "- corpus: "
        + (
            "; ".join(
                f"{s} `{c['corpus_sha256'][:12]}` n={c['n_cases']}"
                for s, c in summary["corpus"].items()
            )
            or "no run report"
        ),
        f"- sample: run cases {summary['sample']['run_cases'] or 'none'}; explore executed "
        f"{summary['sample']['explore_candidates_executed']}/{summary['sample']['explore_candidates_total']}",
        "",
        "| metric | suite | value | n / d | note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, block in summary["metrics"].items():
        entries = (
            block.items()
            if name not in ("family_coverage", "real_user_success_rate") and "status" not in block
            else [("-", block)]
        )
        for suite, m in entries:
            if m["status"] == "not_computed":
                lines.append(f"| {name} | {suite} | not_computed | - | {m['reason']} |")
            elif "value" in m:
                lines.append(
                    f"| {name} | {suite} | {m['value']} | {m['numerator']}/{m['denominator']} | {m['population']} |"
                )
            else:
                lines.append(
                    f"| {name} | {suite} | mean {m['mean']} | {m['total_executions']} runs / {m['cases_measured']} cases | {m['basis']} |"
                )
    cmp_ = summary["comparison"]
    lines += ["", "### Against the previous run"]
    if cmp_["status"] in ("no_previous", "not_computed"):
        lines.append(f"not computed: {cmp_['reason']}")
    else:
        fixed, scope = cmp_["fixed_set"], cmp_["scope_changes"]
        lines += [
            f"**Fixed set** ({fixed['size']} case(s) with identical fixtures): verdict **{fixed['verdict']}**. {fixed['note']}",
        ]
        lines += [
            f"- {t['kind']}: {t['case']} {t['from']} -> {t['to']}" for t in fixed["transitions"]
        ] or ["- no status changes"]
        lines += [
            "",
            f"**Scope changes** (reported separately, never as regression or improvement): "
            f"added {len(scope['added'])}, removed {len(scope['removed'])}, fixtures changed {len(scope['fixture_changed'])}, "
            f"declarations changed {len(scope['declaration_changed'])}, "
            f"denominator {scope['denominator']['previous']} -> {scope['denominator']['current']}",
        ]
        if scope["removed_non_passing"]:
            lines.append(
                "- removed cases that were not passing: "
                + ", ".join(scope["removed_non_passing"])
                + " (not an improvement)"
            )
        if scope["declaration_changed"]:
            lines.append(
                "- declaration changed (status follows from a rewritten expectation, not from "
                "behaviour; not an improvement or regression): "
                + ", ".join(scope["declaration_changed"])
            )
        if any(scope["corpus_version_changed"].values()):
            lines.append(
                "- corpus version changed for: "
                + ", ".join(k for k, v in scope["corpus_version_changed"].items() if v)
                + "; aggregate rates are not compared"
            )
    shards = summary.get("explore_shards")
    if shards:
        lines += [
            "",
            f"### Weekly sweep: {shards['label']}",
            f"- seed {shards['seed']}, HEAD `{shards['commit'][:12]}`; "
            f"{shards['candidates_reported']} candidates reported of "
            f"{shards['candidates_total']} in the pool"
            + (
                ""
                if shards["complete"]
                else f" (missing shards {shards['missing']}: NOT the whole pool)"
            ),
            "- outcomes: " + ", ".join(f"{k}={v}" for k, v in shards["outcome_counts"].items()),
            *(
                [
                    f"- **time budget exhausted in shard(s) {shards['budget_exhausted_shards']}: "
                    f"{shards['not_run']} candidate(s) NOT RUN (partial coverage, not a pass)**"
                ]
                if shards.get("not_run")
                else []
            ),
            "- corpus pass rate is not a user first-success rate (语料通过率 ≠ 用户首次成功率): "
            "these are derived candidates of already-passing corpus shapes",
        ]
    axes = summary.get("explore_axes")
    if axes:
        # order only: heavier axes first (human-maintained weights), then by name; numbers are untouched
        ordered = sorted(axes, key=lambda a: (-SM.axis_weight(a), a))
        lines += [
            "",
            "### Explore by axis (listed by weight, not by severity)",
            "| axis | candidates | executed | differences |",
            "| --- | --- | --- | --- |",
        ] + [
            f"| {a} | {axes[a]['candidates']} | {axes[a]['executed']} | {axes[a]['differences']} |"
            for a in ordered
        ]
    lines += ["", "### Not covered"] + [f"- {item}" for item in summary["uncovered"]]
    lines += ["", *[f"_{n}_" for n in summary["notes"]]]
    return "\n".join(lines) + "\n"
