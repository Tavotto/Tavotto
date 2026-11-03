# Default-disabled agreement selection journal

This slice implements an internal database mechanism for selecting a new exact
agreement tuple while retaining old documents and evidence. It provides no HTTP
route, CLI writer, credentials, legal text, real authorization, signature or
deployment. Base: #1161, `0510d055aaf7adec1f6c3592887eb073cbcf03fc`.
The current repository policy and agreement bytes remain unchanged.

`CLA_AGREEMENT_ACTIVATION_ENABLED` defaults to false. JSON configuration alone
cannot enable it. Legacy mode still accepts only the existing 1.0 configuration
and exactly two agreement rows, and now also requires no activation record.
The checked-in deployment template keeps every capability disabled.

## What is bound

The append-only journal records a generation, predecessor hash, activation
revision, repository and holder numeric IDs, reference SHA, canonical agreement
and exemption policy material, both kind/version/digest tuples, and the supplied
document source SHAs. Its canonical body and SHA-256 are checked on preparation,
application and runtime readiness. A separately reviewed config must pin the
latest exact activation hash; a pin is not a signature or legal approval.

This identity binding does not additionally authenticate a legal name,
repository display string or provider configuration. Source SHAs are explicitly
caller-supplied pins. Their shape and binding are checked; this internal API
does not independently fetch or attest to Git provenance. The current provider
and capability flags retain their own validation and authorization boundaries.

`prepareAgreementActivation` copies its inputs before awaits, validates bounded
stable numeric versions, exactly two documents, their exact UTF-8 content hashes
and one matching version marker each, then returns a canonical proposal. Draft
versions and reused versions with different bytes cannot be installed. The
mechanism preserves the stricter append-only version/bytes discipline; the
conflicting hash-only-edit allowance in `CLA_VERSIONING.md` still requires a
separate policy decision before any real terms change.

## Atomic application and readiness

`applyAgreementActivation` copies and rebuilds the entire proposal rather than
treating a mutable JavaScript object as authority. One D1-shaped batch checks
the original holder/predecessor, appends missing registry/document rows, consumes
the expected revision, checks the final documents and inserts the journal entry.
Existing revision triggers invalidate observations and enqueue durable recovery.
Late failure rolls back the registry, documents, revision and triggered changes.

The first selection must retain a real third agreement row. A source-only first
activation cannot fake that row or overwrite a version. This keeps the original
binary's `count=2` readiness false after activation as well as rejecting current
flag-off and stale configurations. Later source-only selections may retain the
same exact bytes; their observed source pins stay separate from the original
archived document source, which is never overwritten.

Current readiness checks the latest pin, IDs, reference, policy, tuples, database
time and activation revision, then verifies the full body/hash from the same
existing SELECT. It requires current revision **at least** the activation
revision, so subsequent legitimate signatures, holds and revocations remain
usable. It does not add a query to normal readiness/revision checks. A first
successful application consumes 13 metered SQL statements and no external call;
the existing work/total budgets are unchanged.

The same current proposal is an observed idempotent result, not another write.
Only the latest exact proposal can return `already_active`; a superseded replay
rejects. Identical concurrent callers can observe the one committed result;
different proposals cannot consume the same predecessor. Catching a write error
performs a read-only confirmation and never retries the writes.

Final packet creation, intent and signing transactions check current readiness
and the packet's own current tuple. A service that read before activation cannot
commit stale consent afterward. Historical holds remain possible through a
ready current service. Existing signatures and delegated receipts stay bound
to their original bytes; no signature or grant migrates to a new version.
The contributor must personally sign the new tuple. A holder may personally
complete it, or a future separately approved exact new grant may authorize the
system route. Historical signed revocation remains independently available.

## Local evidence and limits

[The evidence manifest](evidence/verification.json) records real Node/SQLite/Web Crypto execution, source
fingerprints, concurrent and late-rollback checks, and uniquely located source
mutations that must produce an actual AssertionError and exit 1. The complete
suite has 399 Node passes, four migration passes and 93 detected mutations,
including 30 new cases. Independent implementation review has 17 passes and
five failing reverse mutants; the separate formal-test delivery has 29 passes
and 19 failing reverse mutants. All identity,
document, key, grant and signature inputs are explicitly synthetic. Independent
review and local execution do not establish legal effect or hosted acceptance.
No publication or successful-publication transition across expiry is tested.

The frozen v1 snapshot schemas remain unchanged. The local migration rehearsal
creates the updated separate native namespace with an empty selection journal;
it does not activate an agreement. This PR does not deliver or execute an
in-place production v2 schema upgrade. An existing v2 database needs separately
reviewed additive DDL, backup/single-authority checks and real D1 transaction
acceptance before this binary can be enabled. Missing schema fails closed.
The legacy bootstrap refuses activation mode and any existing journal; it is
not a migration or a way to enable a prepared proposal.

Local tsc/esbuild remain unavailable, and the earlier dependency-download E403
is preserved without retry, proxy, mirror or environment changes. Current-head
hosted type/build and formal external review remain pending. No workflow,
permission, signing gate or production resource is changed here.

Later operator decisions include approved exact texts and versioning policy,
verified holder/key registration, explicit limited grants and controls, custody
and schema acceptance, plus actual platform validation. Independent confirmation
slots/wakeup, native merge-group and trusted-main CI wiring remain other
engineering work. Neither a prepared selection nor chat instructions approve
these decisions or consent to CLA terms.
