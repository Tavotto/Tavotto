# Limited CLA automation candidate

The implementation is disabled and synthetic tests create only ephemeral test
keys. See [ADR 0134](../../adr/0134-limited-cla-countersigning-authority.md).
The authoritative CLA texts and .github/cla-policy.json are unchanged.

## Reviewable deliverables

- src/native/delegation.ts: detached Ed25519 authorization/control verification,
  internal preparation methods and independent encrypted execution receipts.
- src/native/automatic.ts: authenticated, bounded new-head observation queue.
- authorization-template.json: unsigned placeholders; no registered identity,
  key or authority. A body object is not an already signed body.
- Tests: actual local SQLite transactions, Web Crypto, service/worker code and
  isolated source mutations; global outbound fetch is forbidden.

In a separately approved process, the holder reviews every field and its exact
canonical UTF-8 bytes, signs outside the service, and separately signs a control
whose first previous_sha256 is the grant body hash. Later controls name the
previous control body hash and increment sequence by exactly one. Registration
alone is inactive; revoke is terminal. Controls are limited to 100 events, bodies
to 8 KiB. No preparation method is exposed to a browser. Identity mapping, key
custody, approval evidence and actual import require human verification.

The webhook requires its own limiter and an existing operator-provided secret;
this change creates neither. HMAC covers the original bounded raw bytes.
Admission caps are 100 distinct head requests and 1,000 delivery records, with no
automatic deletion; exhaustion requires reviewed archival/recovery. Only
opened/reopened/synchronize events on main PRs are admitted. The payload is a
locator; current GitHub data and actual evidence determine each new observation.
A done result is historical data and cannot satisfy CI. Evidence revisions clear
it; expired proof data must never be consumed as current eligibility. Manual
requests cannot be released by replaying an event.

## Minimum human activation gates — outstanding

1. Approve legal/operational purpose, narrow scope, time limits, consent UX,
   recovery and treatment of prior contracts. Independently verify holder
   identity and register the correct public key and new authorization ID.
   Obtain the holder's actual grant and enable signatures. Chat instructions,
   repository ownership and this template are not substitutes.
2. Review/apply the namespaced schema and rehearsal/backup/single-authority
   cutover. Grant/control imports must use verified preparation methods,
   never arbitrary SQL that assumes signatures were checked. Verify D1 clock,
   immutability, CAS and concurrent transactions on the actual platform.
3. Complete delegated status/consent UI and authorized audit download. The
   current UI describes personal holder signing; it is not accepted for real
   delegated execution. No legal-sufficiency conclusion is supplied.
4. Separately approve existing App/OAuth identity/permissions, credential
   custody, D1/static-asset/rate-limit bindings, deployment packaging and webhook
   subscription. Preserve the original recovery cron and add the distinct
   reserved observation cron only after quota/plan acceptance. No deployment
   script or real resource bindings are delivered by this PR.
5. Design/review new-head publication, live authority consumption, merge-group
   qualification and trusted-main CI wiring in later PRs. Existing
   cla_service_verify.py checks GitHub proof only; the dual-read consumer in
   src/ci/consumer.ts supplies live authority revalidation. Do not mark the
   provider configured or treat observations as successful GitHub checks.
6. Complete pinned typecheck/build and independent live acceptance before
   enabling any real signing/publication. Existing flags and new
   CLA_DELEGATED_COUNTERSIGNING_ENABLED / CLA_AUTOMATIC_VERIFICATION_ENABLED
   remain absent/false. Never expand budgets or bypass manual states.

The previously rejected dynamic successful-publication-across-proof-expiry test
was not run under another name. No environment, proxy or dependency mirror was
changed to evade the network restriction.
