# Native CLA offline lifecycle acceptance

This change validates the default-disabled native PR lifecycle and completes the
native merge-group publication/authority/CI code path. The group path reuses the
existing whole-prefix evaluator, target/outbox, publication journal, leases and
global confirmation slot. Schema, migrations, deployment configuration, workflow,
permissions and legal text are unchanged. It does not authorize deployment, real
OAuth setup, evidence migration, provider/CI activation or legal signing.

## What the tests execute

`services/cla-cloudflare/tests/publication-lifecycle.test.mjs` drives the actual
HMAC admission, RSA installation authentication, native GitHub adapter, evidence
qualification, D1-shaped SQLite journal, background reconciler, authority
verification and CI consumer. Each invocation has a fresh shared budget and
adapter. The peer accepts only exact synthetic paths and credentials, checks
that a positive mutation follows its committed issued journal and held slot,
and maintains Check Run state across invocations. Nothing calls real GitHub.

Both personal holder and delegated evidence complete the same lifecycle:

1. Admit a head and issue one successful synthetic POST. Its response leaves
   `reconcile`, which cannot pass the authority or CI.
2. Independently observe and qualify the result, then perform both CI authority
   reads. Confirmation retains the slot.
3. Reach the original proof's exact expiry. CI rejects; recovery withdraws the
   original success and independently GETs the exact failure before release.
4. A later invocation creates a new proof/attempt, PATCHes the same owned run,
   independently confirms it, then passes CI. Old proof bytes/expiry never change.

Additional scenarios cover lost applied responses without POST replay, late
wakeup, missing unknown owners and persistent manual backoff, failed post-PATCH
observation without release, installation-token expiry before/after the journal,
authorization/proof/lease boundaries, and expiry during the second CI read.
An invocation beyond cleanup cannot write even its failure state; the next fresh
invocation reclaims its expired head as manual, without a publication journal.
Tests separately move DB time without elapsed monotonic time to exercise SQL
boundary guards; neither clock model is a real scheduling guarantee.

`oauth-lifecycle.test.mjs` exercises the native HTTP start/callback, real adapter
PKCE exchange and SQLite state/session operations against an intercepted peer.
It covers successful JSON/browser redirects and secure cookies, exact expiry,
wrong-browser binding, replay, consumed-state exchange failure, extra token
authority rejection and session expiration. All identities, secrets, keys,
agreements and signature evidence in these tests are synthetic.

**OAuth's existing contract is consume-time expiry.** State must satisfy
`expires > database_now` at its atomic single-use consumption. A callback
consumed at +299 seconds may finish exchange at +301; the session expiry is the
subsequent DB-clock sample +900 seconds. There is no 300-second callback-completion
deadline. The tests preserve this meaning. Failed exchange consumes the state;
the user must initiate a new login. A different completion-deadline policy needs
an explicit decision and implementation, not a change hidden in acceptance tests.

`group-publication-lifecycle.test.mjs` covers legal group admission through the
actual adapter, whole-prefix qualification, journal/send, independent confirmation,
authority query and both native CI reads. Group proofs bind the repository,
exact candidate head/base, all prefix members/heads, policy/revision, trusted main
source, complete queue/prefix hashes and a fixed authorization-capped lifetime.
The assessment result remains read-only; it is not a publication proof.

Group `checks_requested` admission authenticates HMAC and repository/installation,
validates main/head/base and commits bounded, deduplicated delivery/target rows
atomically. The existing outbox schedules evaluation. Token and remote-run
pre-reads finish before fresh complete-prefix evaluation and the durable send
intent. An unknown send is never replayed. Confirmation, expiry/revocation
withdrawal, exact failure GET/private negative witness, held-owner drain and
same-run fresh attempts reuse the existing journal and global slot. A group
cannot enter the PR proof/journal/send path. PR proof parsing remains strict.

The 38 group scenarios include partial/replaced prefixes, unsigned predecessors,
member/base/provenance tampering, exact expiry during the second CI authority
read, revoked evidence, stale lease generation, duplicate/conflicting delivery,
concurrent claims, PR/group slot competition, future revocation revisions, durable
refresh backoff and missing
unknown owners. `group-publication-boundaries.test.mjs` retains read-only assessment
and forged/malformed group rejection checks. The CI consumer handles a strict
group locator and two independent group authority reads; no workflow is activated.

Budgets stay at 20 work / 40 total SQL and 35 work / 40 total external requests.
Group reconciliation has one more target-history read than PR reconciliation.
Its pre-send admission reserves 17 fixed/withdrawal SQL plus qualification cost
(2 per personal or 3 per delegated non-exempt contributor): at present only one
non-exempt contributor can be admitted to automatic group success. This does not
truncate a multi-PR prefix or exempt another contributor. A larger eligible
prefix or two personally qualified contributors can be refused as BudgetDeferred
before any journal/send; eligibility and resource admission remain distinct.
The offline two-member, one-contributor success case fits; the three-member
external-budget counterexample rejects. Platform CPU is not measured by these tests.

## Reproduction and counterexamples

From `services/cla-cloudflare`, use Node 24 and Python 3.10+:

```sh
node --test tests/*.test.mjs
python3 tests/migration.test.py
node tests/mutation-smoke.mjs
npm run typecheck
npm run build
```

Typecheck/build require the existing pinned development dependencies. The normal
CLA offline workflow installs the unchanged lockfile with `npm ci --ignore-scripts`
and runs the complete `npm run check`; no extra workflow, token or deployment is
needed. Report missing tools as missing, never as compiler/build success.

The mutation harness adds 14 group counterexamples and retains the earlier 11
lifecycle counterexamples in `tests/lifecycle-acceptance-mutation-cases.json`. They test
premature confirmation, failure to withdraw an expired success, treating a PATCH
receipt as a release witness, losing durable backoff, omitting the second live
authority read, and weakened OAuth/group boundaries. The callback-expiry mutant
adds an unauthorized post-exchange state-expiry check. Each selected case must
actually run, exit exactly 1 with AssertionError, and be detected by the harness.
The earlier PR-only group-discriminator mutant is updated to the complete group
contract and its positive regression; all other prior mutation cases remain.
These are disposable source mutations, not changes to production behavior.

## Isolated platform acceptance plan — inactive

Execute only after separate approval of the exact staging resources, existing
minimal permissions, configuration and costs. Do not change production policy,
keys, routes, bindings or flags to run this plan. Use a disposable test repository,
synthetic documents/data and an explicitly approved staging authority. No real
legal signature or production evidence is required for this phase.

| Step | Concrete execution and retained evidence | Pass condition |
| --- | --- | --- |
| Pin the subject | Record reviewed commit, production bundle digest, deployed Worker version, configuration digest, compatibility date, account plan/limits and scenario ID. Validate actual Wrangler schema, assets and bindings before an authorized upload. | Every measurement maps to the exact tested source/configuration. An old probe or aggregate across versions is not current-main evidence. |
| D1 fresh/upgrade | Inventory the actual staging schema, immutable history, system tables and writer/queue state. Choose fresh seed, v1 snapshot mapping, or the exact supported v2 profile; run the disabled local planner first. Independently review a D1 execution adapter using ordered atomic batches, not SQLite BEGIN/COMMIT pasted into D1. Rehearse each of the 31 additive DDL fault points, late guard failure, competing callers, lost commit receipt and close/reopen on disposable synthetic fixtures. | Entire transaction rolls back on failure; rows/rowids/ciphertext, holds and revision remain exact; historical send fences remain zero; new capability tables remain empty. Unknown/partial schemas stop without repair. A consistent backup and all old writers/queues stopped are independently evidenced. |
| CPU and resource use | For default-off/denied HTTP, pinned agreement, OAuth start/callback, personal/delegated qualification, 2/3-member prefix reads, dispatch and recovery, retain cold/warm invocation CPU and wall-time logs plus per-scenario quantiles, outcomes, SQL/HTTP counts and D1 rows read/written. Include maximum admitted payload/evidence/control chains. Use platform CPU fields and confirm units; do not substitute Node elapsed time or request counts. | Meet the explicitly accepted plan's CPU, quota and latency limits with headroom, or fail activation. If targeting Free's 10ms HTTP/cron CPU budget, measurements must support that target; no automatic plan/limit increase. |
| Real OAuth | With an independently approved existing test App, check exact registered callback, S256 verifier, secure browser cookies, correct immutable user ID, consumed/replayed/wrong-browser/expired state and rejected/revoked provider codes. Verify token-exchange outage requires fresh login and no token or evidence appears in logs. | The real provider/browser satisfies the documented consume-time contract and rejection cases; extra scopes/identity mismatches fail. This does not establish legal identity. |
| Wakeup/recovery/CI | Run the lifecycle above using only synthetic prevalidated fixtures in staging. Record actual enqueue/due, invocation start, issued commit, HTTP send/response loss, independent confirmation, expiry withdrawal/failure GET, new attempt and both CI authority reads; inject restart and delayed ticks. | Unknown is never resent, positive acknowledgement never releases, and stale proof never passes. For a claimed timely success, measured scheduling + dispatch + independent confirmation + CI must fit the remaining proof lifetime. A two-minute proposal alone cannot guarantee this for a 120-second proof. |
| Native group closure | Review the now-implemented default-off group journal/proof/authority/recovery/CI code and offline positive/negative cases. Independently verify real queue schema and minimal read authorization using the approved credential; do not add permissions to make a synthetic response work. | No PR proof qualifies a queue head; all prefix members, exact candidate/base and fresh facts are bound. Preserve the current required gates and normal Merge Queue during a separate reviewed wiring rollout. |

Do not claim live D1 atomicity, real OAuth compatibility, Workers CPU acceptance
or timely background scheduling from the offline suite. A slot with no exact
remote result can remain held; timeout, 404 or operator assertion is not a release
witness. After real signatures/holds/controls, a stale snapshot is not a safe
rollback. Deployment, provider/CI activation and actual signing/limited holder
authority remain separate explicit authorizations.
