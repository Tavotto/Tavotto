# Security policy

## Reporting a vulnerability

**Please report privately, not in a public issue.** Use GitHub's private
vulnerability reporting:

**[Report a vulnerability →](https://github.com/erwanjun/magplot/security/advisories/new)**

(Security tab → Report a vulnerability. This is enabled on this repository, so
the form is available to anyone with a GitHub account.)

Please include what you did, what happened, and what you expected — plus the
Magplot version and your platform. A proof of concept is welcome but not
required. You'll get a first response within a week; if the report is confirmed,
you'll be credited in the advisory and the release notes unless you'd rather not
be.

## Supported versions

Magplot ships fixes on the latest release only. There are no maintained release
branches: if you are affected, the fix will be in the next version.

| Version | Supported |
|---|---|
| Latest release | ✅ |
| Anything older | ❌ — upgrade |

## What Magplot's threat model actually is

This is a local desktop and localhost application, and knowing what it does and
doesn't defend against will save you time deciding whether something is a bug.

**Your figure scripts are executed as code.** Rendering a panel means running your
matplotlib script in a worker process. Magplot does not sandbox it against you —
opening a figure library is equivalent to running the scripts in it. The worker
does run with its working directory set to a scratch directory and guards
`Path.unlink`, but that is there to stop a *well-meaning* script from deleting or
overwriting files as a side effect of being run by a tool. **It is not a security
boundary against a hostile script.** Treat a figure library the way you'd treat
any other directory of executable code.

**Nothing is uploaded.** Rendering, composition and export are local processes.
The only outbound requests are the once-a-day release check and, if you accept an
update in the desktop app, the download of that update. Both stop when you turn
the check off.

**In scope, and taken seriously:**

- Escaping the localhost HTTP surface — the desktop shell binds `127.0.0.1` on a
  dynamic port and authenticates with a one-time nonce passed over stdin, exchanged
  for an HttpOnly cookie, with Host/Origin checks and a 401 fallback on everything
  outside the bootstrap route. Any way around that is a vulnerability.
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
- Findings against a Magplot you modified yourself, or an unsupported old version.
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
