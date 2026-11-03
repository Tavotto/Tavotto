# Offline timing and capacity contract

This slice addresses the missing admission model between a new-head observation,
one publication, independent confirmation and CI consumption. It adds only a pure
offline planner, bounded local-file CLI, synthetic example and tests. It does not
change the Worker, schema, runtime admission, timers, publication/recovery state
machine, provider, legal text, flags, permissions or workflows.

Base: #1160, `830bd89cc905d28b41f690c7aa6c0236a06aec84`. This change does not
include the sibling historical-revocation PR #1161. All assumptions and proof
inputs are explicitly unverified; a feasible model is never an eligibility,
activation, throughput, legal-effect or live-platform claim. Output always has
`execution_supported`, `runtime_eligibility`, `ready_to_deploy` and
`live_accepted` false.

## What the planner checks

`scripts/timing-plan.mjs` reuses the existing `parseProof`, CI `waiting` validator,
`FREE_BUDGET` and qualification accounting. It validates a currently parseable
PR proof as data, uses its original expiry minus the provided dispatch time, and
rejects a proof exceeding the declared authorization ceiling. It neither mints
nor confirms a proof. The canonical assumptions and output have SHA-256 digests
so a later review can identify exactly which model was evaluated.

For the conservative one-slot/at-most-one-non-exempt-person model:

`send/settle + independent wakeup + backlog + withdrawals ahead + confirmation + final CI reads + margin < remaining proof lifetime`.

Unknown wakeup/backlog/external bounds refuse. The model separately reserves
withdrawal work in a declared load window, refuses an occupied slot, counts
coordination SQL overhead, and preserves the existing work/total SQL, external
request and invocation-time ceilings. Fixed source accounting is 13+qN dispatch
work plus four settlement statements; confirmation is 14+qN plus two reserved
withdrawal reads. These are audited candidate-path counts, not a measurement of
an implemented slot/wakeup adapter. Such an adapter must provide its additional
costs and actual stage measurements before use. The load window models recovery
time only. It does not model the verification limiter's per-location request
rate, all CI authority reads, public signing traffic or sustained system-wide
throughput. `model_feasible` applies only to the explicitly modeled assumptions;
limiter behavior and total traffic require their own live/capacity acceptance.

CI timeouts and attempt counts are separate. Pending reads might complete
immediately, so readiness must strictly precede the earliest final poll. The
latest bounded CI finish includes every possible pending read plus interval,
final reads and safety margin; it must fit both the CI timeout and the original
proof lifetime. This is deliberately conservative, including refusal at exact
boundaries. It does not assume the 60-second timeout guarantees 60 seconds of
polling or that every 120-second proof has 120 seconds left at dispatch.

```sh
cd services/cla-cloudflare
node scripts/timing-plan.mjs planning/timing.disabled.synthetic.json
```

The committed example is visibly synthetic and refuses (exit 2). Exit 0 means
only the supplied model fits; exit 1 means invalid input, and exit 2 is a valid
but refused model. The CLI only reads one bounded regular file and prints data;
it has no network, D1, Wrangler, signing, credential, dispatch or deployment
operation. Current 15-minute automatic / two-minute recovery proposals cannot
establish the required proof/CI envelope. Unknown platform tail latency and
backlog also remain unknown, rather than invented as zero.

## Unimplemented runtime contract

A future adapter must reserve a confirmation slot atomically with the existing
issued/head-dispatched/outbox transaction, bound to target/head/revision/attempt.
It must wake a separate invocation after settlement, preserve the sender fence,
freshly observe/requalify, and keep the durable outbox as the authority when hints
are lost. Duplicate hints must not resend, advance manual failures, transfer a
proof or shorten a live sender fence. Withdrawal fairness, actual resource costs,
delivery tails, D1 concurrency and trusted-main CI remain separately unaccepted.
No local model proves those contracts have been implemented.

The agreement active-selection journal and atomic append/archive/activate/revision
transaction are also still necessary engineering. Their offline implementation
and rollback/race tests do **not** depend on dispatch credentials, merge-group
membership or new platform permissions. Actual new legal text, identity/key
registration, acceptance policy, resource activation and deployment remain
separate decisions. Removing `agreements count=2` without a persisted active
selection would allow a stale config to stay ready; that shortcut remains barred.

## Existing manual validation and pending decisions

The existing workflow is [CLA Cloudflare offline checks](https://github.com/Tavotto/Tavotto/actions/workflows/cla-cloudflare.yml).
It declares `workflow_dispatch` with **no custom inputs**. Select these branches
separately and verify checkout SHA in the resulting run:

| PR | Branch | Expected SHA |
|---|---|---|
| #1160 | `feat/cla-ci-bounded-wait-20261018` | `830bd89cc905d28b41f690c7aa6c0236a06aec84` |
| #1161 | `feat/cla-historical-revocation-20261018` | `6d8809f03150b762cfafeacdaf89af6c9c9bbc05` |

The PR branch filter excludes stacked bases, so their missing PR checks are not
queue delay. Manual execution does not require merging or changing those bases.
Existing authorized web UI access avoids provisioning any credential. Programmatic
dispatch would need a callable dispatch action and a valid token with Actions
write permission; neither is available to this agent. No dispatch was attempted,
no token was replaced, and no alternate endpoint was used to bypass the prior
connector allow-list refusal. Local typecheck/build prerequisites remain blocked
by missing tsc/esbuild and the preserved earlier dependency-download E403.

Native merge-group membership needs a separate decision. The recommendation is
the old service's complete queue prefix from the head of the queue through the
candidate, an explicit conservative superset that can over-block preceding PRs.
The alternative is exact current-group membership, requiring a verified complete
and stable authoritative member API before excluding prefix members. Neither is
selected or enabled here. The current native token pins contents/pull_requests/
checks and rejects extra permissions; a future merge-group subscription requires
separately reviewed Merge queues read permission and installation acceptance.
This is independent of the existing offline workflow's contents-read job token.

Official references: [workflow dispatch permissions](https://docs.github.com/rest/actions/workflows#create-a-workflow-dispatch-event),
[merge_group subscription permissions](https://docs.github.com/en/webhooks/webhook-events-and-payloads#merge_group).
No permission or membership decision is needed to run this planner.

## Verification

Actual commands, exit codes, source fingerprints and independent evidence are in
[the evidence manifest](evidence/verification.json). The test inputs are static
synthetic proofs at fixed instants and numeric hypothetical durations. No success
is published, no clock is advanced through a published proof's expiry, and no
remote or HTTP-peer path is tested. Those excluded validations remain excluded.
