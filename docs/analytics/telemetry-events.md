# Telemetry event contract (schema_version 1)

Authoritative tables:

* client — [`src/tavotto/engine/telemetry.py`](../../src/tavotto/engine/telemetry.py) (`EVENTS`, `AUTO_PROPS`)
* proxy — [`services/telemetry_proxy/tavotto_telemetry_proxy/contract.py`](../../services/telemetry_proxy/tavotto_telemetry_proxy/contract.py)

The two tables are deliberately duplicated and kept in step by
`tests/test_telemetry_proxy.py::test_client_and_proxy_contracts_match`. A schema
compiler would remove ~40 lines of duplication and add a mechanism nobody reads;
a drift test costs one function. **If you change one side, change the other.**

## Ground rules

1. **Nothing is sent before explicit consent.** Consent is tri-state
   (`unset` / `enabled` / `disabled`); a missing setting is not consent. While
   consent is unset, no identifier is generated and no request is made.
2. **`TAVOTTO_NO_TELEMETRY=1` wins over everything**, including a saved
   `enabled`, and suppresses the first-run prompt.
3. **Consent is versioned, and the version is enforced.** Stored consent carries
   the `CONSENT_VERSION` it was given for. `enabled()` requires
   `saved_consent_version >= CONSENT_VERSION`; raise the constant and every
   existing consent stops being sufficient *that instant* — no event is sent
   until the user is asked again (`needs_reconsent` drives the prompt).
   Re-consenting **keeps the same `install_id`**: minting a new one would
   fabricate a wave of "new installs" on upgrade day, breaking every retention
   curve. Declining is not stale consent — someone who said no is not asked
   again on the next version bump.
4. **Delivery is best-effort and geographically uneven.** The endpoint is not
   reachable from mainland China, and the client drops events silently rather
   than failing — so mainland installs are *absent* from product metrics, not
   under-counted. Recorded as a known bias in [`yc-metrics.md`](yc-metrics.md);
   the deployment path that would fix it is in
   `services/telemetry_proxy/README.md`.
5. **Only allowlisted events and properties.** Unknown event → dropped by the
   client, rejected with 400 by the proxy. Unknown property → same. Values may
   only be `bool`, a bounded non-negative `int`, a short enum string, a date
   (`YYYY-MM-DD`), or a version string (`[0-9A-Za-z.+_-]{1,32}`). No nested
   objects or arrays exist anywhere in the schema, so user content cannot be
   smuggled through a container.
6. **Track success, not intent.** Events named `*_completed` fire after the work
   succeeded, never when it started.
7. **No autocapture, no session replay, no click tracking, no DOM capture.**

## `anonymous install ID != human identity`

`distinct_id` is a random UUIDv4 generated on the machine the first time
telemetry is enabled. It is not derived from hardware, OS identifiers, hostname,
username, or account. It is a *pseudonym for one installation*:

* the same person on a laptop and a desktop is **two** IDs;
* a reinstall that clears the config directory produces a **new** ID;
* two people sharing one machine account are **one** ID;
* people who never opted in have **no** ID at all and appear nowhere.

So the correct phrase for a count of distinct IDs is **"opted-in anonymous
installs"**, or "observed users" if you say clearly what observed means. It is a
lower bound on real users and is not a headcount. See
[`yc-metrics.md`](yc-metrics.md).

**Say "anonymous" carefully.** The ID is stable across restarts *on purpose* —
without that, D7/D30 retention could not be computed at all. So events from one
installation are linkable to each other over time; they are simply not linkable
to a person, account, network address, or any *specific* device — `platform` and
`arch` are coarse buckets, not machine identifiers. That is **pseudonymous**, not
anonymised in the data-protection sense. In the product UI "anonymous usage
statistics" is the phrase users actually understand, and the honesty is carried
by the sentence next to it, which states that the identifier persists across
restarts. In anything written for investors, a website, or a policy document,
prefer **"opt-in anonymous install telemetry"** or **"pseudonymous,
installation-level"** — and never imply that individual events cannot be
correlated across sessions.

## `download != user`

GitHub asset download counts and PyPI download counts are collected by a
scheduled job, not by the application, and carry the constant distinct ID
`distribution_metrics`. They are never mixed into user cohorts. A download can
be a reinstall, a CI job, a mirror, a curious person who never launched the app.
Call them **distribution downloads**.

## Automatic properties (on every product event)

| Property | Type | Values | Notes |
|---|---|---|---|
| `schema_version` | int | `1` | envelope field; the proxy copies it into the forwarded properties |
| `app_version` | version | e.g. `0.8.0` | `tavotto.__version__` |
| `platform` | enum | `macos` `windows` `linux` `other` | normalized; never `platform.platform()` |
| `arch` | enum | `arm64` `x86_64` `other` | normalized from `platform.machine()` |
| `distribution` | enum | `desktop` `pipx` `pip` `source` `unknown` | reuses `engine/diagnostics.install_kind()`, the single authority — not a second copy of install detection |

Deliberately absent: `platform.platform()`, kernel build, hostname, full Python
executable path, locale, timezone, screen size. Each adds fingerprinting surface
and answers no question we actually have.

## Events

| Event | Captured at | Where | Properties |
|---|---|---|---|
| `telemetry_enabled` | the first time *this* anonymous ID enables telemetry | server (`engine/telemetry.set_consent`) | `source`: `first_run` \| `settings` |
| `app_started` | a real application service is starting | server (`app.main()`, both branches) | `app_mode`: `desktop` \| `browser` |
| `figure_opened` | user enters the in-figure editing workflow for a panel | client (`store/actions.enterElementEdit`) | `asset_kind`: `pdf` \| `raster`; `editable`: bool |
| `figure_edit_completed` | one semantic edit lands in the undo history | client (`store/documentStore.pushHistory`) | `edit_kind`: `text` \| `series` \| `axes` \| `annotation` \| `layout` \| `style` \| `other`; `patch_count`: int ≤ 1000 |
| `canvas_created` | a canvas is successfully created | client (`documentStore.addCanvas` / `duplicateCanvas`) | `creation_kind`: `blank` \| `project` \| `duplicate` |
| `preflight_completed` | preflight finished computing | client (`components/ExportDialog`) for the canvas; server (`codex-plugin/mcp/tavotto_mcp/bridge.run_preflight`) for the Codex MCP tool | `errors`, `warnings`, `not_verifiable`, `suggestions`: int ≤ 1000; `passed`: bool |
| `export_completed` | `/api/export` wrote every requested file | server (`app.api_export`) | `pdf`, `png`, `with_proof`: bool; `panel_count`: int ≤ 1000 |
| `ai_assistant_invoked` | `engine_ai.run()` returned a session | server (`app.api_ai_run`) | `agent`: `codex` \| `claude` \| `other` |
| `update_completed` | an update finished installing | server (`engine/updater.apply_upgrade`) for pip/pipx; client (`store/updateStore.installDesktop`) for the desktop shell | `update_kind`: `desktop` \| `pip` \| `pipx`; `target_version`: version (omitted when unknown) |
| `project_refresh_completed` | the unified project refresh **succeeded** (`app.refresh_project`, the single funnel for manual / watcher / Codex / AI) | server | `source`: `watcher` \| `manual` \| `codex` \| `ai` (refreshes caused by probe, manual registration or opening a project are **not** captured); `changed_bucket`: `none` \| `one` \| `few` (2–5) \| `many` (6+) — script + asset change count, bucketed |
| `project_readiness_opened` | the readiness center is opened from a named entry point and the report has arrived | client (`store/projectReadinessStore.openCenter`) | `source`: `banner` \| `panel` \| `quickedit` \| `palette`; `status_bucket`: `all_editable` \| `mixed` \| `layout_only` (a project with zero figures sends nothing) |
| `tutorial_started` | the tutorial actually starts or restarts (resuming is not a start) | client (`lib/onboarding/tutorial`) | `source`: `picker` \| `help` \| `settings` \| `palette`; `tutorial_version`: int ≤ 1000 (`ONBOARDING_FLOW_VERSION`) |
| `tutorial_step_completed` | a tutorial step's real completion condition was met (skipping a step sends nothing) | client (`lib/onboarding/flow.completeStep`) | `step_id`: one of the ten ids in `web/src/lib/onboarding/stepIds.ts` (`tests/test_telemetry_integrations.py` keeps the enum in step with that file); `tutorial_version` |
| `tutorial_completed` | the last step completed | client (`lib/onboarding/flow.completeStep`) | `tutorial_version` |
| `context_bar_multi_used` | a multi-selection context-bar action succeeded (the same action from the inspector or the palette is not counted) | client (`lib/activityTelemetry`, the only activity-signal → telemetry mapping) | `action_id`: `align_left` \| `align_center` \| `align_right` \| `align_top` \| `align_middle` \| `align_bottom` \| `distribute_h` \| `distribute_v` \| `same_width` \| `same_height` \| `group` \| `ungroup` \| `more`; `selection_size_bucket`: `2` \| `3_5` \| `6_plus` |
| `document_saved` | a document write to disk finished | client (`store/documentStore.scheduleDiskWrite`) | `trigger`: `manual` \| `autosave`; `outcome`: `ok` \| `conflict` \| `failed` |
| `recovery_action` | the user decided on the crash-recovery banner | client (`documentStore.recoverLocalCopy` / `discardLocalCopy`) | `action`: `restore` \| `keep_main` |
| `package_action` | a managed-environment package job reached a terminal state | server (`app.api_packages_run` progress hook) | `action`: `install` \| `update` \| `remove`; `outcome`: `ok` \| `failed` \| `cancelled`. **No package name** — it could reveal a private project's dependencies |

The nine events in the second half were added in **`CONSENT_VERSION` 2** (2026-09-02).
Consent stored for version 1 stopped being sufficient the moment that constant was
raised; those installs are asked again and keep their `install_id`.

### Explicitly forbidden on every event

file names · stems · script names · paths · project or canvas names · document
titles · gids · element labels · axis labels · legend text · any figure text ·
data values · prompts · AI output · diffs · session IDs · export directories ·
exported file names · release notes · exception text · stack traces · IP ·
user agent · hostname · username · e-mail · locale · **package names · package
logs · private index URLs · tutorial project / document ids · panel ids · exact
project size**.

None of these appear as an allowlisted property name, which is why they cannot
be sent — not because callers remember not to. Regression tests:
`tests/test_telemetry_api.py` (an AI request full of secret-looking text) and
`tests/test_export_endpoint.py` (a stem with confidential-looking words).

## Instrumentation-boundary notes

**Why export and AI are captured server-side.** The frontend knows when the user
*clicked*; the backend knows when the work *succeeded*. `export_completed` fires
immediately before `/api/export` returns its success response, after every
requested file is on disk; a failing export produces zero events. Same for
`ai_assistant_invoked`, which fires only after `engine_ai.run()` hands back a
session.

**Why `figure_edit_completed` is one call site.** It sits in
`documentStore.pushHistory`, the single funnel that both `commit()` and
`endTxn()` pass through. One drag = one transaction = one history entry = **one
event**, not 120 `pointermove` frames. Any new editing action is covered
automatically. The fake-realtime preview plane never touches it (it does not
commit), which is exactly right: a preview is not an edit.

**Why `edit_kind` is coarse.** It is derived from the history label's *key* — a
stable identifier developers write in source — through a closed lookup table in
`web/src/lib/telemetry.ts`, defaulting to `other`. The label *text* is never
used: it is translated and interpolated with the user's own file and property
names. The known coarseness: `setProp` / `clearProp` are the generic entry points
for every in-figure element property, so font size, colour, tick config and
visibility all land in `style`. Splitting them further would require putting
matplotlib property names into the payload, which is exactly what the allowlist
exists to prevent. `series` is currently reachable only in principle; no label
maps to it yet, and that is recorded here rather than faked.

**Why `preflight_completed` has two capture points.** Tavotto has two preflight
evaluators by design (CLAUDE.md): the TypeScript one drives the canvas and the
export dialog, the Python one drives the Codex MCP tools, and Flask has no
preflight endpoint at all. Each is instrumented at its own completion boundary —
the canvas one fires once per export-dialog open, when the memoized result is
available (not on every profile change inside the dialog); the MCP one fires
inside `run_preflight` after `summarize()`. They cover disjoint user flows, so
this is coverage rather than double counting. Both send the same four counts and
nothing else.

**Why the activity bus is not forwarded wholesale.** `web/src/lib/activity.ts`
carries eighteen local signals for onboarding; exactly one mapping
(`lib/activityTelemetry.ts`) turns three of them (`selection.aligned` /
`selection.grouped` / `selection.ungrouped`) into `context_bar_multi_used`, and only
when the action was started from the floating context bar. The other fifteen kinds
are tested one by one to map to nothing. Direction is one-way: activity → telemetry
(behind consent and the server allowlist); telemetry never drives the UI.

**Why `project_refresh_completed` is server-side and skips some reasons.** All four
refresh paths (manual button, watcher, Codex MCP tool, built-in assistant) end in
`app.refresh_project`, so one capture point covers them and a failed refresh produces
nothing. Refreshes that are side effects of another action (probe success, manual
registration, opening a project) are not user "refreshes" and are not counted.

**Why tutorial events carry only ids and a version.** The tutorial's project id,
document id and hint bookkeeping stay on the machine; `step_id` is a developer-written
closed set and `tutorial_version` says which flow the id belongs to.

**Why the embedded Codex canvas sends nothing.** The MCP widget bundles the same
frontend code, but nothing calls `setTelemetryEnabled` there, so
`captureTelemetry` is a no-op. The widget has no sidecar session and no consent
context of its own; inheriting one would be a surprise. Recorded here so it is a
decision rather than an accident.

## Distribution metrics (proxy-only, bearer-authenticated)

Sent by `scripts/collect_distribution_metrics.py` from a scheduled GitHub
Actions job. `distinct_id` is the constant `distribution_metrics`, and these
events always set `$process_person_profile: false` so they never create a person.

| Event | Properties |
|---|---|
| `github_release_asset_snapshot` | `release_id`, `release_tag`, `asset_id`, `asset_role` (`installer` \| `updater` \| `update_check` \| `wheel` \| `sdist` \| `plugin` \| `plugin_manifest` \| `checksum` \| `other`), `platform` (`macos` \| `windows` \| `linux` \| `any` \| `other`), `download_count_total`, `observed_date`, `snapshot_key` |
| `pypi_daily_downloads` | `date`, `downloads`, `category` (`without_mirrors`), `snapshot_key` |
| `github_repo_snapshot` | `stars`, `forks`, `observed_date`, `snapshot_key` |

`download_count_total` is GitHub's **cumulative** counter, so every row is a
snapshot; period downloads are a difference between snapshots. `asset_id` — not
the filename — is the asset identity, so history survives an asset being deleted
and re-uploaded.

**Three of these roles are polls, not downloads.** `update_check`
(`latest.json`) and `plugin_manifest` (`codex-plugin.json`) are fetched by the
updater and the plugin host on a schedule; a machine that never upgrades still
contributes one every time. `updater` (`Tavotto.app.tar.gz`, `*-setup.nsis.zip`)
is a real transfer but the auto-updater starts it, not a person. Measured on
2026-08-27: `codex-plugin.json` was **3382 of 3387** requests in the old combined
`plugin` role, and `latest.json` **44 of 66** in the old combined `updater` role.
Folding either back in overstates adoption by orders of magnitude, so
[`yc-dashboard.json`](yc-dashboard.json) puts them in a separate
*Infrastructure / Automated Traffic* section and forbids summing them into
Downloads, Users, or Installs.

**Rows written before 2026-08-27 carry the old roles** and are not
retroactively reclassified. `asset_role` is a label on a row; the asset's
identity is `asset_id`. Resolve each `asset_id`'s role from its most recent
snapshot and aggregate over all of its rows — filtering rows by `asset_role`
first splits a reclassified asset in two, and a date filter makes it worse, not
better. See `role_resolution` in [`yc-dashboard.json`](yc-dashboard.json).

## Deduplication

Every forwarded event carries a `uuid`. For distribution snapshots it is
`uuid5(namespace, snapshot_key)`, so re-running the collector produces the same
event UUID. **PostHog's public documentation does not guarantee idempotent
deduplication on that field**, so do not rely on it: every snapshot also carries
a stable `snapshot_key`, and the dashboard queries in
[`yc-dashboard.json`](yc-dashboard.json) deduplicate on it explicitly. Product
events use a random UUID per event and are not deduplicated.

## Person profiles

Product events are forwarded with person profiles left on (PostHog's default),
because the analysis unit *is* the anonymous distinct ID and retention/funnel
insights are built on it. Tavotto never calls `identify`, never sets a person
property, and never attaches an email, name, or profile — the person record
contains a random UUID and nothing else. `POSTHOG_PERSON_PROFILES=anonymous`
switches product events to `$process_person_profile: false` if you prefer
cheaper anonymous events; the trade-off is that some person-based insights
degrade, which upstream does not enumerate precisely, so it is not the default.
`$geoip_disable: true` is always set.

## Changing this contract

1. Bump nothing for an additive property — add it to both tables and to the table
   above.
2. Bump `schema_version` for anything the proxy would misinterpret; the proxy
   rejects unknown versions, so **deploy the proxy first, release the client
   second** (see `services/telemetry_proxy/README.md`).
3. If the *scope* of collection materially widens, bump `CONSENT_VERSION` too and
   re-ask: what people agreed to was the old scope.
