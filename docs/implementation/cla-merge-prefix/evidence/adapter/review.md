# Independent native merge-prefix adapter verification

This evidence is synthetic in-process testing of the actual native GitHub adapter, actual RSA App JWT/installation-token handshake and SQLite reads. It is **not real GitHub E2E**, real GraphQL schema acceptance, installation authorization or production acceptance. No runtime source was edited by this reviewer.

Frozen deliverables:

- `services/cla-cloudflare/tests/merge-prefix-fixture.mjs`: `08a5e754aab09ea5938873539d2c81d82c05ff101c9c6660d34fc13570a5072d`
- `services/cla-cloudflare/tests/merge-prefix.test.mjs`: `22863cc979e43029b74042e38dc19c2cafa8b0348e0771504db72b31ff1a994f`
- `services/cla-cloudflare/tests/merge-prefix-adapter-mutation-cases.json`: `975fd4a3fa1243b85ce75a4a9950c1a6651ee4cbd3fe59d5e9b06c6866b768e1`

`node --test tests/merge-prefix.test.mjs`: **61/61 passed, exit0**. The independently executed mutation runner applied 16 unique source substitutions to disposable copies, verified each source count=1, and obtained **selected exit1 + actual AssertionError for all16**; runner exit0. Cases only select this reviewer's test filename. No planner test, HTTP endpoint or publication path was used.

Actual measurements (`accounting.raw.log` contains every call and SQL statement):

| Scenario | External | SQL | Outcome |
| --- | ---: | ---: | --- |
| Two queue-prefix members, one unique qualified synthetic delegated contributor, one queue page |32|6|eligible=true; publication=not_attempted|
| Three queue-prefix members, same contributor |35|5|budget_deferred; no assessment returned|

A one-page snapshot costs `10+3*prefixMembers`: real installation handshake2 + repository/main/policy/legal5 + complete queues2 + final main1 + each PR read/compare/read3. Assessment uses two snapshots. There is no GET /app. Hence two members cost32, while three would need38 and are correctly stopped at the unchanged externalWork35 cap. Identity resolution and extra queue/commit pages consume additional calls. SQL6 is initial revision/time2 + delegated qualification3 + atomic final revision/time1. Both measured database digests stayed identical; default fetch calls0 and publication calls0.

Coverage includes exact locator shape/head+base, opaque visible-ASCII entry IDs, repository identity, existing narrow token scopes, permission-denied refusal without fallback, complete bounded pagination/stable totals/cursors, unique entry/PR/positions, full approved prefix, tail exclusion, unique candidate head+base, every PR's own base/head, current PR rechecks, whole queue changes, complete commit pagination and deduplication, earlier unsigned contributor blocking, malformed/unresolved/mismatching coauthors, and repeated coauthor resolution once per binding. SQL traces allow SELECT only; the synthetic peer permits only GET, token POST and read-only GraphQL query POST.

Python query provenance was checked independently using Python AST extraction from `services/cla/github.py:230` and actual module import of `MERGE_PREFIX_QUERY`: queries match ignoring whitespace, command exit0. Existing Python prefix logic is at lines267–299. Native page validation is `src/native/merge-prefix.ts:59–81`, prefix selection at lines51–58, actual adapter at `src/native/github.ts:274–300`, shared own-PR comparison/recheck at lines247–271, and service assessment/final atomic state at `src/native/service.ts:236–254`.

`source-manifest.json` pins all current TypeScript source and relevant test inputs (30 files), including unchanged source before/after final tests. `commands.json` records commands/exits. `mutations.json` records each unique location/exit/log hash. `raw-logs.json` preserves21 complete raw logs as JSON strings plus each original SHA/byte length; `final-manifest.json` references their member names/hashes.

Remaining blocker: the current token request intentionally remains contents/read, pull_requests/read, checks/read. Actual Merge queues read authorization and real GraphQL schema acceptance remain unverified and require separately authorized resources/permissions. No real network, dependency download, credentials, permissions, deployment, check publication, Worker request or successful publication across an expiry transition was performed.
