# Bounded read-only CI waiting: runtime integration

This change integrates the existing CI runtime from #1160 source
`907af3f3920a62bbc52712c1e9fcf9da371f54d5` with the newer native service stack.
The final integration base is the independent negative-owner recovery commit
`56aca50844ecd85aefe9818d8f9e4be0df063d8c`, built on #1169 head
`7324a2d0541d79b90f106aed74991b22b8af60ae`. It imports no #1163 timing planner.
No workflow, permission, agreement, signing operation, resource or flag is enabled.

The validation uses synthetic GET transports and the actual local native
verification function. **It is not real GitHub end-to-end validation.** No hosted
CI result for this integrated source tree is claimed; successful checks for a
parent or donor commit validate only that earlier tree.

## Source boundary

Nine files are byte-for-byte copies of that source commit:

* `services/cla-cloudflare/deployment/ci-proposal.md`
* `services/cla-cloudflare/deployment/ci.disabled.json`
* `services/cla-cloudflare/scripts/ci-candidate.mjs`
* `services/cla-cloudflare/src/ci/consumer.ts`
* `services/cla-cloudflare/src/ci/wait.ts`
* `services/cla-cloudflare/tests/ci-candidate.test.mjs`
* `services/cla-cloudflare/tests/ci-fixture.mjs`
* `services/cla-cloudflare/tests/ci-wait-mutation-cases.json`
* `services/cla-cloudflare/tests/ci-wait.test.mjs`

The tenth file, `tests/mutation-smoke.mjs`, retains every existing base case,
including both negative-recovery case imports, and adds only the CI wait import.
The final base already carries the necessary literal-title witness,
`selectedTitle ?? pattern`: an escaped regular expression used to select a test
is not the literal title printed when the assertion fails. Its existing nonzero
exit and requested AssertionError requirements remain intact. Exact source
and integrated SHA-256 values are recorded in [the manifest](evidence/verification.json).

## Runtime behavior

Absent waiting or `wait.enabled=false` retains immediate verification. The
checked-in CI configuration has both `enabled=false` and `wait.enabled=false`;
the provider policy remains disabled. Trusted configuration alone may select
2–8 attempts, a 1–10 second interval and a 1–60 second overall timeout.

Only a complete unambiguous missing owned Check Run, or the first authority read
with exactly HTTP 403 and `{error:proof_not_confirmed}`, permits another attempt.
Every attempt repeats source, policy, document, head/base, owned run and current
authority checks. The first trusted source stays pinned. Once observed, the run,
attempt and proof hash stay pinned. A second-authority failure is terminal.

The monotonic deadline covers GET, body reading, pauses and final return. Before
the first authority request, the original proof's remaining milliseconds narrow
that deadline without renewal. Cancellation, wall/monotonic rollback, invalid
clock samples and expiry reject. No earlier eligible response is cached.

All transport is GET-only and confines the GitHub credential to GitHub.
`merge_group` remains rejected before constructing a reader. Native queue binding,
queue permissions and trusted-main workflow wiring remain separate dependencies;
PR proofs cannot qualify queue heads. Existing required gates are unchanged.

## Fresh combination evidence

At the initial `7324a2d0` base, actual local execution passed 550 Node tests,
four migration tests and 166 mutation cases, all with exit 0. The focused CI run
passed 51 tests. These are new combination results, not copied source-head success.
Raw logs remain byte-for-byte under `/tmp/tavotto-cla-ci-integration/`; their exact
commands, exits and hashes are recorded in the manifest. Final original log bytes
are also preserved as UTF-8 JSON string members in
[raw-logs.json](evidence/raw-logs.json); each member carries its original byte
length and SHA-256. Manifest log names refer to those members.

An additional actual escaped-regex probe mutates the pending-response shape guard
at its unique source location in a disposable copy. The selected test exits 1
with AssertionError. The old escaped-pattern title witness is false; the literal
selectedTitle witness is true. This probe changes no integrated source file.

CI success cases consume pre-existing synthetic confirmed records at a fixed
clock. Pending failures and expiry/deadline cases assert rejection. No HTTP,
publication, real signature, credential exchange, deployment or successful
publication transition across proof expiry is performed. The existing migration
and native suites retain their original test boundaries.

Local TypeScript and esbuild dependencies are absent. Their availability check is
recorded separately from compiler/build success. No install, download, proxy,
mirror or alternate environment is attempted after the earlier npm E403.

The initial CI tree and documentation were saved as an exact patch and per-file
hashes before updating the branch to the completed negative-owner recovery base.
The nine source files were replayed unchanged. The final mutation harness is the
new base plus only the CI wait import. Final combined verification and independent
reverse checks are recorded separately below and in the manifest; earlier results
remain attributed to their original base.

On the final `56aca508` base, fresh execution passes **582 Node tests, four
migration tests, 187 mutation cases and 51 focused CI tests**, all exit 0. The
independent author of [the composition review](evidence/independent/review.md)
then reran the same independently written 14-test suite on a new snapshot of
this final base: 14 passed, exit 0. Its four uniquely located reverse mutants
each exit exactly 1 with actual AssertionError; the mutation runner exits 0.
No source-head or earlier `7324` result is credited to this final combination.

The independent successful authority reads each use 11 actual outer SQL
statements and one synthetic owned-run GET. They leave the fixed, pre-existing
confirmed proof and slot unchanged. Static manual/unknown phases exhaust bounded
waiting without confirmation or replay. An independent fresh-negative GET
recovery releases the exact owner and invalidates an in-flight CI read. These
observations remain local synthetic verification, not live GitHub or Cloudflare
acceptance or a timely confirmation guarantee. The independent report, original
test source, source pins and four mutation records are preserved alongside the
log bundle; no runtime snapshot is committed as evidence.

#1163's current-head formal coverage gap remains outstanding. #1169's full and
targeted external review allowance remains exhausted; this integration triggers
neither another external review nor a workflow dispatch. Real platform acceptance,
native merge-group, trusted-main landing/wiring, legal decisions and final
activation remain outstanding.
