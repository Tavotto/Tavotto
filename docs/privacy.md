# Privacy policy

Last updated: 2026-11-04

Tavotto is a local-first scientific-figure editor. Rendering, composition and
project writes happen on the machine running the engine: the user's computer by
default, or the connected server when the user chooses a remote instance. In that
remote mode, the interface communicates with the server through the user's tunnel;
project files, scripts and generated exports are handled there. Other tools or
export destinations are used when the user chooses them.

Network activity includes update checks and update downloads (on the desktop
app, by default in the background, with an in-app switch to turn that off),
package lookups and downloads for environment preparation or dependency
installation, journal-standards downloads when the user picks a journal, the optional AI assistant the user invokes, — only after
an explicit opt-in — usage statistics, and — only when the user confirms each
send — a redacted diagnostics bundle or a letter written through *Contact the maker*. The sections below distinguish these operations; local rendering does
not mean that every feature is offline.

## Data Tavotto does not upload

Tavotto does not upload figure files, source scripts, project files, layouts,
exports, or the contents of a user's scientific data to the Tavotto project or to
any service operated by the maintainers. The one thing a user can choose to send
is a redacted diagnostics bundle, which contains none of those (except, if you used AI
repair, the parts of your script the repair assistant read — see below) and is never sent
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

The MCP server that AI editors start also checks for a newer plugin at most once every
24 hours, in the background, by reading a public version file from GitHub Releases, and
reads the public version index on `dl.tavotto.cn` when GitHub cannot be reached. The
requests contain no project data. `TAVOTTO_NO_UPDATE_CHECK=1`,
`TAVOTTO_DISABLE_UPDATE_CHECK=1` or turning off **Check daily** stops it.

The desktop application uses a separate updater, checks at startup, and currently
has no in-app switch to disable those checks. The browser preference and Python
backend environment variable do not control this desktop channel. By default the
desktop app also downloads a newer version in the background as soon as a check
finds one (**Settings → Updates → Download updates in the background**; turn it off
and the installer is downloaded only after you accept the update prompt, as before).
Either way it contacts only the two sources described below, verifies the installer's
signature before keeping it, and never replaces the running app: a downloaded update is
installed when you press *Restart now* or when you quit Tavotto, so the next launch is
the new version. A failed background download is silent; the manual download stays
available in Settings.

The desktop updater has two sources: GitHub Releases and a mirror operated by the
Tavotto maintainers at `dl.tavotto.cn` (mainland China). If the system time zone is
in mainland China or the system or interface language is Simplified Chinese
(mainland), it asks the mirror first and GitHub second; otherwise GitHub first and the
mirror second. The first source that answers decides: it is asked once per check, and
the other one is contacted only when the first cannot answer, or when a download from
it fails (after you pressed *Download and install*, or during the background download; then the installer is fetched again
from the other source), or when that download is slow (then the other source is
checked and its installer downloaded for a few seconds to compare speed; the faster
one finishes the download and the other is dropped). In mainland China the check
still asks the mirror first, but the installer is downloaded from GitHub first (the
check result and the download are then asked of different sources, and GitHub is
asked for the same version again): the background download stays with GitHub even
when slow and moves to the mirror only after about 3 failed attempts in a row or
when the new version is more than about 3 days old and still not downloaded (the
app remembers the attempt count and the time it first saw that version in a small
local file, `update-source.json`, in its settings folder; it contains no path or
identifier); a download you start yourself gives GitHub about 8 seconds and moves
to the mirror if GitHub sends nothing by then or would need more than about 2 minutes. In mainland China the background download may also be served by a third source, a server operated by the Tavotto maintainers at `up.tavotto.cn` (mainland China), tried after GitHub and before the mirror; it is never used for the update check or for a download you start yourself, and it is switched off on the server side when it is not available. Each source sees what any HTTPS request reveals (IP address,
time, the updater's user agent) and nothing else: no identifier, no telemetry, no
project content. The `up.tavotto.cn` server keeps an access log for 30 days so the maintainers can see
whether it works and how much it carries: time, your IP address with the last part removed (an IPv4 address is
kept only as `a.b.c.0`), the file requested, the response status and size, the requested byte range and the
updater's user agent; no query string, cookie or other header. Which source the installer came from does not affect trust — the
installer's signature is always verified with the public key built into the app, and
an installer that fails verification is not installed.

## Connected editors

When the desktop app starts, it brings the Tavotto skill (the usage notes) in every
AI editor you have already connected up to date. This only copies files that ship
inside the app into that editor's user folder; nothing is downloaded, and a copy you
edited is left alone. While **Download updates in the background** is on, the same
startup step also updates the Tavotto plugin in Codex if it is older than the app,
which downloads the plugin from the same public release source as the app itself. If
it fails, Tavotto does not try again for 24 hours. Turn the setting off, or set
`TAVOTTO_DISABLE_UPDATE_CHECK=1` or `TAVOTTO_NO_UPDATE_CHECK=1`, and the plugin is never updated automatically. The
plugin and the MCP server themselves never download or run anything on their own.

## Journal standards catalog

Tavotto can offer figure requirements for journals, with a source quote for every
number. The catalog is hosted by the Tavotto maintainers (the feature is off in
releases until its data is ready). It works in two steps:

* **The index** (journal names, ISSNs, publisher names and a checksum per journal)
  is downloaded as a whole when Tavotto starts, at most once a day. It is the same
  file for everyone and tells the server nothing about you. Searching and browsing
  journals works offline from it.
* **A journal's requirements** are downloaded only when you pick that journal, and
  kept on your computer afterwards. Reading a journal you already downloaded sends
  nothing.

The separate v2 source-facts API is requested only when an assistant searches or
reads those facts. It uses the same device token and quota as v1, but a separate
local `journal-catalog-v2/` cache. The `tavotto_get_journal_facts` tool does not
emit an anonymous usage event. An unavailable v2 catalog is not replaced with
v1 facts.

What the server receives: the journal id, a random anonymous **device token**
issued by the server the first time it is needed, the catalog version number, a
client type (`app`, or `agent-<name>` when an AI coding assistant's plugin on your
computer asks), and what any HTTPS request reveals (IP address, time,
`Tavotto/<version>` as user agent). Never your project, files, script, paths or
figure content. The token is not the anonymous statistics identifier and is not
derived from it; it lives in Tavotto's data folder (`journal-catalog/device.json`)
and is shared by the app and by assistant plugins on the same computer, so they
count as one device. Deleting that folder makes the next download request a new
token.

What the server keeps: **which token first read which journal and when** (plus the
client type). This is used only to see which journals are popular and to stop
bulk scraping. **Records are kept for 1 year and then deleted.** IP addresses are
not stored in clear: a keyed hash is kept for 48 hours to apply the limits below
(together with how many different anonymous devices and how many refused requests that hash saw, to tune the limits).
The server limits first-time journal downloads to 20 per day and 10 per hour per
token, and 2,000 per day per IP address (app and assistants together; the limits can be adjusted on the server); when a limit
is reached Tavotto keeps working and simply does not apply that journal until
later.

Every catalog file is verified with a signature (or a checksum listed in the signed
index) against a public key built into the app; anything that fails verification, is
older than what is already installed, or fails the catalog's own validation is
discarded in full and Tavotto falls back to the copy on your computer or the small
built-in set. `TAVOTTO_NO_JOURNAL_SYNC=1` stops these requests entirely. They do
not depend on the usage-statistics choice, and `TAVOTTO_NO_TELEMETRY` does not
control them (it only governs usage statistics, as with update checks), because
these requests carry no statistics identifier. The catalog is served from the same
mainland-China host as the website and the diagnostics service, `tavotto.cn`;
outside mainland China Tavotto tries `tavotto.com` first, which only forwards the request to
`tavotto.cn` (it stores nothing and counts nothing, and passes on your IP address only so that
the limits above apply to you rather than to the forwarder). Either way the request is handled by
one service and recorded once: the same records, kept for 1 year.

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

The optional assistant is invoked only by the user, and only with an AI the user
has connected in the "Connect an AI" dialog. Tavotto never relays these
requests through its own servers; the request goes from the user's machine to the
AI the user chose, in one of two ways:

* **Continue with ChatGPT (Sign in with ChatGPT).** The user signs in once in their
  browser with OpenAI; Tavotto keeps the resulting tokens in its own data folder
  (`siwc/`, readable by your user only where the platform supports it — never in
  logs, usage statistics or diagnostics bundles, and never readable by the AI
  assistant's own file tools) and sends requests directly to OpenAI's API
  (`api.openai.com`) under the user's ChatGPT plan, without storing them on OpenAI's
  side (`store: false`). Signing out revokes Tavotto's access with OpenAI. This
  connection is used wherever an API-key connection would be (editing figures, AI
  repair, reading data tables for data-to-figure), so when it is the only AI you have
  connected, those features send their requests to ChatGPT; if you also have API-key
  connections, they are tried first. Usage counts against your ChatGPT plan.
* **Subscription sign-in through a command-line tool (Codex or Claude).** Tavotto passes the request to the
  Codex or Claude command-line tool installed on the user's machine, which talks to
  its own service under its own account and privacy terms.
* **API key or account sign-in (DeepSeek, Kimi, Zhipu GLM, MiniMax, Alibaba Bailian,
  OpenAI, Anthropic, a custom address and others).** The request is sent to that
  provider's API address. When a Codex or Claude command-line tool is installed, it
  may carry the call with the user's key handed to it; otherwise Tavotto's built-in
  agent sends it directly. Either way the destination is the provider's API address
  the user connected, and the key is not sent anywhere else. Plain `http` addresses
  are accepted only on the local machine or a private network. When you save such a
  connection (or press "Check connection"), Tavotto also sends that address a few very short test
  messages (a one-word prompt, at most 16 tokens of reply) to find out which chat protocol it
  speaks; this uses a tiny amount of your quota, and the replies are not stored or logged.

What is sent is the user's instruction plus whatever the assistant reads from the
project to do the job, which can include the script and files in the project folder,
and any files you attach to an instruction (pictures or data files). Attached files are kept
only in Tavotto's local data folder for the duration of the task (unsent ones are
deleted within an hour), are never added to your project, to usage statistics or to a
diagnostics bundle, and go only to the AI you connected.
When the project is checked against a journal, the request also carries that journal's
name and figure requirements (font sizes, fonts, line width, figure widths, resolution).
When AI repair meets a kind of failure it has fixed before on this computer, the request also
carries up to three of those earlier fixes from *Remember what fixed it* (see below): the run
arguments, the data path a file was pointed at, the environment variables a launcher set, or the
execution mode, possibly from another project. Variables whose names look like credentials are sent
by name only. Turn the setting off or delete entries to stop this.

**Remember what fixed it.** After AI repair really fixes a script (its test run in Tavotto
succeeded), Tavotto keeps a note of what it changed in `ai/fix_memory.json` in its own data folder:
run arguments (not when the script's arguments are marked sensitive), the environment variables a
launcher it wrote sets (the launcher text itself is not kept), which file a missing data path was
pointed at, or the execution mode, with the script's file name, the failure type and dates. Secrets
are removed and your home folder is written as `~` before saving. For environment variables only the
names are kept, except for a short list of harmless display settings (such as the plotting backend,
language or thread count) and variables that name a folder whose value is a path; other values set by
the launcher are also removed wherever else they appear in the note. If a launcher in that repair
touched anything that looks like a credential, the run arguments and data paths from that repair are
not kept at all. At most 50 notes are kept, and
a note not used for 90 days is deleted. Settings → Figure assistants → *Remember what fixed it* lists
every note, deletes one or all, and turns the feature off. The notes never leave the computer except
as described above and in a diagnostics bundle you choose to send.
Tavotto itself does not upload AI prompts, responses or diffs anywhere (see "Data
Tavotto does not upload"). The AI service, or the command-line tool in the first
case, has its own network, account and privacy behavior and is not a background
upload performed by Tavotto. Users should review the policy of the selected service
before using it with sensitive material.

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
  it sends nothing. Since consent version 4 the list of tools includes the journal-list tool
  (`tavotto_list_journals`): only the fact that an AI agent looked up the journal list is counted — never the
  search text, which journal, or any result.
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
* **Consent version 4 (2026-11-04).** One item was added: how many times an AI editor looks up the
  built-in journal list (the tool name only, in the MCP tool-call event above). Consent given for
  version 3 stopped being sufficient at that moment: no event is transmitted until the user is asked again,
  the existing identifier is kept, and anyone who had declined is not asked again.
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
* **Adding an image to the canvas** (dragging or pasting a PNG, JPG, TIFF, PDF or SVG)
  copies that file into the project folder on this machine and shows it as an
  ordinary figure. It is validated and written locally by the Tavotto process the
  window is already talking to over the loopback connection; nothing is uploaded,
  and no usage-statistics event is added for it.
* **Turning a Jupyter notebook into a script** (dropping a `.ipynb` on the canvas or the
  home screen, or using the notebook row in the script list) reads the notebook's JSON and
  writes a plain `.py` copy of its code cells next to it, entirely on this machine, by the
  Tavotto process the window is already talking to over the loopback connection. Nothing
  in the notebook is executed, uploaded or sent to an AI service, the notebook itself is
  never modified, and no usage-statistics event is added for it.

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
  render/export/test-run records, what AI repair did in this app session (the outcome, the
  assistant's own summary, the provider and model name — never its address or key — each
  test run's failure type and the hint it was given, the last part of every tool result
  the assistant saw, which can include parts of your script it read and the source lines
  of error messages, and any launcher it wrote; kept only in memory, so nothing remains after
  Tavotto restarts, and included whether or not *script source* is ticked), the notes kept by
  *Remember what fixed it* (see "Optional AI assistant"; also included whether or not *script source*
  is ticked, with secrets and your home folder removed), desktop-shell and install facts (app, Tauri and
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
  registered in the open project, the scripts of recent test runs and of recent "generate editable
  version" conversions — including R and MATLAB scripts — and the unadopted candidate script such
  a conversion left in Tavotto's local cache, and the local Python modules those scripts loaded from outside the project folder when they last ran — only `.py` modules that were really loaded from a folder the script itself added to Python's search path, never third-party packages or Python's own; never files sitting directly in your home folder or at the root of a drive, never anything under the credential folders Tavotto's AI helpers are also barred from (such as `.ssh`), never through a symbolic link; at most 20 per script, each cut at 1 MiB and counted in the same 100-file limit as the scripts; Tavotto remembers only the paths of these modules, in its own data folder for 30 days, and reads their contents only when you tick this box — attached as they are — **secret removal in scripts is best
  effort and cannot be guaranteed, so do not tick it if your scripts contain secrets**;
  without it the bundle contains no script source at all, including the source lines normally
  printed inside tracebacks, apart from the AI-repair record described above), *system crash
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
* **Replies to a report.** If *Contact the maker* is enabled, sending a report also
  opens a linked letter on the server, and the app receives and stores in its data
  folder (readable by your user only, where the platform supports it) a letter
  number and a secret token that can only fetch the maker's reply, together with
  the note you typed (if any). It is used by the same reply check described under
  *Contacting the maker* and is deleted after 30 days if no reply has arrived (365
  days once there is one) or when you remove it in *My letters*. With
  `TAVOTTO_NO_CONTACT=1` nothing of this is requested or stored.
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

## Contacting the maker

*Contact the maker* (top-right of the editor and of the home page) lets you write
to the Tavotto maintainers from inside the app, without signing in and without
setting up an email account. It sends **only when you press *Send***; offering
the button, opening the window and keeping a draft upload nothing, and a
cancelled, closed or failed send is never resumed on its own.

* **What is sent.** The subject (optional) and message you wrote; the app
  version, operating system, CPU architecture, how Tavotto was installed and your
  interface language; the attachments you chose (any file type, at most 5 and 50 MiB in total —
  only what you added, never anything from your projects on its own); the reply email address *only if you
  typed one*; and, *only if you tick Attach diagnostic info*, a redacted
  diagnostics bundle sent through the same *Send problem report* flow described
  above, including any of its extra items (script source, crash reports,
  damaged-session contents) that you tick there — all unticked by default (you see what it contains first, and only its report number travels with
  the letter).
* **Who receives it.** A service operated by the Tavotto maintainers on
  `tavotto.cn` (mainland China; attachments are stored on Tencent Cloud COS,
  Shanghai). Outside mainland China the app tries `api.tavotto.com` first, a
  relay that only forwards the request to `tavotto.cn` and stores and logs
  nothing; inside mainland China it tries `tavotto.cn` first. If one does not
  answer, the other is used. No other host is ever contacted.
* **What it is used for.** Reading your letter and replying to it. The reply
  email address is used only to send you the reply. It is not linked to usage
  statistics (the letter carries no telemetry identifier and no usage event is
  recorded for it), is not shared, and is not used for anything else.
* **Replies inside Tavotto.** Whether or not you gave an email address, the
  maker's reply can be fetched by the app: after sending, the app stores the
  letter's number and a secret reply token in its data folder (readable by your
  user only, where the platform supports it) and asks the service for replies
  when it starts and every 6 hours — and only while such a record exists. The
  reply email address is not kept in that record; it is remembered, if at all,
  only in the app's local interface storage and can be forgotten from the
  window.
* **What stays on your computer (second phase).** Besides the number and the
  reply token, the app keeps a local copy of **the subject and message you
  wrote and the *names* of the files you attached** (not the files themselves, and
  not your reply email address) so *My letters* can show your letter next to the
  maker's reply. It is deleted together with the record: when you remove the
  letter in the app, or after 365 days. If a reply contains images, the app
  downloads them from the same service (same hosts, same reply token, only while
  the record exists) and stores them in its data folder (readable by your user
  only, where the platform supports it); they are deleted with the record too.
  **Opening a reply makes no network request**: the reply is shown from the local
  copy in a sandboxed frame that cannot run scripts or connect anywhere, and its
  images are read from disk. Clicking a link in a reply opens it in your default
  browser, as any link would.
* **Journal tips.** The *Looks wrong* link on a journal source card and the
  *Not listed?* link in the journal picker open this same window and send a
  journal tip. Besides the usual letter contents above, a tip carries only
  catalog data and what you typed: the journal's catalog ID and name, the rule
  and its catalog value that the card shows, the source ID, the catalog
  revision numbers, the text in the picker's search box (at most 120
  characters) when the journal was not found, and the source link you pasted
  (`https://` only). Nothing from your project is added. You cannot enter values:
  the maintainers look the source up themselves and a tip never edits the
  catalog on its own. Attachments you add (a PDF or screenshot of the
  journal's instructions) are used only to check the source, are not
  published or passed on, and are deleted 90 days after the tip is closed;
  the letter itself and its structured fields are kept like any other letter
  (365 days). A tip is not linked to the device token the journal catalog uses
  (replies come back through the letter's own reply token), so the catalog's
  read records and your tips cannot be joined. The links do not appear with
  `TAVOTTO_NO_CONTACT=1` or `TAVOTTO_NO_JOURNAL_SYNC=1`.
* **Crediting a tip.** On a wrong-entry or missing-journal tip you can tick
  *Credit my name on the cited entry* (unticked by default; not offered for editor
  wording, and agents cannot set it). If you do, the name you typed (1–32
  characters) is sent with the tip and, once the maintainers accept the tip and
  confirm the credit, is shown as *Thanks to <name> for the correction* on the source card of the
  entry it changed — **visible to every Tavotto user**. Leave it unticked and
  no name is sent or shown. To have a credit removed, reply to the letter or
  send a new one through *Contact the maker*; it disappears from the next
  catalog release.
* **No email program is involved.** The app never opens your mail program and
  contains no mail links; if sending fails it only offers *Copy content* and
  *Copy address* (`feedback@tavotto.com`) so you can decide how to send it.
* **Retention and deletion.** Letters, replies and attachments are deleted from
  the server after 365 days, and the app's local record is deleted after the same
  period. To have a letter deleted earlier, email `feedback@tavotto.com` quoting
  its number (`TVM-…`).
* **Switch.** `TAVOTTO_NO_CONTACT=1` removes the feature (no button, no network).

## External review links

*Share for review* (top bar, next to *Export*) turns the figure or layout you are
looking at into a link that other people can open in a browser and comment on,
without installing Tavotto or signing in. It uploads **only when you press
*Create link* or *Update this link***; opening the window uploads nothing, and a
cancelled or failed upload is never resumed on its own. An agent in the Tavotto
plugin (Codex, Claude Code…) can also start the same upload, but only after it has
asked you and you have said yes; without your approval it only shows a preview and
sends nothing.

* **What is uploaded.** A rendered picture of the current figure or layout (PNG,
  at most 3000 px on the long side), a list of the elements on that picture —
  where each one is, what kind it is (curve, tick label, legend…), the text you
  can already see on it (axis labels, legend entries), and the outline of curves
  and shapes as they are drawn, cut to the part that is visible in the picture —
  the title, and the note to reviewers if you wrote one. The outlines let a
  reviewer click a curve or a single tick label precisely; they contain nothing
  that is not already visible in the picture. **Your scripts, data, project
  files, file paths and computer details are never uploaded.**
* **Who can see it.** Anyone who has the link. Links are not listed or indexed.
  Reviewers type a display name of their choice; it is not verified.
  Seeing the pictures needs no sign-in. Each picture also has its own web
  address. It cannot be guessed, but it is not encrypted or password-protected:
  anyone who gets hold of it (for example, a reviewer who copies it) can open
  that picture directly, without the link. When you stop sharing or the link
  expires, the pictures are deleted at once; an address someone already opened
  may keep working for up to about 10 minutes.
* **Where it is stored.** A service operated by the Tavotto maintainers on
  Tencent Cloud CloudBase (mainland China), reached at `review.tavotto.cn`. No
  other host is contacted for this feature.
* **How long it is kept.** 30 days after the last update. *Stop sharing* deletes
  the pictures, comments and verdicts at once; expired links are deleted the same
  way.
* **Daily limit.** To count the free daily limit (1 new link per device per
  day; updating an existing link is not limited) the app creates a random device key on first
  use and signs its requests with it. The key identifies this installation only,
  is not linked to usage statistics and is not used for anything else.
* **What stays on your computer.** The link's number, a secret management token,
  thumbnails of the versions you shared and a copy of the comments, in the app's
  data folder (readable by your user only, where the platform supports it), so
  you can still read the comments after the link expires. While you have an
  unexpired link, the app asks the service for new comments every 30 minutes, and
  every minute while the *Review* tab is open; with no such link it makes no
  request at all.
* **Turning it off.** `TAVOTTO_NO_REVIEW_SHARE=1` removes the feature and all of
  its network requests (the plugin passes this switch on to its agent tool).

## Handing review comments to the Agent

*Hand to Agent* (the *Review* tab, shown when the figure has unresolved comments)
asks the AI connection **you** selected to draft a change plan from those comments.
It sends nothing until you press it, and what it produces is only a plan: your
figure changes when you press *Apply*.

* **What is sent.** To the AI connection you chose (the same kind of connection as
  in *Optional AI assistant* above, so it follows that connection's terms): the
  text of the unresolved comments and where each one points, and a table of the
  elements of this figure — each element's label, its editable properties with
  their current values, and its position on the picture. **Reviewers' display
  names are not sent.** Scripts, data, project files, file paths and computer
  details are not sent either.
* **Notes on the revised figure (Premium).** *Redo with notes* sends the notes you
  wrote on the "After" picture (their text and the element or area each one
  points at), the table of elements as they look after the previous version, and
  a short list of what the previous version changed, to the same AI connection.
  The notes themselves are never uploaded to the review service.
* **Suggestions in the figure assistant.** When you ask the assistant for suggestions
  and then say "do what you suggested", the previous assistant answer and your
  previous message in that figure's conversation (together at most 2,000
  characters) are sent to the AI connection you chose, so the follow-up can be
  understood; nothing earlier is sent.
* **Comments that need a script change (only when you press the button).** If a
  comment can only be done by editing the script, the plan card offers *Edit the
  script for the rest*. Only when you press it, the text of those comments (still
  without the reviewers' names) goes to the AI connection **you** selected, together
  with the script of this figure, the same as asking the assistant to edit the
  script; the comments are given to it as material to work from, not as
  instructions. The result is a script plan you review before it changes anything.
  Nothing is sent automatically.
* **What stays on your computer.** The plan, the before/after pictures and your
  edits to it. When you apply a plan, the app automatically marks the matching
  comments as resolved and syncs that to the review service (undoing the plan
  syncs the reversal); only the comment IDs and their resolved state are sent.
  The drafted reply stays on your computer and is composed in the app without
  calling any model; it is sent only when you press *Send* on it. *Update same
  link* (the existing review upload, described above) uploads a new version only
  when you press it.
* **Turning it off.** Not connecting an AI is enough; there is no other network
  request for this feature.

## Change plans in the assistant

When you send an instruction in the assistant for a single figure, the app first asks
the AI connection **you** selected for a *change plan* (a card you can compare and
apply) instead of editing the script right away. Only instructions the plan can
express (colours, sizes, positions, text, visibility of what is already in the figure)
become a plan; anything that needs the script is handed to the Agent as before, after
the plan step has said so.

* **What is sent for the plan.** To the AI connection you chose: the text of your
  instruction, which element you had selected (if any), and the same table of the
  figure's elements as above (labels, editable properties with their current values,
  positions). Scripts, data, project files, paths and computer details are not sent
  in this step. Instructions with attached reference images skip the plan step.
* **Editing drawn objects on the canvas.** When the assistant edits the text, arrows,
  shapes and paths drawn on the canvas (not the figures inside it), it sends the AI
  connection you chose a summary of those objects on the canvas: their text, positions,
  sizes and styles, and the anchor points of paths, together with your instruction and
  which objects you had selected. To place new annotations (a frame, a panel label, a
  bracket, an arrow) the summary also lists where each figure sits on the canvas: its
  position and size, labelled "P1", "P2"… together with the figure's project-relative
  file name. For figures the app can re-draw on your computer (the same drawing step
  used for export, with the edits you have not saved yet), the summary also carries the
  figure's layout so an arrow can point at a curve's peak: each axis's range, and the
  elements' roles, labels (curve names, axis titles, legend entries, tick labels), boxes
  and the main colours. The figure's data values, scripts and project files are not sent.
  The plan is computed on your computer and written nowhere until you apply it.
* **One-click layout.** The comparison pictures are drawn on your computer by the
  export engine; nothing about them is sent anywhere.
* **Turning it off.** Settings → Assistant → Advanced → *Show a change plan before
  editing*. Off means the assistant and one-click layout behave as before (edit the
  script / apply directly).

## Paid edition components

These requests exist only on a computer where a paid-edition license has been
activated; without a license the app makes none of them.

* **Fetching the component release list.** The app sends the license number,
  this device's public key, a signature, a timestamp, the system platform and
  the protocol versions it supports, to get the release list and a short-lived
  download address for the paid-feature component. **It does not send your
  projects, scripts, data, file paths or computer details.**
* **Downloading the component.** The component is downloaded from Tavotto's
  download server, which records the IP address and time, as it does for in-app
  updates. The component runs on this computer as a separate program and does
  not connect to the network.
* **Turning it off.** `TAVOTTO_NO_LICENSE=1` removes the paid edition and all of
  its network requests.

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
