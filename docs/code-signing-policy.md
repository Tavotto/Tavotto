# Code signing policy

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

This policy applies to Tavotto releases published from
<https://github.com/Tavotto/Tavotto>.

## Project and scope

Tavotto is an open-source scientific-figure layout and editing application for
Matplotlib. The project is released under AGPL-3.0-only. This subscription is
used only for Tavotto artifacts built from this repository, currently the Windows
NSIS installer named `Tavotto-<version>-Windows-Setup.exe`.

The project team does not submit binaries from other projects or binaries built
from a source tree that is not this repository. Third-party open-source
components may be included in the installer, but they are not presented as
Tavotto-authored source and are not independently signed with this subscription.

## Build and release process

1. A version tag is checked out by the public GitHub Actions workflow
   `.github/workflows/desktop-tauri.yml`.
2. The Windows build runs on a GitHub-hosted `windows-latest` runner and builds
   the Tauri application, PyInstaller sidecar and bundled rendering runtime from
   the checked-out source.
3. The final installer is uploaded as a GitHub Actions artifact and submitted to
   SignPath for signing using the configured artifact, source and build policies.
4. Each release is manually approved by the project approver before the signed
   installer is attached to the corresponding GitHub Release.

The workflow is fail-closed when SignPath signing is enabled: a release build
that has the variables set but cannot obtain a valid signature fails rather
than attaching an unsigned file.

Until the project subscription and repository variables are configured, a
release still attaches an **unsigned** Windows installer. That is a deliberate
choice made on 2026-08-22, when the release signing gate first fired for real:
refusing to build without SignPath does not withhold an unsigned installer, it
withholds the entire Windows desktop application — and, because the updater
manifest requires both platforms to be present, it also leaves macOS users with
no in-app update at all. Shipping unsigned is the lesser harm, and it is the
same form every release through 0.8.0 took.

The exception is narrow and it is never silent:

* it covers **Authenticode only**. The minisign key that signs update packages
  and the Apple Developer ID certificate remain hard requirements — a release
  build missing either still fails;
* the run page carries a warning annotation and a job summary section saying
  the installer is unsigned, what users will see (a SmartScreen prompt), and
  that the update chain is still trustworthy because it rests on minisign
  rather than on Authenticode;
* the installer still carries a build provenance attestation, so its origin is
  verifiable with `gh attestation verify` even though it is not code-signed.

An unsigned installer must never be described as a signed release. Once the
subscription is in place, restore the hard failure by moving the SignPath check
back into the gate's `missing` list; `tests/test_runtime_build.py` guards both
the remaining hard requirements and the fact that the exception announces
itself.

## macOS artifacts (out of scope for this subscription)

The macOS `.dmg` is **not** signed with the SignPath certificate. It is signed
with an Apple Developer ID Application certificate held by the maintainer and
notarized by Apple, in the same workflow but on a separate runner. It is listed
here only so the two chains are not confused with each other.

Like the Windows installer, the macOS application embeds a private Python
rendering runtime (CPython plus a pinned scientific stack) so that users do not
need to install Python. Every nested Mach-O file in the application — the shell,
the PyInstaller sidecar, the embedded interpreter and every compiled extension
module in that runtime — is signed individually, from the inside out, with the
hardened runtime enabled, by `scripts/codesign_macos.py`. That script then
re-verifies each one and checks that they all target the same architecture;
`codesign --deep` alone is not sufficient, because Mach-O files under
`Contents/Resources` are sealed as resources rather than recognised as nested
code, so `--deep` neither signs nor verifies them.

## Roles

- Committers and reviewers: [erwanjun](https://github.com/erwanjun)
- Approver: [erwanjun](https://github.com/erwanjun)

The project currently has one maintainer, who performs these responsibilities.
Changes from outside contributors are reviewed before merge, and every signing
request is reviewed before approval.

## Privacy

Tavotto does not transfer user figures, scripts, project files or exports to the
maintainers. The complete project privacy policy is available at
<https://github.com/Tavotto/Tavotto/blob/main/docs/privacy.md>.

## Verification

Users can verify the Windows installer with Windows Authenticode tools such as
PowerShell's `Get-AuthenticodeSignature`. The corresponding source revision,
workflow run and release are linked from GitHub so that the signed artifact can
be traced back to this repository.
