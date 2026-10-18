#!/usr/bin/env python3
"""Read-only PR review budget audit. Never requests an AI review or decides merge eligibility.

Reads only GitHub PR/files/comments/reviews. Output is advisory evidence, not a gate.
Python stdlib only, so it can run on a hosted GitHub runner without installing deps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _pin_utf8_streams() -> None:
    # Isolated `python -I` cannot import scripts/ci/_common; keep the entry
    # point's Windows stdout/stderr contract explicit and self-contained.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


_pin_utf8_streams()

BOT = "chatgpt-codex-connector[bot]"
TIERS = {"low": 0, "normal": 1, "high": 2}
TIER_NAMES = {v: k for k, v in TIERS.items()}
MAX_PAGES = 30

# File paths are a conservative FLOOR. A label can raise but never lower it.
# This is triage, not a substitute for a human threat model or code review.
SECURITY_PREFIXES = (
    ".github/workflows/",
    "src-tauri/capabilities/",
    "src-tauri/src/sidecar/",
    "services/telemetry_proxy/",
)
SECURITY_EXACT = {
    ".github/cla-policy.json",
    "scripts/ci/cla_gate.py",
    "scripts/ci/aggregate_gate.py",
    "src/tavotto/security.py",
    "src/tavotto/engine/security.py",
    "src/tavotto/engine/session_client.py",
    "src/tavotto/engine/worker.py",
    "src/tavotto/desktop.py",
    "src-tauri/src/sidecar.rs",
}
HIGH_PREFIXES = (
    ".github/",
    "scripts/ci/",
    "src/tavotto/rendercore/",
    "src/tavotto/pdfbackend/",
    "src-tauri/",
    "workerd/",
    "packaging/",
    "docs/legal/",
)
HIGH_EXACT = {
    "SECURITY.md",
    "LICENSE",
    "TRADEMARKS.md",
    "pyproject.toml",
    "src/tavotto/engine/patchspec.py",
    "src/tavotto/engine/overrides.py",
    "web/src/lib/shapeGeometry.ts",
    "docs/ci/pr-review-tiers.md",
}
RISK_LABELS = {"risk:low", "risk:normal", "risk:high"}
REVIEW_REQUEST = re.compile(r"(?<![\w])@codex\s+(security\s+)?review\b", re.I)
REVIEWED_SHA = re.compile(r"\*\*Reviewed commit:\*\*\s*[\x60]([0-9a-f]{7,40})[\x60]", re.I)
BOT_STATE = re.compile(r"<!-- codex-review-state:v1\s+(\{.*?\})\s*-->", re.S)


class IncompleteEvidence(RuntimeError):
    """The correct response to missing GitHub data is unknown, not approved."""


def _floor(path: str) -> tuple[int, bool]:
    secure = path in SECURITY_EXACT or any(path.startswith(p) for p in SECURITY_PREFIXES)
    if secure or path in HIGH_EXACT or any(path.startswith(p) for p in HIGH_PREFIXES):
        return 2, secure
    # Only demonstrably non-policy prose is automatically low.
    if (
        path == "README.md"
        or path == "CHANGELOG.md"
        or (
            path.startswith("docs/")
            and path.endswith((".md", ".mdx", ".rst"))
            and not path.startswith(("docs/rules/", "docs/adr/", "docs/ci/", "docs/security"))
        )
    ):
        return 0, False
    return 1, False


def classify(files: list[dict], labels: list[str]) -> dict:
    if not files:
        raise IncompleteEvidence("Changed file list missing; cannot assign a low risk tier")
    paths: list[str] = []
    for changed in files:
        if not isinstance(changed, dict):
            raise IncompleteEvidence("Changed file entry is invalid")
        current = changed.get("filename")
        if not isinstance(current, str) or not current:
            raise IncompleteEvidence("Changed file path missing or invalid")
        paths.append(current)
        previous = changed.get("previous_filename")
        # GitHub reports the OLD path separately for a rename. Ignoring it
        # would downgrade deletion/movement of security-sensitive code.
        if changed.get("status") == "renamed" and (not isinstance(previous, str) or not previous):
            raise IncompleteEvidence("Renamed file lacks its previous_filename")
        if previous is not None:
            if not isinstance(previous, str) or not previous:
                raise IncompleteEvidence("Previous file path invalid")
            paths.append(previous)
    floor = 0
    secure = False
    drivers = []
    for path in paths:
        rank, is_secure = _floor(path)
        floor = max(rank, floor)
        secure |= is_secure
        if rank == 2:
            drivers.append(path)
    selected = [label for label in labels if label in RISK_LABELS]
    labeled = max((TIERS[label.partition(":")[2]] for label in selected), default=0)
    effective = max(floor, labeled)
    conflict = len(set(selected)) > 1 or any(
        TIERS[label.partition(":")[2]] < floor for label in selected
    )
    return {
        "tier": TIER_NAMES[effective],
        "path_floor": TIER_NAMES[floor],
        "security_review_recommended": secure,
        "label_conflict": conflict,
        "risk_labels": sorted(set(selected)),
        "drivers": sorted(set(drivers))[:20],
        "files_count": len(files),
    }


def _bot(item: dict) -> bool:
    return (item.get("user") or {}).get("login") == BOT


def _sha_matches(head: str, observed: str | None) -> bool:
    return bool(
        observed
        and re.fullmatch(r"[a-f0-9]{7,40}", observed, re.I)
        and len(head) == 40
        and head.lower().startswith(observed.lower())
    )


def _review_receipts(reviews: list[dict], comments: list[dict]) -> list[dict]:
    results = []
    for obj in [*reviews, *comments]:
        if not _bot(obj):
            continue
        body = obj.get("body") or ""
        sha = REVIEWED_SHA.search(body)
        if not sha:
            continue
        kind = "security" if "Codex Security Review" in body else "code"
        if "codex-review-receipt:v1" not in body and "Codex Review:" not in body:
            continue
        results.append(
            {
                "kind": kind,
                "sha": sha.group(1).lower(),
                "at": obj.get("submitted_at") or obj.get("created_at") or "",
                "automatically_triggered": "Automatically triggered" in body,
                "id": obj.get("id"),
            }
        )
    return sorted(results, key=lambda x: (x["at"], str(x["id"])))


def _bot_summary(comments: list[dict]) -> dict:
    summaries = []
    for c in comments:
        if not _bot(c):
            continue
        found = BOT_STATE.search(c.get("body") or "")
        if found:
            try:
                state = json.loads(found.group(1))
            except (ValueError, TypeError):
                continue
            if isinstance(state, dict):
                summaries.append((c.get("updated_at") or c.get("created_at") or "", state))
    return max(summaries, key=lambda x: x[0])[1] if summaries else {}


def _signals(comments: list[dict]) -> dict:
    manual = {"code": [], "security": []}
    quota = {"code": [], "security": [], "unspecified": []}
    automatic_seen = {"code": False, "security": False}
    for c in comments:
        body = c.get("body") or ""
        when = c.get("created_at") or ""
        if _bot(c):
            if "reached your codex usage limits" in body.lower():
                kind = (
                    "security"
                    if "security review" in body.lower()
                    else "code"
                    if "code review" in body.lower()
                    else "unspecified"
                )
                quota[kind].append(when)
            if "codex-review-state:v1" in body:
                # Bot summary shows only the latest trigger for each kind.
                # Parse per row: a code PR-open must not masquerade as security auto.
                for line in body.splitlines():
                    columns = [part.strip().lower() for part in line.strip().strip("|").split("|")]
                    if len(columns) < 4:
                        continue
                    trigger = columns[3]
                    automatic = trigger in {
                        "pr opened",
                        "pr reopened",
                        "pr ready for review",
                        "pr marked ready",
                        "automatically triggered",
                    }
                    if automatic:
                        for kind in ("code", "security"):
                            if kind + " review" in columns[0]:
                                automatic_seen[kind] = True
        else:
            for m in REVIEW_REQUEST.finditer(body):
                kind = "security" if m.group(1) else "code"
                manual[kind].append(when)
    return {
        "manual_requests": {k: len(v) for k, v in manual.items()},
        # These are *possible* repeats: GitHub issue comments do not pin old head SHAs.
        "possible_repeated_manual_requests": {k: max(0, len(v) - 1) for k, v in manual.items()},
        "quota_replies": {k: len(v) for k, v in quota.items()},
        "last_quota_reply": {k: max(v, default=None) for k, v in quota.items()},
        "last_manual_request": {k: max(v, default=None) for k, v in manual.items()},
        "automatic_review_trigger_observed": automatic_seen,
        "security_auto_trigger_observed": automatic_seen["security"],
    }


def _review_state(
    kind: str, head: str, required: bool, receipts: list[dict], signals: dict, summary: dict
) -> dict:
    if not required:
        return {"state": "not_required", "reviewed_head": False}
    own = [r for r in receipts if r["kind"] == kind]
    current = [r for r in own if _sha_matches(head, r["sha"])]
    bot_current = summary.get(kind + "_review") or {}
    summary_matches = _sha_matches(head, bot_current.get("commit_sha"))
    unresolved = bot_current.get("has_unresolved_findings") if summary_matches else None
    performed = bool(current)
    latest_receipt = max((r["at"] for r in own), default="")
    quota_at = max(
        filter(
            None,
            [
                signals["last_quota_reply"][kind],
                signals["last_quota_reply"]["unspecified"],
            ],
        ),
        default="",
    )
    manual_count = signals["manual_requests"][kind]
    if performed:
        state = "reviewed_findings_open" if unresolved is True else "reviewed_on_head"
    elif quota_at and quota_at >= latest_receipt:
        state = "quota_blocked"
    elif len(own) >= 2:
        state = "manual_escalation"
    elif own:
        state = "targeted_recheck_needed"
    elif manual_count:
        state = "requested_unconfirmed"
    else:
        state = "full_review_suggested"
    return {
        "state": state,
        "reviewed_head": performed,
        "observed_receipts": len(own),
        "reviewed_sha_prefixes": [r["sha"][:12] for r in own[-4:]],
        "current_unresolved_findings": unresolved,
        "quota_observed": bool(quota_at),
    }


def assess(pr: dict, files: list[dict], comments: list[dict], reviews: list[dict]) -> dict:
    head = (pr.get("head") or {}).get("sha") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", head, re.I):
        raise IncompleteEvidence("PR head SHA unavailable")
    base = (pr.get("base") or {}).get("ref")
    if base != "main":
        # Stacked PRs are still classifiable; do not invent normal PR gate coverage.
        stacked = True
    else:
        stacked = False
    labels = [i.get("name", "") for i in pr.get("labels", [])]
    risk = classify(files, labels)
    receipts = _review_receipts(reviews, comments)
    signals = _signals(comments)
    summary = _bot_summary(comments)
    required_code = risk["tier"] != "low"
    code = _review_state("code", head, required_code, receipts, signals, summary)
    security = _review_state(
        "security", head, risk["security_review_recommended"], receipts, signals, summary
    )
    if risk["label_conflict"] or any(s["state"] == "manual_escalation" for s in (code, security)):
        action = "manual_risk_or_review_decision"
    elif any(s["state"] == "quota_blocked" for s in (code, security)):
        action = "pause_requests_check_codex_quota"
    elif any(s["state"] in ("requested_unconfirmed",) for s in (code, security)):
        action = "wait_for_existing_review"
    elif any(
        s["state"] in ("full_review_suggested", "targeted_recheck_needed") for s in (code, security)
    ):
        action = "review_after_fast_checks_pass"
    elif any(s["state"] == "reviewed_findings_open" for s in (code, security)):
        action = "resolve_review_findings_and_verify"
    else:
        action = "no_additional_ai_review_indicated"
    return {
        "schema_version": 1,
        "status": "observed",
        "repository": pr.get("base", {}).get("repo", {}).get("full_name"),
        "pr_number": pr.get("number"),
        "head_sha": head,
        "base_ref": base,
        "stacked_pr": stacked,
        "draft": bool(pr.get("draft")),
        "risk": risk,
        "code_review": code,
        "security_review": security,
        "signals": signals,
        "receipts": {
            "code": len([r for r in receipts if r["kind"] == "code"]),
            "security": len([r for r in receipts if r["kind"] == "security"]),
            "security_automatically_triggered": len(
                [r for r in receipts if r["kind"] == "security" and r["automatically_triggered"]]
            ),
        },
        "suggested_action": action,
        "disclaimer": "Advisory only. No trigger, merge gate, CI verdict or Codex account setting is changed.",
    }


def _api(repo: str, endpoint: str, token: str) -> object:
    url = "https://api.github.com/repos/" + repo + endpoint
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tavotto-review-budget",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise IncompleteEvidence("GitHub API unavailable: " + type(exc).__name__) from exc


def _pages(repo: str, endpoint: str, token: str) -> list[dict]:
    items = []
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in endpoint else "?"
        batch = _api(repo, endpoint + sep + "per_page=100&page=" + str(page), token)
        if not isinstance(batch, list):
            raise IncompleteEvidence("GitHub API expected a list, got something else")
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise IncompleteEvidence("GitHub pagination exceeded budget; evidence is incomplete")


def _markdown(result: dict) -> str:
    if result["status"] != "observed":
        return "### Review Budget (advisory)\n\nEvidence unavailable; no review is considered complete.\n"
    risk = result["risk"]
    code = result["code_review"]
    sec = result["security_review"]
    sig = result["signals"]
    return (
        "### Review Budget (read-only, advisory)\n\n"
        "| Field | Observation |\n| --- | --- |\n"
        f"| Risk floor / effective | {risk['path_floor']} / {risk['tier']} |\n"
        f"| Security review recommended | {risk['security_review_recommended']} |\n"
        f"| Code review | {code['state']} |\n"
        f"| Security review | {sec['state']} |\n"
        f"| Manual code / security requests | {sig['manual_requests']['code']} / {sig['manual_requests']['security']} |\n"
        f"| Quota replies (code / security / unknown) | {sig['quota_replies']['code']} / {sig['quota_replies']['security']} / {sig['quota_replies']['unspecified']} |\n"
        f"| Possible redundant requests (code / security) | {sig['possible_repeated_manual_requests']['code']} / {sig['possible_repeated_manual_requests']['security']} |\n"
        f"| Suggested action | {result['suggested_action']} |\n\n"
        "Does not request reviews, approve a PR, change required checks, or lift security gates. "
        "A completed review receipt is not proof that findings were resolved. "
        "Quota notifications are observations, not an API-based live quota check.\n"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="owner/repo, required except --fixture")
    p.add_argument("--pr", type=int, help="pull request number")
    p.add_argument("--fixture", help="offline JSON with pr/files/comments/reviews")
    p.add_argument("--out", required=True, help="JSON evidence location")
    p.add_argument("--summary", help="Markdown summary for GITHUB_STEP_SUMMARY")
    args = p.parse_args(argv)
    try:
        if args.fixture:
            data = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
            result = assess(data["pr"], data["files"], data["comments"], data["reviews"])
        else:
            if (
                not args.repo
                or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo)
                or not args.pr
                or args.pr < 1
            ):
                p.error("--repo owner/repo and positive --pr are required")
            token = os.environ.get("GITHUB_TOKEN")
            if not token:
                raise IncompleteEvidence("GITHUB_TOKEN not available")
            pr = _api(args.repo, f"/pulls/{args.pr}", token)
            if not isinstance(pr, dict):
                raise IncompleteEvidence("Missing PR response")
            files = _pages(args.repo, f"/pulls/{args.pr}/files", token)
            comments = _pages(args.repo, f"/issues/{args.pr}/comments", token)
            reviews = _pages(args.repo, f"/pulls/{args.pr}/reviews", token)
            result = assess(pr, files, comments, reviews)
        rc = 0
    except (IncompleteEvidence, KeyError, TypeError, ValueError) as exc:
        result = {
            "schema_version": 1,
            "status": "unknown",
            "error": type(exc).__name__ + ": " + str(exc),
            "disclaimer": "Evidence incomplete; cannot claim review coverage.",
        }
        rc = 2
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.summary:
        with Path(args.summary).open("a", encoding="utf-8") as f:
            f.write(_markdown(result))
    print(
        json.dumps(
            {
                "status": result["status"],
                "action": result.get("suggested_action"),
                "out": str(dest),
            },
            ensure_ascii=False,
        )
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
