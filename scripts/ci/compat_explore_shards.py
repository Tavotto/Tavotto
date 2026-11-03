"""CG-09 weekly sweep: slicing an explore run into shards, and merging shard reports back.

Two jobs, both pure:

* `slice_pool` cuts the deduplicated candidate pool into N disjoint slices by a deterministic
  key (sorted candidate_id, index % N). Same seed gives the same pool, so the N slices always
  union back to the unsharded pool, are pairwise disjoint, and differ in size by at most one.
* `check_set` is the gate in front of every consumer (`summarize`, `triage`) that is handed the
  explore reports of one weekly sweep. It refuses a set it cannot prove belongs together: a
  missing or duplicated shard, a different seed / HEAD / generator / filter, an unsharded report
  mixed in. It never merges silently; an incomplete set is only accepted with an explicit
  `allow_partial` and is then labelled "M of N shards completed" everywhere it is shown.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter

import compat_guardian as cg

MAX_SHARDS = 16
_SPEC = re.compile(r"^([1-9][0-9]*)/([1-9][0-9]*)$")


def parse_shard(text: str) -> tuple[int, int]:
    """argparse type for `--shard K/N` (1 <= K <= N <= 16); anything else is a usage error."""
    match = _SPEC.fullmatch(text or "")
    if not match:
        raise argparse.ArgumentTypeError(f"--shard must look like K/N (e.g. 2/4), got {text!r}")
    k, n = int(match.group(1)), int(match.group(2))
    if n > MAX_SHARDS:
        raise argparse.ArgumentTypeError(f"--shard N must be at most {MAX_SHARDS}, got {n}")
    if k > n:
        raise argparse.ArgumentTypeError(f"--shard K must be between 1 and N, got {k}/{n}")
    return k, n


def slice_pool(pool: list[dict], k: int, n: int) -> list[dict]:
    """Shard `k` of `n` (1-based) of `pool`, in the pool's own order."""
    if not (1 <= k <= n <= MAX_SHARDS):
        raise cg.EvidenceError(f"invalid shard {k}/{n}")
    ids = [c["candidate_id"] for c in pool]
    if len(set(ids)) != len(ids):
        raise cg.EvidenceError("candidate pool has duplicate ids; refusing to shard it")
    mine = {cid for i, cid in enumerate(sorted(ids)) if i % n == k - 1}
    return [c for c in pool if c["candidate_id"] in mine]


def shard_block(k: int, n: int, in_shard: int, total: int, filters: dict) -> dict:
    return {
        "k": k,
        "n": n,
        "candidates_in_shard": in_shard,
        "candidates_total": total,
        "filters": filters,
    }


def _same(reports: list[dict], what: str, get) -> None:
    values = {repr(get(r)) for r in reports}
    if len(values) > 1:
        raise cg.EvidenceError(f"explore shards disagree on {what}: {sorted(values)}")


def check_set(reports: list[dict], allow_partial: bool = False) -> dict | None:
    """Validate the explore reports of one sweep. None when none of them is sharded.

    Returns the merged facts (counts over the whole set, how many shards completed).
    """
    sharded = [r for r in reports if r.get("shard")]
    if not sharded:
        return None
    if len(sharded) != len(reports):
        raise cg.EvidenceError("sharded and unsharded explore reports cannot be merged")
    n_values = {r["shard"]["n"] for r in sharded}
    if len(n_values) > 1:
        raise cg.EvidenceError(f"explore shards disagree on the shard count: {sorted(n_values)}")
    n = n_values.pop()
    _same(sharded, "seed", lambda r: r["seed"])
    _same(sharded, "HEAD", lambda r: r["source"]["commit"])
    _same(sharded, "generator digest", lambda r: r["generator"])
    _same(sharded, "candidate coverage", lambda r: r["coverage"])
    _same(sharded, "selection filters", lambda r: r["shard"]["filters"])
    _same(sharded, "candidates_total", lambda r: r["shard"]["candidates_total"])
    numbers = Counter(r["shard"]["k"] for r in sharded)
    duplicated = sorted(k for k, c in numbers.items() if c > 1)
    if duplicated:
        raise cg.EvidenceError(f"duplicate explore shard(s): {duplicated}")
    outside = sorted(k for k in numbers if not 1 <= k <= n)
    if outside:
        raise cg.EvidenceError(f"explore shard number(s) outside 1..{n}: {outside}")
    missing = [k for k in range(1, n + 1) if k not in numbers]
    if missing and not allow_partial:
        raise cg.EvidenceError(
            f"explore shards incomplete: {len(numbers)} of {n} completed, missing {missing}"
        )
    seen: dict[str, int] = {}
    for report in sharded:
        k = report["shard"]["k"]
        if len(report["candidates"]) > report["shard"]["candidates_in_shard"]:
            raise cg.EvidenceError(f"shard {k} reports more candidates than its slice holds")
        for row in report["candidates"]:
            if row["candidate_id"] in seen:
                raise cg.EvidenceError(
                    f"candidate {row['candidate_id']} appears in shards {seen[row['candidate_id']]} and {k}"
                )
            seen[row["candidate_id"]] = k
    total = sharded[0]["shard"]["candidates_total"]
    in_shards = sum(r["shard"]["candidates_in_shard"] for r in sharded)
    if not missing and in_shards != total:
        raise cg.EvidenceError(
            f"shard slices add up to {in_shards} candidates but the pool has {total}"
        )
    outcomes: Counter = Counter()
    for report in sharded:
        outcomes.update(report["outcome_counts"])
    # time budget: what the report says must match its rows, so a spent budget cannot hide
    exhausted = []
    for report in sharded:
        k = report["shard"]["k"]
        rows_not_run = sum(1 for r in report["candidates"] if r["outcome"] == "not_run")
        budget = report.get("time_budget")
        if rows_not_run and not budget:
            raise cg.EvidenceError(f"shard {k} has not_run candidates but no time_budget record")
        if budget and (
            budget["not_run"] != rows_not_run or budget["exhausted"] != bool(rows_not_run)
        ):
            raise cg.EvidenceError(f"shard {k} time_budget disagrees with its candidate rows")
        if rows_not_run:
            exhausted.append(k)
    not_run = sum(1 for r in sharded for row in r["candidates"] if row["outcome"] == "not_run")
    return {
        "n": n,
        "completed": sorted(numbers),
        "missing": missing,
        "complete": not missing,
        "seed": sharded[0]["seed"],
        "commit": sharded[0]["source"]["commit"],
        "candidates_total": total,
        "candidates_in_completed_shards": in_shards,
        "candidates_reported": len(seen),
        "outcome_counts": dict(sorted(outcomes.items())),
        "label": f"{len(numbers)} of {n} shards completed",
        "not_run": not_run,
        "budget_exhausted_shards": sorted(exhausted),
        "fully_executed": not missing and not_run == 0,
    }


def merge_reports(reports: list[dict]) -> dict:
    """One virtual explore report for a (checked) set of shards, so coverage and metrics are
    computed over the whole sweep instead of once per shard. Call `check_set` first."""
    first = reports[0]
    gaps: dict[str, dict] = {}
    for report in reports:
        for gap, info in report["known_gaps"].items():
            merged = gaps.setdefault(gap, {**info, "found": False, "candidates": []})
            merged["found"] = merged["found"] or info["found"]
            merged["candidates"] = merged["candidates"] + list(info["candidates"])
    outcomes: Counter = Counter()
    for report in reports:
        outcomes.update(report["outcome_counts"])
    return {
        **{k: v for k, v in first.items() if k != "shard"},
        "run_id": "+".join(
            r["run_id"][:12] for r in sorted(reports, key=lambda r: r["shard"]["k"])
        ),
        "candidates": [row for r in reports for row in r["candidates"]],
        "known_gaps": gaps,
        "outcome_counts": dict(sorted(outcomes.items())),
    }
