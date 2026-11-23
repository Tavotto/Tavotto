# Code signing policy

This policy applies to Tavotto releases published from
<https://github.com/Tavotto/Tavotto>.

Windows installers are signed with the project maintainer's own Certum
code signing certificate ("Standard Code Signing in the Cloud", issued by
Certum Code Signing 2021 CA) once signing is enabled for releases. The private
key stays in Certum's cloud HSM and cannot be exported. Earlier releases were
published unsigned under the per-tag waiver below; the notes of every release
state whether its Windows installer is signed. The decision record is
[ADR 0202](adr/0202-windows-signing-certum-ssign.md).

## Project and scope

Tavotto is an open-source scientific-figure layout and editing application for
Matplotlib. The project is released under AGPL-3.0-only. The certificate is
used only for Tavotto artifacts built from this repository, currently the Windows
NSIS installer named `Tavotto-<version>-Windows-Setup.exe`, and for the
maintainer's own Tavotto add-on components.

The project does not sign binaries from other projects or binaries built from a
source tree that is not this repository. Third-party open-source components may
be included in the installer, but they are not presented as Tavotto-authored
source and are not independently signed.

## Build and release process

1. The release workflow `.github/workflows/release.yml` resolves the release
   tag to an exact commit that must already be on the protected `main` branch,
   and calls `.github/workflows/desktop-tauri.yml` with that commit.
2. The Windows build runs on a GitHub-hosted `windows-latest` runner and builds
   the Tauri application, PyInstaller sidecar and bundled rendering runtime from
   the checked-out source.
3. When signing is enabled (`vars.CERTUM_SIGNING_ENABLED`), that Windows job
   runs in the protected GitHub environment `windows-signing`. The job does not
   start until the project approver approves it, and the environment only accepts
   runs from `main` and `v*` tags. The signing credentials are secrets of that
   environment and are passed to the single signing step only.
4. The installer is signed with [ssign](https://github.com/Le-Syl21/ssign), a
   pinned open-source client for the SimplySign cloud, built from source on the
   runner. Every signature carries an RFC 3161 timestamp from Certum.
5. Windows itself then checks the result: the signature must be `Valid`, the
   signer must match the expected certificate subject, and a timestamp must be
   present. Only then are the update package, provenance attestation and
   artifact manifest produced from the signed installer.

A release build with signing enabled that cannot produce a valid, timestamped
signature from the expected signer fails rather than attaching an unsigned file.

Without signing enabled, a release build fails instead of attaching an unsigned
Windows installer. The gate emits a `::error` annotation, writes the reason to
the job summary, and exits non-zero — the release is stopped, not shipped.

The only exception is a maintainer waiver that names one exact release tag:
when the repository variable `WINDOWS_UNSIGNED_WAIVER_TAG` equals the tag
being built (for example `v0.19.0`) and signing is not enabled, the gate
emits a `::warning` instead of failing, for that version only. The next
version no longer matches and is stopped again. A waived release is published
with the "not code signed" disclosure, and the waiver does not relax the
minisign or Apple requirements below.
The hard requirements are unchanged: the minisign key that signs update packages
and the Apple Developer ID certificate remain hard requirements — a release
build missing either still fails.

Manual non-release trial builds may still produce unsigned diagnostic artifacts;
this does not authorize publishing them as a release. The signing gate is skipped
only when `IS_RELEASE_BUILD` is not `1`, and the late Windows guard uses the same
release-only boundary.

An unsigned installer must never be described as a signed release. The release
notes state whether the Windows installer of that release is signed.
`tests/test_runtime_build.py` executes the gate with isolated fixture values to
guard missing credentials, fail-closed releases and unsigned non-release trials;
`tests/test_release_workflow_contract.py` guards where the signing credentials
can reach and what the signature check requires.

## macOS artifacts (out of scope for this subscription)

The macOS `.dmg` is **not** signed with the Certum certificate. It is signed
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
run is approved by the maintainer before it starts.

## Privacy

Tavotto does not transfer user figures, scripts, project files or exports to the
maintainers. The complete project privacy policy is available at
<https://github.com/Tavotto/Tavotto/blob/main/docs/privacy.md>.

## Verification

Users can verify the Windows installer with Windows Authenticode tools such as
PowerShell's `Get-AuthenticodeSignature`. The corresponding source revision,
workflow run and release are linked from GitHub so that the signed artifact can
be traced back to this repository.
