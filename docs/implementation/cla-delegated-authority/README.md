# Limited CLA automation candidate

This draft is default disabled. All tests use synthetic identities and ephemeral
keys. Authoritative CLA text/hashes and provider.configured=false are unchanged.
See [ADR 0137](../../adr/0137-limited-cla-countersigning-authority.md).

## Reviewable implementation

- delegation.ts verifies exact Ed25519 grants and signed enable/suspend/revoke
  chains, retains immutable registered public-key snapshots and records separate
  encrypted execution receipts after the contributor personally signs.
- service.ts / worker.ts / UI provide authenticated status and private audit
  export, distinguish personal holder signatures from delegated system receipts,
  preserve manual holder signing and clear consent on account/packet changes.
  Historical downloads after rotation/revocation do not restore eligibility.
- automatic.ts admits bounded signed webhook locators and revalidates current
  heads/contributors/evidence. Observation is the default. Separately enabled
  new-head publication makes one attempt after an atomic issued/dispatched/outbox
  transaction; uncertainty cannot retry. Existing recovery independently confirms
  or withdraws. Observations and POST responses cannot satisfy CI.
  A persisted send lease excludes concurrent recovery until settlement or its
  deadline. Phase comparisons reject stale recovery writes; atomic settlement
  wakes current evidence without releasing another lease.
- [Disabled deployment inputs and planner](../../../services/cla-cloudflare/deployment/README.md)
  supply explicit binding shapes and approval checkpoints without provisioning.
- [Inactive trusted CI proposal](../../../services/cla-cloudflare/deployment/ci-proposal.md)
  and tested default-off CLI consume two live authority reads. No live workflow
  is wired; merge-group candidates are rejected.
- authorization-template.json remains unsigned, with no registered identity/key.
  Internal preparation methods have no HTTP route. Actual import and signing
  require separate holder approval and verified key/identity registration.

Authorization bodies are canonical UTF-8. The first control references the grant
hash; later controls increment sequence and hash-link predecessors. Registration
is inactive; revoke is terminal. Controls are bounded to 100 events and 8 KiB per
body. Three SQL reads per delegated contributor retain cryptographic verification;
read-only verification fits four contributors, automatic dispatch/confirmation
one within unchanged 20 work/40 total SQL and 35 work/40 total external limits.
Complex remote reads can exhaust budgets and require manual handling.

Webhook admission caps are 100 distinct heads and 1,000 delivery records with no
automatic deletion. Only opened/reopened/synchronize main PR locators are admitted.
Expired/uncertain/manual work never replays. The distinct reserved automatic cron
must not replace or starve the original withdrawal/recovery cron. Evidence
revisions clear observations; dispatched attempts recover through the outbox.

## Verification and boundaries

Original committed evidence is in evidence/verification.json. Followup evidence
is in evidence/followup-verification.json with actual logs and source fingerprints.
The formal-review P1 fix and migration regression evidence are recorded in
evidence/inflight-verification.json. Those tests use failed proposals and local
transactions, with no HTTP or successful publication.
Tests execute service/worker/Web Crypto/local SQLite transactions; the UI harness
executes shipped client code in a browser-shaped VM, not a real browser. CI tests
use real qualification and pre-existing synthetic confirmed records at fixed time;
no HTTP, successful publication or proof-expiry publication transition is tested.
A separately audited existing GitHub offline job passed original head fe22f4b;
that result must not be attributed to later heads without reading their jobs.

Local dependency installation was blocked by npm E403. No mirror, proxy, alternate
execution environment or repeat download was used. Local tsc/esbuild and pytest
remain unavailable. The previously rejected dynamic successful-publication-across-
proof-expiry test was not run under another name. Offline passes establish no
legal effect, true holder identity, actual D1/GitHub behavior or plan acceptance.

## Minimum human activation gates — outstanding

1. Approve exact legal purpose/scope/term, personal consent UX, key custody and
   treatment of prior agreements. Verify holder identity, register its actual
   public key/authorization ID, obtain real grant and enable signatures. Chat,
   ownership, code and this template are not substitutes for those actions.
2. Approve single-authority cutover, backups and immutable archival mappings.
   Rehearse actual D1 batch/clock/CAS/concurrency and schema changes, including
   delegated_grants.public_key and head_requests.dispatched. No guessed historical
   keys, arbitrary SQL imports or returning to stale snapshots after real writes.
3. Independently accept real browser/session flows and private audit handling;
   confirm the material's legal sufficiency outside these technical tests.
4. Approve existing App/OAuth permissions/secret custody, explicit resource IDs,
   assets, limiter namespaces, quota and separate recovery/automatic crons. Review
   the disabled manifest and offline plan; this PR creates or enables none.
5. Land trusted source first, then separately review CI wiring. Resolve native
   merge-group binding/queue policy before replacing required checks. Do not
   treat old PR proofs, observations or the Python proof-only verifier as live
   native authority validation. Keep provider.configured false until acceptance.
6. Complete current-head pinned typecheck/build, high-risk full CI and independent
   live acceptance, including the excluded publication boundary through a separately
   permitted process. No flag, manual state or resource limit is bypassed here.
