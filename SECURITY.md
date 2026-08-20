# Security policy

## Reporting a vulnerability

**Please report privately, not in a public issue.** Use GitHub's private
vulnerability reporting:

**[Report a vulnerability →](https://github.com/Tavotto/Tavotto/security/advisories/new)**

(Security tab → Report a vulnerability. This is enabled on this repository, so
the form is available to anyone with a GitHub account.)

Please include what you did, what happened, and what you expected — plus the
Tavotto version and your platform. A proof of concept is welcome but not
required. You'll get a first response within a week; if the report is confirmed,
you'll be credited in the advisory and the release notes unless you'd rather not
be.

## Supported versions

Tavotto ships fixes on the latest release only. There are no maintained release
branches: if you are affected, the fix will be in the next version.

| Version | Supported |
|---|---|
| Latest release | ✅ |
| Anything older | ❌ — upgrade |

## What Tavotto's threat model actually is

This is a local desktop and localhost application, and knowing what it does and
doesn't defend against will save you time deciding whether something is a bug.

**Your figure scripts are executed as code.** Rendering a panel means running your
matplotlib script in a worker process. Tavotto does not sandbox it against you —
opening a figure library is equivalent to running the scripts in it. The worker
does run with its working directory set to a scratch directory and guards
`Path.unlink`, but that is there to stop a *well-meaning* script from deleting or
overwriting files as a side effect of being run by a tool. **It is not a security
boundary against a hostile script.** Treat a figure library the way you'd treat
any other directory of executable code.

**Your content is never uploaded.** Rendering, composition and export are local
processes. Outbound requests are limited to: the once-a-day release check (and
the update download, if you accept one in the desktop app); the AI assistant you
invoke yourself, which runs a CLI installed on your machine; and — only after you
explicitly opt in — anonymous usage statistics. The telemetry channel carries a
fixed allowlist of events whose properties are booleans, bounded integers and
short enum strings; figures, scripts, filenames, paths, data, figure text and
prompts are not representable in it. It is off until you opt in, can be turned
off in Settings, and is disabled entirely by `TAVOTTO_NO_TELEMETRY=1`
(a separate control from `TAVOTTO_NO_UPDATE_CHECK`). See
[docs/privacy.md](docs/privacy.md) and
[docs/analytics/telemetry-events.md](docs/analytics/telemetry-events.md).

**In scope, and taken seriously:**

- Escaping the localhost HTTP surface — desktop and browser mode share one security
  middleware (ADR 0008). The desktop shell binds `127.0.0.1` on a dynamic port and
  passes a one-time nonce over stdin; browser mode carries its one-time nonce in the
  launch URL's fragment and additionally writes a `0600` local-process credential
  file for CLI handoff. In both modes the nonce is exchanged once for an HttpOnly
  `SameSite=Strict` cookie, Host is pinned to `127.0.0.1:<port>`, requests carrying
  an Origin must be same-origin, and everything outside the first-page static assets
  and `/api/version` answers 401 without a session. Any way around that — including
  DNS rebinding, nonce replay, or driving the API from another local web page — is
  a vulnerability. (`--insecure-no-auth` is a development-only bypass that prints a
  warning; running it is an explicit choice, not a default.)
- Reading or writing outside the project a user opened, without them asking.
- "Write back to original file" damaging or losing a file it wasn't asked to touch,
  or leaving one in a partially written state.
- Anything that leaks a stored API key. Keys live in the user config directory with
  `0700` permissions; the API only ever returns whether a key exists and its last
  four characters, and the diagnostics bundle redacts keys and personal paths.
- Handing figure content or data to an external service without an explicit action.
- Update packages: the desktop updater verifies a minisign signature against a key
  compiled into the app before installing. A way to make it install something
  unsigned or mis-signed is a vulnerability.

**Out of scope:**

- Arbitrary code execution achieved through a figure script — see above; that's the
  documented design.
- The AI assistant running `codex` or `claude` on your machine. That is the feature:
  it is opt-in, it snapshots your script first, and it shows you the diff.
- Findings against a Tavotto you modified yourself, or an unsupported old version.
- Reports produced only by an automated scanner, with no working path to impact.

## A note on unsigned Windows installers

Windows installers are built by GitHub Actions from this repository. Code signing
is provided by [SignPath.io](https://signpath.io) with a certificate from the
SignPath Foundation, and installers are described as signed only once they have
actually been through that process — see the
[code signing policy](docs/code-signing-policy.md). A release whose installer has
not been signed will still show a SmartScreen warning; that is expected, and is not
a vulnerability. If you want to verify what you downloaded, every release is built
in public and the build logs are readable.
