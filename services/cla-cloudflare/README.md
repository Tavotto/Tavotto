# Cloudflare CLA lifecycle candidate

An isolated Workers/D1 TypeScript candidate. Default off; not deployed, not connected to a real GitHub App, and not approved for real data migration or legal signing. The existing Python service in ../cla and the repository's authoritative agreement texts and .github/cla-policy.json are unchanged. Only one service may ever be the signing authority.

## Reproduce locally

Use Node 24 and Python 3.10+ with sqlite3. From this directory:

    npm ci --ignore-scripts
    npm run check

The exact development versions and integrity hashes are in package-lock.json. There are no production npm dependencies. check runs TypeScript checking, Node tests, local SQLite migration tests, an esbuild production-import check and isolated mutation checks. The build writes only ignored dist/ files. Review-only disabled deployment inputs and an offline planner live in [deployment/](deployment/README.md). The only real binding IDs and deploy commands are the disposable staging rehearsal in [deployment/staging/](deployment/staging/README.md) (sandbox repository, staging texts with no legal effect, every flag false in the file).

Tests use synthetic identities, ephemeral test encryption keys, a test-only database clock and local SQLite. Outbound fetch and publication are forbidden. The concurrency test uses two worker threads with separate WAL connections and real BEGIN IMMEDIATE transactions; it is not a Cloudflare D1 concurrency claim. Test databases are removed afterward.

## Lifecycle contract

Seven days limits completion of a pending packet. Valid, independently completed signatures remain technically eligible for the same agreement, subject to policy and hold checks. Every qualification decrypts and verifies evidence, immutable packet/actor/document binding and original signing timestamps. A holder timestamp cannot precede the contributor. Proof lifetime remains separately 120 seconds.

Expired incomplete packets remain immutable history. A new packet requires fresh independent signatures; no old signatures or intents are transferred. Creation is transactional. A hold on any packet blocks the subject across all packets and digests until the rights holder releases it; nothing releases a hold automatically. A hold blocks automated qualification; it does not purport to revoke or amend a legal agreement.

A release (`POST /release {packet}`, rights holder only, once per hold) is append-only: it adds one immutable `eligibility_releases` row and never edits the hold or any signature. A hold keeps counting against a packet unless it was released **and** the packet was created strictly after the release, so the held packet and every earlier packet stay ineligible for good (no signature is revived). After a release the contributor creates and signs a new packet, the holder countersigns it and it qualifies normally; a later hold on that packet blocks again and can itself be released. A release is not a signature: it publishes no success check and nothing becomes eligible until a new signature. Packet creation and `/status` treat a subject as blocked while any hold is unreleased; `/status` reports `blocked`, `released`, `block` and `canRelease`. A packet created in the same second as the release is refused, so wait for the next second.

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

The [complete-prefix metadata transport fix](../../docs/implementation/cla-prefix-metadata-budget/README.md)
batches fresh member metadata only within the enabled prefix read session. Its
single-page synthetic P1/P2/P3/P4 dispatch costs are 26/28/30/32 external calls,
with full prefix, two snapshots and independent confirmation retained. More
pages/coauthors can still exceed the unchanged budget; the whole-group SQL
limits above remain unchanged. This is offline evidence, not live acceptance.

## Live eligibility for CI (ADR 0172)

`GET /eligibility/pr?pr&head&nonce` and `GET /eligibility/group?base&head&nonce` answer whether a target
is eligible right now. They are public, take no credential, write nothing, publish nothing, and share
`CLA_VERIFICATION_LIMITER`; `CLA_CI_VERIFICATION_ENABLED` turns them on. Every call re-reads GitHub and D1
and re-verifies evidence through `evaluateTarget`. The answer binds repository, target, head, base, policy
digest, contributors and the caller's nonce, and carries one `eligible` boolean: it never says who has not
signed. `scripts/ci/cla_eligibility_fetch.py` fetches it and `scripts/ci/cla_gate.py` validates it; an
unreachable service or a mismatched answer never passes.

### Exempt traffic and bounded retention

The main repository is mostly rights-holder and bot traffic, which is policy-exempt and needs no service proof: the
trusted CI gate passes all-exempt changes locally by immutable GitHub id. The service therefore does not spend its
capped tables or its one-at-a-time queues on it.

- **Exempt-author PR webhooks are skipped.** After the usual signature/repository/head validation, a `pull_request`
  whose `pull_request.user.id` is one of `exemptionIds` (ids only, never logins) returns the normal `202
  {accepted:true,publication:'not_attempted'}` without any SQL, GitHub call or row (`head_requests`,
  `head_deliveries` and `targets` stay untouched). A PR by an exempt author that carries a non-exempt co-author never
  receives a service proof, so the CI gate fails closed for it; this is rare and accepted.
- **All-exempt merge groups finish without the slot and without a check run.** Merge-group webhooks do not list their
  members, so they are admitted. In the background path, `publishGroup` reads the first prefix snapshot (the same
  verified binding, including the exemption identity re-resolution) and, when every contributor id is exempt,
  settles the job as `group_exempt_no_publication`: under the job's lease and generation it deletes the outbox row and
  the target's idle rows (deliveries, progress cursor, the target). No check run is created and the confirmation slot
  is never needed; a busy slot is probed with that one snapshot (about 16 external calls) and only a group with a
  non-exempt contributor keeps waiting. A group with any non-exempt contributor takes the existing path unchanged:
  the check happens inside the first snapshot of the normal two-snapshot read session, so it adds no external call.
  Offline: an all-exempt two-PR group uses 11 SQL / 19 external calls; a mixed one publishes with 20 SQL / 32
  external, the baseline plus its exempt identity lookups.
- **Bounded retention keeps the admission caps about live work.** Each admission transaction first prunes at most 20
  rows per statement in its own committed batch (so a refused admission still makes room), then applies the caps:
  head requests whose status is `done`, `dispatched` or `manual` and that are older than one day, with their
  `head_deliveries` (the immutability trigger now allows exactly this delete and nothing else); merge-group
  `deliveries` older than one day whose target has no outbox work and no unresolved publication; and `merge:` targets
  referenced by nothing. `pending`/`running` heads, anything younger than a day, and every `publication_attempts`,
  `published_checks`, `slot_policy`, `refresh_requests`, signature, packet, intent, agreement, audit and
  `eligibility_*` row are never deleted. The target cap of 100 counts live targets only: a target with an unresolved
  publication, or one queued for its first evaluation. Resolved publication history is audit and stays, uncounted.
  `head_requests` < 100 and `head_deliveries` < 1000 remain total-row caps, now over a pruned table. Every revision
  re-queues all targets (`revision_outbox`), so unbounded resolved history would lengthen that drain; this is the
  accepted cost of never deleting the audit journal.

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

### Signed PDF copy (generated in the browser)

After both parties have signed, the signing page can offer a PDF copy: the agreement text with its own
signature block filled in place (or, when the agreement PDF carries no usable block positions, a separate **Signatures**
page) and an **Electronic signature certificate** page, with "Page N of M" footers.
The PDF is assembled entirely in the visitor's browser, so the Worker spends no CPU on it; the Worker
only streams four fixed static files, always through explicit routes (no caller-derived path, no
bypass; `assets.run_worker_first` stays `true`) and with the same security headers as every other
response:

| Route | Asset in `CLA_PUBLIC_AGREEMENT` | Content-Type |
|---|---|---|
| `GET /cla-pdf.js` | `/cla-pdf.js` | `text/javascript` |
| `GET /agreement.pdf` | `/CLA_INDIVIDUAL.pdf` | `application/pdf` |
| `GET /cla-pdf-font.ttf` | `/cla-pdf-font.ttf` | `font/ttf` |
| `GET /cla-pdf-font-OFL.txt` | `/cla-pdf-font-OFL.txt` | `text/plain` |
| `GET /cla-sig-{signature,pen,hand}.ttf` | same path | `font/ttf` |
| `GET /cla-sig-signature-OFL.txt`, `/cla-sig-pen-LICENSE.txt`, `/cla-sig-hand-OFL.txt` | same path | `text/plain` |

`GET /capabilities` gains `signedPdf: true` only while all four assets are actually retrievable from the binding with the expected types (probed on each call, nothing cached); otherwise the key is absent.

A query string, another method or any other path is not served. An asset that is missing, redirected, of
another type or too large gives 503 `pdf_asset_unavailable`.

Preparing the assets (operator step, offline, never at request time):

    npm run render:agreement-pdf   # staging: CLA_INDIVIDUAL.md -> CLA_INDIVIDUAL.pdf (headless Chrome)
    npm run fetch:pdf-font         # the four signature TTFs -> assets/ (commit-pinned URLs + SHA-256; not committed)
    npm run build:pdf-client       # fetches the font if missing, then esbuild bundle of src/pdf -> assets/cla-pdf.js

For production run, on the exact reviewed bytes,

    node scripts/render-agreement-pdf.mjs assets-reviewed/CLA_INDIVIDUAL.md assets-reviewed/CLA_INDIVIDUAL.pdf \
      --title "Tavotto Individual Contributor License Agreement" \
      --published-url https://github.com/Tavotto/Tavotto/blob/main/docs/legal/CLA_INDIVIDUAL.md \
      --attribution "Derived from the Harmony Individual CLA 1.0, © 2011 Project Harmony, CC BY 3.0"

and put `cla-pdf.js`, the font and its licence next to them. Staging passes `--published-url`
(the sandbox repository's copy) and `--banner "STAGING COPY - NO LEGAL EFFECT - cla-staging.tavotto.com"`, drawn at
the top of every page; the staging Markdown is the real agreement preceded by a staging notice, so its digest differs
from the real one and a staging signature can never carry the real agreement's fingerprint.
`--published-url` and `--attribution` are stored in the PDF Subject as `tavotto-agreement-meta:{json}`; the
builder reads them only after the hash check passes, ignores anything malformed or non-https, and lets
values passed in `build()` input win.

**In-place signing (`fields`).** When the Markdown contains the usual `**You (...)**` / `**Us**` signature blocks (a fenced
block of `Signature:` / `Name:` / `Address:` / `Date:` / `GitHub:` lines for the contributor; `Signature:` / `Name:` /
`Title:` / `Address:` / `Date:` for the holder; anything else is rendered as plain Markdown), the renderer prints them as
a form (monospace labels, one underlined line per field, the contributor address on as many lines as the block has) and adds
`fields` to the same Subject JSON: `{contributor:{signature,name,address:[…],date,github},holder:{signature,name,title,address:[…],date}}`,
each box `{page,x,y,w,printed?}` in PDF points (page index from 0, origin bottom-left, `y` = upper edge of the underline,
`printed:true` for a value already in the Markdown, such as the holder's name, which the builder leaves alone). The positions
are read back from the produced file with pdfjs-dist (dev dependency): the underlines Chrome painted and invisible marker words
from a first pass; the shipped file is a second pass without markers whose underlines must be identical (otherwise the
renderer fails). Nothing is computed from CSS. The builder reads `fields` only after the hash check and validates it
strictly (exact key set, integer page index inside the document, finite numbers inside the page, 1 to 3 address lines);
anything else, or any value that cannot fit its line (signature below 12 pt, text or address below 7 pt), leaves the
agreement pages untouched and appends the Signatures page as before. In place, the signature is drawn on the line in the
signature typeface, name, address (wrapped over the lines), long date, `@login` and "Rights holder" in a serif; the last
agreement page's footer says the copy is signed electronically; the certificate page is last and ends with the thanks line. The signing page therefore does not need to know either. The renderer writes the Markdown's SHA-256 into the PDF keywords
(`tavotto-agreement-sha256:<hex>`); the builder refuses to run unless that equals the agreement hash being
signed. This ties the PDF to the pinned text by the renderer's claim, not by re-reading the pages: a PDF
rendered from different text but stamped with the pinned hash would pass, so review the PDF like the
Markdown. The signature typeface is Great Vibes (SIL OFL 1.1, licence committed beside it); the TTF itself is never committed (repository invariant) and is fetched by `scripts/fetch-pdf-font.mjs` from a pinned google/fonts commit and checked against a pinned SHA-256. Tests need network once to obtain it and fail loudly without it (stable codes `pdf_font_network_unreachable`, `pdf_font_http_status`, `pdf_font_hash_mismatch`); they never skip. CI fetches it in its own step, "Fetch signed-PDF font (sha256-pinned)", before `npm run check`. For production, `node scripts/fetch-pdf-font.mjs --dest assets-reviewed`. Text a standard

Signature styles (`src/pdf/styles.ts`). The signing page offers four looks for the typed signature and stores the
choice as `signature_style` in the signed evidence (optional; validated against the same list in `service.ts`):
`signature` Mrs Saint Delafield, `formal` Great Vibes, `pen` Homemade Apple (Apache 2.0), `hand` Patrick Hand —
all but `pen` SIL OFL 1.1, licences committed beside the fonts. Evidence without a style (everything signed before
styles existed) draws in `formal`, exactly as before. The style is only appearance: the typed name is what signs. The
page previews with the same fonts (`font-src 'self'`); the PDF builder fetches only the fonts of the styles in use.
PDF font cannot encode (for example a Chinese name or address) is drawn by the browser onto a canvas at
4x and embedded as an image, so nothing is dropped or replaced; without a canvas the builder fails
instead of printing placeholders. The `devDependencies` pdf-lib, @pdf-lib/fontkit and marked exist only
to build those static files; the Worker still has no production dependencies.

`GET /evidence?packet=…&role=…` now also returns `evidence_sha256`: the stored hash of exactly the
decrypted evidence returned (already verified against it). The certificate prints it as the evidence
fingerprint.

#### Browser interface (for the signing page)

`/cla-pdf.js` defines exactly one global, `globalThis.TavottoClaPdf = { build }` (frozen):

    const bytes = await TavottoClaPdf.build({
      agreement: {kind, version, sha256, publishedUrl?, attribution?},  // from GET /agreement?packet=…; publishedUrl (https) optional: row omitted if absent
      contributor: <JSON of GET /evidence?packet=…&role=contributor>,
      holder:      <JSON of GET /evidence?packet=…&role=holder>,
      contributorSha256?, holderSha256?,   // optional; evidence_sha256 inside the evidence is used first; row omitted if neither
    });                                    // -> Promise<Uint8Array> (a PDF); save with a Blob + download link

`build` fetches `/agreement.pdf` and the font of each party's signature style itself (same origin, `redirect:'error'`) and throws an
`Error` whose `message` is a stable code: `agreement_input_invalid`, `evidence_role_mismatch`,
`evidence_agreement_mismatch` (evidence hash/kind/version differ from `agreement`), `evidence_packet_mismatch`,
`evidence_invalid`, `evidence_attestations_invalid`, `evidence_fingerprint_missing` (a fingerprint was given but malformed or contradictory),
`agreement_pdf_hash_mismatch`, `asset_unavailable`, `canvas_unavailable`, `build_timeout` (the whole build is capped at 20 s). Nothing is drawn or downloaded when
it throws. `attribution` is an optional one-line footer on the Signatures page (for example the Harmony
credit); nothing is printed if it is omitted. Extra `overrides` (`agreementPdf`, `font`, `rasterizer`, `now`,
`serviceHost`) exist for offline tests and sample generation; the signing page does not pass them.
`scripts/sample-signed-pdf.mjs` builds samples in a real headless Chrome with synthetic data, driven in real time over the DevTools protocol (`--runs N` prints per-run results and timings). Do not use `--virtual-time-budget --dump-dom` for this: canvas encoding is untracked by virtual time, so it dumps the page before the build finishes.

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
immutable document hash and holds that still apply to it (a released hold never makes its own packet reusable). That metadata-only response
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

The [offline timing/capacity contract](../../docs/implementation/cla-timing-contract/README.md)
adds a data-only planner for remaining proof lifetime, independent confirmation,
CI attempts and declared recovery load. Its output never grants eligibility or
activation. The persistent confirmation slot has since landed default-off;
independent wakeup and live acceptance remain pending.
