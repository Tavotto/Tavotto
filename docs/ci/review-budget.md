# Tavotto Review Budget Controller — P0 read-only rollout (2026-10-17)

## Purpose and authority

AI review quota is a scarce engineering resource. This controller is **read-only triage**, not a
replacement for the existing review policy in docs/ci/pr-review-tiers.md, an approval, a
security clearance, or a fourth required CI check. It never posts review-trigger comments,
requests a review, modifies repository files, changes branch protection, or merges a PR.

Authoritative merge eligibility remains the existing **CI fast gate / CI integration gate /
CodeQL gate**, the Merge Queue, unresolved review threads, and human approval. In particular
a receipt marked reviewed_on_head proves only that Codex emitted a receipt for that commit;
it **does not** prove the change is secure, findings are resolved, or tests passed.

## Current evidence — small, explicit sample, not a quota-cost estimate

- PR #1130 has a substantive code review and a later security review receipt explicitly
  labeled "Automatically triggered". Its discussion also records multiple quota errors
  after additional review requests. This shows that code/security triggers are coupled
  in at least one real PR. It does not show tokens or credits consumed.
- PR #1143 has two quota-exhaustion replies within seconds after review activity.
- PR #1144 has a Code Review usage-limit reply and an independently completed Security
  Review; its summary identifies the security trigger as "PR opened".
- The 2026-09-29 policy already says automatic Codex review should be switched off and
  manual requests limited. The UI setting itself **cannot be verified or changed** via
  this repository PR. Do not equate a written policy with an account setting.

The controller deliberately distinguishes **a failed attempt, an actual review receipt,
and a summary that reports unresolved findings**.

## Mechanism

Files:
- scripts/ci/review_budget.py — Python stdlib audit and JSON ledger generator.
- tests/test_review_budget.py — deterministic tests for floors, spoofing, repeated calls,
  quota replies, pagination failure, and the workflow trust boundary.
- .github/workflows/review-budget.yml — hosted, read-only advisory run on main-bound
  PR lifecycle events, review submissions/edits/dismissals, review-comment changes,
  Codex replies and review-request comments, plus optional manual dispatch.
  It checks out **default branch** so a fork PR cannot replace the audit program.
  Until this PR lands on main, its bootstrap run must record status=unknown
  rather than silently trusting PR code.

Observed data: GitHub Pull Request metadata, all changed file pages, all issue comment
pages, and all review pages. Only the exact chatgpt-codex-connector[bot] author can supply
a Codex receipt. Human comments containing a review mention are **request candidates**,
not proof that a job ran. The bot's quota-exhaustion reply is a **historical signal**, not
a query of the live Codex quota API.

Risk floor is conservative:
- **low**: non-policy prose only (README, changelog, ordinary docs);
- **normal**: source and tests by default; never automatically infer that a test-only PR
  is a mechanically proven low-risk refactor;
- **high**: render, PDF, desktop shell, supervisor, packaging, CI, legal controls and
  other explicitly mapped boundaries. Security-sensitive paths separately recommend
  a Security Review. Example: rendercore correctness is high but does not automatically
  spend a security review; authentication or workflow permissions recommend both.
- GitHub renames are evaluated using **both** `filename` (new) and `previous_filename`
  (old); moving or disabling a protected path cannot reduce the risk floor. A rename
  with missing origin evidence is `unknown`, not low. Deleted files retain their own path.
- Sidecar security coverage includes `src-tauri/src/sidecar.rs` and its submodules.
- Author risk labels may **raise**, but never lower, the path floor. Inconsistent labels
  emit label_conflict and require a human decision.

Review states: not_required / full_review_suggested / requested_unconfirmed /
targeted_recheck_needed / reviewed_on_head / reviewed_findings_open /
quota_blocked / manual_escalation.

Deduplication is **informational** and conservative. A substantive bot receipt whose
reviewed commit prefix matches the exact PR head prevents a recommendation to repeat
the same full review. A stale SHA is *not* silently declared equivalent after rebase,
even if the title and filenames are unchanged. GitHub request comments do not record
the previous head SHA, so repeated manual comments are reported as **possible** repeats,
not billed attempts. Two stale substantive receipts cause manual_escalation instead of
a third routine recommendation; a maintainer can explicitly authorize a safety-critical
re-review. Security findings cannot be dismissed by exhausting this budget.

Every run emits a small JSON evidence file (schema_version=1, status=observed or unknown)
and a GitHub job summary. Artifacts are kept for 30 days and are not committed to main.
All API calls are GET; data/history truncation and API errors emit status=unknown and
never state that a review is complete. A green advisory workflow must never be read as
approval. No user code, figure data, token, or full comment text enters the ledger.

## Event-driven ledger refresh (after merge)

A PR commit is not the only time review evidence changes. The advisory workflow
also listens to `pull_request_review` (`submitted`, `edited`, `dismissed`),
`pull_request_review_comment` (`created`, `edited`, `deleted`), and
`issue_comment` (`created`, `edited`, `deleted`). Review events are
limited to PRs targeting main. Issue-comment jobs require a PR (not an issue)
and a Codex bot response, a comment mentioning Codex, or a deletion that might
invalidate recorded evidence; unrelated comments are skipped before any runner
is allocated. The runner gets the numeric PR identifier from the event's
`pull_request.number` or `issue.number`.

These events **re-read existing evidence only**; they never request Code Review
or Security Review. GitHub runs `issue_comment` workflows only after their
workflow file is present on the default branch. All triggers checkout trusted
main, use a read-only `GITHUB_TOKEN`, and never execute PR-head code or
evaluate comment bodies in a shell. A single advisory-only concurrency group
per PR cancels stale audit attempts, not CI or CodeQL. `workflow_dispatch`
remains the manual recovery route for webhook delays, missed review events,
or review-thread resolution that produces no supported event. GitHub may
suppress some events created with its own `GITHUB_TOKEN`; automatic refresh
is best effort, and stale or unknown evidence is never approval.

## Usage

Manual from a trusted checkout of the repository:

    GITHUB_TOKEN=... python3 -I scripts/ci/review_budget.py \
      --repo Tavotto/Tavotto --pr 1130 --out /tmp/review-budget.json

Offline regression fixture:

    python3 -I scripts/ci/review_budget.py \
      --fixture example.json --out /tmp/review-budget.json

A fixture is a JSON object with four fields: pr, files, comments, reviews, matching the
GitHub REST shapes. The PR workflow is also manually dispatchable after merge.

## Before broadening automation

1. Audit a representative period of PRs using the resulting per-run evidence; track
   request candidates, quota replies, actual bot receipts, stale SHA rechecks, unresolved
   findings and regressions. **Do not infer money, tokens, or true review counts from
   comment count.**
2. Verify the Codex settings listed below. No GitHub workflow can neutralize the Codex
   account's existing automatic triggers.
3. Confirm high-risk security path coverage with a human. Add new sensitive paths with
   tests. Never rely solely on an author-supplied risk:low label.
4. Only then consider a guarded separate dispatcher. This P0 intentionally does not
   use GitHub issue write scopes or invoke any payable AI review endpoint.

## Codex website actions for repository owner — not done by this PR

- At https://chatgpt.com/codex/cloud/settings/general inspect Tavotto repository
  settings and turn off automatic **Code Review on PR open / ready / push** as available.
- Separately inspect **Security Review** automatic triggering. Disable automatic
  security alongside review / PR-open triggers if the goal is manual, budgeted scans.
  Check team and personal scopes if both apply.
- Use the existing SECURITY.md as the documented threat model. If the Codex Security
  Review settings support an explicit threat-model source, configure it to use this
  repository document; this PR has not changed Codex cloud configuration.
- Verify settings with a **draft** test PR or settings UI, not repeated billable review
  prompts. Check the Codex usage dashboard for actual allowance and reset timing;
  this controller has no access to that balance.

Rollback: revert the new files. Existing CI, CodeQL, three required gate contexts,
merge_group and release configuration were deliberately not edited.
