# Historical authorization termination candidate

An authorization registered under an earlier ID/key cannot currently be revoked
by `Delegation.append`: that path intentionally requires today's ID, key and
agreement. This independent slice adds `appendHistoricalRevocation(id, record)`.
It is an internal preparation API with no HTTP route, CLI, provisioning, signing
or activation hook. Every deployed capability remains default disabled.

The caller supplies only the exact registered authorization ID and an already
signed canonical control. The implementation verifies the immutable registered
public key, original grant signature/hash/identity, exact historical individual
agreement tuple and the entire bounded control chain. The new control must be
`revoke` signed by the original registered key. Current holder ID/name and
repository boundaries still apply. It cannot enable or suspend historical
authority, retarget a receipt, replace a contributor's consent, or establish
identity/legal effect.

Terminal revocation remains possible while signing/delegation is disabled and
after the grant expires or before its start. Readiness, revision CAS, predecessor
and commit-time database clock checks remain mandatory. One transaction appends
the control and advances revision; existing triggers invalidate observations and
enqueue recovery. Failed writes roll back those changes. No old signature,
encrypted receipt, agreement, send fence or manual failure is rewritten.
Ordinary current-grant execution and independent personal holder recovery retain
their existing rules.

## Evidence and precise limits

Base: `d2b0ba59340761b61f7ac5b8b534d43bcdd731f4` (#1151). This is a sibling of
#1160; it does not contain the CI-wait change. Latest main observed through the
GitHub connector was `78a12af161cd95d02aeb62c7ab23780a9a57bc83`; its eight commits
after the shared base did not change CLA paths. Neither parent branch was changed.

[Command exits, source hashes and logs](evidence/verification.json) record real
Node 24 / in-memory SQLite / Web Crypto execution at the initially reviewed head: 358 tests, four migration tests,
and 62 detected mutations (eight new unique source locations each require an
actual AssertionError and exit 1). The new operation consumes 11 SQL statements
and zero external calls in a metered synthetic fixture, within unchanged limits.
All keys, grants, signatures and documents in tests are visibly synthetic.
There is no publication, HTTP peer, live platform or dynamic successful
publication-across-expiry test.

ID/key rotation is tested with production-style `LiveStore(..., config)` readiness.
The additional old-version tuple case is explicitly an unpinned registry unit
fixture: production still rejects a third agreement row and versions other than
1.0. It is **not** a production agreement migration or activation test.

The first review covered exact head `6d8809f03150b762cfafeacdaf89af6c9c9bbc05` after
[hosted typecheck, build and all offline checks succeeded](https://github.com/Tavotto/Tavotto/actions/runs/39112930285/job/117632693576).
Security review reported no findings; code review found P2 audit-state precedence.
Terminal signed `revoke` now remains visible as `revoked` through disabled, rotated,
held and expired current callers. It always stays `current=false`; hold status and
independent personal holder recovery keep their separate eligibility checks.

[The P2 evidence](evidence/p2-audit-priority/verification.json) records 363 Node
tests, four migration tests and 63 detected mutations, all exit 0. The independent
five-case reproduction exits 1 on the reviewed source and 0 on the actual fix;
two reverse mutations each fail with AssertionError / exit 1. Original raw failure
text and hashes are preserved alongside whitespace-normalized display logs.

The local typecheck still exits 127 (tsc absent) and build exits 1 (esbuild absent).
No dependency download, mirror, proxy, credential replacement or alternate
environment was attempted. The prior hosted success does not validate a new fix
head. New-head hosted verification and the one permitted targeted followup remain
pending; no repeat review is requested before deterministic prerequisites pass.
The operator may use the existing manual CLA workflow; this connector exposes no
dispatch action. No workflow, permission, branch protection or live gate is changed.

The 100-control ceiling remains. A signed revoke may occupy slot 100; an already
full chain cannot append slot 101 and must clearly reject without claiming
success. Unavailable original private keys, changed holder/repository identity,
unready storage and corrupt historical records also reject. This slice cannot
solve those recovery cases or infer legal cancellation of an existing contract.

## Remaining engineering and decisions

- Agreement migration needs an immutable active-selection journal and an atomic
  append/archive/activate/revision transaction, including final packet/intent
  guards. Removing the two-row readiness count alone would let stale configs
  remain ready. Existing bytes and consent must remain immutable. The legal
  versioning document also permits hash-only non-material edits while prohibiting
  version reuse elsewhere; that policy conflict needs a decision before changing
  real terms. This change preserves the stricter existing behavior.
- Timing needs admission against actual remaining proof/authorization lifetime,
  a durable independent confirmation wake-up/slot and unchanged phase budgets.
  Current 15-minute automatic / two-minute recovery proposals and a 120-second
  proof cannot guarantee confirmation or the 60-second CI wait envelope. Real
  wake-up latency, queue load, throughput and platform resource acceptance remain
  unverified; simply changing cron would not establish those guarantees.
- Native merge-group support is absent across snapshot, proof, journal, recovery,
  live authority and CI. Exact group membership versus the old Python service's
  conservative queue prefix is an explicit queue-policy decision. Extra queue
  API permissions and real schema acceptance are not authorized here. Keep
  rejecting groups until their own bindings/lifecycle are implemented and reviewed.
- Reviewed source must land on trusted main before a separate CI wiring change.
  No merge or wiring is performed here. Real identity/key registration, narrowly
  signed grants/enable controls, resource/custody approval and live acceptance
  are later operator actions. Chat engineering instructions are never consent.
