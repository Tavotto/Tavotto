"""CG-03 `triage`: attribute findings to root-cause families and cluster them.

Attribution is a data table (tests/compat_guardian/triage_rules.json): ordered rules,
first match wins, each rule names the family, stage, confidence, likely modules and the
next verification action. A finding no rule can prove is `unknown`; there is no scoring and
no default family. Clustering never looks at traceback text: it keys on stage, family,
stable code, execution policy, dependency source, the reproducible behavior signature and a
normalized call-site signature taken from the candidate's AST.

Triage reads reports; it runs nothing, installs nothing, writes no baseline or manifest.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import compat_explore_shards as ES
import compat_guardian as cg
import compat_guardian_summary as GS
import compat_guardian_taxonomy as TX

SCHEMA = "tavotto-guardian-triage/1"
RULES_PATH = cg.ROOT / "tests/compat_guardian/triage_rules.json"
RULES_SCHEMA_PATH = cg.ROOT / "tests/compat_guardian/triage_rules.schema.json"
FAMILIES_DIR = cg.ROOT / "tests/compat_guardian/families"
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
#: Facts a rule may reference. A typo in a rule is a load error, not a silent non-match.
FACT_FIELDS = {
    "explore": {
        "kind",
        "outcome",
        "tavotto_error",
        "tavotto_error_message",
        "native_ok",
        "native_figure_count",
        "native_figures_shown",
        "native_savefig_calls",
        "tavotto_figure_count",
        "chain",
    },
    "row": {
        "status",
        "native_status",
        "failed_stage",
        "problem_codes",
        "error_type",
        "native_figures",
        "tavotto_captured",
        "captured_lt_native",
        "raw_runner_error",
        "raw_adapter_timeout",
        "raw_missing_prerequisites",
    },
}


# ---------------------------------------------------------------- rules


def load_rules(path: Path | None = None) -> list[dict]:
    document = json.loads(Path(path or RULES_PATH).read_text(encoding="utf-8"))
    cg.schema_check(document, json.loads(RULES_SCHEMA_PATH.read_text(encoding="utf-8")))
    seen = set()
    for rule in document["rules"]:
        if rule["id"] in seen:
            raise cg.EvidenceError(f"duplicate triage rule id: {rule['id']}")
        seen.add(rule["id"])
        known = FACT_FIELDS[rule["source"]]
        for cond in rule["when"]:
            if cond["field"] not in known:
                raise cg.EvidenceError(f"{rule['id']}: unknown fact {cond['field']!r}")
            needs_value = cond["op"] in ("eq", "ne", "in", "gt", "lt", "contains")
            if needs_value != ("value" in cond):
                raise cg.EvidenceError(f"{rule['id']}: operator {cond['op']} and value disagree")
        for module in rule["modules"]:
            if (
                cg.contained(cg.ROOT, module) != cg.ROOT / module
                or not (cg.ROOT / module).is_file()
            ):
                raise cg.EvidenceError(f"{rule['id']}: module {module} does not exist")
    return document["rules"]


def _holds(cond: dict, facts: dict) -> bool:
    value, op = facts.get(cond["field"]), cond["op"]
    want = cond.get("value")
    if op == "present":
        return value is not None
    if op == "absent":
        return value is None
    if value is None:
        return False  # an absent fact never satisfies a positive condition
    if op == "eq":
        return value == want
    if op == "ne":
        return value != want
    if op == "in":
        return value in want
    if op == "gt":
        return isinstance(value, (int, float)) and value > want
    if op == "lt":
        return isinstance(value, (int, float)) and value < want
    if op == "contains":
        return want in value
    if op == "true":
        return value is True
    return value is False


def attribute(facts: dict, source: str, rules: list[dict]) -> dict:
    for rule in rules:
        if rule["source"] == source and all(_holds(c, facts) for c in rule["when"]):
            stage = rule["stage"]
            if stage == "$failed_stage":
                stage = facts.get("failed_stage") or "execute"
            return {
                "family": rule["family"],
                "stage": stage,
                "confidence": rule["confidence"],
                "likely_modules": rule["modules"],
                "next_action": rule["next_action"],
                "rule_id": rule["id"],
                "basis": rule["why"],
            }
    return {
        "family": "unknown",
        "stage": facts.get("failed_stage") or "unknown",
        "confidence": "low",
        "likely_modules": [],
        "next_action": "No rule proves a root cause. Collect native and Tavotto evidence before attributing.",
        "rule_id": None,
        "basis": "no triage rule matched; unproven means unknown",
    }


# ---------------------------------------------------------------- facts


def explore_facts(row: dict, diff: dict | None) -> dict:
    ev = row.get("evidence") or {}
    nat, tav = ev.get("native") or {}, ev.get("tavotto") or {}
    error = tav.get("error") or {}
    code = error.get("code") or ((diff or {}).get("evidence") or {}).get("tavotto_error")
    return {
        "kind": (diff or {}).get("kind"),
        "outcome": row["outcome"],
        "tavotto_error": code,
        "tavotto_error_message": error.get("message") or "",
        "native_ok": True if nat.get("exit_status") == 0 else None,
        "native_figure_count": nat.get("figure_count"),
        "native_figures_shown": nat.get("figures_shown"),
        "native_savefig_calls": nat.get("savefig_calls"),
        "tavotto_figure_count": len(tav.get("stems") or {}) if tav else None,
        "chain": row["chain"],
    }


def row_facts(row: dict) -> dict:
    failed = next((n for n, c in row["stages"].items() if c["status"] == "failed"), None)
    raw = row.get("raw") or {}
    observed = raw.get("observed") or {}
    nat, cap = row["figures"]["native"], row["figures"]["tavotto_captured"]
    return {
        "status": row["status"],
        "native_status": row["native"]["status"],
        "failed_stage": failed,
        "problem_codes": observed.get("problem_codes") or [],
        "error_type": observed.get("error_type"),
        "native_figures": nat,
        "tavotto_captured": cap,
        "captured_lt_native": isinstance(nat, int) and isinstance(cap, int) and cap < nat,
        "raw_runner_error": "runner_error" in raw,
        "raw_adapter_timeout": "adapter_timeout" in raw,
        "raw_missing_prerequisites": "missing_prerequisites" in raw or "precondition_unmet" in raw,
    }


# ---------------------------------------------------------------- call-site signature

_EVENTS = {
    "show": "show",
    "close": "close",
    "savefig": "savefig",
    "figure": "figure",
    "subplots": "figure",
}


def call_site_signature(source: str) -> list[str]:
    """Normalized figure-lifecycle events in program order, loop-aware, alias-independent."""
    tree = ast.parse(source)
    events: list[str] = []

    def visit(node, in_loop):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                name = None
                if isinstance(child.func, ast.Attribute):
                    name = child.func.attr
                elif isinstance(child.func, ast.Name):
                    name = child.func.id
                if name in _EVENTS:
                    tag = ("loop:" if in_loop else "") + _EVENTS[name]
                    if not events or events[-1] != tag:
                        events.append(tag)
            visit(child, in_loop or isinstance(child, (ast.For, ast.While)))

    visit(tree, False)
    return events


# ---------------------------------------------------------------- families on disk


def load_families() -> list[dict]:
    if not FAMILIES_DIR.is_dir():
        return []
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(FAMILIES_DIR.glob("*.json"))
        if not p.name.endswith(".schema.json")
    ]


def known_family(signature: dict, families: list[dict]) -> str | None:
    for fam in families:
        want = fam["signature"]
        same_shape = (
            want["behavior_kind"] == signature["behavior_kind"]
            and want["call_site"] == signature["call_site"]
        )
        # `diff:<kind>` is the stable code of a finding with no Tavotto error. A recorded
        # `tavotto_error: null` means exactly "no error", not "any error": a candidate that also
        # carries a new error code is a different cluster identity and must stay NEW.
        stable = signature["stable_code"]
        observed_error = None if stable.startswith("diff:") else stable
        same_code = want["tavotto_error"] == observed_error
        if same_shape and same_code:
            return fam["id"]
    return None


# ---------------------------------------------------------------- findings from reports


def _explore_findings(report: dict, report_path: Path):
    run_dir = report_path.parent.parent
    scripts = {}
    listing = run_dir / "candidates.json"
    if listing.is_file():
        for manifest in json.loads(listing.read_text(encoding="utf-8"))["candidates"]:
            scripts[manifest["candidate_id"]] = manifest["parent"]["script"]
    for row in report["candidates"]:
        items = row["differences"] or (
            [None] if row["outcome"] not in ("no_difference", "static_only", "not_run") else []
        )
        for diff in items:
            source = None
            script = scripts.get(row["candidate_id"])
            if script:
                path = run_dir / "candidates" / row["candidate_id"] / "files" / script
                if path.is_file():
                    source = path.read_text(encoding="utf-8")
            yield {
                "id": f"{report['run_id'][:12]}:{row['candidate_id']}"
                + (f":{diff['kind']}" if diff else ""),
                "kind": "explore",
                "ref": row["candidate_id"],
                "row": row,
                "diff": diff,
                "source": source,
            }


def _guardian_findings(report: dict):
    for row in report["cases"]:
        if row["status"] in (
            "passed",
            "partial_coverage",
            "expected_limitation",
            "control_met",
            "not_reproduced",
        ):
            continue
        yield {
            "id": f"{report['run_id'][:12]}:{row['id']}",
            "kind": "row",
            "ref": row["id"],
            "row": row,
            "diff": None,
            "source": None,
        }


def _diff_summary(finding: dict) -> dict:
    row, diff = finding["row"], finding["diff"]
    if finding["kind"] == "explore":
        ev = row.get("evidence") or {}
        return {
            "summary": diff["summary"] if diff else row.get("note") or row["outcome"],
            "native": ev.get("native"),
            "tavotto": ev.get("tavotto"),
            "difference": (diff or {}).get("evidence"),
        }
    return {
        "summary": f"{row['status']} at stage {next((n for n, c in row['stages'].items() if c['status'] == 'failed'), 'n/a')}",
        "native": {"status": row["native"]["status"], "figures": row["figures"]["native"]},
        "tavotto": {
            "captured": row["figures"]["tavotto_captured"],
            "valid": row["figures"]["tavotto_valid"],
        },
        "difference": None,
    }


def behavior_signature(finding: dict, attribution: dict) -> dict:
    """What actually differs, independent of parent script, tracebacks and extra transforms."""
    row, diff = finding["row"], finding["diff"]
    if finding["kind"] == "explore":
        facts = explore_facts(row, diff)
        kind = (diff or {}).get("kind") or row["outcome"]
        relation = ""
        if kind == "zero_figures" and facts["native_figures_shown"] is not None:
            relation = f"shown>0:{facts['native_figures_shown'] > 0}|saved>0:{(facts['native_savefig_calls'] or 0) > 0}"
        code = facts["tavotto_error"] or f"diff:{kind}"
        return {"behavior_kind": kind, "relation": relation, "stable_code": code}
    failed = next((n for n, c in row["stages"].items() if c["status"] == "failed"), "none")
    return {
        "behavior_kind": f"row:{failed}",
        "relation": "",
        "stable_code": row["failure_category"]["source_code"] or "none",
    }


def classify(f: dict, rules: list[dict]) -> tuple[dict, dict, dict]:
    """(facts, attribution, behavior signature incl. call site) of one finding.

    The one place that turns a finding into the identity `cluster_key` groups by, so explore's
    confirmation compares exactly what triage clusters on.
    """
    facts = explore_facts(f["row"], f["diff"]) if f["kind"] == "explore" else row_facts(f["row"])
    attr = attribute(facts, f["kind"] if f["kind"] == "explore" else "row", rules)
    sig = behavior_signature(f, attr)
    sig["call_site"] = call_site_signature(f["source"]) if f["source"] else ["unavailable"]
    return facts, attr, sig


def explore_identity(row: dict, source: str | None, rules: list[dict] | None = None) -> tuple:
    """Behavior identity of one explore row: the sorted triage cluster keys of its findings."""
    rules = rules if rules is not None else load_rules()
    items = row["differences"] or (
        [None] if row["outcome"] not in ("no_difference", "static_only", "not_run") else []
    )
    keys = set()
    for diff in items:
        finding = {"kind": "explore", "row": row, "diff": diff, "source": source}
        _facts, attr, sig = classify(finding, rules)
        keys.add(
            cluster_key(
                {
                    **attr,
                    "stable_code": sig["stable_code"],
                    "signature": sig,
                    "execution_policy": "safe",
                    "dependency_source": "installed_environment",
                }
            )
        )
    return tuple(sorted(keys, key=repr))


def triage(
    report_paths: list[Path], rules: list[dict] | None = None, allow_partial_shards: bool = False
) -> dict:
    rules = rules if rules is not None else load_rules()
    families = load_families()
    # the shards of one weekly sweep must prove they belong together before any finding is read
    shards = ES.check_set(
        [
            r
            for r in (json.loads(Path(p).read_bytes()) for p in report_paths)
            if r.get("schema") == "tavotto-guardian-explore/1"
        ],
        allow_partial_shards,
    )
    attributions, sources = [], []
    for path in report_paths:
        raw = Path(path).read_bytes()
        report = json.loads(raw)
        if report.get("schema") == "tavotto-guardian-explore/1":
            findings = list(_explore_findings(report, Path(path).resolve()))
            kind = "explore"
        elif report.get("schema_version") == cg.SCHEMA_VERSION and "cases" in report:
            # 与汇总同一道结构校验（schema + verdict/coverage 由 cases 推出 + 逐行证明不变量）：
            # 被改过或自相矛盾的报告不能产出看似权威的根因归类（Codex #1136 r4311781508）
            GS.load_report(Path(path))
            findings = list(_guardian_findings(report))
            kind = "run"
        else:
            raise cg.EvidenceError(f"{path}: not an explore or run report")
        sources.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "run_id": report["run_id"],
                "kind": kind,
            }
        )
        for f in findings:
            facts, attr, sig = classify(f, rules)
            attributions.append(
                {
                    "finding_id": f["id"],
                    "report_run_id": report["run_id"],
                    "ref": f["ref"],
                    **attr,
                    "stable_code": sig["stable_code"],
                    "diff": _diff_summary(f),
                    "axes": sorted(
                        {v.split(":", 1)[0] for v in (f["row"].get("axis_vector") or [])}
                    ),
                    "signature": sig,
                    "confirmed": (f["row"].get("confirmation") or {}).get("reproduced")
                    if f["kind"] == "explore"
                    else None,
                    "execution_policy": "safe",
                    "dependency_source": "missing"
                    if (f["kind"] == "row" and facts.get("raw_missing_prerequisites"))
                    else "installed_environment",
                }
            )
    return {
        "schema": SCHEMA,
        "sources": sources,
        **({"explore_shards": shards} if shards else {}),
        "rules_sha256": hashlib.sha256(RULES_PATH.read_bytes()).hexdigest(),
        "attributions": attributions,
        "clusters": (clusters := cluster(attributions, families)),
        "alerts": alerts(clusters),
        "summary": summary(attributions),
        "unverified": [
            "attribution is rule-based evidence, not a confirmed root cause: next_action says how to confirm",
            "explore findings come from corpus-derived candidates; clusters are families of behavior, not user impact",
        ],
    }


def cluster_key(a: dict) -> tuple:
    s = a["signature"]
    return (
        a["stage"],
        a["family"],
        a["stable_code"],
        a["execution_policy"],
        a["dependency_source"],
        s["behavior_kind"],
        s["relation"],
        tuple(s["call_site"]),
    )


def cluster(attributions: list[dict], families: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for a in attributions:
        groups.setdefault(cluster_key(a), []).append(a)
    out = []
    for key, members in sorted(groups.items(), key=lambda kv: repr(kv[0])):
        cid = "cl-" + hashlib.sha256(repr(key).encode()).hexdigest()[:10]
        first = members[0]
        out.append(
            {
                "cluster_id": cid,
                "key": {
                    "stage": key[0],
                    "family": key[1],
                    "stable_code": key[2],
                    "execution_policy": key[3],
                    "dependency_source": key[4],
                    "behavior_kind": key[5],
                    "relation": key[6],
                    "call_site": list(key[7]),
                },
                "known_family": known_family({**first["signature"]}, families),
                "size": len(members),
                "members": sorted(m["finding_id"] for m in members),
                "axes": sorted({a for m in members for a in m.get("axes", [])}),
                "reproducible": _reproducible(members),
                "confidence": min((m["confidence"] for m in members), key=CONFIDENCE_ORDER.get),
                "representative": first["finding_id"],
                "next_action": first["next_action"],
            }
        )
    return out


def _reproducible(members: list[dict]) -> bool | None:
    """True if a rerun reproduced a member, False if reruns only failed, None if never rerun."""
    flags = [m["confirmed"] for m in members if m.get("confirmed") is not None]
    if not flags:
        return None
    return any(flags)


#: Families that say something about the fixture or the environment, not about Tavotto.
NOT_PRODUCT_FAMILIES = ("invalid_fixture", "environment_resolution")


def alerts(clusters: list[dict]) -> list[dict]:
    """Only a NEW (no family record), REPRODUCED, product-relevant cluster alerts a maintainer."""
    return [
        {
            "cluster_id": c["cluster_id"],
            "family": c["key"]["family"],
            "stage": c["key"]["stage"],
            "behavior_kind": c["key"]["behavior_kind"],
            "call_site": c["key"]["call_site"],
            "size": c["size"],
            "representative": c["representative"],
            "next_action": c["next_action"],
        }
        for c in clusters
        if c["known_family"] is None
        and c["reproducible"] is True
        and c["key"]["family"] not in NOT_PRODUCT_FAMILIES
    ]


def render_markdown(result: dict) -> str:
    lines = [
        "### Guardian triage",
        *(
            [
                f"explore shards: **{result['explore_shards']['label']}**"
                + (
                    ""
                    if result["explore_shards"]["complete"]
                    else f" (missing {result['explore_shards']['missing']}; findings cover only the completed shards)"
                )
                + (
                    f"; time budget exhausted in shard(s) {result['explore_shards']['budget_exhausted_shards']}: "
                    f"{result['explore_shards']['not_run']} candidate(s) not run"
                    if result["explore_shards"].get("not_run")
                    else ""
                )
            ]
            if result.get("explore_shards")
            else []
        ),
        f"findings: {result['summary']['findings']}, clusters: {len(result['clusters'])}, "
        f"unknown: {result['summary']['unknown']}",
        "",
        "| cluster | family record | attribution | x | reproduced |",
        "| --- | --- | --- | --- | --- |",
    ]
    # listing order only: clusters on heavier axes first (GS.AXIS_WEIGHTS); the data is untouched
    ordered = sorted(
        enumerate(result["clusters"]),
        key=lambda ic: (-max((GS.axis_weight(a) for a in ic[1].get("axes", [])), default=1), ic[0]),
    )
    weight = {
        c["cluster_id"]: max((GS.axis_weight(a) for a in c.get("axes", [])), default=1)
        for c in result["clusters"]
    }
    for _, c in ordered:
        k = c["key"]
        lines.append(
            f"| {c['cluster_id']} | {c['known_family'] or 'NEW'} | "
            f"{k['family']}/{k['stage']} {k['behavior_kind']} | {c['size']} | {c['reproducible']} |"
        )
    if result["alerts"]:
        lines += ["", "**New reproducible families (no record yet):**"]
        lines += [
            f"- {a['cluster_id']}: {a['family']}/{a['stage']} {a['behavior_kind']} x{a['size']} "
            f"({a['representative']}); next: {a['next_action']}"
            for a in sorted(result["alerts"], key=lambda a: -weight.get(a["cluster_id"], 1))
        ]
    else:
        lines += [
            "",
            "No new reproducible family: every reproduced difference matches a recorded family.",
        ]
    lines += ["", "Attribution is rule-based evidence; no issue or PR was opened."]
    return "\n".join(lines) + "\n"


def summary(attributions: list[dict]) -> dict:
    by: dict[str, int] = {}
    for a in attributions:
        by[a["family"]] = by.get(a["family"], 0) + 1
    return {
        "findings": len(attributions),
        "by_family": dict(sorted(by.items())),
        "unknown": by.get("unknown", 0),
        "families_in_taxonomy": len(TX.CATEGORIES),
    }


def run(args) -> int:
    result = triage(
        [Path(p) for p in args.reports],
        allow_partial_shards=bool(getattr(args, "allow_partial_shards", False)),
    )
    out = Path(args.out).resolve()
    if out.is_relative_to(cg.ROOT):
        raise cg.EvidenceError("--out must be outside the source worktree")
    # same containment as --out, checked before anything is written: the observation-only
    # command never overwrites its own inputs (Codex #1139 r4312412195)
    if getattr(args, "markdown_out", None) and Path(args.markdown_out).resolve().is_relative_to(
        cg.ROOT
    ):
        raise cg.EvidenceError("--markdown-out must be outside the source worktree")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out)
    if getattr(args, "markdown_out", None):
        Path(args.markdown_out).write_text(render_markdown(result), encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for a in result["alerts"]:
            print(
                "::warning title=Guardian: new reproducible family::"
                f"{a['family']}/{a['stage']} {a['behavior_kind']} x{a['size']} first seen in "
                f"{a['representative']}; no issue was opened"
            )
    for c in result["clusters"]:
        label = c["known_family"] or "NEW"
        print(
            f"{c['cluster_id']} [{label}] {c['key']['family']}/{c['key']['stage']} x{c['size']} {c['key']['behavior_kind']} {c['key']['call_site']}"
        )
    print(
        json.dumps({"triage": str(out), **result["summary"], "clusters": len(result["clusters"])})
    )
    return 0
