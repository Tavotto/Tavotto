# Tavotto private dual-signature CLA service

Status: local implementation with real HTTP and SQLite contract tests; **not deployed,
not connected to a real GitHub App, and no real signing/merge acceptance has occurred**.
The repository policy remains `configured: false`. This is an independent trusted
service, not code that runs inside PR CI. It is not part of the Tavotto application.

## What runs

- `app.py`: WSGI `/login`, OAuth/PKCE callback, private agreement/record pages,
  separate contributor and Jiaqi Wan signing POSTs, qualification-review hold, and
  HMAC-authenticated PR/merge-group webhooks.
- `store.py`: real SQLite WAL/FULL transactions; immutable exact agreement bytes,
  immutable packet identity, append-only encrypted signature evidence and audit;
  short-lived one-use intents, hashed sessions, deduplicated webhook deliveries,
  durable PR/candidate jobs. Private data lives outside every source checkout.
- `github.py`: real HTTP GitHub App RSA JWT and installation-token exchange, OAuth
  identity lookup, count-checked commit pagination, immutable co-author ID checks,
  queue enumeration, check-run publication and trusted workflow identification.
- `worker.py`: one serial worker, durable retries and periodic reconciliation;
  updates the existing own check, recovers uncertain create outcomes by lookup,
  retires closed PRs and vanished candidates, and reevaluates after signature/hold
  changes. It never executes contribution code.
- `scripts/ci/cla_service_verify.py`: a real read-only trusted CI entry point which
  polls GitHub for a newly issued authoritative proof. The corresponding narrow
  `cla_gate.py` adapter rejects old, wrong-App, wrong-policy, wrong-SHA, wrong-PR,
  wrong-candidate-base, missing or negative proofs. Active service mode requires
  the service proof even for login-exempt contributors.

## Local verification

From repository root, in a Python environment with the requirements available:

```sh
python services/cla/tests/run_ci.py
ruff check services/cla scripts/ci/cla_gate.py scripts/ci/cla_service_verify.py
ruff format --check services/cla scripts/ci/cla_gate.py scripts/ci/cla_service_verify.py
python -m pytest tests/test_legal_contribution_policy.py
```

The tests start two actual loopback HTTP servers and use actual SQLite files in a
private temporary directory. One HTTP server implements a controlled GitHub peer;
it verifies PKCE and RSA-signed JWTs, and checks requested permissions and request
contracts. All names, addresses, credentials and signatures are synthetic and are
removed when tests finish. These are **not** a live GitHub/signature-provider test.
No test credential can access a real account. Test mode is only a constructor
argument; the production environment loader cannot enable it.

Repository CI runs the entire service suite once in the existing `backend-fast`
Python 3.13 / shard 1 matrix leg on PR and merge-group events. A fresh venv under
`RUNNER_TEMP` installs the exact three-package core dependency closure from official
PyPI; it does not install the Tavotto app or Gunicorn. The runner rejects missing
or mismatched dependencies, fewer than 38 tests, skips and expected failures.
Failure propagates through the existing fast gate; no new job/context or write
permission is added. The application dependency closure remains unchanged.

No Flask, app runtime, rendering dependencies or product tests are needed to run
this service suite. The production server is Gunicorn (Linux only); WSGI test
coverage does not replace TLS/proxy/real-browser/real-App acceptance.

## Signing boundary

Only self-owned adult individual contributions use the automated path. Both
parties log in independently and personally type their legal names, enter their
private postal addresses, review the full immutable agreement, and actively
check the consent fields. Jiaqi Wan must personally review the contributor's
identity/capacity before countersigning. No signature is copied from a profile,
inserted on another person's behalf, or derived from a GitHub comment.

GitHub authentication establishes control of a numeric GitHub account ID. It does
**not** establish a legal identity, age, capacity, copyright ownership or corporate
authority. Corporate/employer/guardian/unresolved-identity cases remain blocked
and need a separately reviewed route. No claim about legal effectiveness is made.
Agreement legal text/version/hash are unchanged by this implementation.

The service stores name, address, typed signature, account ID/login-at-signing,
role, UTC timestamp, exact version/hash, and the exact consent attestations in
Fernet-authenticated encrypted evidence. Both parties can download their private
record while authenticated. The database also contains identity/session metadata;
therefore encryption of evidence is not a substitute for private disk encryption,
least-privilege access and encrypted backups. SQL immutability protects against
application updates, **not** a malicious database administrator. Backup/WORM and
key custody are deployment decisions, not an unimplemented promise of tamperproof
storage. Lost keys cannot be recovered by the application.

A hold only blocks automated qualification. It does not purport to revoke an
irrevocable licence, amend an agreement or erase a signature. Old signatures never
migrate to new bytes. Reusing a version with different bytes is rejected even if a
repository policy edit attempts it. Address/corporate/rights-transfer corrections
and hold resolution need a reviewed operational/legal process; this MVP offers no
silent administrator override or bulk migration endpoint.

See [deployment and acceptance](DEPLOYMENT.md) and [verification record](VALIDATION.md).
