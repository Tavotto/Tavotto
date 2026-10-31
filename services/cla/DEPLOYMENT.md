# Deployment and activation checklist (not executed)

## Decisions that require the owner's approval

1. Host/provider, region, domain, currency and maximum monthly cost. Recommended
   first deployment: one small Linux VM, a trusted service account, TLS reverse
   proxy, one Gunicorn process and one serial worker; durable encrypted local disk.
   No price, provider, region, purchase or deployment has been selected here.
2. Private evidence retention/deletion policy, storage location, access holders,
   encrypted off-host backup and restore procedure, encryption-key custody and
   disaster recovery. The service contains names, postal addresses and signatures.
   Never put its database, keys, logs or receipts into a public repo or CI artifact.
3. App installation limited to `Tavotto/Tavotto`, OAuth sign-in, immutable owner and
   exempt-account IDs verified independently, and the permissions below. Creating
   the App and credentials or expanding persistent access requires separate approval.
4. Add the distinct App-owned `Tavotto CLA merge decision` as a required check,
   pinned to this App, alongside the existing three stable CI gates. This is an
   explicit proposed exception to the repository's current three-context rule;
   **no ruleset, branch protection or policy was changed by this implementation**.
   Confirm the merge queue is mandatory and bypass policy is understood.
5. Decide whether old already-failed CI runs should be rerun by a human (minimum
   privileges) or whether a narrowly scoped automatic rerun component is wanted.
   The shipped service updates its own checks automatically but does not rerun
   Actions workflows. Automatic reruns need separately approved Actions: write
   plus a workflow/run allowlist; that optional write adapter is not shipped.

## Proposed GitHub App permissions

| Feature | Repository permission | Purpose |
|---|---|---|
| All modes | Metadata: read (implicit) | Repository identity |
| All modes | Contents: read | Default-branch policy and commit comparisons |
| All modes | Pull requests: read | PR identities and metadata |
| All modes | Checks: write | Create/update only this App's checks; includes reads |
| Candidate verification | Merge queues: read | `merge_group` webhook + live queue reads |
| Two-stage final decision | Actions: read | Bind gate checks to immutable workflow IDs and their exact candidate run/check-suite, rather than names alone |

No Contents/PR/Issues/Administration write, no Actions write, no organization
permissions, no email-address scope. The App token request narrows itself to one
numeric repository ID and the selected modes' permissions. User OAuth tokens are
used only for `/user`, then discarded; they are never used to post checks.

Official references checked for this implementation:
- [GitHub App user access tokens/PKCE](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [merge_group webhook and Merge queues read](https://docs.github.com/en/webhooks/webhook-events-and-payloads#merge_group)
- [GraphQL MergeQueue and MergeQueueEntry](https://docs.github.com/en/graphql/reference/pulls#mergequeue)
- [Repository.mergeQueue(branch)](https://docs.github.com/en/graphql/reference/repos#repository)
- [Check runs](https://docs.github.com/en/rest/checks/runs)
- [Workflow runs](https://docs.github.com/en/rest/actions/workflow-runs)

## Trusted runtime and environment

Build/review a pinned release out of band. Do not execute PR code on the host,
checkout PR revisions there, or grant PR Actions access to its filesystem/secrets.
Run on a single host, with local SQLite on a durable encrypted disk; do not use an
ephemeral/serverless filesystem, multiple hosts sharing SQLite, or a network drive.
The code enforces a data directory outside the source tree, mode 0700, DB/WAL/SHM
mode 0600 and one worker lock. Back up via SQLite's online backup interface (a live
DB file copied without its WAL is not a valid backup); test restoration privately.

Required non-secret environment (values must be independently verified, not guessed):

```
CLA_RELEASE_ROOT=/opt/tavotto-cla/release
CLA_PRIVATE_DIR=/var/lib/tavotto-cla
CLA_ORIGIN=https://<approved-domain>
CLA_REPOSITORY_ID=<verified numeric repository ID>
CLA_HOLDER_ID=<verified numeric GitHub ID of Jiaqi Wan>
CLA_APP_ID=<verified numeric independent App ID>
CLA_APP_SLUG=<verified App slug>
CLA_INSTALLATION_ID=<verified repository installation ID>
CLA_CLIENT_ID=<the App OAuth client ID>
CLA_EXEMPT_IDS_JSON=<JSON login-to-numeric-ID map for each current policy exemption>
CLA_SIGNING_ENABLED=false
CLA_MERGE_QUEUE_ENABLED=false
CLA_FINAL_CHECK_ENABLED=false
CLA_TRUSTED_GATES_JSON={}
```

Secret-file path variables (values are supplied through an approved secure flow;
no real credentials have been created or configured):
`CLA_CLIENT_SECRET_FILE`, `CLA_APP_PRIVATE_KEY_FILE`,
`CLA_WEBHOOK_SECRET_FILE`, `CLA_EVIDENCE_KEY_FILE`.
Use separate mode-0600 files readable only by the service account; a strong random
webhook secret and a Fernet key. Do not log or place contents in command arguments.
The environment loader has no test-mode override and pins production API hosts.

Before final-stage activation, `CLA_TRUSTED_GATES_JSON` must map exactly
`CI fast gate`, `CI integration gate`, `CodeQL gate` to their verified numeric
`workflow_id`, `.github/workflows/...` `path`, and `app_id: 15368`.
This identifies the existing CI producer; the CLA provider App itself must never
be GitHub Actions (15368). Never guess these IDs from a fixture.

After approval, reproducibly lock and vulnerability-scan dependencies from official
registries. The local contract tests used cryptography 50.0.0. Gunicorn's production
server and dependency installation are not acceptance-tested here. Example service
commands, run from the immutable release root by the dedicated account:

```
gunicorn 'services.cla.runtime:application()' --bind 127.0.0.1:8080 --workers 1 --threads 4 --forwarded-allow-ips=127.0.0.1 --access-logfile /dev/null --error-logfile /dev/null
python -m services.cla.runtime
```

Use a supervisor to restart either process on failure; `UMask=0077`, no root,
read-only release directory, private writable data path and explicit outbound
GitHub HTTPS access. Start with all three activation flags false.

The TLS proxy must preserve the exact approved Host, set `X-Forwarded-Proto: https`
itself, strip client-supplied forwarding headers, and connect only from the
explicitly trusted loopback address. Gunicorn must not be publicly reachable.
The application checks Host and WSGI scheme and does not trust arbitrary proxy
headers. Production cookies are `__Host-...; Secure; HttpOnly; SameSite=Lax`.

Disable access/error/APM capture of URL queries (OAuth codes), Cookie/Authorization
headers, request/response bodies and evidence downloads. Monitor only aggregate
status/error/queue-age metrics without personal data. A default proxy request-line
log can expose OAuth codes even when application logging is silent. Apply edge
rate/body/time limits: suggested starting cap `/login` 10/min per IP, signing
POSTs 10/min per IP, overall 30 requests/sec, 1 MiB webhook body and 32 KiB forms;
then validate thresholds for the actual deployment. Edge rate limiting and TLS
configuration are required deployment work, not features the WSGI tests certify.

## CI integration and final candidate sequencing

Land the trusted scripts first, with `provider.configured=false`. In a separate
reviewed activation change, set provider identity/name/check name and
`protocol: "tavotto-cla-v1"`, add `checks: read` to the existing secret-free
`cla-check` job, fetch both scripts and policy from trusted default branch, and
run this real entry point on both PR and merge-group events:

```
python3 -I "$TRUSTED/scripts/ci/cla_service_verify.py" \
  --event "$GITHUB_EVENT_NAME" --event-file "$GITHUB_EVENT_PATH" \
  --policy "$TRUSTED/.github/cla-policy.json" --wait-seconds 180
```

`GH_TOKEN` is the read-only workflow token. The trusted fetch must include the
exact agreement documents/hash verification already used by this job. Do not
checkout PR code, add a PR fallback, or provide signing-service secrets. For
unconfigured policy keep the existing legacy path. The new consumer returns
failure if called before activation; it does not change configuration.

Subscribe the independent App to `pull_request` and `merge_group`. GitHub HMACs
and numeric repository/installation IDs are checked before enqueueing. Every
candidate proof binds the exact head and base SHA and freshly fetched queue.
All queued PRs through that candidate's position are checked, including preceding
groups: this is a conservative superset, so it can block more work but never
intentionally omit earlier authors. Queue pagination/count gaps, unknown candidate,
head drift, unresolved authors or changed composition block. The GraphQL field
mapping must be validated against real multi-PR queue payloads before activation.

Two stages, with no cycle:
1. The normal App check `Tavotto CLA` supplies fresh eligibility to `cla-check`, so
   `CI fast gate` and the other existing CI gates can finish.
2. The distinct `Tavotto CLA merge decision` remains pending until the latest runs
   of all three operator-pinned CI workflows/check suites report success on that
   exact candidate SHA. The service then **re-fetches** current queue membership,
   policy, complete contributor lists, both signatures and any review hold, and
   publishes its final decision. The final check must not be in fast-gate `needs`.

The final check is proposed as separately required in branch rules, App-pinned.
It is not enough merely to configure its name. Activation is incomplete until a
real unsigned PR and candidate demonstrably cannot merge under the actual rules.

**Point-in-time semantics:** proof TTL is checked by the CI consumer; GitHub does
not expire a green check based on text in its output. Reconciliation refreshes
checks and places the final check pending before fallible reevaluation. An API
outage cannot revoke an already published success, and a narrow race always
exists after the last successful check and before GitHub merges. The two-stage
check narrows this window; it does not offer atomic continuous revocation or
perpetual fail-closed assurance. No deployment should claim otherwise.

## Real acceptance (still required; no real signatures collected)

Use a private staging repository/App and approved identities first. Confirm:

1. Production Gunicorn behind TLS: secure cookies, exact Host/scheme, OAuth PKCE,
   callback state/browser binding, expiry/replay, no query/header/body capture.
2. A real contributor personally signs the reviewed exact agreement; qualification
   remains blocked. Jiaqi Wan independently signs it himself; private receipts
   match both parties and immutable bytes. No assistant performs either signature.
3. A different account, spoofed App/check, login reassignment, unsigned co-author,
   employer/minor/uncertain identity, head change, old version and missing evidence
   cannot qualify. Check metadata contains no private names/addresses/signatures.
4. Exercise fork PR, >100 commits, malformed/unmapped co-author and PR reopening.
   Confirm old failed workflow behavior and the chosen manual/automatic rerun policy.
5. Exercise one- and multi-PR merge queues, preceding groups, reorder, removal,
   changed base/head and pagination. Confirm exact live GraphQL field semantics;
   any unsupported shape must block rather than be inferred.
6. Hold after initial green while CI is running: final check must remain pending
   or become failure. Wrong workflow, spoofed same-name gate, newer failed run,
   cancelled/skipped/missing gate and stale queue must never pass final decision.
7. Stop worker, block GitHub, rotate/revoke App credentials, restart during a
   transaction/check-create, corrupt an evidence copy, restore backup, and lose
   the evidence key in staging. No new green should be produced; observe/document
   existing-green limitations. Measure reconciliation latency and API budget.
8. Only after user approval of storage, permissions, rules and successful real
   acceptance: activate repository policy and all required modes. Rollback means
   disable new signing and block qualification/merge while investigating, never
   mark unsigned people as signed or silently delete their executed agreements.
