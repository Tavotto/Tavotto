# Default-disabled durable confirmation slot

This slice adds one global positive-publication slot to the existing native D1
journal, outbox and recovery paths. Stack parent: #1167,
`a4695f830d321ffa03184595afd895d2055db3b3`. It creates no real grant, signature,
credential, route, Queue, cron, App permission or production resource.
`CLA_CONFIRMATION_SLOT_ENABLED` defaults to false and requires background
recovery and check publishing when true. The disabled deployment plan retains
all flags false. Contributor personal signing and the independent personal
holder signature route retain their existing requirements.

## Admission and permanent latch

A positive automatic new-head or same-head attempt inserts an exact one-use
intent marker and its original `issued` journal in the same batch as its
existing head/request state and durable recovery signal. The journal trigger
consumes the marker only after establishing the singleton `slot_policy` latch
and its `active_attempt` pointer. Transaction failure rolls back the marker,
slot, journal and request together. The candidate keeps existing publication
columns and migration defaults unchanged. `confirmation_slot` is a projection
of that pointer, never an independently writable row flag.

The first enrollment requires no prior unresolved positive lifecycle. It is a
one-way data protocol change: clearing a runtime flag or starting an old binary
cannot reopen legacy positive admission after the latch exists. Its original
identity and first attempt cannot be changed or deleted. `REPLACE` is fenced
before its implicit delete, including SQLite configurations that skip recursive
DELETE triggers. No second owner can be admitted while the pointer is held.
Even `confirmed`, expired, manual or uncertain owners retain capacity.

The same-head owner cannot be shadowed by another journal. A distinct-head
negative journal also cannot replace an issued/unknown owner through the
existing unresolved-target unique index. Held and released history is protected
against identity changes, deletion and replacement. Release observations are
permanent canonical facts. Ordinary legacy observations remain plain strings.
These guards protect application protocol transitions; an administrator with
arbitrary DDL access remains a trusted operator, not a cryptographic adversary.

The holder-triggered positive publishing path lacks the shared measured budget
and send fence, so it refuses slot mode. This does not remove personal signing.
The supported positive dispatch paths retain one HTTP attempt and their existing
unknown/no-replay handling. This slice supplies no alternate sending route.

## Release and recovery

A slot is released only by an independent fresh GET showing the exact App,
check name, original attempt/head and owned run as completed failure. A list
response, positive confirmation, PATCH response, timeout, expired proof,
exception, missing run or plain caller JSON cannot release it. The adapter
mints an instance-private, opaque, single-use witness after the GET; it is bound
to the shared invocation budget, original proof tuple/run/phase, repository,
current revision, outbox lease and generation. Its lifetime is strictly less
than ten seconds and the remaining lease. Inputs are copied before awaits.

The guarded settlement consumes the witness once and atomically persists its
canonical facts in `observation`, then clears the singleton pointer. SQLite
checks the old tuple and observed phase before update, then checks the resulting
resolved row and live current lease/revision before clearing the pointer.
Deadline comparisons are strict. If SQL fails or its result is uncertain, the
caller must obtain a new independent observation; it cannot reuse the witness
or replay the original positive send. Process interruption leaves the pointer
held. There is no timeout-based or manual capacity-clear endpoint.

A live send fence blocks recovery in every owner phase, including revision or
generation changes. New-head attempts preserve the existing 120-second claim
fence; same-head attempts now retain their recovery lease fence as well. Normal
sender settlement may clear it only from issued to unknown/reconcile. A binary
that attempts to mark a held row resolved without fresh negative facts is
rejected. A compatible binary with the runtime slot flag false still projects
and selects held owners for recovery. Manual rows retain the manual boundary;
operators must inspect evidence and restore the existing recovery path rather
than delete the latch or grant capacity by assertion.

Expired/revision-stale positive withdrawals take precedence among claimable
outbox jobs and within the existing bounded journal pass. A still-live fence or
lease is never bypassed for priority. The pass horizon stays bounded. Missing
runs and infrastructure exhaustion retain manual recovery and capacity; this
mechanism makes no unconditional fairness or completion-time guarantee.

## Backpressure and cost

A busy slot leaves new-head requests pending without claiming them. A race lost
after claim may return a still-live, journal-free request to pending; expired or
already-issued requests cannot be reset. Same-head capacity waits preserve the
refresh attempt count and defer the durable outbox; genuine infrastructure
failures retain the existing bounded retry/manual rules. Negative recovery is
independent of positive admission.

Slot enrollment adds one outer SQL statement to an eligible issued batch.
Confirmation/release adds no separate outer SQL release statement; trigger
operations still perform real database work and are not assumed to be free.
The unchanged limits are 20 work / 40 total SQL and 35 work / 40 total external
requests. Evaluation reserves the extra statement. The existing conservative
confirmation bound `16 + qualificationSQL * people <= 20` still admits one
non-exempt person in delegated mode. See the evidence manifest for actual
fixed-synthetic stage measurements; those measurements do not establish real
D1 charging, CPU/latency or scheduling acceptance.

## Verification and outstanding acceptance

The final full Node suite passes 456 tests; migration passes four tests; the
mutation harness detects 124 cases (31 new unique-location AssertionError
counter-mutations). Independent contract tests pass 39 cases with 22 counter-mutations; budget
and recovery tests pass 14 cases with six counter-mutations. The configuration
boundary adds four cases and three counter-mutations. Original independent logs
are preserved byte-for-byte as JSON strings in each `raw-logs.json`, alongside
their original SHA-256 values; the encoding avoids changing assertion output
whitespace. Original log names in independent manifests refer to those members.

The new suites use fixed synthetic SQLite/D1 data, local transaction attempts,
negative/unknown settlement and strictly GET-only transport. They execute no
successful publication, Worker endpoint or publication across proof expiry.
The existing baseline suite is recorded separately. Independent reverse review
and uniquely located counter-mutations are recorded with exit codes in
[evidence/verification.json](evidence/verification.json).

The frozen deployed schemas and migration default whitelist remain unchanged.
Local snapshot rehearsal creates an empty latch in the separate native
namespace. This is not an in-place v2 production upgrade. Missing new schema
fails closed. A reviewed additive upgrade, backup/single-authority checks and
real D1 transactional/trigger acceptance remain required. Switching back to an
old binary after enrollment is not an availability rollback: it cannot safely
resume positive publishing. Use the compatible recovery path with positive
admission disabled and retain the latch/history.

Local dependencies remain unavailable after the earlier npm E403. Downloads,
proxies, mirrors and environment changes were not retried. Current-head hosted
type/build, full CI and any permitted formal external review remain pending.
This candidate is neither deployed nor enabled and establishes no legal effect.

Remaining code work includes native merge-group support, trusted-main CI wiring,
a reviewed production v2 upgrade and deterministic wakeup/confirmation deadline
execution beyond the offline timing model. Operator decisions include exact
texts/version policy, holder identity/key custody, signed limited authority and
controls, resource acceptance and recovery operations. In #1167 an explicit new
generation/pin may select a historical exact agreement tuple; that tuple's old
evidence can qualify again. Only replay of an old prepared proposal is refused;
monotonic agreement-version selection requires a separate product decision.

## Current-head CI correction: TS2700

The manual job for original head `dc2431f725798bd78c40a3b07e0a8054690fe82c`
(run 39126012271, job 117671183649) successfully installed dependencies, then
failed typechecking at store.ts:153 with TS2700. Its `&&` chain did not run Node,
migration, build or mutation checks. The child #1169 original head failed for
the same shared type. This is a real compilation failure, not missing CI tools.

The database CASE returns a numeric `capacity_blocked`, while the business
RefreshRequest uses an optional boolean. Intersecting both without omitting the
old property collapses the row type; the fix explicitly omits that property
before declaring the numeric raw row. No any or compiler suppression is used.
Node's built-in type erasure produces byte-identical JavaScript before/after.
Actual local 456 Node / 4 migration / 124 mutation checks pass; six independent
row-boundary/erasure probes pass. These results do not establish compiler
success: current-fix hosted typecheck/build remain pending, and local tsc is
still unavailable without retrying the earlier rejected download.

[The correction evidence](evidence/ts2700/verification.json) retains raw old-head
CI logs and separates original failed runs from current-fix local verification.
Earlier evidence manifests remain historical records of their pinned sources.
No review is triggered until the correct new head satisfies verification.
