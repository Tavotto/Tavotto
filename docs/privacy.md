# Privacy policy

Last updated: 2026-08-20

Tavotto is a local-first scientific-figure editor. Rendering, composition, project
files, scripts, figures and exports stay on the user's machine unless the user
explicitly chooses another tool or destination.

Tavotto makes exactly three kinds of outbound request, all described below:
the update check, the optional AI assistant the user invokes, and — only after
an explicit opt-in — anonymous usage statistics.

## Data Tavotto does not upload

Tavotto does not upload figure files, source scripts, project files, layouts,
exports, or the contents of a user's scientific data to the Tavotto project or to
any service operated by the maintainers.

This holds for the anonymous usage statistics too. The event schema physically
cannot carry any of the following, on the client and again at the server:

* figures, PDFs, PNG/JPEG contents
* Python source, scripts, or source snippets
* filenames, stems derived from filenames, absolute or relative paths
* project names, canvas names, document titles
* axis labels, annotations, legend contents, any text drawn inside a figure
* scientific values or data points
* package names imported by user scripts
* AI prompts, AI responses, diffs
* usernames, email addresses, account IDs, hostnames
* MAC addresses, hardware serial numbers, Windows SID, machine GUIDs
* raw exception text or stack traces
* arbitrary free-form strings

Only a fixed allowlist of events, each with a fixed allowlist of properties whose
values are booleans, bounded integers, short enum strings, or a version string,
is accepted. Anything else is rejected rather than silently forwarded. The full
list is in [`docs/analytics/telemetry-events.md`](analytics/telemetry-events.md).

## Update checks

When enabled, Tavotto checks GitHub Releases for the newest public version. The
request contains the public repository endpoint and standard network metadata
handled by GitHub; it does not intentionally include the user's project files,
figures, scripts, exports, or account credentials. Automatic checks can be
disabled in the application settings, and `TAVOTTO_NO_UPDATE_CHECK=1` disables
them completely.

In the desktop application the in-app updater additionally downloads the signed
installer when the user accepts an update.

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
* **Random anonymous identifier.** When telemetry is first enabled, Tavotto
  generates a random UUIDv4 and stores it locally. It is not derived from any
  machine information — no MAC address, no machine GUID, no hostname, no
  username, no hardware serial. It is a pseudonym, not an identity: the same
  person on two machines is two identifiers, and reinstalling produces a new one.
  Tavotto does not build a device fingerprint.
* **What is sent.** Broad product events (application started, a figure opened
  for editing, an edit committed, a canvas created, a preflight run finished, an
  export succeeded, the assistant started, an update installed), plus the
  application version, operating-system family (macos/windows/linux/other),
  processor architecture (arm64/x86_64/other), and how Tavotto was installed
  (desktop/pipx/pip/source/unknown).
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

## Diagnostics bundle

The diagnostics bundle (*Settings → Privacy, diagnostics & About → Export
diagnostics*) is created locally and shared only if the user chooses to send it.
Secrets and the user's home directory are redacted, and the anonymous telemetry
identifier is redacted as well, so pasting a bundle into a public issue does not
link that issue to the anonymous analytics records. Whether telemetry is on or
off is included — that is useful for troubleshooting and is not sensitive.

## Local service

The desktop application uses a local service bound to `127.0.0.1` for the user
interface and rendering bridge. It is not intended to expose the user's projects
to other computers.

## Changes and contact

Changes to this policy will be recorded in the repository history. Privacy or
security questions can be raised through the public issue tracker:
<https://github.com/Tavotto/Tavotto/issues>.
