# Independent implementation review: offline repository-v2 upgrade

Source schema: main `77e2cab77597318859f874d9319c47f15da0e1dd`.
Target schema/base worktree HEAD: `dc2431f725798bd78c40a3b07e0a8054690fe82c`.
Reviewed implementation: uncommitted `scripts/v2-upgrade-offline.mjs` in `/workspace/Tavotto-cla-v2-upgrade`; final complete file hashes are in `manifest.json`.

The reviewer changed only `tests/independent-v2-upgrade.test.mjs` and `tests/v2-upgrade-mutation-cases.json`. All reviewer databases were newly created `:memory:` synthetic fixtures. Production source was read only. No HTTP, publishing, Worker fetch, production database, deployment, dependency download or excluded successful-publication-across-expiry test occurred.

## Verdict and actual execution

No unresolved reproducible correctness or fail-open issue was found in the exercised synthetic upgrade boundaries. The final independent suite is **46/46, exit 0**. It is independent of the author's CLI/durability tests and never imports a running service.

The submitted JSON contains **18** reverse source mutations. Every before target matches exactly once; each selected test executes, exits **1**, and reports a real **AssertionError**. The final harness exits **0**. There are no surviving selected mutants, syntax/import-only failures or repaired mutation targets. Original per-case logs and before/after source hashes are preserved in `mutations.json`, `mutation-01.log` through `mutation-18.log`, and `mutations-run.log`. The earlier successful 17-case run, before the BEGIN timing fix, remains separately archived under `first-17-mutations/`.

## What is independently verified

- The precise main77 repository-v2 frozen fixture is accepted. The synthetic fixture populates every original application table except `_guards`, which must remain empty for quiescence. It includes individual/corporate tuples, both opaque personal-signature roles, holds/audit, documents, sessions/intents, BLOB/NULL/Unicode/control characters, SQLite int64 rowid `9223372036854775806`, audit INTEGER PRIMARY KEY values above 2^53, nonzero revision 7, pending outbox state, manual refresh and reconciliation cursors.
- An independent snapshot compares every original rowid/cell, storage type and raw bytes before/after. The actual upgrade preserves all those old values, revision, tuple, manual/cursor state and evidence, adds only the reviewed zero send fence plus seven empty tables, stays in the same namespace, and executes 31 DDL statements. No fixture is described as legally valid or cryptographically qualified.
- Default-off execution cannot touch even a poison database/plan proxy. Strict booleans for both execution and synthetic acknowledgement are required. A caller-owned transaction is rejected without rollback or mutation of that caller's pending data.
- Unknown main tables/views/indexes/triggers, `sqliteXspoof` (where LIKE's underscore would have hidden it), mixed namespace, relabeled frozen v1 and partial send_until-only upgrade are refused unchanged. TEMP tables/triggers and ATTACH :memory: are refused with main/temp/attached schema and rows preserved.
- Every issued/unknown phase, regardless of conclusion, arbitrary guard, nonempty stale/zero-deadline lease and empty-lease/nonzero-deadline mismatch refuses upgrade. Disabled foreign keys and damaged FK data refuse unchanged.
- A changed source row or plan source/target/statements/new-table/data field cannot pass the transaction's exact recheck. A forbidden lease injected immediately before BEGIN is detected inside the actual transaction and left intact, while no DDL commits.
- Distinct malformed UTF-8 byte strings (80 vs 81) that decode to the same JS replacement character cannot collide in a prepared plan. Equal raw bytes stored as BLOB versus TEXT also invalidate a stale plan. Full int64 reads are exercised, not merely declared.
- Failures injected after ALTER, after a new table, after a new trigger, and before COMMIT restore the entire original schema and data. Unexpected old-row writes, schema writes, nonzero historical send fences and synthesized new mechanism rows are detected and rolled back by final verification.
- A BEGIN that has executed but whose wrapper throws before returning is cleaned up. Reversing the ownership flag to the original after-exec order is mutation 18 and fails the selected regression with AssertionError/exit 1. The independent baseline ran after the author's fix; the reviewer does not mislabel that green baseline as a pre-fix red.
- A COMMIT that executes before its reply is lost retains the complete target and old data; the exact original plan recognizes it using only BEGIN/COMMIT and reads, never repeating DDL. Ordinary exact retry is also read-only. Changed old data or populated new mechanisms reject old-plan recognition without repair. A freshly prepared target is reported as a no-op.

## Source changes during review and original failures

The author independently added precise GLOB filtering, TEMP/ATTACH exclusion, raw TEXT/BLOB byte fingerprints, the `activation_performed:false` metadata name, and the BEGIN ownership timing fix before the final frozen execution. Final evidence hashes include these changes.

Exploratory TEMP-outbox shadowing first reached `upgraded_schema_mismatch` and rolled back; this was not a successful quiescence bypass. A later TEMP-guard shadow probe hit the newly added isolation guard. Both original exploratory logs exit 1 with the protective rejection; neither is counted as an assertion regression or a successful upgrade. The file named `temp-shadow-confirmed.log` was retained with its original filename, but its actual contents show refusal, not confirmation; this report and the manifest are the interpretation of record.

Initial formal run: 42 selected, 3 pass/39 fail, exit 1, because the reviewer's synthetic fixture omitted required `deliveries.created`. Correcting only that fixture field produced 41/42, exit 1; the remaining test expected the previous metadata name after the author renamed it. Updating that assertion to `activation_performed` produced 42/42, exit 0. The subsequent expanded 44-case baseline passed. The final frozen 46-case baseline and 18-case mutant run supersede those intermediate development states; all original logs remain retained.

## Boundaries

`syntheticFixture` is an explicit caller acknowledgement, not verified database provenance or production authorization. The module accepts an externally supplied local SQLite handle; it does not prove that it is an in-memory handle. The CLI has no file target/execution argument. The parent accepted this boundary and is separately testing disposable file/WAL persistence; this reviewer did not create or mutate any file-backed database and does not claim that additional test result.

These results do not establish real D1 application, WAL/close-reopen durability, independent multi-connection locking, process-kill recovery, real writer cessation, live cutover, deployment or platform legal/cryptographic qualification. A wrapper simulates the pre-BEGIN race and lost replies; it is not evidence of a real concurrent production writer. The tests concern the local synthetic API, not proof-expiry publication behavior.

The incremental operation neither selects nor rolls back an agreement version. It keeps old tuples and new activation/grant/latch tables untouched/empty. Rollback is established for this uncommitted DDL transaction; restoring an old production image after new signatures would be a separate unapproved recovery decision.
