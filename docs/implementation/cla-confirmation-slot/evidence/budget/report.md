# Independent slot SQL and lifecycle tests

Ready. Only two workspace files were authored by this agent:
- services/cla-cloudflare/tests/independent-slot-budget.test.mjs
- services/cla-cloudflare/tests/slot-budget-mutation-cases.json

Base HEAD: a4695f830d321ffa03184595afd895d2055db3b3. The tested immutable snapshot matches all seven relevant current runtime source files and both deliverables; exact SHA256 values are in final-manifest.json. In particular store.ts = f56a0d9effb65a44ebb118ee33fd1611340361ea4bce9f5c3a31f77690dfad83 and schema.sql = 0e4cc1672c78837b219eba0c1f54de89fc86169c87ffd26277085a229dfe3a08.

## Actual commands and outcomes

`node --test tests/independent-slot-budget.test.mjs`: 14 tests, 14 pass, exit 0. This command was run against the immutable /tmp snapshot; initial-tests.log also preserves the earlier workspace run, exit 0. `node --check tests/independent-slot-budget.test.mjs`: exit 0.

`python3 /tmp/cla-slot-budget-independent/run.py`: exit 0. It runs the baseline and six independently copied mutants. Each mutant first asserts exactly one source occurrence and the changed byte offset. Every selected Node command exits exactly 1 with an actual AssertionError. commands.json records command/cwd/exit/log; mutants.json records original and changed source SHA256, unique count and offset. Registered cases are all assertionError:true for the parent's mutation-smoke integration.

## Measured outer D1 statement attempts

The shared production InvocationBudget/meterDatabase counts each actual executed SELECT/update/transaction statement. Fixture initialization and raw static snapshot seeding use an unmetered setup connection and are excluded. There is no cached eligibility or mocked qualification in the two issuing measurements: one synthetic contributor has real verified personal evidence and a cryptographic delegated receipt.

| Local invocation | Work SQL | Total SQL | Relevant deltas |
| --- | ---: | ---: | --- |
| New-head qualification, Store issued journal, unknown settlement | 17 | 21 | issue transaction 7, unknown settlement 4 |
| Same-head fresh qualification, Store issued journal, unknown settlement | 17 | 21 | issue transaction 5, unknown settlement 4 |
| Conservative full real qualification + two withdrawal authorization reads, then negative GET witness release | 19 | 36 | release/progress 8, finish 9 |
| Actual reconcileOne negative GET recovery after restart, flag false | skips qualification | 27 | two synthetic GETs; no mutations |

The first two paths execute the Store boundary directly, together with their real qualification and SQL prefix; neither driver publication callback is invoked. The conservative 19/36 probe intentionally executes a full qualification and authorization prefix before negative GET recovery, while the actual negative branch skips those reads. It is a controlled upper-prefix SQL measurement, not a dynamic successful publication or real-platform latency result. All measured work is <=20, all measured totals <=40. The new policy trigger body is internal to one outer issued INSERT, consistent with the existing meter's revision-trigger treatment; it is not claimed to execute zero internal SQLite operations.

## Behavioral evidence

- Two different-target requests scheduled together via Promise.allSettled commit exactly one positive issued row, one active pointer and one dispatched head; the losing transaction leaves its head running and rolls back outbox/target/marker changes. SQLite serializes the local D1 adapter's transactions; this is a request-interleaving atomicity test, not distributed D1 load testing.
- Occupied slot leaves automatic heads pending without qualification. Both explicit capacity deferral and the raced failRefresh path retain pending status and attempts=2.
- Expired owner is selected before an older-due ordinary target and before an older eligible ordinary journal in the same target. The latter is explicitly phase=reconcile, so it genuinely competes with the owner.
- Static expired owner snapshots in issued, unknown, reconcile, confirmed and manual phases all retain the live persisted send fence. Claim, publicationBatch and recordReconciliation cannot terminalize them. Expiry itself never frees the pointer.
- A physical SQLite close/reopen and new flagfalse Store still fence head admission and legacy positive INSERT. An old-binary direct resolved phase update fails. Negative recovery finds the exact held older row even when a later different-head failure is globally newest, and consumes two adapter GETs to generate a fresh opaque witness. It releases the pointer without changing original proof/expiry, and retains the first-attempt latch and immutable release archive.
- Missing remote observation produces manual state and retains the owner/outbox.
- Same-head issued work persists the full 120s lease as send_until; 120s >= the unchanged 90s cleanup ceiling. Unknown settlement clears the sender fence without freeing capacity.

The six reverse mutations prove exact issuing SQL cost, busy retry preservation, any-phase claim fence, flagfalse latch admission, target withdrawal priority and within-target withdrawal priority. Each gives exit 1/AssertionError.

## Scope and limits

No HTTP, default transport fetch, worker.fetch, publication API, credential exchange, deployment, dependency download, commit or push occurred. All clocks remain fixed at 1800000000; expired owners are static pre-existing synthetic snapshots, never successful operations carried across expiry. A positive unsent Store journal is followed only by unknown settlement, never publication/confirmation. These tests do not measure actual Cloudflare scheduling delay, distributed D1 concurrency, platform HTTP throughput or CI completion. No implementation blocker was found in this scope. The parent owns full-suite validation and wiring the mutation case registry into mutation-smoke.
