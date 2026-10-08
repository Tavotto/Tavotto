# Local validation record — 2026-10-08

## Scope and baseline

Narrow change based on current locally fetched main
`36b39031305e18df6a1b2217ddd1c5a67bb309ac` (not the earlier PR #826 snapshot).
The CLA gate adapter, new read-only CI consumer, independent CLA service and its
small existing-fast-lane test integration are changed. No PR #826 product changes, agreement text, policy configuration,
signing/verification activation, ruleset, App, credential or deployment mutation is included.

## Executed

| Verification | Result |
|---|---|
| `python services/cla/tests/run_ci.py` | 38 passed; real loopback HTTP + SQLite + RSA/PKCE contracts |
| `python services/cla/tests/mutation_check.py` | 6/6 isolated counterexamples caught, each target test exited 1 |
| Full-repository `ruff check .` | Passed |
| Full-repository `ruff format --check .` (768 files) | Passed |
| `git diff --check` | Passed |
| CI/workflow/aggregate/legal contract suite (except the exact missing-history test) | 235 passed, 1 deselected |
| Removing the service CI step in an isolated in-memory workflow copy | Contract assertion fails as intended |
| Existing `tests/test_legal_contribution_policy.py` | 89 passed; one audit-history test failed because the shallow local Git history cannot resolve/reach audited baseline `7952ceb…` |
| Same existing suite excluding that exact unavailable-history assertion | 89 passed; **not** a claim that the full suite passed |

Tests verify:
- Both personally initiated signatures are necessary; owner cannot sign first.
- SQL persistence survives reopening; immutable evidence/packet/audit updates fail;
  evidence is encrypted and wrong keys cannot qualify a contributor.
- GitHub OAuth code/PKCE exchange and `/user` numeric identity, RSA App JWT,
  repository-scoped token permissions, App impersonation failure.
- Session expiry, one-use signing intent, CSRF Origin/session/packet binding,
  OAuth state/browser binding, replay, private evidence access isolation.
- Missing capacity/ownership attestations block; no corporate/minor shortcut.
- HMAC webhook validation, repository/installation binding, duplicate delivery,
  conflicting delivery reuse, durable outage retry and uncertain check-create recovery.
- PR opener, every commit author and co-author; >100-commit pagination/count check;
  unresolved/malformed/mismatched noreply ID blocks; policy drift blocks.
- Review hold queues a negative refresh without deleting original signatures;
  closed PR retirement preserves a concurrently queued reopening job.
- Latest check wins, all-exempt-login requests still require authoritative proof,
  stale/wrong-head/policy/PR/repository proofs fail; real CI consumer reads the
  loopback HTTP candidate proof and rechecks time after network delay.
- Merge-group signed webhook, candidate/base binding, replay/rebinding rejection,
  queue-position gaps, missing candidate retirement, new-delivery reactivation, replay remaining a no-op, head changes and holds.
- Final decision stays pending until all exact-candidate, trusted-workflow,
  current-attempt CI checks pass; spoofed App/check-suite/SHA, newer failed workflow,
  older-attempt check and changed queue fail. Previously green final decision is
  cleared before a later policy failure.

The CI runner negative probes additionally reject missing dependencies, empty discovery,
skipped suites and expected-failure suites. The new CI dependency install itself has
not run on GitHub yet; local tests use the same verified installed versions. Official
PyPI availability of all three exact pins was checked.

The six hand counterexamples independently remove the second-signature requirement,
webhook HMAC, authoritative exemption proof, latest-verdict selection, trusted-CI
wait, and private-record access restriction. Every mutated copy produced a failing
assertion. Original reviewed files are never mutated by this runner.

## Not established by these results

No real GitHub App exists for this implementation; no real OAuth ceremony,
electronic signature, private evidence storage, hosted TLS/proxy, live multi-PR
merge queue, branch-rule enforcement, Gunicorn deployment or recovery drill has
been accepted. No contract-law effectiveness opinion is offered. The controlled
HTTP peer models the documented APIs; it cannot establish undocumented live queue
semantics. Full Tavotto product tests were not run because application code was
not changed. Independent review is a separate step.

The service is locally executable and the integration entry points are real.
Production activation still requires the explicit approvals and staged acceptance
listed in `DEPLOYMENT.md`. In particular, TTL metadata cannot itself expire a
GitHub green check, and final success is a point-in-time decision with a post-check
race, not an atomic continuous-revocation guarantee.


## Published CI follow-up

The first published CI run executed the service suite successfully. Its existing
Windows entry-point guard found missing UTF-8 stdout/stderr setup in the new CI
consumer; the six-line standard setup fixed it, with 143 Windows regression tests
passing and five platform-condition skips. No signing logic changed.

Two inline CodeQL findings were separately reconciled in `SECURITY_REVIEW.md`.
The test-peer response splitting was reproduced and repaired; the PKCE S256
password-hashing classification is a documented, protocol-backed false positive.
Three added HTTP/security regressions raise the enforced suite minimum to 41.
No scanner suppression or alert dismissal was applied.
