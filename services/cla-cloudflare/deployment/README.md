# Review-only deployment inputs

`wrangler.disabled.json` is a concrete binding-shape template with deliberately
invalid placeholder identifiers. It is not a production or auto-provisioning
configuration. Every capability flag is false, routes and crons are empty, and
workers.dev/preview URLs are disabled. No credential value is supplied.

Run only the offline checker in this PR:

    node scripts/deployment-plan.mjs deployment/wrangler.disabled.json

It prints a plan and fingerprints the input. It never opens D1, contacts a
service, creates a secret, or runs Wrangler. Even a valid disabled manifest
returns ready_to_deploy=false: placeholder replacement, schema/plan acceptance,
origin, authorizations and actual activation require separate approval.

The documented binding fields follow the official [Wrangler configuration](https://developers.cloudflare.com/workers/wrangler/configuration/),
[rate-limit binding](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)
and [assets binding](https://developers.cloudflare.com/workers/static-assets/binding/).
The three limiter namespaces must be distinct and explicitly reviewed; their
counters are per-location and eventually consistent, not global quota guarantees.
Wrangler schema/runtime validation is not claimed by this offline shape check.

Before a separately approved deployment:

1. Review existing D1/limiter IDs, usage and quotas. Do not omit IDs to trigger
   provisioning. Review separate staging/production manifests and origin/routes.
2. Supply CLA_CONFIG as reviewed JSON matching existing authority/agreement rows.
   It must retain autoHolder=false. Public-key pinning needs separate verified
   legal-identity registration; this manifest does not perform it.
3. Export the exact reviewed individual agreement bytes as assets-reviewed/
   CLA_INDIVIDUAL.md; check their SHA-256 against policy. Configure its content
   type as text/plain or text/markdown. Keep assets.run_worker_first=true and
   never expose a bypass asset route.
4. Rehearse namespaced schema/seed statements, verify backups, immutable history,
   DB clock, all D1 atomic guards and a single signing authority. Existing
   head_requests schemas need the new dispatched status before enabling dispatch.
5. Separately approve/custody the listed secrets and existing App/OAuth scopes.
   No private key, OAuth secret or token belongs in vars, source, CLI arguments
   or logs. This PR creates or configures none.
6. Preserve the recovery cron and add the distinct observation/dispatch cron only
   after resource acceptance. Automatic publishing additionally requires its own
   flag plus verification, existing publishing and background recovery flags.
   Enable no flag until independent live acceptance and signed authorization.
7. Land trusted code before any separately reviewed CI gate wiring. A deployment
   or a green observational result cannot replace live authority validation.

Rollback after real signatures/holds/revocation requires a coherent forward
recovery plan. Returning to a stale database snapshot is not a safe rollback.
