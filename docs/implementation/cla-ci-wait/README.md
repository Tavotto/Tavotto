# Bounded read-only CI waiting candidate

Base: d2b0ba59340761b61f7ac5b8b534d43bcdd731f4, the reviewed head of
draft #1151. This followup is stacked on that branch. It changes only the inactive
CI consumer/entry, disabled CI configuration, tests and this audit. Worker,
signing, publication, D1 schema, schedules, policy and live workflows are unchanged.

## Evidence and classification of the remaining work

These are engineering boundaries, not a claim that approval alone completes the
automatic flow. No new fail-open defect was established by this audit.

| Area | Concrete evidence at the base | Classification and necessary followup |
| --- | --- | --- |
| Agreement version migration | [policy.ts:18](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/core/policy.ts#L18) rejects versions other than 1.0; [store.ts:34](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/native/store.ts#L34) requires exactly two immutable agreement rows. | Deliberate compatibility limit. A separate change must define active versus historical agreement mappings, source/hash validation, atomic revision changes, archival reads, and new-version personal consent/grants. Simply relaxing the checks is unsafe. Synthetic version tests can be developed without real legal terms; deployment needs approved texts and migration acceptance. |
| Dispatch, confirmation and throughput | [automatic.ts:12](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/native/automatic.ts#L12) reserves a 15-minute automatic schedule; its claim at line 42 selects one head. [deployment-plan.mjs:15](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/scripts/deployment-plan.mjs#L15) proposes recovery every two minutes. [background.ts:19-25](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/native/background.ts#L19) confirms only an unexpired, freshly requalified proof. | Availability/design gap. These separate invocations provide no accepted end-to-end latency or throughput guarantee within the 120-second proof. A later design must coordinate dispatch and recovery with bounded admission while preserving original SQL/external/time ceilings, manual recovery and no replay. This followup does not change schedules or validate successful publication. |
| Early CI reads | [ci-candidate.mjs:12](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/scripts/ci-candidate.mjs#L12) performs one verification; [consumer.ts:72](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/ci/consumer.ts#L72) rejects an absent Check Run. [verification.ts:11](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/native/verification.ts#L11) rejects an unconfirmed attempt; [worker.ts:181](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/native/worker.ts#L181) maps that rejection to 403. | Fail-closed but premature availability failure. This followup adds optional bounded waiting for precisely those two early states; it does not turn every 403 or service outage into a retry. |
| Trusted CI and merge-group | [ci-proposal.md](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/deployment/ci-proposal.md) requires trusted source to land before wiring; [proof.ts:9](https://github.com/Tavotto/Tavotto/blob/d2b0ba59340761b61f7ac5b8b534d43bcdd731f4/services/cla-cloudflare/src/core/proof.ts#L9) accepts PR proofs only, and the CLI rejects merge_group before requests. | Trusted-main wiring is intentionally deferred until the reviewed consumer closure is merged. Merge-group support is missing engineering, requiring a distinct queue-head/member/contributor binding and independent recovery/authority contract. PR proofs must not satisfy it. Neither workflow nor gate is changed here. |

The version, scheduling and merge-group work remain separate engineering tasks;
none are silently enabled or treated as completed by adding a CI wait loop.
Cloudflare's [Cron Triggers documentation](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
describes scheduling configuration; this audit does not infer a platform timing
guarantee or deployed acceptance from a cron expression.

## Waiting contract

The trusted CI config may explicitly contain:

```json
"wait": {"enabled": true, "maxAttempts": 3, "intervalMs": 1000, "timeoutMs": 10000}
```

The checked-in example has wait.enabled=false and provider enabled=false. Absent
or false preserves single-attempt behavior. Event data cannot configure waiting.
Enabled configuration must have exactly these fields: 2-8 attempts, a 1-10 second
interval, and an overall timeout of 1-60 seconds. These are hard bounds, not an
automatic-completion or quota claim.

Only complete, unambiguous enumeration with no owned Check Run, or the first live
authority response with exactly status 403 and {error:proof_not_confirmed}, can
wait. The latter must follow a fresh, valid success proof and all current trust
checks. A typed internal pending result carries the verified source and candidate
anchor. Another authority state, malformed response, redirect, 429, 503, transport
failure or failure on the second authority read terminates immediately.

Every attempt repeats trusted policy/documents, head/base and Check Run reads,
and both live authority checks before success. The first trusted-main SHA stays
pinned. Once observed, run ID, attempt and proof hash stay pinned, including if
the candidate later disappears. Config and event locator are captured once.
No earlier eligible response is cached or reused.

The overall monotonic deadline covers every GET, response body, pause and final
return, including a transport/body that ignores cancellation. Abort is propagated
to the request and body reader. Before the first authority read, remaining proof life
also fixes a monotonic deadline that cannot be extended. The default clock retains
the fractional wall second; a custom seconds-only test clock uses the conservative
end of its reported second. Initial samples are validated. Deadline mapping and
timer arming use validated snapshots rather than extra unchecked clock samples.
Wall/monotonic rollback,
either deadline and proof expiry all reject before another read or success.

## Validation and acceptance

Tests use local SQLite, ephemeral synthetic signatures and an in-memory GET
transport. Successful cases consume a pre-existing synthetic confirmed record at
a fixed clock; no Check Run is sent and no successful-publication transition
across proof expiry is executed. Expiry/deadline scenarios assert rejection.
The current Worker 403 response is exercised through its actual fetch handler
without HTTP or remote calls. Source mutations are isolated, uniquely located
and must make the selected test actually fail.

The first external code/security review covered exact head
`830bd89cc905d28b41f690c7aa6c0236a06aec84` after its [hosted typecheck,
build and offline checks succeeded](https://github.com/Tavotto/Tavotto/actions/runs/39112917441/job/117632653129).
Security reported no findings; code review found subsecond cancellation overrun.
The fix and an independent review also closed unchecked initial/extra clock-sample
paths found while reproducing it. [Current evidence](evidence/p2-subsecond/verification.json)
records 358 Node tests, four migration tests and 73 detected mutations, all exit 0.
The original two subsecond regressions fail with AssertionError / exit 1. Independent
default-clock tests, 240 clock-fault invocations and seven actual AssertionError / exit 1
mutants are archived with source hashes and the intermediate red evidence.

Local typecheck still exits 127 (tsc absent) and build exits 1 (esbuild absent).
No dependency-download retry, proxy, mirror or alternate environment was used.
The earlier hosted success belongs only to its original head. New-head hosted
verification and the one permitted targeted followup remain pending; no further
review is requested before deterministic prerequisites pass. No extra #1151 review,
workflow, permission, gate or deployment is triggered by this fix.

Real legal approval/signatures, identity-key registration, secret/resource
custody, single-authority migration, full CI and live platform acceptance remain
outstanding. A 60-second read-only wait cannot guarantee a 15-minute dispatch or
two-minute recovery schedule will complete in time. This is one independently
reviewable improvement, not acceptance of the full automatic loop.
