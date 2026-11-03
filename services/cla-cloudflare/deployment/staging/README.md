# CLA staging rehearsal

STAGING ONLY. These inputs deploy the native service to
`https://cla-staging.tavotto.com` against the private sandbox
`erwanjun/cla-e2e-sandbox-20261026` and App `erwanjun-cla-e2e-20261026`
(id 5480322, installation 175465132). The agreements it serves are staging
test texts: a staging signature has no legal effect. Nothing here touches the
Tavotto repository's `cla-policy.json`, `provider.configured`, CI or required
checks.

| File | What it is |
|---|---|
| `config.json` | The reviewed `CLA_CONFIG`. Identity pins only, no secrets. `referenceSha` is the sandbox `main` commit carrying the policy. |
| `wrangler.staging.json` | Manifest with real resource IDs. Every capability flag is `false` in the file. `CLA_CONFIG` is `config.json` as a string (a test keeps them equal). |
| `assets/CLA_INDIVIDUAL.md` | Byte copy of the sandbox's `docs/legal/CLA_INDIVIDUAL.md`; its SHA-256 is the pinned individual digest. |

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
   disposable. If the readiness query below is not `1 / 88193520 / 2 / 39`,
   delete and recreate the database instead of repairing it.

       node scripts/staging-d1.mjs deployment/staging/config.json > /tmp/cla-staging.sql
       $W d1 execute tavotto-cla-staging --remote $C --file /tmp/cla-staging.sql
       $W d1 execute tavotto-cla-staging --remote $C --command "SELECT (SELECT count(*) FROM cla_native_v2_authority) a,(SELECT holder FROM cla_native_v2_authority) h,(SELECT count(*) FROM cla_native_v2_agreements) g,(SELECT count(*) FROM sqlite_master WHERE type='trigger') t"

   Rehearsed on local D1 (`--local`) on 2026-11-03: `1 / 88193520 / 2 / 39`.

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
`holderName` in `config.json` (`Jiaqi Wan (staging)`); anything else is
`personal_evidence_required`.

    $W deploy $C --var CLA_GITHUB_ENABLED:true --var CLA_OAUTH_ENABLED:true --var CLA_UI_ENABLED:true --var CLA_SIGNING_ENABLED:true

Expect: the non-exempt test account signs in, sees the staging text, signs the
individual agreement; `erwanjun` countersigns as holder; the signature rows
carry the pinned digest. Record the observed responses before S4.

## S4 — automatic results on sandbox PRs

App settings: webhook active, URL `https://cla-staging.tavotto.com/webhooks/github`,
secret from `~/.cla-staging-webhook-secret`, subscribe to **Pull request**
(merge group is not available in a personal-account repository).

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

## Later steps

S5 (CI consumer in the sandbox) and S6 (merge queue, which needs an
organization-owned sandbox) are planned in the rehearsal plan and get their
own PRs. Production cut-over is a separate approval: production resources,
moving the paper signature in `offline_signatures` into the service in the
same PR that sets `provider.configured=true`, then `ci.yml` wiring (risk:high).
