# #1163 final P2 narrow independent review

Worktree: /workspace/Tavotto-cla-timing-contract. HEAD at review: 20fa7f5ced9eb85bcd4fb3dd5e10aa0a488d1e44, with the six author-edited uncommitted files. Reviewed the final dispatch-work phase and nonblocking input fixes plus three exact phase deadline boundaries. No workspace/Git/GitHub source edits were made by this reviewer. All independent test/copy/mutation output is in /tmp.

Conclusion: no remaining blocking defect reproduced in this narrow scope. Dispatch work is required, positive, strictly below 60,000ms and <= the aggregate send/settle duration. That aggregate is counted only once on the critical path and must be strictly below the 90,000ms cleanup deadline. Confirmation at 60,000ms, send/settlement at 90,000ms and withdrawal pass at 90,000ms all reject, consistently with existing InvocationBudget checkWork/chargeSQL >= deadline semantics. Existing ceilings are unchanged.

Concrete code: scripts/timing-plan.mjs:18-20 requires and validates dispatch_work_ms; :36-37 enforces phase relationships and strict ceilings; :50 counts send_settle_ms once, without adding its work subset; :84 opens read-only/nonblocking before fstat, :85 rejects non-regular inputs before reading, :86-89 retains complete bounded short-read handling. :67-78 always returns all execution/deployment/eligibility/acceptance flags false.

Executed tests:
1. node --test services/cla-cloudflare/tests/timing-plan.test.mjs => actual exit 0, 62 tests passed. /tmp/cla-timing-final-independent-formal.tap.
2. node --test /tmp/cla-timing-final-independent.test.mjs => actual exit 0, 9 tests passed. /tmp/cla-timing-final-independent.tap.
3. python3 /tmp/cla-timing-final-independent-mutations.py => harness actual exit 0; all five isolated mutations had targeted AssertionError and node exit 1. /tmp/cla-timing-final-independent-mutants/report.json includes original/mutated source SHA256, unique mutation precondition, selected test and real exit.

Independent subjects:
- Each exact confirmation/send-settle/withdrawal limit is isolated to the SOLE reason invocation_time_budget_exceeded. Each limit minus one millisecond passes with otherwise feasible assumptions. Withdrawal uses window=180,000ms so recovery capacity cannot mask this boundary.
- Work=59,999ms inside aggregate=70,000ms remains feasible with critical_path_with_margin=74,000ms. It is not double-added. Work=60,000 or 70,000ms rejects, as does a work duration larger than its aggregate. Work equal to aggregate remains permitted (the original feasible fixture uses 1000/1000).
- Missing/null/zero/negative/string dispatch_work_ms rejects before planning.
- Actual InvocationBudget checks reject work at exactly 60,000ms and settlement SQL at exactly 90,000ms.
- A real FIFO, plus a symlink to it, reject with regular_input_file_required and process exit 1 before the one-second child timeout. Tested both no writer and a live O_RDWR|O_NONBLOCK endpoint held open. The child never blocks waiting for a FIFO peer.
- A normal regular file with forced ONE-BYTE readSync chunks still yields a complete feasible plan and exit 0, with input bytes unchanged.
- The disabled synthetic example remains infeasible with CLI exit 2 and all four execution/eligibility/deployment/live-acceptance flags false; no input bytes are changed.

Isolated falsification subjects (all real exit 1): remove dispatch work phase check; remove FIFO nonblocking open (child times out and the test assertion detects it); relax exact confirmation work deadline >= to >; relax exact settlement cleanup deadline >= to >; relax exact withdrawal cleanup deadline >= to >. Every source anchor was proven unique before replacement, and each mutated source SHA is recorded.

Final source fingerprints:
services/cla-cloudflare/scripts/timing-plan.mjs 5b1ac135d576b8998acca4c1c5bfc923bf38f60adf2bc555d2556eab9a38ff29
services/cla-cloudflare/tests/timing-plan.test.mjs 97a9b0a7847d93a20a56daf8b7d98e4c7f5065b2a2b39e72a76b295f0e8582f9
services/cla-cloudflare/tests/timing-plan-fixture.mjs 7ee57779fbfab67e0da9f75c32b76f0d946b85be2c2d0f79b6225491e3a512bf
services/cla-cloudflare/tests/timing-plan-mutation-cases.json 2f7003809611b1d48547c85e50a3dd9eb3d6311273caf829ce2ab4b3e68da930
services/cla-cloudflare/planning/timing.disabled.synthetic.json 3ffdcc8dc9de62a6fda20dfbfa1437c6b8b05b5b723a437aa01c3fe918cedc18
docs/implementation/cla-timing-contract/README.md fe7bf0735d21afc017a6bfbe5003434818c1eb30fa1a21f5aa05df39618cbe28

All proof data and durations are fixed synthetic assumptions. No HTTP, worker.fetch, deployment, publication, downloads, external review, real signing or credential work was executed. No successful publication was advanced across expiry. This review does not assert hosted status or live/platform/legal acceptance. No remaining independent blocker.
