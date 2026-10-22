# Independent review: persistent confirmation slot

Base commit: `a4695f830d321ffa03184595afd895d2055db3b3`. Review target is the uncommitted implementation in `/workspace/Tavotto-cla-confirmation-slot`; exact final file hashes and their aggregate are recorded in `manifest.json`. The reviewer wrote only `tests/independent-confirmation-slot.test.mjs` and `tests/confirmation-slot-mutation-cases.json` in the worktree. Production source was read only.

## Verdict

No reproducible fail-open or negative-recovery regression was found in the exercised local boundaries. The singleton pointer representation avoids changing historical publication columns and preserves the existing migration whitelist. The irreversible latch, exact issued marker, active/history identity guards, invocation-local negative witness and current recovery CAS cooperate to reject legacy positive bypass and stale release. This is offline mechanism evidence, not D1/GitHub deployment acceptance or a conclusion about legal effect.

The independently authored formal file contains **39 tests**, all passing, exit **0**. The 22 submitted source counter-mutations each have **one** actual source match, execute their named test, and exit **1** with an actual **AssertionError**. The independent mutation harness exits **0**. No selected mutant survived, and no source target or test had to be repaired after the mutant run. `mutations.json`, the JSON cases and individual `mutation-01.log` through `mutation-22.log` preserve original commands, hashes and results.

## Exercised contracts

- An unsent automatic issued batch enrolls the singleton and active owner, dispatches its exact head, wakes its outbox and consumes its marker in seven outer metered SQL statements. An injected final SQL failure rolls back every application table, including latch, marker, journal, head and outbox.
- Legacy original-success issued/unknown/reconcile/confirmed/manual rows block first enrollment globally across targets. Resolved legacy success and unresolved failure do not. Existing capacity rejects a different target without suppressing unrelated negative journals.
- Matching-marker policy REPLACE, owner-id failure REPLACE, direct NULL/no-op/metadata/reassignment transitions, owner proof/identity/run edits and deletions reject. A duplicate legacy id with INSERT OR IGNORE cannot commit a marker-only no-op.
- A baseline-shaped resolved update without the negative witness rejects and retains its recovery signal. Runtime flag false still selects and projects the owner, while old positive INSERT stays fenced. The holder positive path rejects before creating a journal. Manual state does not release the owner.
- A static already-expired unsent synthetic row with a live sender fence stays unclaimable despite changed revision. This is one fixed-clock negative/recovery test, with no successful send or expiry-crossing successful publication.
- Owned-run negative list results require a second, exact owned-run GET before a witness exists. Both reads use a local in-process read-only GET transport, no token exchange, network or persistent credentials. Exact negative release preserves original proof/expiry and immutable canonical observation, clears only its owner and leaves the initial latch identity unchanged. A later owner may then enroll; legacy NULL-positive insertion is still refused after release.
- The unknown journal can initially have run_id NULL; fresh GET run 77 is recorded only in the negative release, with old_run_id NULL and new run_id 77. Original publication revision may differ from a freshly authorized current recovery revision.
- Forged/cross-adapter/replayed witness, different owned run, adapter expiry boundary, current lease/generation/revision/phase changes, altered proof snapshot and mismatched SQL observed phase reject without changing application rows. Direct capability consumption is single-use even without relying on the SQL CAS fallback.
- At the SQL deadline test, adapter consumption succeeds while its clock is still valid, then the test advances only the synthetic database clock to valid_until before batch execution. SQL rejects, the pointer and observation remain intact, and the already-consumed capability cannot be replayed. There is no successful publication in this test.
- An injected late negative-settlement failure rolls back pointer, observation and reconciliation progress in the same transaction. The exact-owner release path without progress uses five outer SQL statements and two synthetic GET calls; it does not add a release query.

## Limits and remaining acceptance

These tests do not establish real multi-connection D1 contention, process crash durability, platform trigger cost, scheduler latency or a complete whole-invocation 20-work/40-total admission budget. Seven-statement issue and five-statement negative release counts are measured local subpaths; the parent's whole-suite/invocation and migration evidence must remain separately identified.

SQL deadline is deliberately defended by the Store WHERE, publication BEFORE trigger and policy transition guard. The integration deadline rejection is exercised; this independent set does not claim that weakening any one of those redundant cutoff predicates alone is killed. SQL observed_phase is independently counter-mutated and killed by the direct negative SQL test.

No HTTP publication, Worker fetch, real authority/signature, resource/credential installation, deployment, download, production enablement or excluded dynamic successful-publication-across-proof-expiry scenario was executed. Typecheck, production build, hosted CI and migration were not rerun by this reviewer and are not claimed as independent passes here.

## Execution record

1. Initial formal file: 31/31, exit 0 (`first-run.log`).
2. Expanded intermediate file: 38/38, exit 0 (`second-run.log`).
3. Final frozen file including SQL observed_phase: 39/39, exit 0 (`final-tests.log`).
4. All 22 independent source mutants: exit 1 / AssertionError individually, harness exit 0 (`mutations-run.log`, `mutations.json`, original per-case logs).
5. One earlier read command used a duplicated service path from an already-service cwd: sed exited 2 before Node started; no product test ran in that failed command. It was corrected to the actual service cwd before the intermediate run. This was a command/path error, not a product or assertion failure. No policy denial or uncertain external action occurred.
