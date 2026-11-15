# CLA production

PRODUCTION. These inputs deploy the native service to `https://cla.tavotto.com`
for `Tavotto/Tavotto` (repository 1335721568) with the GitHub App `tavotto-cla`
(id 5740523, installation 181505049, owned by the Tavotto organization; checks,
contents, pull requests and merge queues **read only**; no webhook). Signatures
collected here are real and legally binding.

Production runs the live-eligibility protocol only ([ADR 0172](../../../../docs/adr/0172-cla-live-eligibility.md)):
CI asks `/eligibility/pr` and `/eligibility/group` directly. There is no check
publishing, no webhook, no cron and no confirmation slot. Contributors who
countersigned a paper agreement before the service existed stay exemptions
([ADR 0183](../../../../docs/adr/0183-cla-paper-signatures-as-exemptions.md)).

| File | What it is |
|---|---|
| `config.json` | The reviewed `CLA_CONFIG`. Identity pins only, no secrets. `policy` is the default-branch `.github/cla-policy.json` (agreements, exemptions, provider) verbatim — the service refuses to sign or answer (`trusted_policy_changed`) when they differ, and a test keeps them equal. `referenceSha` is only checked by the disabled agreement-activation journal. |
| `wrangler.production.json` | Manifest with real resource IDs: D1 `tavotto-cla`, limiter namespaces 26111201–03 (distinct from staging). Every flag is `false` in the file, no cron. `CLA_CONFIG` is `config.json` as a string (tested). |
| `assets/CLA_INDIVIDUAL.md` | Byte copy of `docs/legal/CLA_INDIVIDUAL.md`; its SHA-256 is the pinned individual digest (tested). |
| `assets/CLA_INDIVIDUAL.pdf` | US Letter rendering of that exact Markdown (`npm run render:agreement-pdf:production`); a test fails on a stale copy. |
| `assets/*-OFL.txt`, `assets/cla-sig-pen-LICENSE.txt` | Font licences, copied from staging. The `.ttf` files and `cla-pdf.js` are not committed; `npm run build:pdf-client -- deployment/production/assets/cla-pdf.js` and `node scripts/fetch-pdf-font.mjs --dest deployment/production/assets` produce them before every deploy. |

Offline checks (no network):

    node scripts/deployment-plan.mjs --production deployment/production/wrangler.production.json
    node --test tests/production-deployment.test.mjs

All commands below run from `services/cla-cloudflare/` after `npm ci --ignore-scripts`, by
the maintainer with their own Cloudflare login:

    W="npx --yes --package=node@24 --package=wrangler@4.158.0 -- wrangler"
    C="-c deployment/production/wrangler.production.json"

## P1 — database (empty → schema → seed)

1. Schema, on the **empty** database only (`wrangler d1 create tavotto-cla` was run on 2026-11-13):

       node scripts/production-d1.mjs schema deployment/production/config.json > /tmp/cla-prod-schema.sql
       $W d1 execute tavotto-cla --remote $C --file /tmp/cla-prod-schema.sql

2. Seed, atomically. `apply` first checks that the remote schema is exactly this checkout's
   (same tables, indexes, triggers) and that no signing table has rows, then submits the
   bootstrap statements as **one `db.batch`** (a D1 transaction) through wrangler's remote
   binding, then runs the service's own readiness read:

       npx --yes --package=node@24 --package=wrangler@4.158.0 -- node scripts/production-d1.mjs apply deployment/production/config.json deployment/production/wrangler.production.json

   Expect `{"authority":1,"holder":88193520,"agreements":2,"triggers":41}`. A repeat is a no-op.
   If step 1 failed half-way or `apply` reports `production_schema_mismatch`, nothing is seeded:
   delete and recreate the empty database and start again. **Never** use the staging
   `--rebuild` here, and never drop tables once anyone has signed.

## P2 — first deploy (all off) and secrets

    npm run build:pdf-client -- deployment/production/assets/cla-pdf.js
    node scripts/fetch-pdf-font.mjs --dest deployment/production/assets
    $W deploy $C

Expect `https://cla.tavotto.com/health` → HTTP 503 `{"runtime":"workers-d1","enabled":false,...}`.

Secrets, each through a pipe (an interactive `secret put` run without a terminal stores an
empty string and still reports success). Nothing is printed, committed or sent elsewhere.
There is no webhook secret: the webhook route stays off.

    (umask 077; openssl rand -base64 32 | tr -d '\n' > ~/.cla-prod-evidence-key)   # save it in the password manager now
    $W secret put CLA_EVIDENCE_KEY $C < ~/.cla-prod-evidence-key
    openssl pkcs8 -topk8 -nocrypt -in PATH/TO/tavotto-cla.private-key.pem -outform DER | base64 | tr -d '\n' | $W secret put CLA_APP_PKCS8 $C
    pbpaste | tr -d '\n' | $W secret put CLA_CLIENT_SECRET $C   # copy the App's client secret first

**`CLA_EVIDENCE_KEY` encrypts every signature's evidence and is not recoverable.** Copy
`~/.cla-prod-evidence-key` into the maintainer's password manager before deleting the file. Losing it makes every stored signature unreadable; rotating it is
not supported. The App private key can be regenerated in the App settings (put the new one,
then delete the old key there).

App settings: callback URL `https://cla.tavotto.com/oauth/callback`, App public, webhook inactive.
The installation must have accepted all four read permissions (Organization settings → GitHub
Apps → tavotto-cla → review the permission request).

## P3 — turn the service on (before the policy PR merges)

    $W deploy $C --var CLA_GITHUB_ENABLED:true --var CLA_OAUTH_ENABLED:true --var CLA_UI_ENABLED:true --var CLA_SIGNING_ENABLED:true \
      --var CLA_CI_VERIFICATION_ENABLED:true --var CLA_MERGE_PREFIX_ASSESSMENT_ENABLED:true --var CLA_MERGE_PREFIX_READ_SESSION_ENABLED:true

`--var` applies to that deploy only: **every later deploy repeats this full flag set**, or the
service returns to all-off. Never set `CLA_CHECK_PUBLISHING_ENABLED`, `CLA_BACKGROUND_ENABLED`,
`CLA_AUTOMATIC_*`, `CLA_CONFIRMATION_SLOT_ENABLED`, `CLA_DELEGATED_COUNTERSIGNING_ENABLED` or
`CLA_AGREEMENT_ACTIVATION_ENABLED` here.

Until the policy PR is on `main`, signing and eligibility answer `trusted_policy_changed`
(the default-branch provider still says `configured: false`). That is expected.

## P4 — merge the policy PR, then verify

Merging the PR that sets `provider.configured: true` switches the `cla-check` job to service
mode for every pull request and merge group. Then:

1. A non-exempt test account signs at `https://cla.tavotto.com`; the rights holder
   countersigns with name and signature exactly `Jiaqi Wan`.
2. That account opens a pull request: `cla-check` → success after signing, failure before.
3. Queue it: the merge group's `cla-check` → `fresh_merge_candidate_qualified`.
4. An internal pull request (rights holder only) still passes locally by id, without a
   service request.

Merge queue: there is no confirmation slot and no ordering rule. External pull requests are
queued like any other; a merge group asks the service once with a fresh nonce.

## Rollback

- **Repository side, immediate:** revert the policy PR. `provider.configured` goes back to
  `false`, the gate stops asking the service, and `countersigned_paper` entries keep
  qualifying locally. Contributors who signed online are blocked again until it is restored;
  their signatures stay in D1.
- **Service side:** `$W deploy $C` without `--var` returns every capability to off.
- **Never** roll the database back to a snapshot or rebuild it after real signatures exist:
  signatures, holds and withdrawals are immutable history.

Free plan: Workers Free (10 ms CPU per invocation nominal). The staging rehearsal saw
peaks of 17–75 ms that were not stopped (2026-11-03); the maintainer decided not to pay
for the CLA service. If invocations start ending `exceededCpu`, report it — do not
change limits in code.
