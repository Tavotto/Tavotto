# Independent narrow review: #1160 subsecond wait cancellation

Worktree: /workspace/Tavotto-cla-ci-wait; branch base 830bd89cc905d28b41f690c7aa6c0236a06aec84. No workspace/Git/GitHub implementation changes were made by this review. All independent tests, copies and mutation evidence are under /tmp. No HTTP, worker.fetch, publication, downloads, real signatures/credentials or excluded dynamic successful-publication-across-expiry scenario was executed.

Final reviewed wait.ts SHA256: c4b3276ce018dc9db3ff9a90485d089ccbae6d5bd31d4498ce0b20173d1faebb.
ci-candidate.mjs SHA256: 86f27446ed18b4a181dff2db488c414c1bc85b36165863d4fac29ac7875d8e01.

Conclusion: no remaining blocking defect reproduced in the final snapshot. The original seconds-floor calculation's subsecond overrun is fixed for BOTH the default Date.now path and explicit milliseconds injection. Two abnormal-sample gaps found in the earlier version were reported, repaired by the parent and independently rechecked.

Default-path findings:
- Freezing Date.now at the fixture's second +900ms and passing NEITHER a seconds clock nor wallMilliseconds yields a captured proof cancellation delay of 1100ms for an existing fixed proof expiring at second +2.
- A hanging authority transport is raced against cancellation and its signal becomes aborted. A hanging response body is canceled via readOnly's existing abort listener; cancellation is observed.
- A custom seconds-only clock with no millisecond tool uses the deliberate end-of-second fallback, producing a conservative 1000ms budget for the same proof.
- A fractional monotonic sample retains the expected millisecond wall precision within 0.001ms. The epoch arithmetic does not reintroduce the lost 900ms; its tiny floating point rounding is below a microsecond in the tested case.
- Steady NaN/Infinity/fractional millisecond values reject before transport. A guarded millisecond rollback rejects before another transport call.
- Deadline uses min(previous deadline, proof deadline), so retries/observations cannot extend it. Normal snapshots and abort checks remain present after asynchronous work.

Independent normal-path test command:
node --test /tmp/cla-ci-wait-independent-subsecond.test.mjs
Final result: exit 0, 9 tests passed; /tmp/cla-ci-wait-independent-subsecond-after-snapshot.tap.

Original suite within restrictions:
node --test --test-name-pattern='^(?!CI wait current Worker pending contract)' services/cla-cloudflare/tests/ci-wait.test.mjs
Final result: exit 0, 41 selected tests passed; /tmp/cla-ci-wait-independent-allowed-suite-after-snapshot.tap. The one current Worker pending contract test calls worker.fetch and was explicitly NOT executed by this reviewer. Test counts increased while the author added formal regressions; the final saved allowed-suite TAP has 41 passes.

Earlier defects found and fixed:
1. Initialization stored an unchecked first millisecond value. When ONLY the initial sample was NaN and all later values were normal, the candidate could return successful fixed-time verification instead of rejecting. Independent actual exit 1: Missing expected rejection.
2. observe read an extra unvalidated wallMilliseconds value after guard. A one-time rollback only at that direct sample (normal samples now*1000+900, exceptional sample now*1000-10000) caused a hanging authority transport to arm 10000ms rather than <=1100ms. The test captured and immediately fired that timer, without advancing a clock or performing any publication. Independent actual exit 1: transient rollback armed 10000 milliseconds instead of at most 1100.

Old red source SHA: ab0f85f53fd90ab1716d141e928624efe14cb9faa1e630cc249fc0d57f08dbd0.
Old red evidence is preserved in /tmp/cla-ci-wait-abnormal-samples.test.mjs and /tmp/cla-ci-wait-abnormal-samples.tap. The direct-sample probe uses a verified caller-stack target to establish the original observe sampling site; it is a red localization probe, not the final regression suite after that site was removed.

Fix independently reviewed at wait.ts:27-45: validate initial monotonic/seconds/millisecond samples; guard returns its validated {now,milliseconds} snapshot; observe maps proof expiry using that snapshot; arm uses guard's validated now rather than another naked monotonic read. The parent implemented the fixes; this reviewer did not edit workspace implementation.

Independent final sampling matrix:
node --test /tmp/cla-ci-wait-snapshot-clock-review.test.mjs
Actual exit 0, 5 tests passed. Across 240 invocations (millisecond or monotonic clock, one-time NaN or rollback, Nth sample 1..60), 92 bad samples were actually reached; 83 invocations rejected before authority; all 157 authority invocations armed cancellation <=1100ms and received an aborted signal. /tmp/cla-ci-wait-snapshot-clock-review.tap. Samples occurring inside content retrieval can be wrapped by the existing consumer catch as ci_source_invalid rather than ci_wait_deadline; both are actual fail-closed rejection, and the suite records that distinction.

Independent isolated mutation command:
python3 /tmp/cla-ci-wait-independent-mutations.py
Harness exit 0. Seven unique, prevalidated source transforms each produced actual node exit 1 and an AssertionError in the named selected test. Report and full outputs: /tmp/cla-ci-wait-independent-mutants/report.json and each case/result.tap. Source and mutant SHA256 values, selected titles and exit codes are recorded.
- Revert proof mapping to second-based expiry.
- Floor default Date.now back to whole seconds.
- Remove millisecond sample validity.
- Remove millisecond rollback check.
- Remove millisecond proof-expiry check.
- Remove initial sample validation.
- Reintroduce naked sampling in observe instead of the validated snapshot.

Scope limits: this is local independent read-only review evidence. It does not claim hosted CI/reviewer approval or establish any legal effect, live provider acceptance, deployment state, or the excluded publication scenario. No remaining independent blocker was found in the final source SHA above.
