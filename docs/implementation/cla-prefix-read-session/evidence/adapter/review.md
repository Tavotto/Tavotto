# Independent prefix read-session adapter verification

Scope: actual native GitHub adapter, cryptographically verified synthetic RSA App JWT/installation handshake, in-process synthetic GitHub responses, actual local SQLite and existing signature/evidence code. **This is not real GitHub E2E, GraphQL schema acceptance, queue read authorization or platform acceptance.** No runtime source, #1172 test or old metric was changed by this reviewer. The three new test files are frozen.

Base: `6f309ec896e0a4a786cc2775e79e76210abe480a`. Actual final local command `node --test tests/prefix-read-session.test.mjs`: **37/37 pass, exit0**. Actual independent disposable-copy mutation runner: **15/15 count=1 unique source locations, selected exit1 and actual AssertionError; runner exit0**. Each mutation's original source SHA matches the final source manifest, not an old head. The mutation JSON selects only `tests/prefix-read-session.test.mjs`.

| Actual scenario | External | SQL | Installation GET | Token POST | Identity GET | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| P2, one qualified delegated synthetic contributor |28|6|2|1|0|eligible=true, publication=not_attempted|
| P3, same contributor |34|6|2|1|0|eligible=true, publication=not_attempted|
| P4, same contributor |35|5|2|1|0|budget_deferred, no result|
| P2, same additional unsigned coauthor in both bindings |32|7|4|1|2|eligible=false, publication=not_attempted|
| P3, same coauthor work |35|6|3|1|1|budget_deferred before completion, no result|
| P2, two personally completed synthetic contributors |28|7|2|1|0|eligible=true, publication=not_attempted|

The last row uses existing packet/intent/sign operations to collect each contributor and holder's own synthetic evidence, with delegated qualification disabled. Preparation happens before the metered read-only assessment. It does not represent real legal consent or a real signature authorization.

Complete P2/P3 traces assert metadata2, main4, complete queue4, each PR before/after4 and compare2. They assert exact request-path order, one narrow-scope token POST, unchanged scope body, three pinned-source content reads, and no identity lookup substitution. The P2 coauthor case checks that both actual /users GETs immediately follow an installation GET. Additional cases measure a two-page queue32 and a PR101-commit assessment30, and refuse an additional queue page at35. Existing invocation charges7/8 are preserved: the former ends at35, the latter rejects at35.

Default false and absent preserve the previous32-call behavior. Standalone snapshots remain uncached. Config rejects nonboolean values and missing assessment/network capability. Worker opt-in requires exact environment string true; JSON cannot enable it.

Negative cases exercise changed installation ID/app/slug/suspended state at the second snapshot and second identity lookup, changed second metadata, changed main reads2/3/4 despite unchanged legal bytes, changed second snapshot binding, changed fourth queue read after PR enumeration, changed second PR reread base, changed second coauthor numeric identity, and synthetic403 on the second identity GET without retry or token remint. All fixture database digests remain unchanged; SQL traces are SELECT-only, default fetch0, publication calls0.

No runtime correctness issue was found within this adapter/capacity scope. Context, lease expiry/closure/concurrency and legal-source tampering are independently assigned to the contract reviewer; this report does not claim their tests as this reviewer's evidence. No Worker fetch, Check Run call, deployment, real network/permissions/credentials, dependency download, or successful publication across expiry was performed.

Frozen SHA256:

- `tests/prefix-read-session-fixture.mjs`: `93aa78e885251983b47d7c1d498c0baa4d580aa19dd9e384d61597f1fed21e08`
- `tests/prefix-read-session.test.mjs`: `afe3c7e0df96a6c24b8a224fb5909665ddda7487a75e755fab7f4618cbe912a2`
- `tests/prefix-read-session-mutation-cases.json`: `f80524aabd879e59164bf02fc27b73390b0f0c587e8e841ed0aec6c13c5beefc`

`accounting.raw.log` preserves full per-request and SQL traces for all six scenarios. `commands.json` records final command exits. `source-manifest.json` pins31 source/test inputs and confirms unchanged bytes during final tests. `mutations.json` records source/mutated/log SHA, unique count, selected title and exit for each final mutant. `raw-logs.json` preserves exact UTF-8 raw logs as JSON strings with original SHA/byte length, including earlier35-test and14-mutant runs separately. `final-manifest.json` references raw members and artifact hashes.
