# Trusted CI wiring proposal — inactive

No workflow or required check is changed by this candidate. The checked-in
ci.disabled.json is explicitly disabled and contains invalid placeholder IDs.
scripts/ci-candidate.mjs is a reviewable Node 24 entry for src/ci/consumer.ts;
it performs GET requests only. It is not a signing or publishing client.

The optional trusted-config wait policy is default disabled. Its bounded retry
contract and remaining scheduling/version/merge-group engineering are documented
in [the followup audit](../../../docs/implementation/cla-ci-wait/README.md).
It cannot change event locators, extend a proof or wire this entry into a workflow.

The existing gate-discipline rule requires two changes in order:

1. Land and review the consumer and its complete source closure on main while
   provider.configured remains false. Record the reviewed main SHA and exact
   config file. Never execute a PR checkout's script, configuration, dependency
   hook, agreement or event-supplied URL in this trusted job.
2. In a separate reviewed PR, check out that trusted main SHA into a separate
   directory with persist-credentials=false. Use the exact approved repository
   ID, authority origin, App ID/slug and check name. Supply a read-only GitHub
   token solely through GH_TOKEN. Run the following from the trusted directory,
   using the runner's original event file as an untrusted locator:

       node services/cla-cloudflare/scripts/ci-candidate.mjs \
         --config /reviewed/config.json \
         --event-file "$GITHUB_EVENT_PATH" --event pull_request

   Check its actual exit code; there is no continue-on-error or cached-success
   path. Enable policy/provider and required context only after independent live
   service and recovery acceptance. No new token/credential is created here.

The consumer obtains current repository metadata, reads policy and both legal
texts at the current trusted default-branch commit, checks exact hashes, then
requires the current PR head/base and one unambiguous owned Check Run. It queries
the live authority twice with no GitHub credential, between final GitHub reads;
the authority revalidates real signing facts, contributor set, revision and
current authorization. A suspended/revoked/expired/disabled or stale proof fails.
Event fields cannot replace configuration or trusted source. Redirects, extra
origins, changed default-branch commits and retargeted base branches fail closed.

Merge queue remains an explicit blocker: native proofs currently support PRs,
not merge-group candidates. This entry rejects merge_group before any request.
Do not reuse a PR proof for a queue head, make a merge-group branch return green,
or replace the current required gate until an independently reviewed native
merge-group binding/recovery design or an explicit approved queue policy exists.
The existing Python cla_service_verify.py does not provide these dual live
authority reads and is not silently substituted into a successful native gate.

Offline CI tests use actual service/evidence verification with a pre-existing
synthetic confirmed publication at fixed time, and an in-memory GET transport.
They send no HTTP, publish no check and establish no live platform acceptance.
