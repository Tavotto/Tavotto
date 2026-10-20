# Privacy policy

Last updated: 2026-10-13

Tavotto is a local-first scientific-figure editor. Rendering, composition and
project writes happen on the machine running the engine: the user's computer by
default, or the connected server when the user chooses a remote instance. In that
remote mode, the interface communicates with the server through the user's tunnel;
project files, scripts and generated exports are handled there. Other tools or
export destinations are used when the user chooses them.

Network activity includes update checks and user-approved update downloads,
package lookups and downloads for environment preparation or dependency
installation, the optional AI assistant the user invokes, — only after
an explicit opt-in — usage statistics, and — only when the user confirms each
send — a redacted diagnostics bundle. The sections below distinguish these operations; local rendering does
not mean that every feature is offline.

## Data Tavotto does not upload

Tavotto does not upload figure files, source scripts, project files, layouts,
exports, or the contents of a user's scientific data to the Tavotto project or to
any service operated by the maintainers. The one thing a user can choose to send
is a redacted diagnostics bundle, which contains none of those and is never sent
automatically — see [Diagnostics bundle](#diagnostics-bundle).

This holds for the anonymous usage statistics too. The event schema physically
cannot carry any of the following, on the client and again at the server:

* figures, PDFs, PNG/JPEG contents
* Python source, scripts, or source snippets
* filenames, stems derived from filenames, absolute or relative paths
* project names, canvas names, document titles
* axis labels, annotations, legend contents, any text drawn inside a figure
* scientific values or data points
* package names imported by user scripts, package names the user installs into
  Tavotto's managed environment, package-manager logs, private index URLs
* AI prompts, AI responses, diffs
* tutorial project or document identifiers, panel identifiers
* usernames, email addresses, account IDs, hostnames
* MAC addresses, hardware serial numbers, Windows SID, machine GUIDs
* raw exception text or stack traces
* arbitrary free-form strings

Only a fixed allowlist of events, each with a fixed allowlist of properties whose
values are booleans, bounded integers, short enum strings, or a version string
(at most 32 characters of `[0-9A-Za-z.+_-]`, taken from the release feed and never
from user content), is accepted. Anything else is rejected rather than silently forwarded. The full
list is in [`docs/analytics/telemetry-events.md`](analytics/telemetry-events.md).

## Update checks

When enabled, Tavotto checks GitHub Releases for the newest public version. The
request contains the public repository endpoint and standard network metadata
handled by GitHub; it does not intentionally include the user's project files,
figures, scripts, exports, or account credentials. In browser mode, the **Check
daily** toggle controls automatic checks; a manual check can still be requested. `TAVOTTO_NO_UPDATE_CHECK=1` suppresses the Python
backend's startup background check, not every update request.

The desktop application uses a separate updater, checks at startup, and currently
has no in-app switch to disable those checks. The browser preference and Python
backend environment variable do not control this desktop channel. The desktop
updater downloads the signed installer when the user accepts an update.

The desktop updater has two sources: GitHub Releases and a mirror operated by the
Tavotto maintainers at `dl.tavotto.cn` (mainland China). If the system time zone is
in mainland China or the system or interface language is Simplified Chinese
(mainland), it asks the mirror first and GitHub second; otherwise GitHub first and the
mirror second. The first source that answers decides: it is asked once per check, and
the other one is contacted only when the first cannot answer, or when a download from
it fails after you pressed *Download and install* (then the installer is fetched again
from the other source). Either source sees what any HTTPS request reveals (IP address,
time, the updater's user agent) and nothing else: no identifier, no telemetry, no
project content. Which source the installer came from does not affect trust — the
installer's signature is always verified with the public key built into the app, and
an installer that fails verification is not installed.

## Package lookup and environment preparation

When the user requests a package lookup, Tavotto runs `pip index versions` against
the package index configured for the selected environment. Typing in the package
search field only filters installed packages locally; choosing the lookup action
sends the package name to that index.

Environment preparation, dependency repair and package installation or updates
can download packages and their dependencies. These requests go to the effective
package index and distribution hosts. Existing pip configuration is respected;
when no custom index is configured, an eligible failed or stalled install can
retry against the supported PyPI mirror. The package service receives the package
names and ordinary request metadata. This is separate from opt-in telemetry:
turning statistics off does not disable requested package operations. Package
names, private index URLs and package-manager logs are not telemetry properties.

The installed desktop rendering runtime is bundled; describing package downloads
does not mean that every desktop startup downloads that runtime.

## Optional AI assistant

The optional assistant is invoked only by the user. Tavotto passes the request to
a Codex or Claude command-line tool selected on the user's machine. That tool may
have its own network, account and privacy behavior; it is not a background upload
performed by Tavotto. Users should review the policy of the selected tool before
using it with sensitive material.

## Optional anonymous usage statistics

Tavotto can send anonymous product usage statistics. This is **off until the user
explicitly opts in**, either in the one-time first-run prompt or under
*Settings → Privacy, diagnostics & About*.

* **Disabled until consent.** Consent is stored as one of three states — unset,
  enabled, disabled. A missing setting is never treated as consent. While consent
  is unset, no event is transmitted and no identifier is even generated.
* **Consent is versioned.** The stored consent records which version of this
  collection scope it was given for. If that scope is ever materially widened,
  the version is raised, previously stored consent stops being sufficient —
  transmission halts immediately — and Tavotto asks again. Re-consenting keeps
  the existing identifier rather than minting a new one.
* **Random anonymous identifier.** When telemetry is first enabled, Tavotto
  generates a random UUIDv4 and stores it locally. It is not derived from any
  machine information — no MAC address, no machine GUID, no hostname, no
  username, no hardware serial. It is a pseudonym, not an identity: the same
  person on two machines is two identifiers, and reinstalling produces a new one.
  Tavotto does not build a device fingerprint.

  To be precise rather than flattering: this identifier is **stable across
  restarts, and that is deliberate**. Without it we could not tell a returning
  installation from a new one, and questions like "do people come back a week
  later" would be unanswerable. So events from one installation *can* be linked
  to each other over time. What they cannot be linked to is a person, an
  account, an email, or a network address — nor to a *specific* device: the
  events do carry a coarse `platform` and `arch` (macos/windows/linux,
  arm64/x86_64), but never a hardware serial, MAC address, machine GUID or
  anything else that could single one machine out. In data-protection terms
  this is pseudonymous rather than anonymised, and the sections below describe
  exactly what that pseudonym is attached to.
* **What is sent.** Broad product events (application started, a figure opened
  for editing, an edit committed, a canvas created, a preflight run finished, how an
  export ended (done, partly done, failed or cancelled — with which formats were
  requested (PDF, PNG, EPS, TIFF, SVG), never a file name or error text), the assistant started, an update installed; since consent
  version 2 also: a project refresh completed and which entry point triggered it,
  the readiness view opened and from where, which tutorial step was completed,
  which kind of multi-selection arrange button was used, how a save / recovery /
  package operation ended), plus the application version, operating-system family
  (macos/windows/linux/other), processor architecture (arm64/x86_64/other), and
  how Tavotto was installed (desktop/pipx/pip/source/unknown). Every value is a
  fixed enumeration or a bucketed count; the full list is in
  [`docs/analytics/telemetry-events.md`](analytics/telemetry-events.md).
* **Export outcome (consent version 3).** The `export_result` event reports how an
  accepted export ended: done, partly done, failed or cancelled, which formats were
  requested (PDF, PNG, EPS, TIFF, SVG), how many image files were written, at which coarse stage it stopped and a
  coarse error class. No file name, path, error text or figure content is included.
* **MCP tool calls (consent version 3).** When an AI agent (Codex, Claude Code or another
  host) calls a Tavotto MCP tool, the `mcp_tool_call` event reports which tool, whether it
  succeeded, a coarse error class, a duration bucket, whether the host was Codex, Claude or
  something else, and whether the result was trimmed. The tool's arguments and results —
  paths, script names, figure text, prompts — are never included. The MCP server uses the
  consent saved by the desktop app and never asks on its own; if you have not agreed there,
  it sends nothing.
* **Consent version 2 (2026-09-02).** Adding those events widened the scope, so the
  consent version was raised: consent given for version 1 stopped being sufficient
  at that moment, no event is transmitted until the user is asked again, and the
  existing identifier is kept.
* **Consent version 3 (2026-10-16).** Three things were added at once, so the consent
  version was raised a single time: the export outcome event and the MCP tool-call event
  above, and the optional error summary described under [Diagnostics bundle](#diagnostics-bundle). Consent given
  for version 2 stopped being sufficient at that moment: no event is transmitted until
  the user is asked again, the existing identifier is kept, and anyone who had declined
  is not asked again.
* **How to turn it off.** The toggle under *Settings → Privacy, diagnostics &
  About* takes effect immediately; already-queued events are dropped. Setting
  `TAVOTTO_NO_TELEMETRY=1` disables transmission entirely regardless of the saved
  setting, and suppresses the first-run prompt. This is a separate control from
  `TAVOTTO_NO_UPDATE_CHECK`; neither one governs the other.
* **No durable queue.** Events live in a small bounded in-memory queue. If the
  machine is offline or the queue is full, events are dropped and never resent.
  Tavotto does not keep a record of user activity on disk for later upload.
* **Delivery path.** Events go to a Tavotto-controlled proxy at
  `telemetry.tavotto.com`, which validates them against the same allowlist and
  forwards a normalized event to **PostHog**, the analytics backend. The
  application contains no PostHog key or address; it only knows the proxy URL.
* **IP addresses and request headers.** The proxy does not intentionally forward
  the client IP address, `X-Forwarded-For`, user agent, cookies, or any other
  request header to PostHog as event properties, and it explicitly disables
  PostHog's GeoIP enrichment. PostHog therefore sees a request originating from
  the proxy rather than from the user's machine.
* **No session replay, no autocapture.** Tavotto does not record the screen, the
  DOM, keystrokes, mouse movement, or clicks. Only the semantic events listed in
  the event documentation are sent.

We do not claim that no infrastructure anywhere logs anything: the proxy runs on
a third-party hosting provider that operates its own network and access logs
outside our control, and PostHog operates its own infrastructure. What we do
control is what Tavotto sends, what the proxy forwards, and what we ourselves
record — and none of those include your content.

## Project analysis stays local

Tavotto keeps a derived picture of each open project — which script produces which
figure, whether a figure can be edited element by element, whether a source file is
missing. Computing it never leaves the machine and never runs the user's code:

* **The project watcher** polls file metadata (names, sizes, modification times) of
  the project directory and reads Python sources only for local static analysis of
  which figure a script saves. It does not execute scripts, install anything or
  send anything.
* **Readiness analysis** is computed entirely locally from that static picture.
* **A trial run ("probe")** — actually executing a plotting script to discover its
  figures — happens only when the user explicitly triggers it in the interface or
  through `tavotto open`. Nothing runs scripts automatically: not the watcher, not
  the readiness view, not the tutorial, not the assistant's refresh.
* **The Codex plugin's refresh tool** (`tavotto_refresh_project`) asks the local
  Tavotto service, or the plugin's own copy of the same code when the app is not
  running, to re-read the project. It uploads no figures, scripts or data to any
  Tavotto service; the only network involved is the loopback connection to the
  Tavotto process on the same machine.
* **Tutorial progress** (which step you are on, which one-time hints you have
  seen) is stored in the browser's local storage on this machine only.

## Diagnostics bundle

**Export (always available).** The diagnostics bundle (*Settings → Privacy,
diagnostics & About → Export diagnostics*) is created locally and shared only if
the user chooses to send it. It is the strictly redacted form: secrets and the user's home directory are redacted,
and the anonymous telemetry identifier is redacted as well, so pasting a bundle
into a public issue does not link that issue to the anonymous analytics records.
Whether telemetry is on or off is included — that is useful for troubleshooting
and is not sensitive. Redaction is automatic and is not a guarantee that nothing
identifying remains; inspect the file before sharing it publicly.

**Send problem report (offered by default, sends only when you confirm).** The
same page, and the failure cards that suggest it, offer *Send problem report*,
which uploads a diagnostics bundle to a service operated by the Tavotto
maintainers (`tavotto.cn`) and stores it on Tencent Cloud COS (Shanghai).
Offering the option uploads nothing; sending does:

* **Only on your confirmation, every time.** Nothing is uploaded until you open the
  dialog, read what will be sent, and press *Send*. The choice is not remembered:
  there is no automatic, background, crash-triggered or scheduled sending, and a
  cancelled, closed or failed send is never resumed on its own — after a restart
  nothing is retried. Telemetry consent is a separate decision and does not imply
  this one (nor the reverse). Pressing *Send* does not consent to any future automatic
  reporting.
* **Optional error summary (only if anonymous statistics are on, consent version 3).**
  If, at the moment you press *Send*, anonymous usage statistics are enabled under the
  current consent version and `TAVOTTO_NO_TELEMETRY` is not set, the send request also
  carries a small summary of the most recent failed export or environment repair: the
  internal component and operation, a fixed internal error code, and a snapshot of that
  consent. It contains no message text, path, file name, script content, note, contact
  detail or anonymous telemetry identifier. If enabled on the server side (off by
  default), the diagnostics service may turn it into one error-tracking event at
  PostHog under a random one-time reference that is unrelated to the telemetry
  identifier. Turning statistics off stops summaries for later sends; a summary that was
  already submitted is not guaranteed to be withdrawn. Without statistics consent only
  the bundle itself is sent.
* **You see what you send.** The dialog lists the kinds of content in the bundle, each
  file with its size, and lets you save the exact file that would be uploaded and read
  it first. Figures, project files and data files are not part of the bundle.
* **What the sent bundle contains (more than the export).** So that maintainers can
  diagnose in depth, the *sent* bundle (not the exported one, which is unchanged) adds
  a set of extra files (`detail-…` / `optional-…`): error messages with full stack traces (engine, render process,
  interface console), the last part of the application, desktop-shell (sidecar) and
  render-process logs as written, the session state files (and a list of any corrupted
  copies), the most recent backend requests (HTTP method, route pattern, status code,
  duration and error code only — no request bodies, query strings or arguments), the
  style edits of the figure being edited, the render environment (Python and matplotlib
  versions, installed packages, non-default matplotlib settings, missing fonts), recent
  render/export/test-run records, desktop-shell and install facts (app, Tauri and
  WebView versions, OS version, CPU architecture, Rosetta, install method, update result
  and whether the mirror or GitHub was used when known), storage facts (free disk space,
  writability, whether the project is in a cloud-sync folder, the Windows long-path
  setting), recent interface `console.error` / `console.warn` messages, display facts
  (pixel ratio, window and screen size, language, theme) and network facts (whether a
  proxy is configured, with credentials removed; whether `tavotto.cn` and `github.com`
  resolve to a TUN-rewritten `198.18.0.0/15` address — preparing the bundle performs
  these two name lookups with the system resolver, with a 2-second limit, and makes no
  connection; time zone). Project folder names, file names and text inside
  figures are **not** removed from these files. Your home directory is written as `~`,
  your system user name as `<user>`, and credentials, cookies, nonces, API keys,
  tokens and proxy passwords are removed from every one of them.
* **Three extras, off by default and never remembered.** The dialog has three
  checkboxes that start unticked every time it opens: *script source* (the scripts
  registered in the open project, attached as they are — **secret removal in scripts is best
  effort and cannot be guaranteed, so do not tick it if your scripts contain secrets**;
  without it the bundle contains no script source at all, including the source lines normally
  printed inside tracebacks), *system crash
  reports* (Tavotto, sidecar and render-Python crash reports from the last 7 days:
  macOS reports and Windows WER reports are redacted text, but Windows crash dumps
  (`.dmp`) are snapshots of the program's memory that may contain the contents and
  paths of files that were open and **cannot be redacted** — they are attached as they
  are, whole files only, newest first, within a size share) and *contents of corrupted session copies* (otherwise only their
  names and sizes). Changing a checkbox rebuilds the bundle.
* **A limit of automatic redaction.** Passwords or tokens that appear in logs without anything
  that marks them as secrets cannot be recognised automatically. Before sending you can
  expand the dialog to see the list of files, or save the exact bundle to your computer and read it.
* **Size limit.** The upload limit is 64 MiB (it was 10 MiB before 2026-10-13). When the bundle is larger, content is
  cut in a fixed order — the three extras first, then the default categories from the
  last — and the dialog and the bundle's manifest say what was cut. The core files are
  never cut; if they alone are too large the bundle is not uploaded and you can save it.
* **What is transmitted.** The bundle itself, plus: the problem type you choose,
  an optional free-text note you may type (up to 1000 characters; please do not
  put names, email addresses, paths or data in it — it is sent as typed), the
  Tavotto version, operating-system family, CPU architecture, install method,
  bundle format version, and the file's size and SHA-256. A random request ID is
  generated for each send; it is not the telemetry identifier and is not derived
  from any machine information. The telemetry identifier is never sent.
* **Where it goes.** Your computer's Tavotto process sends the bundle directly to a
  private storage bucket (Tencent Cloud COS, mainland China) using a short-lived
  permission that the receiving service issues for that single file; the file does
  not pass through the Tavotto server. Like any network request, the receiving
  service and the storage provider see your IP address in the connection. The
  receiving service itself uses the address only, transiently and salted-and-hashed,
  to rate-limit requests. Its requests arrive through the `tavotto.cn` web server,
  which writes the same access log as for the website (address shortened as
  described in *The tavotto.cn website* below, no query string, no request body,
  kept 30 days). The storage provider's own handling follows its terms.
* **How to turn it off.** Set the environment variable `TAVOTTO_NO_DIAG_UPLOAD=1`
  (or `TAVOTTO_DIAG_UPLOAD=0`) for the Tavotto process; the option then disappears
  from every screen and the local endpoints that serve it do not exist. Exporting
  a bundle to a file stays available. The destination is built in; a misconfigured
  `TAVOTTO_DIAG_ENDPOINT` turns the feature off rather than falling back to it.
* **A local exit marker (never uploaded).** The desktop app keeps a tiny file,
  `session-state.json`, in its configuration folder. It holds only the app version,
  the start time, whether the last session ended normally (`running` / `clean`) and,
  if the rendering engine stopped unexpectedly, which of three kinds of stop it was
  (killed by a signal, non-zero exit, zero exit). It contains no project, path, script
  name, user identifier or error text; it is not part of any diagnostics bundle, is
  never uploaded and is not telemetry. It is read once at start-up so that, after an
  abnormal exit, the app can offer *Send problem report* — which still sends only if
  you open the dialog and press *Send*. Deleting the file only means one fewer prompt.
* **Rate limits.** The receiving service limits each address to a few reports per
  hour and caps the total accepted per day; a send that hits a limit fails with a
  message and can be retried later.
* **Remote instances.** If you are connected to a remote Tavotto instance, the
  engine that builds and uploads the bundle is the one on the remote machine, so
  the upload leaves that machine, not yours (the dialog on your screen only
  controls it). The bundle then describes the remote machine.
* **Report ID.** When the send succeeds you get a report ID (`TVD-…`) to quote in a
  GitHub issue or chat so the maintainers can find your report. It is the only
  handle to the report; the bundle is not tied to any account or to your telemetry
  identifier.
* **Retention and access.** The current server design keeps a report for 30 days
  and then deletes it; the retention period is a server setting and this document
  is updated if it changes. Reports are not publicly readable; only maintainers
  with administrative access to the service can read or download them.
* **Deletion.** Reports are deleted automatically after 30 days. To have a report
  removed earlier, open an issue on the public issue tracker quoting the report ID
  (without attaching anything else) and a maintainer will delete it.
* **Failure.** If you are offline, the service is unavailable or the bundle is
  larger than the limit (64 MiB by default), nothing is cut down or retried behind
  your back: you can export the bundle and keep or send it yourself.

## Local service

The desktop application uses a local service bound to `127.0.0.1` for the user
interface and rendering bridge. It is not intended to expose the user's projects
to other computers.

## The tavotto.cn website

The Chinese-language website <https://tavotto.cn/> (also reached through
`www.tavotto.cn`) is a static site run by the project maintainer. Its pages load
no analytics or tracking scripts and set or read no cookies. The same server also
receives *Send problem report* uploads from the desktop app (three API paths under
`/v1/reports/`); those requests are logged the same way.

To see how many people visit and where they come from, the website's server keeps
an access log. Each entry keeps only: the time; the visitor's IP address with the
last part removed (IPv4 keeps the first three octets, IPv6 keeps the first 48
bits); the request method; the host name; the page path without anything after
`?`; the HTTP status code; the number of bytes sent; the referring page, also
without anything after `?`; and the browser's User-Agent string. Full IP
addresses, cookies, `Authorization`, query strings, TLS details and any request
headers other than the referring page and User-Agent listed above are not
recorded.

The log stays on the website's server, is readable only by the maintainer, is
deleted after 30 days and is not included in backups or shared with third
parties. A daily report built from it contains only aggregate figures (page
views, approximate unique visitors, popular pages, referrers, browsers and
operating systems, status codes, daily trends and, when enabled, country or
region) and covers at most the last 30 days.

This section covers the website only. The Tavotto application does not contact
the website; the download mirror `dl.tavotto.cn` used for update checks is a
separate host and is described under "Update checks" above.

## Supporting the project on tavotto.com

Payments made through the support page <https://www.tavotto.com/support/> are
made on Ko-fi or 爱发电 (afdian.com), under their own privacy policies; the
website never sees card or account details. A Ko-fi payment is paid out directly
to the maintainer's PayPal or Stripe account, so PayPal (the payer's name and
email address) or Stripe (name, email address and part of the billing address)
shows those details to the maintainer as the payment recipient, under PayPal's
or Stripe's own policies. 爱发电 likewise shows the maintainer the supporter's
爱发电 account and order. What follows is what the website itself receives and
keeps.

When a Ko-fi payment completes, Ko-fi notifies the website. The website sends
one thank-you mail to the supporter's email address in that notification
(through the website's mail provider) with a link for choosing how to appear on
the supporters wall. The notification also carries the supporter's Ko-fi display
name; it is only checked to recognise Ko-fi's own test notification and is then
discarded, and the message is never kept.

What is kept, for each payment: the Ko-fi transaction id, the month and time,
whether it was a one-off payment or a membership (and the tier name), and the
amount and currency. For each supporter: their email address and the list of
their payments with the totals. The address and the amounts are kept so that
payments from the same person can add up to an avatar on the wall, so that a
membership can be shown while it is paid up, and so that the maintainer can
later contact supporters about supporter benefits, such as a discount on a
future Pro edition. They are never shown on the website and never shared with
anyone, except that the address is handed to the website's mail provider to
deliver these mails and both are held by its database provider. Only if the
supporter fills in the form are the display name and optional avatar they
chose kept. A name or avatar appears on the page only after the supporter
submitted it and the maintainer approved it; a supporter who stays anonymous is
counted but never named.

爱发电 sends no email address. When it reports an order, the website looks the
order up through 爱发电's API to confirm it was paid, and keeps the order number,
the month and time, the amount, and whether it allows an avatar; the supporter's
爱发电 user id is kept only as a keyed hash, under which the website also keeps
the list of that person's orders and their total. To appear on the wall, the
supporter enters an order number on the support page and gets the same form;
the rest is as for Ko-fi above. Registering asks for an email address, next to
the order number (or on the form itself when the support page opened it
directly): it receives a thank-you mail carrying the same form link (at most
one per order), and a mail when their orders first add up to an avatar, and it
is kept with their orders for the purposes above.

To change or remove an entry, or to have your email address and payment
records deleted, write to <hello@tavotto.com>.

## Changes and contact

Changes to this policy will be recorded in the repository history. Privacy or
security questions can be raised through the public issue tracker:
<https://github.com/Tavotto/Tavotto/issues>.
