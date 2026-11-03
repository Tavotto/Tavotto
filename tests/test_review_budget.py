"""Review Budget Controller regression tests: false green / hidden repeats are failures."""

from __future__ import annotations

import json
import re
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
        "src-tauri/src/sidecar.rs",
        "src-tauri/src/sidecar/port_memory.rs",
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


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("src/tavotto/security.py", "src/tavotto/auth.py"),
        (".github/workflows/ci.yml", "docs/guide/archived-ci.md"),
        ("src-tauri/src/sidecar.rs", "docs/guide/old-sidecar.md"),
        ("src-tauri/src/sidecar/port_memory.rs", "docs/guide/old-port.md"),
        ("docs/guide/old-layout.md", "src/tavotto/security.py"),
    ],
)
def test_renaming_security_sensitive_paths_never_lowers_review_floor(previous, current):
    result = B.classify(
        [{"filename": current, "previous_filename": previous, "status": "renamed"}],
        ["risk:low"],
    )
    assert result["tier"] == "high"
    assert result["path_floor"] == "high"
    assert result["security_review_recommended"] is True
    assert result["label_conflict"] is True
    assert result["files_count"] == 1
    assert previous in result["drivers"] or current in result["drivers"]


def test_ordinary_document_rename_stays_low_without_double_counting():
    result = B.classify(
        [
            {
                "filename": "docs/guide/new.md",
                "previous_filename": "docs/guide/old.md",
                "status": "renamed",
            }
        ],
        ["risk:low"],
    )
    assert result["tier"] == "low"
    assert result["security_review_recommended"] is False
    assert result["files_count"] == 1


def test_deleted_security_file_keeps_its_security_requirement():
    result = B.classify([{"filename": "src/tavotto/security.py", "status": "removed"}], [])
    assert result["tier"] == "high"
    assert result["security_review_recommended"] is True


def test_missing_or_invalid_rename_origin_is_unknown_not_low():
    for entry in (
        {"filename": "README.md", "status": "renamed"},
        {"filename": "docs/guide/new.md", "status": "renamed", "previous_filename": ""},
        {"filename": "docs/guide/new.md", "status": "renamed", "previous_filename": 12},
        {"filename": "docs/guide/new.md", "status": "modified", "previous_filename": ""},
    ):
        with pytest.raises(B.IncompleteEvidence):
            B.classify([entry], ["risk:low"])


def test_sidecar_security_rule_points_to_real_module():
    assert (ROOT / "src-tauri/src/sidecar.rs").is_file()
    assert "src-tauri/src/sidecar.rs" in B.SECURITY_EXACT
    assert B._floor("src-tauri/src/sidecar.rs") == (2, True)
    assert B._floor("src-tauri/src/sidecar/port_memory.rs") == (2, True)


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


def test_automatic_review_trigger_is_detected_per_bot_summary_row():
    body = (
        '<!-- codex-review-state:v1 {"code_review":{},"security_review":{}} -->\n'
        "| Review | Status | Commit | Review trigger |\n"
        "| --- | --- | --- | --- |\n"
        "| 📝 **Code Review** | Failed | abc | PR opened |\n"
        "| 🔒 **Security Review** | Completed | abc | Manual request |\n"
    )
    r = _audit(["src/tavotto/security.py"], comments=[_comment(body, author=B.BOT)])
    assert r["signals"]["automatic_review_trigger_observed"] == {
        "code": True,
        "security": False,
    }
    assert r["signals"]["security_auto_trigger_observed"] is False
    assert r["signals"]["manual_requests"]["code"] == 0


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
    # Validate actual event keys, not strings in comments or job descriptions.
    # The allowed event set is deliberately closed, including workflow_dispatch.
    on_section = body.partition("\non:\n")[2]
    assert on_section, "Expected a literal top-level on: block"
    event_section, sep, _ = on_section.partition("\npermissions:\n")
    assert sep, "Expected an explicit top-level permissions: block"
    event_keys = set(re.findall(r"(?m)^  ([a-z_]+):\s*$", event_section))
    assert event_keys == {
        "pull_request",
        "pull_request_review",
        "pull_request_review_comment",
        "issue_comment",
        "workflow_dispatch",
    }
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


def test_review_budget_refreshes_for_review_and_pr_comment_evidence_only():
    body = (ROOT / ".github/workflows/review-budget.yml").read_text(encoding="utf-8")
    on_section = body.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    event_actions = {
        "pull_request_review": {"submitted", "edited", "dismissed"},
        "pull_request_review_comment": {"created", "edited", "deleted"},
        "issue_comment": {"created", "edited", "deleted"},
    }
    for event, expected in event_actions.items():
        match = re.search(rf"(?m)^  {event}:\n    types: \[([^\]\n]+)\]$", on_section)
        assert match is not None, event
        assert {a.strip() for a in match.group(1).split(",")} == expected, event

    # Relevant PR-only webhook events must not run untrusted fork code or issue jobs.
    guard = body.split("\njobs:\n", 1)[1].split("\n    runs-on:", 1)[0]
    assert "if: >-" in guard
    assert "github.event.pull_request.base.ref == 'main'" in guard
    assert "github.event.issue.pull_request" in guard
    assert "github.event.comment.user.login == 'chatgpt-codex-connector[bot]'" in guard
    assert "contains(github.event.comment.body, '@codex')" in guard
    assert "github.event.action == 'deleted'" in guard

    # All event families resolve the correct PR and cancel only older advisory audits.
    pr_selector = (
        "github.event.pull_request.number || github.event.issue.number || inputs.pr_number"
    )
    assert f"PR_NUMBER: ${{{{ {pr_selector} }}}}" in body
    # Concurrency must be job-level: a workflow-level group would let an unrelated PR
    # comment (later skipped by the job `if`) cancel an audit that is already running.
    top_level, jobs_section = body.split("\njobs:\n", 1)
    assert "concurrency:" not in top_level
    assert (
        f"group: review-budget-${{{{ github.workflow }}}}-${{{{ {pr_selector} || github.ref }}}}"
        in jobs_section
    )
    assert "cancel-in-progress: true" in jobs_section
    assert "ref: ${{ github.event.repository.default_branch }}" in body
    assert "persist-credentials: false" in body
    assert "pull_request_target:" not in on_section
