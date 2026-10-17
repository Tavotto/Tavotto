"""Review Budget Controller regression tests: false green / hidden repeats are failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/review_budget.py"
if not SCRIPT.is_file():
    if (ROOT / ".github").is_dir():
        raise AssertionError("Source checkout unexpectedly lost scripts/ci/review_budget.py")
    pytest.skip(
        "sdist does not ship scripts/ci; workflow-level test needs source checkout",
        allow_module_level=True,
    )
sys.path.insert(0, str(SCRIPT.parent))
import review_budget as B  # noqa: E402


def _pr(*, head="a" * 40, labels=(), base="main"):
    return {
        "number": 9000,
        "head": {"sha": head},
        "base": {"ref": base, "repo": {"full_name": "Tavotto/Tavotto"}},
        "draft": False,
        "labels": [{"name": name} for name in labels],
    }


def _files(*paths):
    return [{"filename": p, "status": "modified"} for p in paths]


def _comment(body, *, at="2026-10-16T10:00:00Z", author="erwanjun", id=12):
    return {"id": id, "body": body, "user": {"login": author}, "created_at": at}


def _receipt(*, sha="a" * 40, security=False, at="2026-10-16T11:00:00Z", id=42):
    title = "Codex Security Review" if security else "Codex Review"
    mark = chr(96)
    body = (
        "<!-- codex-review-receipt:v1 -->\n"
        + title
        + "\n**Reviewed commit:** "
        + mark
        + sha[:10]
        + mark
    )
    if security:
        body += "\nAutomatically triggered"
    return _comment(body, at=at, author=B.BOT, id=id)


def _audit(paths, comments=(), reviews=(), labels=(), head="a" * 40):
    return B.assess(_pr(head=head, labels=labels), _files(*paths), list(comments), list(reviews))


def test_pure_nonpolicy_docs_do_not_request_ai():
    r = _audit(["README.md", "docs/guide/getting-started.md"], labels=["risk:low"])
    assert r["risk"]["tier"] == "low"
    assert r["code_review"]["state"] == "not_required"
    assert r["security_review"]["state"] == "not_required"
    assert r["suggested_action"] == "no_additional_ai_review_indicated"


def test_tests_and_rules_are_not_silently_assumed_safe():
    for path in (
        "tests/test_security.py",
        "docs/rules/ci/ci-lanes.md",
        "docs/adr/0008-unified-local-session-auth.md",
        "web/src/App.tsx",
    ):
        r = _audit([path], labels=["risk:low"])
        assert r["risk"]["tier"] == "normal", path
        assert r["risk"]["label_conflict"] is True, path
        assert r["code_review"]["state"] == "full_review_suggested"


def test_security_sensitive_path_overrides_low_label_and_needs_both_reviews():
    for path in (
        ".github/workflows/ci.yml",
        "src/tavotto/security.py",
        "src-tauri/capabilities/main.json",
        "scripts/ci/aggregate_gate.py",
    ):
        r = _audit([path], labels=["risk:low"])
        assert r["risk"]["tier"] == "high"
        assert r["risk"]["path_floor"] == "high"
        assert r["risk"]["security_review_recommended"] is True
        assert r["risk"]["label_conflict"] is True
        assert r["code_review"]["state"] == "full_review_suggested"
        assert r["security_review"]["state"] == "full_review_suggested"
        assert r["suggested_action"] == "manual_risk_or_review_decision"


def test_core_correctness_high_without_inventing_security_requirement():
    r = _audit(["src/tavotto/rendercore/geometry.py"])
    assert r["risk"]["tier"] == "high"
    assert r["risk"]["security_review_recommended"] is False
    assert r["security_review"]["state"] == "not_required"


def test_label_can_raise_floor_and_conflicting_labels_never_lower_it():
    r = _audit(["README.md"], labels=["risk:high"])
    assert r["risk"]["tier"] == "high"
    assert r["risk"]["label_conflict"] is False
    bad = _audit(["README.md"], labels=["risk:low", "risk:high"])
    assert bad["risk"]["tier"] == "high"
    assert bad["risk"]["label_conflict"] is True


def test_missing_or_unreadable_changed_files_fails_closed():
    with pytest.raises(B.IncompleteEvidence):
        B.classify([], ["risk:low"])
    with pytest.raises(B.IncompleteEvidence):
        B.classify([{"status": "modified"}], ["risk:low"])
    with pytest.raises(B.IncompleteEvidence):
        B.assess(_pr(head="short"), _files("README.md"), [], [])


def test_exact_sha_receipt_deduplicates_full_review():
    r = _audit(["web/src/App.tsx"], reviews=[_receipt(), _receipt(id=43)])
    assert r["code_review"]["state"] == "reviewed_on_head"
    assert r["code_review"]["reviewed_head"] is True
    assert r["code_review"]["observed_receipts"] == 2
    assert r["suggested_action"] == "no_additional_ai_review_indicated"


def test_old_review_is_not_head_coverage_even_when_sha_prefix_matches_its_old_head():
    old = "b" * 40
    r = _audit(["web/src/App.tsx"], reviews=[_receipt(sha=old)])
    assert r["code_review"]["state"] == "targeted_recheck_needed"
    assert r["code_review"]["reviewed_head"] is False


def test_security_receipt_does_not_satisfy_code_review_and_reverse():
    secure = "src/tavotto/security.py"
    r1 = _audit([secure], reviews=[_receipt(security=True)])
    assert r1["security_review"]["state"] == "reviewed_on_head"
    assert r1["code_review"]["state"] == "full_review_suggested"
    r2 = _audit([secure], reviews=[_receipt()])
    assert r2["code_review"]["state"] == "reviewed_on_head"
    assert r2["security_review"]["state"] == "full_review_suggested"


def test_spoofed_review_text_from_human_cannot_mark_coverage():
    impostor = _receipt()
    impostor["user"]["login"] = "outside-contributor"
    r = _audit(["web/src/App.tsx"], reviews=[impostor])
    assert r["code_review"]["state"] == "full_review_suggested"


def test_unresolved_findings_are_not_silently_approved():
    summary = _comment(
        '<!-- codex-review-state:v1 {"code_review":{"commit_sha":"'
        + "a" * 40
        + '","has_unresolved_findings":true,"status":"failed"},'
        + '"security_review":{}} -->',
        author=B.BOT,
    )
    r = _audit(["web/src/App.tsx"], comments=[summary], reviews=[_receipt()])
    assert r["code_review"]["state"] == "reviewed_findings_open"
    assert r["code_review"]["current_unresolved_findings"] is True
    assert r["suggested_action"] == "resolve_review_findings_and_verify"


def test_quota_responses_halt_retry_recommendations_without_claiming_current_quota():
    r = _audit(
        ["web/src/App.tsx"],
        comments=[
            _comment("@codex review", at="2026-10-16T10:00:00Z"),
            _comment(
                "You have reached your Codex usage limits for code reviews.",
                at="2026-10-16T10:00:10Z",
                author=B.BOT,
            ),
            _comment("@codex review", at="2026-10-16T10:05:00Z"),
            _comment(
                "You have reached your Codex usage limits for code reviews.",
                at="2026-10-16T10:05:10Z",
                author=B.BOT,
            ),
        ],
    )
    assert r["signals"]["manual_requests"]["code"] == 2
    assert r["signals"]["possible_repeated_manual_requests"]["code"] == 1
    assert r["signals"]["quota_replies"]["code"] == 2
    assert r["code_review"]["state"] == "quota_blocked"
    assert r["suggested_action"] == "pause_requests_check_codex_quota"


def test_after_quota_an_actual_head_receipt_counts_but_only_as_reviewed():
    r = _audit(
        ["web/src/App.tsx"],
        comments=[
            _comment("You have reached your Codex usage limits for code reviews.", author=B.BOT)
        ],
        reviews=[_receipt(at="2026-10-17T12:00:00Z")],
    )
    assert r["code_review"]["state"] == "reviewed_on_head"
    assert r["code_review"]["quota_observed"] is True


def test_request_or_bot_help_text_is_not_evidence_of_a_completed_review():
    r = _audit(
        ["web/src/App.tsx"],
        comments=[
            _comment("@codex review"),
            _comment('To review, comment "@codex review".', author=B.BOT),
        ],
    )
    assert r["signals"]["manual_requests"]["code"] == 1
    assert r["code_review"]["state"] == "requested_unconfirmed"
    assert r["code_review"]["reviewed_head"] is False


def test_repeated_completed_stale_reviews_escalate_to_human_instead_of_third_round():
    r = _audit(["web/src/App.tsx"], reviews=[_receipt(sha="b" * 40), _receipt(sha="c" * 40, id=46)])
    assert r["code_review"]["state"] == "manual_escalation"
    assert r["suggested_action"] == "manual_risk_or_review_decision"


def test_stacked_pr_does_not_invent_fast_ci_coverage():
    r = B.assess(_pr(base="feature-stack"), _files("README.md"), [], [])
    assert r["stacked_pr"] is True
    assert r["base_ref"] == "feature-stack"


def test_paginated_api_never_treats_incomplete_history_as_empty(monkeypatch):
    pages = []

    def get(_repo, endpoint, _token):
        pages.append(endpoint)
        return [{"id": 1}] * (100 if len(pages) == 1 else 3)

    monkeypatch.setattr(B, "_api", get)
    assert len(B._pages("Tavotto/Tavotto", "/pulls/1/reviews", "token")) == 103
    assert len(pages) == 2

    monkeypatch.setattr(B, "_api", lambda *_: [{"id": 1}] * 100)
    with pytest.raises(B.IncompleteEvidence, match="pagination"):
        B._pages("Tavotto/Tavotto", "/pulls/1/reviews", "token")


def test_cli_fixture_emits_json_evidence_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    inp = tmp_path / "in.json"
    out = tmp_path / "ledger.json"
    summary = tmp_path / "summary.md"
    inp.write_text(
        json.dumps({"pr": _pr(), "files": _files("README.md"), "comments": [], "reviews": []}),
        encoding="utf-8",
    )
    assert B.main(["--fixture", str(inp), "--out", str(out), "--summary", str(summary)]) == 0
    assert json.loads(out.read_text())["suggested_action"] == "no_additional_ai_review_indicated"
    assert "advisory" in summary.read_text().lower()


def test_cli_reports_unknown_and_nonzero_when_evidence_is_bad(tmp_path):
    inp = tmp_path / "bad.json"
    out = tmp_path / "ledger.json"
    inp.write_text(
        json.dumps({"pr": _pr(), "files": [], "comments": [], "reviews": []}), encoding="utf-8"
    )
    assert B.main(["--fixture", str(inp), "--out", str(out)]) == 2
    result = json.loads(out.read_text())
    assert result["status"] == "unknown"
    assert "no" not in result.get("suggested_action", "")


def test_advisory_workflow_does_not_elevate_permissions_or_touch_required_gates():
    workflow = ROOT / ".github/workflows/review-budget.yml"
    if not workflow.is_file():
        pytest.fail("Review Budget Controller is missing its advisory workflow")
    body = workflow.read_text(encoding="utf-8")
    assert "branches: [main]" in body
    assert "runs-on: ubuntu-latest" in body
    assert "pull_request_target:" not in body
    assert "merge_group:" not in body
    assert "workflow_run:" not in body
    assert "review_budget.py" in body
    assert "default_branch" in body
    assert "contents: read" in body
    assert "pull-requests: read" in body
    assert "issues: read" in body
    assert "contents: write" not in body
    assert "pull-requests: write" not in body
    assert "issues: write" not in body
    assert "security-events: write" not in body
    assert "@codex review" not in body
    assert "@codex security review" not in body
    assert "CI fast gate" not in body
    assert "CI integration gate" not in body
    assert "CodeQL gate" not in body
