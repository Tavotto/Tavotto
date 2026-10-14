# Cloudflare CLA lifecycle candidate

An isolated Workers/D1 TypeScript candidate. Default off; not deployed, not connected to a real GitHub App, and not approved for real data migration or legal signing. The existing Python service in ../cla and the repository's authoritative agreement texts and .github/cla-policy.json are unchanged. Only one service may ever be the signing authority.

## Reproduce locally

Use Node 24 and Python 3.10+ with sqlite3. From this directory:

    npm ci --ignore-scripts
    npm run check

The exact development versions and integrity hashes are in package-lock.json. There are no production npm dependencies. check runs TypeScript checking, Node tests, local SQLite migration tests, an esbuild production-import check and isolated mutation checks. The build writes only ignored dist/ files; there is no deployment script or resource binding configuration.

Tests use synthetic identities, ephemeral test encryption keys, a test-only database clock and local SQLite. Outbound fetch and publication are forbidden. The concurrency test uses two worker threads with separate WAL connections and real BEGIN IMMEDIATE transactions; it is not a Cloudflare D1 concurrency claim. Test databases are removed afterward.

## Lifecycle contract

Seven days limits completion of a pending packet. Valid, independently completed signatures remain technically eligible for the same agreement, subject to policy and hold checks. Every qualification decrypts and verifies evidence, immutable packet/actor/document binding and original signing timestamps. A holder timestamp cannot precede the contributor. Proof lifetime remains separately 120 seconds.

Expired incomplete packets remain immutable history. A new packet requires fresh independent signatures; no old signatures or intents are transferred. Creation is transactional. A hold on any packet blocks the subject across all packets and digests, with no automatic release mechanism. A hold blocks automated qualification; it does not purport to revoke or amend a legal agreement.

Both parties must personally sign. This is workflow automation, not automatic rights-holder countersigning. GitHub account authentication does not establish legal identity, ownership or authority. Corporate/guardian/unresolved-authority cases remain outside this automated route.

The native entry is src/entry.ts. Each capability requires its explicit environment flag; omitted flags keep it off. No credentials, account bindings, worker IDs, queues or resource names are shipped. Agreement version 1.0 remains the only supported configuration; changing the same version's digest fails closed. A future version rollout needs separate review.

## Offline migration rehearsal

migrations/migrate-local-snapshot.py reads a canonical local v1 snapshot and creates a different local output file. It refuses unknown schemas/triggers or an existing output/v2 namespace. It creates separate cla_native_v2_* tables, retains v1, copies every declared column and rowid, and checks row equality and foreign keys. Rowid preservation matters to publication reconciliation cursors.

This is not a D1 migration command or authorization to read production evidence. source_opened_read_only means this tool does not write its input; it does not establish that external writers are absent. Copied signature rows are not automatically trusted: the service still validates cryptographic evidence.

## Remaining activation gates

- Verify actual D1 schema, authorized key custody, encrypted evidence and a coherent backup. Unknown historical mappings stop migration.
- Freeze all v1 writers and queues before an individually reviewed single-authority cutover. Never run v1/v2 writers concurrently. After v2 accepts a signature or hold, returning to a stale v1 snapshot is not a valid rollback.
- Integrate the production native entry, background processing and the existing trusted CI contract. Prior synthetic queue executors have not been accepted against this lifecycle change.
- Separately approve App/OAuth setup, secrets, publication and real-person signing acceptance. This PR does not enable the provider, deploy, create infrastructure, or merge itself.

## Validation boundary

The offline regression covers 110 Node cases, 3 SQLite migration cases and 8 isolated mutation checks, including wrong/invalid encryption keys, tampered evidence, expired intents, role/order checks, exact deadline boundaries, completed qualification after eight days and subject-wide holds. Re-run the commands above for the checkout under review. Local passes do not establish legal sufficiency or live GitHub/Cloudflare behavior.


The dedicated path-filtered PR workflow uses a GitHub-hosted Ubuntu runner, Node 24 and Python 3.12, with contents:read only, no secrets, no deployment and no dependency cache. It runs npm ci --ignore-scripts and npm run check. It adds no required context and does not change the existing aggregate gates. Local and CI passes do not establish live free-tier acceptance.

Installation tokens are treated as opaque; the adapter does not inspect a legacy prefix or JWT structure. It accepts 520-character stateless tokens while retaining repository, permission and expiration checks. Its separate 8192-byte visible-ASCII/no-whitespace header bound is a service resource-safety limit, not an assertion about GitHub's token format. No token is logged. See https://github.blog/changelog/2026-10-02-stateless-github-app-installation-tokens-rolled-out/.

The signing UI permits an expired incomplete, unblocked contributor packet to be replaced without transferring consent. Holder history uses explicitly requested, authorized packet-ID keyset pages (20 results plus one lookahead), ordered by creation time and ID. Every cursor and page is reauthorized; pagination never grants access to another contributor.

Cached packet selections are authorized again for the current account. Inaccessible selections are cleared from the URL and session storage; account changes also clear prior personal fields and consent, even when both accounts can view the same packet.

## Same-head background refresh candidate

The withdrawal reconciler and fresh publication execute in separate scheduled invocations. After every earlier publication for a tracked target is durably resolved, a refresh decision is deduplicated by its source attempt and evidence revision. A fresh evaluation that is still ineligible finishes without a write; subsequent evidence revisions can request another evaluation. Manual or uncertain publication records block the entire target.

Refresh is limited to the same head and an existing, exactly identified owned check run. It does not create runs for new heads or automatically handle a push; the explicit rights-holder check entry remains necessary for those cases. Before refreshing, the adapter verifies the predecessor run, attempt, app, name and recorded failure. After authentication and remote pre-reads, it rereads the current binding and commits a new attempt with a new 120-second proof under lease, generation and revision guards. Earlier proof text and expiry remain unchanged. Only one PATCH is attempted. Its response leaves the new attempt awaiting reconciliation; it is not immediately confirmed. A later invocation must independently observe and revalidate it. Unknown outcomes are observed or escalated for manual review, never replayed.

Resource limits remain 20 work SQL statements / 40 total and 35 work external calls / 40 total per invocation. Dispatch needs 12 + 2N work SQL statements, where N is the number of non-exempt contributors. Confirmation needs 14 + 2N, plus two reserved reads if withdrawal becomes necessary. Consequently this initial automatic refresh path admits at most two non-exempt contributors. More contributors or complex remote reads can exhaust the unchanged budget: no fresh publication is sent, retries are bounded to three with 60-second backoff, then manual review is required. This is a resource/capability boundary, not a negative legal or eligibility determination. There is no automatic clearing of manual states.

Dynamic success-publication-across-expiry integration validation is not part of this candidate's passing evidence. New tests are restricted to offline rejection, budget, durable deduplication and uncertainty handling with failed proposals; they do not establish successful end-to-end refresh behavior. Live GitHub/Cloudflare acceptance and the excluded integration scenario remain activation gates.
