"""Guardian `summarize` and `promote`.

`summarize` aggregates already-produced reports without re-running anything and
without inventing a combined rate: reports only aggregate together when they
share suite, corpus digest and environment; otherwise each group is reported on
its own and the groups are marked incomparable.

`promote` is proposal-only. It emits a candidate record for a human to turn into
a reviewed PR; it never writes the manifest, the baseline, or anything under
tests/compat/.
"""

from __future__ import annotations

import json
from pathlib import Path

import compat_guardian as cg

SUMMARY_SCHEMA = "tavotto-guardian-summary/1"
PROMOTION_SCHEMA = "tavotto-guardian-promotion-candidate/1"
#: Paths promote must never write to, resolved relative to the repository root.
PROTECTED = ("tests/compat", "tests/compat_guardian", "tests/fixtures", "tests/support")


def load_report(path: Path) -> dict:
    """Structural validation only: schema + internal consistency + per-row proof invariants.

    Evidence files and the checkout the report was produced on are not available
    to an aggregator, so claims about *this* checkout stay with `validate`.
    """
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    cg.schema_check(report, json.loads(cg.SCHEMA_PATH.read_text(encoding="utf-8")))
    if report["schema_version"] != cg.SCHEMA_VERSION:
        raise cg.EvidenceError(f"{path}: unsupported schema_version")
    if report["verdict"] != cg.verdict(report["cases"]):
        raise cg.EvidenceError(f"{path}: aggregate verdict does not follow from its cases")
    if report["coverage"] != cg.coverage_table(report["cases"]):
        raise cg.EvidenceError(f"{path}: coverage table does not follow from its cases")
    for row in report["cases"]:
        expectation = row.get("expectation") or {}
        # `expectation` is report data, i.e. untrusted here. A declared mismatch can only waive
        # the proof of a row that is itself `failed` and whose block is self-consistent (declared
        # != observed); a green status is never waived, whatever the block claims.
        mismatch = (
            row.get("status") == "failed"
            and expectation.get("result") == "mismatch"
            and expectation.get("declared_outcome") != expectation.get("observed_outcome")
        )
        try:
            cg.check_row_proof(row, mismatch)
        except (cg.EvidenceError, KeyError, TypeError) as exc:
            # A row its own facts cannot support must not be counted in any rate.
            raise cg.EvidenceError(
                f"{path}: case {row.get('id')} fails its proof invariants: {exc}"
            ) from exc
    return report


def _env_key(report: dict) -> list[str]:
    """Distinct environment fingerprints of the rows that actually executed."""
    return sorted(
        {
            row["environment_before"]["fingerprint"]
            for row in report["cases"]
            if row["environment_before"].get("fingerprint")
        }
    )


def _env_view(report: dict) -> dict:
    for row in report["cases"]:
        facts = row["environment_before"].get("facts")
        if facts:
            return {
                "python": str(facts.get("python", "?")).split()[0],
                "platform": facts.get("platform", "?"),
                "machine": facts.get("machine", "?"),
                "key_distributions": facts.get("key_distributions", {}),
            }
    return {}


def summarize(reports: list[dict]) -> dict:
    if not reports:
        raise cg.EvidenceError("nothing to summarize")
    groups: dict[tuple, dict] = {}
    for report in reports:
        key = (report["suite"], report["corpus_sha256"], tuple(_env_key(report)))
        group = groups.setdefault(
            key,
            {
                "suite": report["suite"],
                "corpus_sha256": report["corpus_sha256"],
                "env_fingerprints": list(key[2]),
                "env": _env_view(report),
                "runs": [],
                "cases": {},
                "dirty_runs": 0,
            },
        )
        group["runs"].append(
            {
                "run_id": report["run_id"],
                "commit": report["source"]["commit"],
                "dirty": report["source"]["dirty"],
                "verdict": report["verdict"],
            }
        )
        group["dirty_runs"] += bool(report["source"]["dirty"])
        for row in report["cases"]:
            group["cases"].setdefault(row["id"], []).append(row)
    out_groups = []
    for group in groups.values():
        statuses: dict[str, int] = {}
        uncovered: dict[str, int] = {}
        non_comparable = 0
        attempts = 0
        for rows in group["cases"].values():
            for row in rows:
                attempts += 1
                statuses[row["status"]] = statuses.get(row["status"], 0) + 1
                non_comparable += not row["comparable"]
                for name, cell in row["stages"].items():
                    if cell["status"] == "not_covered":
                        uncovered[name] = uncovered.get(name, 0) + 1
        out_groups.append(
            {
                "suite": group["suite"],
                "corpus_sha256": group["corpus_sha256"],
                "env": group["env"],
                "env_fingerprints": group["env_fingerprints"],
                "runs": group["runs"],
                "distinct_cases": len(group["cases"]),
                # Denominator = every case attempt; non-green attempts are never dropped.
                "n": attempts,
                "status_counts": dict(sorted(statuses.items())),
                "passed": f"{statuses.get('passed', 0)}/{attempts}",
                "controls_met": statuses.get("control_met", 0),
                "not_covered_stage_counts": dict(sorted(uncovered.items())),
                "not_comparable_attempts": non_comparable,
                "dirty_runs": group["dirty_runs"],
            }
        )
    reasons = []
    if len({(g["suite"]) for g in out_groups}) > 1:
        reasons.append("different suites")
    if len({g["corpus_sha256"] for g in out_groups if g["suite"] == out_groups[0]["suite"]}) > 1:
        reasons.append("corpus version differs between groups")
    if len({tuple(g["env_fingerprints"]) for g in out_groups}) > 1:
        reasons.append("environment differs between groups")
    return {
        "schema": SUMMARY_SCHEMA,
        "groups": out_groups,
        "incomparable": bool(reasons),
        "incomparable_reasons": reasons,
        "note": "Rates are per group only. Corpus pass rate is not a user success rate. "
        "partial_coverage is not a pass; not_covered layers are listed, never counted green.",
    }


def render_markdown(summary: dict) -> str:
    """Short, public-log-safe text: counts and versions only, no package list, no stderr."""
    lines = ["## Compatibility Guardian summary", ""]
    if summary["incomparable"]:
        lines += [
            "**INCOMPARABLE groups: no combined rate is given** ("
            + "; ".join(summary["incomparable_reasons"])
            + ")",
            "",
        ]
    for group in summary["groups"]:
        env = group["env"]
        lines += [
            f"### {group['suite']} / corpus `{group['corpus_sha256'][:12]}`",
            f"- passed {group['passed']} (n = {group['n']} case attempts over {len(group['runs'])} run(s))",
            "- status: " + ", ".join(f"{k}={v}" for k, v in group["status_counts"].items()),
            f"- env: python {env.get('python', '?')}, {env.get('platform', '?')}, {env.get('machine', '?')}",
        ]
        if group["controls_met"]:
            lines.append(
                f"- declared controls met: {group['controls_met']} (not counted as passed, not failures)"
            )
        if group["not_covered_stage_counts"]:
            lines.append(
                "- layers not covered: "
                + ", ".join(f"{k} (x{v})" for k, v in group["not_covered_stage_counts"].items())
            )
        if group["not_comparable_attempts"]:
            lines.append(f"- not comparable attempts: {group['not_comparable_attempts']}")
        if group["dirty_runs"]:
            lines.append(f"- dirty-tree runs: {group['dirty_runs']}")
        lines.append("")
    lines.append(summary["note"])
    return "\n".join(lines) + "\n"


def case_markdown(report: dict) -> str:
    """Per-case table for one run: id, status, failure category. Nothing raw."""
    lines = [
        f"### Guardian {report['suite']} run `{report['run_id'][:12]}` @ `{report['source']['commit'][:12]}`",
        f"verdict: **{report['verdict']}**",
        "",
        "| case | status | failure category | native | tavotto | execs (n/t) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["cases"]:
        category = row["failure_category"]["category"] or "-"
        figures = row["figures"]
        execs = row["executions"]
        lines.append(
            f"| {row['id']} | {row['status']} | {category} | {figures['native']} figs "
            f"| {figures['tavotto_valid']} valid | {execs['native']}/{execs['tavotto']} |"
        )
    controls = [r for r in report["cases"] if r.get("expectation")]
    if controls:
        lines += ["", "Declared expectations (control_met is not a pass):"]
        for row in controls:
            exp = row["expectation"]
            lines.append(
                f"- {row['id']}: declared {exp['declared_outcome']}, observed "
                f"{exp['observed_outcome']} -> {exp['result']}"
                + (f" ({exp['diff']})" if exp["diff"] else "")
            )
    uncovered = sorted(
        {n for r in report["cases"] for n, c in r["stages"].items() if c["status"] == "not_covered"}
    )
    if uncovered:
        lines += ["", "Layers not covered by this adapter (not a pass): " + ", ".join(uncovered)]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- promote
def promote(report: dict, case_id: str, out: Path) -> dict:
    """Write a candidate record (proposal only). Refuses protected destinations."""
    destination = Path(out).resolve()
    for protected in PROTECTED:
        base = (cg.ROOT / protected).resolve()
        if destination == base or destination.is_relative_to(base):
            raise cg.EvidenceError(f"promote never writes under {protected}/: {destination}")
    if destination.is_relative_to(cg.ROOT.resolve()) and destination.name in (
        "manifest.json",
        "baseline.json",
    ):
        raise cg.EvidenceError("promote never writes a manifest or baseline")
    rows = [r for r in report["cases"] if r["id"] == case_id]
    if len(rows) != 1:
        raise cg.EvidenceError(f"case {case_id!r} not present exactly once in the report")
    row = rows[0]
    reasons = []
    if report["source"]["dirty"]:
        reasons.append("report was produced on a dirty tree")
    if row["status"] not in ("passed", "failed"):
        reasons.append(f"status {row['status']} is not a decided outcome")
    if not row["comparable"]:
        reasons.append("run was not comparable (environment/launch evidence)")
    if row["native"]["status"] != "passed":
        reasons.append("native reference did not pass")
    candidate = {
        "schema": PROMOTION_SCHEMA,
        "proposal_only": True,
        "applied": False,
        "case_id": case_id,
        "suite": report["suite"],
        "source_commit": report["source"]["commit"],
        "run_id": report["run_id"],
        "corpus_sha256": report["corpus_sha256"],
        "fixture": row["fixture"],
        "outcome": row["status"],
        "failure_category": row["failure_category"],
        "eligible_for_review": not reasons,
        "ineligible_reasons": reasons,
        "next_step": "A maintainer opens a normal PR that adds or updates the corpus entry and "
        "its expectation; this record changes nothing by itself.",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_text(json.dumps(candidate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(destination)
    return candidate
