# Privacy policy

Last updated: 2026-10-02

Tavotto is a local-first scientific-figure editor. Rendering, composition and
project writes happen on the machine running the engine: the user's computer by
default, or the connected server when the user chooses a remote instance. In that
remote mode, the interface communicates with the server through the user's tunnel;
project files, scripts and generated exports are handled there. Other tools or
export destinations are used when the user chooses them.

Network activity includes update checks and user-approved update downloads,
package lookups and downloads for environment preparation or dependency
installation, the optional AI assistant the user invokes, and — only after
an explicit opt-in — usage statistics. The sections below distinguish these
operations; local rendering does not mean that every feature is offline.

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
  for editing, an edit committed, a canvas created, a preflight run finished, an
  export succeeded, the assistant started, an update installed; since consent
  version 2 also: a project refresh completed and which entry point triggered it,
  the readiness view opened and from where, which tutorial step was completed,
  which kind of multi-selection arrange button was used, how a save / recovery /
  package operation ended), plus the application version, operating-system family
  (macos/windows/linux/other), processor architecture (arm64/x86_64/other), and
  how Tavotto was installed (desktop/pipx/pip/source/unknown). Every value is a
  fixed enumeration or a bucketed count; the full list is in
  [`docs/analytics/telemetry-events.md`](analytics/telemetry-events.md).
* **Consent version 2 (2026-09-02).** Adding those events widened the scope, so the
  consent version was raised: consent given for version 1 stopped being sufficient
  at that moment, no event is transmitted until the user is asked again, and the
  existing identifier is kept.
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
