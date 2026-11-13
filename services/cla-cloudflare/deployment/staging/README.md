# CLA staging rehearsal

STAGING ONLY. These inputs deploy the native service to
`https://cla-staging.tavotto.com` against the public organization sandbox
`tavotto-cla-sandbox/cla-mq-sandbox` (repository 1445721664, merge queue on
`main`) and App `erwanjun-cla-e2e-20261026` (id 5480322, installation 178570638;
permissions checks:write, contents/pull_requests/merge_queues:read; events
Pull request and Merge group). S1–S5 ran against the personal sandbox
`erwanjun/cla-e2e-sandbox-20261026`, which is no longer configured. The agreements it serves are staging
test texts: a staging signature has no legal effect. Nothing here touches the
Tavotto repository's `cla-policy.json`, `provider.configured`, CI or required
checks.

| File | What it is |
|---|---|
| `config.json` | The reviewed `CLA_CONFIG`. Identity pins only, no secrets. `referenceSha` is the sandbox `main` commit carrying the policy. |
| `wrangler.staging.json` | Manifest with real resource IDs. Every capability flag is `false` in the file. `CLA_CONFIG` is `config.json` as a string (a test keeps them equal). |
| `assets/CLA_INDIVIDUAL.md` | Byte copy of the sandbox's `docs/legal/CLA_INDIVIDUAL.md`: a one-paragraph STAGING COPY / no-legal-effect notice followed by the real `docs/legal/CLA_INDIVIDUAL.md`, byte for byte (a test checks both). Its SHA-256 is the pinned individual digest. When the real text changes, update the sandbox copy, `referenceSha`, both digests, re-render the PDF and rebuild the staging database. |
| `assets/CLA_INDIVIDUAL.pdf` | US Letter rendering of that exact Markdown, with its SHA-256 in the PDF keywords. Regenerate with `npm run render:agreement-pdf` (headless Chrome, offline) whenever the Markdown changes; a test fails on a stale copy. |
| `assets/cla-pdf-font-OFL.txt` | Licence (SIL OFL 1.1) of the Great Vibes signature typeface; committed, served unchanged. |
| `assets/cla-sig-{signature,hand}-OFL.txt`, `assets/cla-sig-pen-LICENSE.txt` | Licences of the other signature styles (Mrs Saint Delafield, Patrick Hand: OFL 1.1; Homemade Apple: Apache 2.0); committed. |
| `assets/cla-sig-{signature,pen,hand}.ttf` | Not committed, like `cla-pdf-font.ttf`; fetched by the same command. |
| `assets/cla-pdf-font.ttf` | Not committed (git-ignored; the repository ships no font binaries). `npm run fetch:pdf-font` downloads it from a commit-pinned google/fonts URL and verifies its SHA-256 (`.part`, rename only on success, delete and fail on mismatch). `npm run build:pdf-client` runs it first; CI has a separate fetch step before `npm run check`. Failures use the codes `pdf_font_network_unreachable`, `pdf_font_http_status`, `pdf_font_hash_mismatch` and exit non-zero. |
| `assets/cla-pdf.js` | Not committed (git-ignored). The browser-side PDF builder; produce it with `npm run build:pdf-client` before `wrangler deploy`. |

Offline checks (no network):

    node scripts/deployment-plan.mjs --staging deployment/staging/wrangler.staging.json
    node --test tests/staging-deployment.test.mjs

The `staging` profile of `deployment-plan.mjs` admits exactly one custom-domain
route (`cla-staging.tavotto.com`), only the two proposed crons, and still
requires every flag to be `false` in the file. The default profile keeps
rejecting any route or cron.

## How flags are turned on

The Worker reads capabilities only from the `CLA_*_ENABLED` vars; the JSON
config cannot turn anything on. Each step deploys the same manifest and passes
the step's flags with `--var NAME:true`. `--var` applies only to that deploy,
so **every deploy repeats the full flag set of its step**; a deploy without
them returns the service to all-off. Crons live in the manifest and are added
in the manifest (added for S4).

Never set during the rehearsal: `CLA_DELEGATED_COUNTERSIGNING_ENABLED`
(needs the rights holder's real authorization, ADR 0137) and
`CLA_AGREEMENT_ACTIVATION_ENABLED`.

All commands run from `services/cla-cloudflare/` after `npm ci --ignore-scripts`.
`W="npx wrangler@4"` and `C="-c deployment/staging/wrangler.staging.json"`.

## S2 — database, first deploy (all off), secrets

1. Schema and seed. **Not atomic**: unlike the production bootstrap (one
   `db.batch`, see the service README), staging applies a file with
   `d1 execute`. That is acceptable only because this database is new and
   disposable. If the readiness query below is not `1 / 88193520 / 2 / 41`,
   delete and recreate the database instead of repairing it.

       node scripts/staging-d1.mjs deployment/staging/config.json > /tmp/cla-staging.sql
       $W d1 execute tavotto-cla-staging --remote $C --file /tmp/cla-staging.sql
       $W d1 execute tavotto-cla-staging --remote $C --command "SELECT (SELECT count(*) FROM cla_native_v2_authority) a,(SELECT holder FROM cla_native_v2_authority) h,(SELECT count(*) FROM cla_native_v2_agreements) g,(SELECT count(*) FROM sqlite_master WHERE type='trigger') t"

   Rehearsed on local D1 (`--local`) on 2026-11-03 (before holder releases, 39 triggers); the current schema gives `1 / 88193520 / 2 / 41` (in-memory SQLite, 2026-11-04).

   **Rebuild** (wipes every staging signature, cannot be undone): needed
   after changing `holderName` or the agreement text, or to clear a test
   account's withdrawal (withdrawals are immutable by design; the rights
   holder can also release one in the signing page, which leaves the record). Same
   database ID; config and secrets stay. Then rerun the readiness query
   above and expect the same result.

       node scripts/staging-d1.mjs --rebuild deployment/staging/config.json > /tmp/cla-staging-rebuild.sql
       $W d1 execute tavotto-cla-staging --remote $C --file /tmp/cla-staging-rebuild.sql

   Used on 2026-11-04 to clear a held test account: 104 statements, readiness
   `1 / 88193520 / 2 / 39`, no withdrawals left.

   The `eligibility_releases` table (holder release of a withdrawal) adds one
   table and two triggers: a database built before it needs one `--rebuild`,
   and every later readiness query reads `1 / 88193520 / 2 / 41`.

2. First deploy, every flag off:

       $W deploy $C

   Expect `https://cla-staging.tavotto.com/health` → HTTP 503
   `{"runtime":"workers-d1","enabled":false,...}`.

3. Secrets. Values go straight from the generator into Wrangler; none is
   printed, committed or sent anywhere else.

       openssl rand -base64 32 | tr -d '\n' | $W secret put CLA_EVIDENCE_KEY $C
       openssl pkcs8 -topk8 -nocrypt -in PATH/TO/app.private-key.pem -outform DER | base64 | tr -d '\n' | $W secret put CLA_APP_PKCS8 $C
       pbpaste | tr -d '\n' | $W secret put CLA_CLIENT_SECRET $C   # copy the App's client secret first
       umask 077; openssl rand -hex 32 > ~/.cla-staging-webhook-secret
       tr -d '\n' < ~/.cla-staging-webhook-secret | $W secret put CLA_WEBHOOK_SECRET $C

   Every secret goes in through a pipe. Run without a terminal (for example
   through an agent's shell), an interactive `secret put` reads an empty stdin,
   stores an empty string and still reports success; the 2026-11-03 rehearsal
   lost its OAuth client secret this way (`oauth_rejected` on every callback).

   `CLA_EVIDENCE_KEY` must decode to exactly 32 bytes (AES-256-GCM).
   `CLA_APP_PKCS8` is base64 DER without PEM armour. The webhook secret file
   is read once more at S4 to paste into the App settings.

## S3 — signing page

App settings: add the callback
`https://cla-staging.tavotto.com/oauth/callback` (the earlier workers.dev
callback can stay; GitHub Apps accept several), and make the App **public**:
a private App returns GitHub's 404 to every account except its owner. The
sandbox repository must be public too: the token exchange is scoped with
`repository_id`, and GitHub answers `repository_not_found` for a contributor
who cannot see a private repository.

The holder countersigns with name and signature exactly equal to
`holderName` in `config.json` (`Jiaqi Wan`); anything else is
`personal_evidence_required`.
Stored holder signatures are re-checked against the current `holderName`
on every read, so changing it makes earlier staging countersignatures
unreadable: rebuild the database (below) whenever it changes.

    $W deploy $C --var CLA_GITHUB_ENABLED:true --var CLA_OAUTH_ENABLED:true --var CLA_UI_ENABLED:true --var CLA_SIGNING_ENABLED:true

Expect: the non-exempt test account signs in, sees the staging copy of the agreement, signs the
individual agreement; `erwanjun` countersigns as holder; the signature rows
carry the pinned digest. Record the observed responses before S4.

## S4 — automatic results on sandbox PRs

App settings: webhook active, URL `https://cla-staging.tavotto.com/webhooks/github`,
secret from `~/.cla-staging-webhook-secret` copied **without** its trailing
newline (`tr -d '\n' < ~/.cla-staging-webhook-secret | pbcopy`), and tick
**Pull request** under *Subscribe to events* (without it GitHub sends only
`ping`). Merge group is not available in a personal-account repository.

    $W deploy $C --var CLA_GITHUB_ENABLED:true --var CLA_OAUTH_ENABLED:true --var CLA_UI_ENABLED:true --var CLA_SIGNING_ENABLED:true \
      --var CLA_CHECK_PUBLISHING_ENABLED:true --var CLA_BACKGROUND_ENABLED:true --var CLA_AUTOMATIC_VERIFICATION_ENABLED:true \
      --var CLA_CONFIRMATION_SLOT_ENABLED:true --var CLA_AUTOMATIC_PUBLISHING_ENABLED:true

Expect: a PR by the test account gets a `Tavotto CLA (staging)` Check Run
within one `7-59/15` tick; failure before signing, success after a new commit
once signed, failure again after revocation.

The manifest carries both crons from S4 on: `*/2 * * * *` drives recovery
(`CLA_BACKGROUND_ENABLED`) and `7-59/15 * * * *` drives new-head observation and
dispatch (`CLA_AUTOMATIC_VERIFICATION_ENABLED`). With those flags off a tick
returns immediately, so S3-only deploys stay inert.

CPU: keep `$W tail tavotto-cla-staging --format json` running during S3–S4 and
record each invocation's platform CPU time (and the dashboard's CPU
percentiles). Free allows 10 ms per invocation. Exceeding it means moving to
Workers Paid; no code change and no limit bypass.

Observed on 2026-11-03 (free plan): PR signed → `success`, hold + new commit
→ `failure`; 275 invocations all `ok`. CPU peaks above the 10 ms figure
without being stopped: new-head cron 75 ms, recovery confirmation 44 ms,
webhook admission 33–50 ms.

## S5 — CI consumer in the sandbox

`ci.json` is the consumer configuration. The sandbox workflow checks out this
repository at a pinned commit into its own directory
(`persist-credentials: false`), never the PR checkout, and runs:

    node trusted/services/cla-cloudflare/scripts/ci-candidate.mjs \
      --config trusted/services/cla-cloudflare/deployment/staging/ci.json \
      --event-file "$GITHUB_EVENT_PATH" --event pull_request

with `GH_TOKEN` set to the workflow's read-only token. Deploy with the S4 flags
plus `--var CLA_CI_VERIFICATION_ENABLED:true`. The exit code is the verdict.

## Later steps

S6 (merge queue). Deploy with the S5 flags plus
`--var CLA_MERGE_PREFIX_ASSESSMENT_ENABLED:true --var CLA_MERGE_PREFIX_READ_SESSION_ENABLED:true`.
The read session is required on the free plan, not optional: without it every
installation token costs two requests and the two prefix snapshots are read in
full, so a one-PR group needs 36 external requests against the 35 work cap and
every attempt ends `group_resource_capacity_exhausted` (staging, 2026-11-05).
With it a one-PR group publishes in about 30. Each scheduled run logs one JSON
line with its disposition and, on failure, the fixed rejection code (Workers
Logs, enabled in `wrangler.staging.json`). On a `merge_group`
`checks_requested` webhook the service records the group, assesses every PR in
the queue prefix (each member's author must be eligible) and publishes the check
on the group head. Repointing to a new sandbox repository changes
`repositoryId`, so rebuild the database (`--rebuild`) first. In the sandbox:
a ruleset on `main` with the merge queue and the required check
`Tavotto CLA (staging)` restricted to the App; the workflow also runs on
`merge_group` (`--event "${{ github.event_name }}"`). Production cut-over: [`../production/README.md`](../production/README.md) (ADR 0183 keeps the paper signature as a
`countersigned_paper` exemption instead of moving it into the service).
