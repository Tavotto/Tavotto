# Metric dictionary — what may and may not be called a "user"

This document exists to make one specific failure impossible: putting a number in
a YC application that we cannot defend in the follow-up question. Every metric
below states its population, its window, and what it is **not**.

Companion files:

* [`telemetry-events.md`](telemetry-events.md) — the event contract
* [`yc-dashboard.json`](yc-dashboard.json) — machine-readable dashboard spec

## Vocabulary (use these exact words)

| Term | Means | Does **not** mean |
|---|---|---|
| **opted-in anonymous install** | one random UUIDv4 that has sent ≥1 event | a person, an account, a company |
| **observed user** | shorthand for "opted-in anonymous install" — only use it if the opt-in caveat is stated in the same breath | every user |
| **successful exporter** | an opted-in anonymous install with ≥1 `export_completed` in the window | someone who *tried* to export |
| **distribution download** | one increment of a GitHub asset counter or one PyPI download | an install, a user, a retained user |
| **engaged** | performed ≥1 meaningful event (not merely started the app) | opened the app |

### The four caveats that must travel with the numbers

1. **Product metrics are opt-in only.** Anyone who declined, never answered
   (they are asked once), or runs with `TAVOTTO_NO_TELEMETRY=1` is invisible.
   Product counts are a **lower bound** on real usage, by an unknown factor.
2. **One install ≠ one person.** Two machines are two IDs; a reinstall that
   clears the config directory is a new ID; a shared account is one ID.
3. **Downloads are not users, in either direction.** They over-count
   (reinstalls, CI, mirrors, curiosity) and under-count (one download can serve a
   lab). Never add downloads to observed users, and never present their sum as a
   user count.
4. **Mainland China is missing from the product metrics.** See below — this one
   is easy to forget precisely because it produces no errors.

### Known bias: mainland China is not reachable

The telemetry endpoint (`telemetry.tavotto.com`) resolves to Vercel, which is not
reachable from mainland China. The client does not fail loudly there — events are
dropped by design, the user notices nothing, and no error is logged anywhere.

**So mainland users are not under-reported; they are absent.** Every product
metric in this document — Weekly Successful Exporters, Engaged WAU/MAU,
activation, retention, AI adoption, platform mix — counts only installs that
could reach the endpoint. Given who Tavotto is built for, the missing share is
plausibly large, and its size is **unknown and unmeasurable from this data**:
the same silence covers "no mainland users" and "many mainland users".

What this does *not* affect: the **distribution metrics** (GitHub asset
downloads, PyPI, stars). Those are collected by a scheduled job running in GitHub
Actions against public APIs, never by the client, so they include mainland
downloads normally. Which means the two families of numbers have *different*
geographic coverage — another reason never to mix them into one figure.

When quoting product metrics externally, the honest framing is:

> N weekly successful exporters among opted-in anonymous installs **that can
> reach our telemetry endpoint** (mainland China excluded).

**Fixing it is deliberately deferred.** The mechanism is understood and the code
is ready — a second instance on Tencent Cloud SCF, split-horizon DNS, client
unchanged; steps are in `services/telemetry_proxy/README.md`. The blocker is that
pointing `telemetry.tavotto.com` at a mainland provider requires an ICP filing
(mainland legal entity, 10–20 working days). The alternative — baking a provider
domain such as `service-xxx.gz.apigw.tencentcs.com` into shipped clients — was
rejected: it hard-codes a vendor into binaries that live on users' machines,
which is exactly what a self-owned `DEFAULT_ENDPOINT` exists to prevent.

Revisit when there is evidence of mainland usage worth measuring — GitHub
installer downloads with no matching product events would be one such signal.

## North Star

### Weekly Successful Exporters (WSE)

> The number of distinct anonymous `distinct_id` values with at least one
> `export_completed` event in the trailing 7 days.

Why this one: exporting a publication-ready figure is the job Tavotto exists to
do. It is captured **server-side after the files are on disk**, so it cannot be
inflated by clicks, retries, or failed runs. It is a weekly measure because
figure work is bursty and tied to submission deadlines — a daily number would be
mostly noise about which day of the week it is.

Report it as: *"N weekly successful exporters (opted-in anonymous installs)"*.

## Engagement

### B. Engaged WAU
Distinct IDs in the trailing 7 days with ≥1 of:
`figure_opened`, `figure_edit_completed`, `canvas_created`,
`preflight_completed`, `export_completed`, `ai_assistant_invoked`.

**`app_started` alone does not count.** Launching an app and doing nothing is not
usage, and counting it is the single easiest way to make a retention curve look
better than the product is.

### C. Engaged MAU
Same event set, trailing 30 days.

### D. WAU/MAU (stickiness)
`Engaged WAU ÷ Engaged MAU`. For a desktop research tool, expect this to be
lower than for a consumer app — people write papers in bursts. Report the ratio,
don't editorialize it.

## Activation

### E. Activation funnel
For installs whose **first observed `app_started`** falls in the cohort window:

```
app_started  →  figure_opened  →  figure_edit_completed  →  export_completed
```

**Primary activation rate** = share of new opted-in installs with a first
`export_completed` within **7 days** of their first observed `app_started`.
Also report the 24-hour rate; it is the same query with a different window and
tells you whether the first session is enough.

"First observed" is doing real work in that sentence: an install that opted in
after weeks of use enters the cohort on the day we first saw it, not the day it
was really installed. That biases activation *upward* for such installs. Keep the
cohort restricted to installs whose first `app_started` and first
`telemetry_enabled` are within 24 hours of each other if you want a clean number,
and say which variant you used.

### F. Time to first successful export
Median of (`first export_completed` − `first observed app_started`) per install,
over installs that ever exported. Report the median and the share that never
exported; a median computed only over successes is a survivorship number and
must be labelled as such.

## Retention

Two definitions, because they answer different questions. Always say which one.

### G1. General engagement retention
Cohort: installs by the day of their first observed `app_started`.
Return event: any engaged event (the B list).
Report **D1 / D7 / D30**.

### G2. Core-value retention
Cohort: installs by the week of their first `export_completed`.
Return event: a later `export_completed`.
Report **W1 / W2 / W4**.

A weekly formulation is the honest one for core value here: expecting a
researcher to export a figure on two consecutive *days* measures deadline
proximity, not product value. Daily buckets are kept for G1 only, where the
population is larger and the signal is "did they come back at all".

## Depth

### H. Export intensity
`export_completed count ÷ weekly successful exporters` (exports per exporter per
week). Optionally also `export_completed count ÷ engaged WAU`.

### I. AI adoption
`distinct IDs with ai_assistant_invoked ÷ engaged WAU`, trailing 7 days.

### J. Preflight quality
`preflight runs with errors = 0 ÷ all preflight runs`, trailing 30 days. This is
a product-quality signal (are figures compliant before export?), not a usage
signal.

### K. Platform mix
Distinct IDs grouped by `platform` (`macos` / `windows` / `linux`), trailing 30
days. Drives build and support priorities.

## Acquisition / distribution (public counts, **not users**)

### GitHub lifetime installer downloads

```
for each asset_id whose asset_role = 'installer':
    take MAX(download_count_total) over all snapshots ever seen
sum those maxima across asset_ids
```

Take the maximum, not the sum of rows: `download_count_total` is a **cumulative
counter** snapshotted daily, so summing rows multiplies the number by however
many days we have been collecting. Taking the per-asset maximum also preserves
history for assets that were later deleted or replaced (which is why the identity
is `asset_id`, not the filename).

**`asset_role = 'installer'` only.** The `updater` role (`Tavotto.app.tar.gz`,
`*-setup.nsis.zip`, `latest.json`) is traffic from the auto-updater — it grows
with every existing user on every release and has nothing to do with new
installs. `latest.json` alone will dwarf everything else. Mixing it in is the
most tempting available way to exaggerate adoption.

### GitHub 30-day installer downloads

```
for each installer asset_id:
    last  = MAX(download_count_total) where observed_date <= period_end
    first = MAX(download_count_total) where observed_date <= period_start,  else 0
    delta = max(0, last - first)
sum deltas
```

Baseline 0 for assets first seen inside the window (a release published mid-period
correctly contributes all of its downloads). `max(0, …)` guards against a counter
appearing to move backwards when an asset is replaced.

### PyPI downloads

Per date, take **one** deduplicated `without_mirrors` value (dedupe by
`snapshot_key = pypi:tavotto:<date>`, since the healing window re-reports the
same day for up to 14 days), then sum across the period.

Even excluding known mirrors, **PyPI counts include CI systems, Docker builds,
dependency scanners and other automation.** They are a distribution signal, not a
user count, and are reported separately from GitHub installers — never merged
into one "installs" number.

### Combined "Distribution downloads"

`GitHub installer downloads + PyPI downloads` may be shown **only** under the
label *Distribution downloads*, never *Users* or *Installs*, and always with the
caveat that reinstalls, updates, CI and automation are included and that the two
sources count different things.

### GitHub stars / forks
Vanity-adjacent but standard for a traction record. Report them as what they are:
repository popularity, not usage.

## Recommended YC summary card

| Metric | Kind |
|---|---|
| Weekly Successful Exporters | observed / opt-in |
| Engaged WAU | observed / opt-in |
| Engaged MAU | observed / opt-in |
| D7 / D30 engagement retention | observed / opt-in |
| 7-day activation rate | observed / opt-in |
| Median time to first successful export | observed / opt-in |
| GitHub installer downloads (30d / lifetime) | public distribution count |
| PyPI downloads (30d) | public distribution count |
| GitHub stars | public repository count |
| WoW growth of Weekly Successful Exporters | derived from observed / opt-in |

Put one line under the card:

> Product metrics cover opted-in anonymous installs that can reach our telemetry
> endpoint (mainland China excluded) and are a lower bound. Download counts are
> public distribution counts, not users, and do include mainland downloads.

## What must never be called a user count

* GitHub asset downloads (any role) — including installers
* PyPI downloads
* GitHub stars, forks, watchers
* `latest.json` requests or any `updater`-role asset
* the sum of any of the above
* `app_started` event count (it is events, not people, and launching is not usage)

And one phrase to avoid: **"we have no users in China."** The data cannot say
that. It says nothing at all about China.

## Reproducing the dashboard

[`yc-dashboard.json`](yc-dashboard.json) carries every definition above in
machine-readable form — event names, aggregations, windows, filters, and the
deduplication rule for snapshot-based metrics — so the dashboard can be rebuilt
from scratch without reverse-engineering a provider's saved-insight IDs. It
deliberately contains **no PostHog insight/dashboard IDs**: those are unstable
and would rot faster than the definitions.
