# Cloudflare CLA lifecycle candidate

An isolated Workers/D1 TypeScript candidate. Default off; not deployed, not connected to a real GitHub App, and not approved for real data migration or legal signing. The existing Python service in ../cla and the repository's authoritative agreement texts and .github/cla-policy.json are unchanged. Only one service may ever be the signing authority.

## Reproduce locally

Use Node 24 and Python 3.10+ with sqlite3. From this directory:

    npm ci --ignore-scripts
    npm run check

The exact development versions and integrity hashes are in package-lock.json. There are no production npm dependencies. check runs TypeScript checking, Node tests, local SQLite migration tests, an esbuild production-import check and isolated mutation checks. The build writes only ignored dist/ files. Review-only disabled deployment inputs and an offline planner live in [deployment/](deployment/README.md); no deploy command or real binding IDs are supplied.

Tests use synthetic identities, ephemeral test encryption keys, a test-only database clock and local SQLite. Outbound fetch and publication are forbidden. The concurrency test uses two worker threads with separate WAL connections and real BEGIN IMMEDIATE transactions; it is not a Cloudflare D1 concurrency claim. Test databases are removed afterward.

## Lifecycle contract

Seven days limits completion of a pending packet. Valid, independently completed signatures remain technically eligible for the same agreement, subject to policy and hold checks. Every qualification decrypts and verifies evidence, immutable packet/actor/document binding and original signing timestamps. A holder timestamp cannot precede the contributor. Proof lifetime remains separately 120 seconds.

Expired incomplete packets remain immutable history. A new packet requires fresh independent signatures; no old signatures or intents are transferred. Creation is transactional. A hold on any packet blocks the subject across all packets and digests, with no automatic release mechanism. A hold blocks automated qualification; it does not purport to revoke or amend a legal agreement.

The contributor must personally sign. Personal holder signing remains the default.
The disabled delegated-authority candidate in src/native/delegation.ts requires a
separately approved, signed, narrowly scoped holder authorization and current
signed enable control. It records a distinct system receipt, never a personal
signature. No real authorization is shipped. GitHub authentication does not
establish legal identity, ownership or authority. Corporate/guardian/unresolved
cases remain outside this route. See [ADR 0137](../../docs/adr/0137-limited-cla-countersigning-authority.md)
and the [outstanding activation gates](../../docs/implementation/cla-delegated-authority/README.md).

The internal `appendHistoricalRevocation(id, signedControl)` candidate can append
only a terminal revoke signed by the original registered key after an ID/key
rotation. It has no HTTP or activation route. It retains the exact historical
agreement, current holder/repository identity, full signed chain and atomic
revision checks; it cannot enable old authority. Full chains, unavailable old
keys and identity changes fail closed. See the [historical revocation evidence
and remaining migration work](../../docs/implementation/cla-historical-revocation/README.md).

The native entry is src/entry.ts. Each capability requires its explicit environment flag; omitted flags keep it off. No credentials, account bindings, worker IDs, queues or resource names are shipped. Agreement version 1.0 remains the only supported configuration; changing the same version's digest fails closed. A future version rollout needs separate review.

## Offline migration rehearsal

migrations/migrate-local-snapshot.py reads a canonical local v1 snapshot and creates a different local output file. It refuses unknown schemas/triggers or an existing output/v2 namespace. It creates separate cla_native_v2_* tables, retains v1, copies every declared column and rowid, and checks row equality and foreign keys. Rowid preservation matters to publication reconciliation cursors.

Object names come from SQLite parsing the canonical DDL, including delegated and
head-request tables. Comparison covers every source column and rowid; the sole
reviewed target-only column is publication_attempts.send_until, initialized to
zero for historical attempts. Migration grants no historical attempt an active
send fence or technical eligibility.

This is not a D1 migration command or authorization to read production evidence. source_opened_read_only means this tool does not write its input; it does not establish that external writers are absent. Copied signature rows are not automatically trusted: the service still validates cryptographic evidence.

## Remaining activation gates

- Verify actual D1 schema, authorized key custody, encrypted evidence and a coherent backup. Unknown historical mappings stop migration.
- Freeze all v1 writers and queues before an individually reviewed single-authority cutover. Never run v1/v2 writers concurrently. After v2 accepts a signature or hold, returning to a stale v1 snapshot is not a valid rollback.
- Integrate the production native entry, background processing and the existing trusted CI contract. Prior synthetic queue executors have not been accepted against this lifecycle change.
- Separately approve App/OAuth setup, secrets, publication and real-person signing acceptance. This PR does not enable the provider, deploy, create infrastructure, or merge itself.

## Validation boundary

The offline regression covers personal/delegated evidence, audit export, fixed-time CI reads, budget admission, durable uncertainty, local SQLite migration and isolated source mutations. Exact counts/commands/exit codes are recorded in the [implementation evidence](../../docs/implementation/cla-delegated-authority/README.md). Re-run the commands above for the checkout under review. Local passes do not establish legal sufficiency or live GitHub/Cloudflare behavior.


The dedicated path-filtered PR workflow uses a GitHub-hosted Ubuntu runner, Node 24 and Python 3.12, with contents:read only, no secrets, no deployment and no dependency cache. It runs npm ci --ignore-scripts and npm run check. It adds no required context and does not change the existing aggregate gates. Local and CI passes do not establish live free-tier acceptance.

Installation tokens are treated as opaque; the adapter does not inspect a legacy prefix or JWT structure. It accepts 520-character stateless tokens while retaining repository, permission and expiration checks. Its separate 8192-byte visible-ASCII/no-whitespace header bound is a service resource-safety limit, not an assertion about GitHub's token format. No token is logged. See https://github.blog/changelog/2026-10-02-stateless-github-app-installation-tokens-rolled-out/.

The signing UI permits an expired incomplete, unblocked contributor packet to be replaced without transferring consent. Holder history uses explicitly requested, authorized packet-ID keyset pages (20 results plus one lookahead), ordered by creation time and ID. Every cursor and page is reauthorized; pagination never grants access to another contributor.

Cached packet selections are authorized again for the current account. Inaccessible selections are cleared from the URL and session storage; account changes also clear prior personal fields and consent, even when both accounts can view the same packet.

## Same-head background refresh candidate

The withdrawal reconciler and fresh publication execute in separate scheduled invocations. After every earlier publication for a tracked target is durably resolved, a refresh decision is deduplicated by its source attempt and evidence revision. A fresh evaluation that is still ineligible finishes without a write; subsequent evidence revisions can request another evaluation. Manual or uncertain publication records block the entire target.

Refresh is limited to the same head and an existing, exactly identified owned check run. New-head dispatch has its own default-off capability described below. Before refreshing, the adapter verifies the predecessor run, attempt, app, name and recorded failure. After authentication and remote pre-reads, it rereads the current binding and commits a new attempt with a new 120-second proof under lease, generation and revision guards. Earlier proof text and expiry remain unchanged. Only one PATCH is attempted. Its response leaves the new attempt awaiting reconciliation; it is not immediately confirmed. A later invocation must independently observe and revalidate it. Unknown outcomes are observed or escalated for manual review, never replayed.

Resource limits remain 20 work SQL statements / 40 total and 35 work external calls / 40 total per invocation. Dispatch needs 13 + 2N work SQL statements, where N is the number of non-exempt contributors. Confirmation needs 14 + 2N, plus two reserved reads if withdrawal becomes necessary. Consequently this initial automatic refresh path admits at most two non-exempt contributors. More contributors or complex remote reads can exhaust the unchanged budget: no fresh publication is sent, retries are bounded to three with 60-second backoff, then manual review is required. This is a resource/capability boundary, not a negative legal or eligibility determination. There is no automatic clearing of manual states.

The [offline lifecycle acceptance](../../docs/implementation/cla-offline-acceptance/README.md)
now exercises successful synthetic publication, independent confirmation, expiry
withdrawal, exact negative-witness release, fresh same-head publication and both
CI authority reads, with personal and delegated evidence. It also covers lost
responses without replay and OAuth consume-time expiry. The native adapter,
journal and verifiers execute against an in-process peer and SQLite; this is not
live GitHub/Cloudflare acceptance or actual CPU evidence. The default-off native
merge-group path now has synthetic positive publication, recovery, authority and
CI coverage as described in that evidence. It reuses the complete-prefix reader,
existing outbox/journal and shared confirmation slot; no workflow is activated.
Group publication reserves 14 fixed/withdrawal work SQL plus qualification cost;
the SQL ceiling is three personal or two delegated non-exempt contributors, with
the full two-person lifecycle verified in both modes and every prefix member
retained. These are whole-group bounds, not per-wakeup batches. Unissued SQL/HTTP
capacity exhaustion now uses three bounded retries and retains the exhausted
outbox; a busy shared slot remains a durable wait. No stale qualification cache
is reused across wakeups. Real
queue-schema/permission, CPU, D1, OAuth and timing acceptance remain separate gates.

### Public endpoints: abuse limits and pinned agreement asset

The two-person same-head refresh limit above applies with delegation disabled.
With delegated countersigning enabled, qualification uses three SQL reads per
non-exempt contributor with full evidence verification: at most four fit read-only
verification. Same-head automatic refresh supports one contributor: dispatch
13+3N, confirmation 14+3N plus two reserved withdrawal reads. The global flag
conservatively applies these limits even to personal signatures. Budgets remain
unchanged. The new-head queue defaults to observations. Its additional
CLA_AUTOMATIC_PUBLISHING_ENABLED flag requires verification, existing publishing
and background recovery flags; one non-exempt contributor is admitted so later
confirmation fits. One POST follows a durable issued/dispatched/outbox transaction;
uncertainty cannot replay. Current policy/provider remains disabled. UI/audit,
distinct crons and disabled deployment/CI proposals are in the activation checklist.
No trigger, credential or resource is configured here.

OAuth remains off by default. Before enabling it, supply the `CLA_OAUTH_LIMITER`
Workers Rate Limiting binding. The worker uses a fixed `oauth-start` key before
reading a body, importing the evidence key, or accessing D1. Missing/failed
bindings fail closed with 503; a denied request returns 429. A suggested binding
setting is 20 requests per 60 seconds, using a distinct operator-selected
namespace. The [official binding documentation](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)
explains that these counters are permissive, eventually consistent and local to
each Cloudflare location. This is not a global accounting or complete DoS defense.
No IP address is collected or used as an authentication signal. Availability and
account-plan costs must be verified before a separately approved deployment;
this PR creates no binding or paid resource.

A separate atomic D1 rule allows at most 10 unexpired OAuth states globally in
a rolling 300-second interval, including consumed states. It allows a small
concurrent burst instead of serializing logins. Busy callers receive 429 with a
300-second retry hint. Each admission transaction prunes at most 20 expired rows
and inserts only if both the active limit and total-row limit of 100 permit it.
Unused, unexpired states are never pruned. Existing oversized snapshots are not
silently discarded: bounded expiry cleanup may need multiple requests before
admission resumes. Failed admissions can still prune expired rows. Normal
steady-state admission is approximately 2,880 states/day, but this is not a D1
billing guarantee: cleanup backlog, index maintenance, callbacks, reads and other
service operations also consume quota. An attacker can occupy the shared slots;
the service deliberately favors bounded cost and fail-closed behavior over an
unconditional login-availability promise. Callback single-use state/browser/PKCE
binding is unchanged. `oauth_expiry` is included in new snapshot schemas; existing
v2 deployments require a separately reviewed schema update before activation.

Public `GET /agreement` no longer calls GitHub or imports the evidence key. It
requires a `CLA_PUBLIC_AGREEMENT` Workers Static Assets binding containing exactly
`/CLA_INDIVIDUAL.md`. Before deployment, export the exact approved document bytes
from a fixed reviewed commit, verify SHA-256 against the deployed policy's
individual-agreement digest, and stage them as that asset. This is an operator
preparation step, not a contributor-signing prerequisite. Do not alter legal text
to make a digest match. Set the asset's Content-Type explicitly to `text/plain`
or `text/markdown` (for example using the asset `_headers` file); an octet-stream
fallback is rejected. Configure `assets.run_worker_first = true` so requests are
routed through this worker, and do not expose the asset directory through another
unvalidated route. The worker has no direct asset pass-through route.

The fixed asset fetch rejects redirects and non-200/error responses, bounds the
stream to 128 KiB, verifies the original bytes against the current configured
digest, and then requires valid UTF-8. A missing binding, bad hash, bad type or
unavailable asset returns 503 with no GitHub fallback. There is no persistent
in-memory cache and no fabricated source commit in the response. This public
text is not evidence that upstream policy is still current: authenticated packet
creation, signing and eligibility retain their existing policy/identity checks.
Requests containing `packet` still require the appropriate session and return
that packet's immutable historical document. This PR supplies only synthetic
asset fixtures, no real agreement asset upload or deployment configuration.

### Identity fan-out and verification admission

Binding construction now validates and deduplicates all local commit identities
before resolving any login. The producer and proof parser share the 1,000-person
limit. Commit text is bounded to 200,000 characters per commit and 2,000,000 UTF-8
bytes across the candidate. A single binding permits at most 10 unique remote
identity lookups, including exemption identity checks; larger remote-lookup
requirements fail closed and need manual handling. This is not support for 1,000
remotely resolved coauthors. Repeated trailers already matching a trusted commit
identity need no extra lookup. A login/ID mismatch is never silently exempted.
Each exemption actually used by a candidate is resolved against GitHub during
that binding and checked against both the policy login and configured ID. No
identity result is cached across binding/proof evaluations. These extra remote
reads remain subject to the existing invocation budget in background work.

Before enabling public CI verification, supply a separate
`CLA_VERIFICATION_LIMITER` Rate Limiting binding (suggested 10 calls per 60 seconds,
operator-selected namespace). Locator syntax is validated before the limiter,
key import or D1, and an admitted request uses one shared `InvocationBudget`
through database reads and GitHub reevaluation. Missing/error bindings return
503, denial returns 429, and exhausted work fails closed. The CI caller needs no
new secret; temporary admission or budget failures do not yield eligibility.
The binding has the same per-location/eventual-consistency limitations described
above; this does not guarantee protection from a distributed quota-exhaustion
attack. No cached successful verification or renewed old proof is introduced.

Repeated authenticated packet creation may return an existing eligible-for-reuse
packet only after checking its subject, configured agreement version/digest,
immutable document hash and subject-wide holds. That metadata-only response
makes no GitHub request, creates no intent or signature, and is not a fresh
qualification result. Expired incomplete packets still require a new upstream
policy/agreement validation before atomic replacement creation. Existing signing
and fresh qualification checks remain separate from packet retrieval.


### Explicit database bootstrap and consumable proposal limits

In default legacy mode the production service binds every store to its configured
holder and exactly two agreement kind/version/digest rows. The existing revision read incorporates this
shared readiness predicate without adding a query to evaluation/verification.
Signing/OAuth writes also perform an explicit read-only readiness check. The
background driver reads readiness/revision before claiming work; normal
confirmation keeps its previous statement count, while fresh dispatch adds one
read (13 + 2N). Actual budget charging and pre-send reservations still apply.
Missing, extra or mismatched configuration rows fail closed; request handlers do
not initialize or repair them. Authority and agreement rows are immutable.

For a new database, prepare initialization explicitly before enabling service:

    node scripts/generate-bootstrap.mjs CONFIG.json > bootstrap.sql

This command reads only the supplied configuration and emits SQL. It opens no
database, accesses no network and deploys nothing. The output targets only the
v2 namespace and records a digest of the seed configuration. Rehearse it against
a disposable local database containing the raw namespaced base/native schema.
Its SQLite transaction inserts missing exact configuration rows, is idempotent
for a matching database, and rolls back on mismatch or incomplete seeding. It
never overwrites an existing holder/agreement or removes signing history.
Do not feed the explicit SQLite BEGIN/COMMIT script directly to D1 as an assumed
supported migration: an independently approved D1 integration must apply the
same ordered statements atomically through its batch mechanism and verify
readiness before enabling requests. Existing v2 schema upgrades also need the
new immutable authority triggers. No live initialization is authorized here.

The separately disabled [agreement selection journal](../../docs/implementation/cla-agreement-activation/README.md)
adds an internal atomic append/archive/select transaction for future approved
versions. It requires an explicit environment flag and exact latest hash pin;
old configs/binaries reject after a real third agreement is retained. Old
signatures do not migrate. The legacy bootstrap refuses this mode and any
existing selection journal. Existing v2 databases need a separately reviewed
additive schema upgrade before this binary is enabled.

A proposal that can become successful is limited to six non-exempt contributors:
verification requires eight fixed SQL reads plus two per contributor, so six
uses the unchanged 20-work-statement ceiling. The producer rejects seven or more
with `contributor_capacity_exceeded` before qualification/publication. This is a
service-capacity limitation, not a legal eligibility determination. Verification
reserves its final three reads; old proofs are never extended. Automatic
background refresh retains its stricter two-person bound. Read-only fixed-time
verification budget tests do not publish a check or exercise expiry transitions.

For review of that future D1 batch, generate the same source statements without
SQLite transaction wrappers:

    node scripts/generate-bootstrap.mjs CONFIG.json --d1-statements > bootstrap-statements.json

The JSON array is data only, not an uploader. After separate authorization, an
integration can map each string to `db.prepare(sql)` and submit the entire array
in one `db.batch(...)`, then perform the production readiness read. Do not split
it into independently committed requests. The local adapter test exercises this
single-batch shape without invoking Cloudflare.

The final [offline repo-v2 upgrade candidate](../../docs/implementation/cla-v2-upgrade/README.md)
rehearses the exact main77 v2 namespace to #1168 with synthetic SQLite only.
Its CLI emits a disabled in-memory schema plan and has no file-target writer or
production execution option. It does not replace real D1 schema/backup/cutover
acceptance, enable capabilities or resolve the single-slot availability limit.
