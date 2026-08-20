# Tavotto telemetry proxy

A small, standard-library-only service that sits between Tavotto clients and the
analytics backend (PostHog).

```
Tavotto (desktop / browser)
   │  privacy-safe event, allowlisted, no provider key
   ▼
telemetry.tavotto.com          ← this service
   │  server-side normalized event, $geoip_disable, no client IP/headers
   ▼
PostHog (US or EU)
```

**It is not part of the Tavotto package.** It is excluded from the wheel and
sdist (`pyproject.toml`), adds no runtime dependency to Tavotto, and the
application only ever knows a URL.

## Why a proxy at all

* The application must not contain a PostHog project key. Anything embedded in an
  open-source desktop app is public; a "secret" there authenticates nobody.
* The provider must be replaceable. Everything PostHog-specific lives in one
  file (`tavotto_telemetry_proxy/posthog.py`), the way the PDF backend is
  confined to one module in the main repo.
* Defense in depth: the client filters events, and the proxy filters them again
  against its own copy of the contract. Neither side trusts the other.
* PostHog sees a request from this service, not from the user's machine, so no
  client IP reaches it as analytics data.

## Layout

```
services/telemetry_proxy/
  tavotto_telemetry_proxy/
    contract.py   event + property allowlists (mirror of the client's)
    core.py       validation, auth, routing — all business logic, platform-neutral
    posthog.py    the ONLY module that knows PostHog's JSON
    wsgi.py       generic WSGI adapter + `python -m …wsgi` dev server
  api/index.py    thin Vercel entry point (protocol shuttling only)
  vercel.json     rewrites /healthz and /v1/* onto that one function
```

Swapping hosting providers means rewriting `api/index.py`. Nothing else.

## Endpoints

| Route | Auth | Purpose |
|---|---|---|
| `GET /healthz` | none | liveness |
| `POST /v1/events` | none (necessarily public) | product events from desktop clients |
| `POST /v1/metrics` | `Authorization: Bearer $TAVOTTO_METRICS_TOKEN` | distribution snapshots from the scheduled collector |

`/v1/events` enforces: POST only · `Content-Type: application/json` · body ≤ 8 KiB ·
`schema_version` must match · event name on the allowlist · every property on the
allowlist · scalar values only (no nested objects or arrays exist in the schema) ·
bounded integers · bounded string lengths and character sets · `distinct_id` must
be a UUIDv4.

`/v1/metrics` uses a **separate** allowlist, forces the constant distinct ID
`distribution_metrics`, always sets `$process_person_profile: false`, and accepts
a batch of at most 500 events (≤ 256 KiB).

Token comparison uses `hmac.compare_digest`. The token is never echoed in a
response and never logged. When `TAVOTTO_METRICS_TOKEN` is unset the metrics
endpoint is **closed**, not open.

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `POSTHOG_INGEST_URL` | no | `https://us.i.posthog.com/batch/` | **full** batch-ingest URL. EU: `https://eu.i.posthog.com/batch/`. Self-hosted: your own host + `/batch/`. No region is hardcoded in the code. |
| `POSTHOG_PROJECT_KEY` | **yes** | — | the project API key (`phc_…`). Without it the service fails loudly with 502 rather than silently accepting and dropping events. |
| `TAVOTTO_METRICS_TOKEN` | yes for `/v1/metrics` | — | long random string; also set as a GitHub Actions secret. |
| `POSTHOG_PERSON_PROFILES` | no | `identified` | `anonymous` switches product events to `$process_person_profile: false`. See below. |

### Person profiles

Product events are forwarded with person profiles left on, because the anonymous
`distinct_id` *is* the analysis unit and retention/funnel insights are built on
it. Tavotto never calls `identify`, never sets a person property, and never
attaches an email or name — the person record holds a random UUID and nothing
else. Setting `POSTHOG_PERSON_PROFILES=anonymous` makes product events anonymous
(cheaper), at the cost of degrading some person-based insights; upstream does not
enumerate exactly which, so it is not the default. Distribution snapshots are
**always** anonymous regardless of this setting.

### Deduplication

Every forwarded event carries a `uuid`; for distribution snapshots it is
`uuid5(ns, snapshot_key)` so re-running the collector yields the same UUID.
PostHog's public docs do **not** guarantee idempotent dedup on that field, so
every snapshot also carries a stable `snapshot_key` and the dashboard queries
deduplicate on it explicitly (see `docs/analytics/yc-dashboard.json`).

## Deploy

1. **Create or pick a PostHog project.** Product analytics project; no session
   replay, no autocapture (the client sends neither).
2. **Choose a region.** US → `https://us.i.posthog.com/batch/`;
   EU → `https://eu.i.posthog.com/batch/`. Pick before sending data — moving a
   project between regions later is not a config change.
3. **Copy the project API key** (Project settings → Project API key, `phc_…`).
   This key is write-only ingestion, but treat it as a secret anyway.
4. **Set `POSTHOG_INGEST_URL`** to the full batch URL from step 2.
5. **Set `POSTHOG_PROJECT_KEY`** to the key from step 3.
6. **Generate `TAVOTTO_METRICS_TOKEN`:**
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
   Store it in the host's environment settings. Never in git, never in YAML.
7. **Deploy this directory.** With Vercel:
   ```bash
   cd services/telemetry_proxy
   vercel deploy --prod
   ```
   `vercel.json` routes `/healthz` and `/v1/*` to `api/index.py`. There is
   nothing to build — no dependencies, no lockfile.
8. **Map `telemetry.tavotto.com`** to the deployment (host dashboard → Domains →
   add `telemetry.tavotto.com`, then add the CNAME/A record the host shows at
   your DNS provider). Confirm HTTPS is issued, then:
   ```bash
   curl -sS https://telemetry.tavotto.com/healthz
   # {"ok": true, "service": "tavotto-telemetry-proxy"}
   ```
9. **Add the GitHub secret.** Repository → Settings → Secrets and variables →
   Actions → New repository secret: `TAVOTTO_METRICS_TOKEN`, same value as
   step 6. Optionally add repository *variable*
   `TAVOTTO_TELEMETRY_METRICS_URL` if the proxy is not at the default address
   (a URL is not a secret).
10. **Run the collector manually.** Actions → *Distribution metrics* → Run
    workflow. Run it once with `dry_run = true` first and read the printed
    classification, then again for real.
11. **Verify the distribution events arrived** in PostHog: filter on
    `distinct_id = distribution_metrics` and confirm
    `github_release_asset_snapshot`, `pypi_daily_downloads` and
    `github_repo_snapshot`. Spot-check that `asset_role` splits `installer` from
    `updater` the way `docs/analytics/yc-metrics.md` expects.
12. **Test one development client** without touching production:
    ```bash
    TAVOTTO_TELEMETRY_ENDPOINT=https://telemetry.tavotto.com/v1/events tavotto
    ```
    Opt in when prompted, do one export, and confirm the event appears. To test
    against a *local* proxy instead:
    ```bash
    cd services/telemetry_proxy
    POSTHOG_PROJECT_KEY=phc_… POSTHOG_INGEST_URL=https://us.i.posthog.com/batch/ \
      python3 -m tavotto_telemetry_proxy.wsgi          # listens on 127.0.0.1:8787
    # in another shell
    TAVOTTO_TELEMETRY_ENDPOINT=http://127.0.0.1:8787/v1/events tavotto
    ```
13. **Verify no forbidden fields are present.** In PostHog, open a raw
    `export_completed` event and confirm the property list is exactly the
    allowlist plus `$geoip_disable` and `schema_version` — no filename, path,
    stem, prompt, `$ip`-derived geo, or user agent.
14. **Only then ship telemetry-enabled Tavotto builds.**

### Deployment order (matters)

```
1. deploy the proxy with the new schema
2. verify PostHog ingestion
3. configure / run the collector
4. release the Tavotto client
```

The proxy rejects unknown events and unknown `schema_version`s. Releasing a
client that emits an event the deployed proxy has not learned yet means that
event is silently 400'd for as long as the old proxy is live — the client drops
it and nobody notices.

## Rate limiting (do this at the edge)

`/v1/events` is necessarily public: a desktop application cannot hold a secret.
The schema, the size cap, the short upstream timeout and the UUID check make
abuse cheap to reject but do not make it impossible. Configure rate limiting at
the hosting/CDN layer, which is where it belongs (a per-process counter is
meaningless on serverless — every instance counts separately):

* `POST /v1/events` — on the order of 60 requests/minute per IP, burst 20.
* `POST /v1/metrics` — a handful per hour; it is called once a day by CI.
* Prefer the platform's built-in WAF/rate-limit rules over anything in this code.

Do not add a fake "app secret" to the client to compensate. It would be public
within a day of the first release and would only make the threat model harder to
reason about.

## Tests

```bash
python -m pytest tests/test_telemetry_proxy.py tests/test_distribution_metrics.py
```

They run from the main repository (the proxy is imported by path) and cover
schema rejection, the metrics bearer token, secret leakage in error paths,
header non-forwarding, GeoIP disabling, and **client↔proxy contract parity**.
No test contacts PostHog.
