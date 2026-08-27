# IP / copyright provenance audit

> This document is an engineering/licensing inventory, not legal advice. Final
> commercial licensing and trademark decisions should be reviewed by qualified
> counsel.

## Audited baseline

| | |
|---|---|
| Commit | `aaa065f298ac4ce8a66a3482786bedf516a1154b` |
| Branch | `main` |
| Audit date | 2026-08-27 |
| History audited | all refs (`git log --all`), 745 commits |
| History span | 2026-08-16 (`c1cec93`, "Magplot 0.1.0") → 2026-08-27 |
| Merged pull requests | 101 |

Method: `git shortlog -sne --all`, `git log --format='%H %an %ae %cn %ce' --all`,
full-history scans for `Co-authored-by` and `Signed-off-by` trailers, and
`gh pr list --state merged` for PR authorship. Third-party inventory by tree scan
for licence headers, vendor directories and binary assets; dependency inventory
from the manifests and the installed distributions' own metadata.

## Commit authors

Four distinct author identities appear in the entire history.

### Maintainer-controlled

| Identity | Commits | Note |
|---|---|---|
| `erwanjun <1259959884@qq.com>` | 607 | |
| `erwanjun <88193520+erwanjun@users.noreply.github.com>` | 110 | GitHub web/API identity for account `erwanjun` |
| `erwanjun <malajiaqi@gmail.com>` | 27 | |

**744 of 745 commits.** These are three email addresses of one person: the
GitHub noreply address encodes account id `88193520` / login `erwanjun`, which is
also the account that authored 100 of the 101 merged pull requests. The
repository records that person as the project author (`pyproject.toml`
`authors = [{ name = "erwanjun" }]`).

Committer identities add only `GitHub <noreply@github.com>` (110 commits) — the
web-flow committer for squash merges performed through the GitHub UI. It is a
mechanism, not an author.

The first commit (`c1cec93`, 182 files, 37,668 insertions) is a project
bootstrap authored by `erwanjun`, not an import of a pre-existing codebase from
elsewhere.

### Known bots

| Identity | Commits | What it did |
|---|---|---|
| `dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>` | 1 | `1cb6ff7` — "Bump pytest from 8.4.2 to 9.0.3 (#6)". Dependency version bump only. |

`dependabot[bot]` also appears once as a `Co-authored-by` trailer, and carries
the history's only `Signed-off-by` trailer
(`dependabot[bot] <support@github.com>`). That trailer is Dependabot's own
convention; **the repository has never operated a DCO**, and this single trailer
must not be read as one.

### External human contributors

**None found.** No commit, PR, co-author trailer or merge in the audited history
originates from a human other than the maintainer.

There are no merges from forks: every merge commit in the history is the
maintainer merging `origin/main` into their own branch.

### Needs review

| Identity | Where | Why it is listed here |
|---|---|---|
| `Copilot Autofix powered by AI <175728472+Copilot@users.noreply.github.com>` | `Co-authored-by` on `b812473` and `edf5469` (both 2026-08-18) | See below. |

Both commits are authored **and** committed by `erwanjun`; Copilot Autofix
appears only as a co-author trailer. They touch one file
(`src/magplot/engine/handoff.py`, now `src/tavotto/engine/handoff.py`):

- `b812473` — rewrote a four-line docstring paragraph.
- `edf5469` — added a three-line `should_write` guard around a config write.

This is recorded rather than waved through because the copyright status of
machine-generated code suggestions is unsettled, and differs by jurisdiction.
Two things are worth separating:

- There is **no third-party human rights holder** here. Copilot Autofix is a
  GitHub feature operating on this repository's own code; no other person is
  asserting authorship.
- Whether the suggested lines attract copyright *at all*, and if so whose, is a
  legal question this audit does not answer.

**NEEDS LEGAL REVIEW** — before proprietary distribution, confirm that accepted
AI-suggested edits carry no third-party rights encumbrance. The practical
exposure is small and bounded (one file, seven lines, both changes reviewed and
accepted by the rights holder), and if counsel wants it removed entirely, both
changes are trivially reimplementable.

## Historical contributions requiring follow-up

**None.** No external human contribution was found in the audited history, so
there is nothing for a retroactive CLA to cover.

This section exists so that it is not silently absent. If a future audit finds
an external contribution predating CLA enforcement, record it here with:
author, commit/PR, affected paths, rough significance, current licence, and one
of the four permitted recommended actions (obtain retroactive CLA / rewrite or
replace / exclude from the proprietary branch / seek legal review).

## Third-party source inventory

**No third-party source code has been copied into this repository.**

Evidence:

- A tree-wide scan of tracked files for `Copyright (c)`, `Copyright ©` and
  `SPDX-License-Identifier` returns **zero** hits outside `LICENSE` itself.
- There is no `vendor/`, `third_party/`, `external/` or equivalent directory,
  and no checked-in minified library file.
- No fonts are bundled: zero tracked `.ttf`/`.otf`/`.woff`/`.woff2`/`.eot`
  files, and the frontend declares no `@font-face` and no Google Fonts link.

This is the distinction the audit turns on:

| | Governed by | Tavotto's rights |
|---|---|---|
| **Dependencies** — resolved by pip/pnpm/cargo, or bundled at package time | Each component's own licence | Tavotto has **no** copyright in them and cannot relicense them. See [COMMERCIALIZATION_DEPENDENCY_AUDIT.md](COMMERCIALIZATION_DEPENDENCY_AUDIT.md). |
| **Copied-in third-party source** — files living in this tree | Each file's own licence | **None found.** |

Only the second category would create files in this repository that Tavotto
cannot relicense. The first is a separate and much larger question, and it is
*not* resolved by the CLA — see [LICENSING.md](LICENSING.md).

Binary assets in the tree are project-created brand and documentation material:
`assets/brand/` (SVG marks, DMG background), `assets/icon/`, `assets/readme/`
(screenshots and diagrams of Tavotto's own interface), `docs/ux/img/`
(before/after screenshots of Tavotto's own interface),
`codex-plugin/assets/tavotto.svg`, and the example figures under `examples/`
(generated by the example scripts in the same directory). No stock imagery,
icon set or third-party illustration was found.

## Generated files

Two kinds, and the distinction matters:

### Generated from Tavotto's own source — no separate rights question

| Path | Generated by | Contains |
|---|---|---|
| `src/tavotto/web/` (not tracked; built into wheel/desktop) | `scripts/build_frontend.py` | Compiled output of `web/src` **plus bundled third-party JS** — see below |
| `examples/*/Fig*.pdf` | the example scripts beside them | Tavotto/matplotlib output of the repository's own scripts |
| `docs/ux/img/**`, `assets/readme/*.png` | screenshots of Tavotto | Tavotto's own interface |
| `packaging/runtime-lock.json`, `packaging/playground-runtime.json` | `scripts/build_worker_runtime.py --resolve` | Version pins and hashes — facts, not expression |

### Generated artefacts that embed third-party content

| Path | Generated by | Third-party content |
|---|---|---|
| `codex-plugin/mcp/widget/canvas.html` (**tracked**, 956 KB) | `scripts/build_mcp_widget.py` | A minified single-file bundle. It contains React and the other frontend runtime dependencies inlined, not just Tavotto's own code. |
| `web/playground.html` (**tracked**) | `scripts/build_browser_playground.py` | Same category. |
| `src/tavotto/web/` build output | `scripts/build_frontend.py` | Bundles the `dependencies` closure of `web/package.json`. |

These are **not** a provenance problem for the repository — every bundled
component is a permissively licensed dependency (see the dependency audit) and
their presence in a build artefact is ordinary bundling. They are called out
because "it's generated, so it's ours" is exactly the reasoning that would get
this wrong: a generated file is only free of third-party rights questions if
its *inputs* were. Here the inputs include third-party JavaScript, and the
licence obligations of those components (principally MIT attribution) travel
with the bundle.

**Known gap:** the desktop application does not currently ship `LICENSE` or any
third-party notices. See [Notices in distributed artefacts](#notices-in-distributed-artefacts).

## Notices in distributed artefacts

| Artefact | `LICENSE` included? | Evidence |
|---|---|---|
| Wheel / sdist | **Yes** | `pyproject.toml` `license-files = ["LICENSE"]`; hatchling places it in `.dist-info/licenses/` |
| GitHub Release | **Yes**, plus an SPDX SBOM of the wheel generated by `anchore/sbom-action` (`release.yml`) | |
| Desktop app (PyInstaller + Tauri) | **No** | No packaging step copies `LICENSE`: `packaging/tavotto.spec` contains no licence or notice entry in `datas`, and a scan of `packaging/`, `scripts/build_desktop.py` and `src-tauri/tauri.conf.json` for `LICENSE` returns nothing |
| Codex plugin | **No** | `codex-plugin/` ships `README.md`, `AGENTS.md`, `assets/`, `mcp/`, `skills/` — no licence file |

**NEEDS FOLLOW-UP** — the desktop and plugin artefacts are distributed binaries
of an AGPL-3.0-only program that also bundle MIT- and BSD-licensed components
whose licences require their notices to travel with binary distributions. This
is a genuine gap in the *current* AGPL distribution, independent of any future
commercial edition.

It is deliberately **not** fixed in this change, which is scoped to contributor
governance and licensing documentation; touching the packaging pipeline here
would mix an unrelated risk into a legal-infrastructure diff. It should be its
own change, and it should add a notices file assembled from the dependency
closure rather than a hand-maintained list.

Note that the CLA must **not** be shipped in any product artefact. It is
contributor governance, not a runtime licence, and has no business in a
user-facing package.

## Open questions

| # | Question | Status |
|---|---|---|
| 1 | Who is the legal rights holder? The repository records no legal entity — `README.md` says only "Tavotto™ is a trademark of the Tavotto project", which is not a contracting party. | **RIGHTS_HOLDER_CONFIGURATION_REQUIRED** — blocks CLA execution and any commercial licensing. See [CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md). |
| 2 | Do the two accepted Copilot Autofix edits carry any third-party rights encumbrance? | **NEEDS LEGAL REVIEW** — scope bounded to seven lines in one file. |
| 3 | Is the `erwanjun` identity's work encumbered by any employment or institutional agreement? An individual's own commits can still be owned by an employer. This audit can establish *who committed*; it cannot establish what agreements that person is subject to. | **NEEDS LEGAL REVIEW** — only the rights holder can answer. This is the single largest unverifiable assumption behind "rights-clean baseline". |
| 4 | Was any part of the pre-rename `Magplot` history developed under a different arrangement? The rename is a clean break within this same repository (first commit 2026-08-16), not an import, so no separate rights chain was found — but the question is recorded rather than assumed away. | Nothing found; recorded for completeness. |

## Conclusion

Subject to open questions 1–3, the repository is a **single-rights-holder
baseline**: 744 of 745 commits from one person, one dependency-bump commit from
Dependabot, no external human contributor, and no third-party source code copied
into the tree.

That is a strong starting position for dual licensing — and it is precisely why
the CLA matters *now*: the baseline is clean today, and the cost of keeping it
clean is a policy applied before the first external pull request, not after.

**This is not the same as saying a proprietary edition could ship today.** The
code in this tree is one of three layers, and the other two are unresolved: see
[COMMERCIALIZATION_DEPENDENCY_AUDIT.md](COMMERCIALIZATION_DEPENDENCY_AUDIT.md)
for the third-party dependency position (PyMuPDF in particular) and
[COMMERCIAL_EDITION_RIGHTS_POLICY.md](COMMERCIAL_EDITION_RIGHTS_POLICY.md) for
the overall readiness verdict.
