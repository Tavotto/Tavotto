# Code signing policy

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

This policy applies to Magplot releases published from
<https://github.com/erwanjun/magplot>.

## Project and scope

Magplot is an open-source scientific-figure layout and editing application for
Matplotlib. The project is released under AGPL-3.0-only. This subscription is
used only for Magplot artifacts built from this repository, currently the Windows
NSIS installer named `Magplot-<version>-Windows-Setup.exe`.

The project team does not submit binaries from other projects or binaries built
from a source tree that is not this repository. Third-party open-source
components may be included in the installer, but they are not presented as
Magplot-authored source and are not independently signed with this subscription.

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

The workflow is fail-closed when SignPath signing is enabled. Until the project
subscription and repository variables are configured, an unsigned build may be
produced for testing only and must not be described as a signed release.

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

Magplot does not transfer user figures, scripts, project files or exports to the
maintainers. The complete project privacy policy is available at
<https://github.com/erwanjun/magplot/blob/main/docs/privacy.md>.

## Verification

Users can verify the Windows installer with Windows Authenticode tools such as
PowerShell's `Get-AuthenticodeSignature`. The corresponding source revision,
workflow run and release are linked from GitHub so that the signed artifact can
be traced back to this repository.
