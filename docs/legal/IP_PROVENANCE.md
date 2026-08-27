# IP / copyright provenance audit

> This document is an engineering/licensing inventory, not legal advice. Final
> commercial licensing and trademark decisions should be reviewed by qualified
> counsel.

## Audited baselines

This audit has been run twice. Both are recorded: the first establishes when the
rights position was first examined, the second is the one that describes the tree
as it stands.

### Initial audited baseline (historical record — do not update)

| | |
|---|---|
| Commit | `aaa065f298ac4ce8a66a3482786bedf516a1154b` |
| Branch | `main` |
| Audit date | 2026-08-27 |

The first full audit, performed when this legal infrastructure was written.
Retained so the chain of review is traceable.

### Current audited baseline

| | |
|---|---|
| Commit | `ff732eaa8b58df9eeccf32ec5e0cbf5efb928851` |
| Branch | `main` (`origin/main` at the time of the audit) |
| Audit date | 2026-08-27 |
| Commits reachable from this baseline | **419** |
| Merged pull requests | **104** |

**This is a "last audited rights baseline", not a claim about `HEAD`.** `main`
moves; this marker is not re-stamped on every commit, and no test requires it to
equal the current tip. Ongoing assurance for *new* work comes from the CLA gate
running on each pull request, not from re-auditing the whole history each time.

### A correction to the commit count

The initial audit reported **745 commits**, counted with `git rev-list --all`.
That number counts every reference in the local clone — including working
branches and worktrees that were never merged. The figure that matters for the
distributed product is what is **reachable from `main`**:

| Measure | Count |
|---|---|
| Reachable from the current baseline (`git rev-list --count ff732ea`) | **419** |
| All local refs (`git rev-list --count --all`) | 756 |

The 419 figure is used throughout this document. The conclusion is unchanged in
either denominator — the composition of authors is the same — but the earlier
number overstated the size of the audited history and is corrected here.

## Method

`git shortlog -sne`, `git log --format='%H %an %ae %cn %ce'`, full-history scans
for `Co-authored-by` and `Signed-off-by` trailers, and
`gh pr list --state merged` for PR authorship. Third-party inventory by tree scan
for licence headers, vendor directories and binary assets; dependency inventory
from the manifests and from each installed distribution's own metadata
(`cargo metadata`, PyPI JSON API, `node_modules/*/package.json`).

## Commit authors

Four distinct author identities appear in the entire history.

### Maintainer-controlled

| Identity | Commits | Note |
|---|---|---|
| `erwanjun <1259959884@qq.com>` | 305 | |
| `erwanjun <88193520+erwanjun@users.noreply.github.com>` | 107 | GitHub web/API identity for account `erwanjun` |
| `erwanjun <malajiaqi@gmail.com>` | 6 | |

**418 of the 419 commits reachable from the baseline.** These are three email
addresses of one person: the GitHub noreply address encodes account id
`88193520` / login `erwanjun`, which is also the account that authored 103 of the
104 merged pull requests. The repository records that person as the project
author (`pyproject.toml` `authors = [{ name = "erwanjun" }]`).

Committer identities add only `GitHub <noreply@github.com>` — the web-flow
committer for squash merges performed through the GitHub UI. It is a mechanism,
not an author.

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

### Incremental audit: `aaa065f` → `ff732ea`

The 19 commits added between the two baselines were audited separately, because
"the PR opener is the maintainer" is not by itself evidence that the *content*
has a single rights source.

| Check | Result |
|---|---|
| Commits | 19 |
| Authors | **19/19** `erwanjun <88193520+erwanjun@users.noreply.github.com>` |
| Committers | 19/19 `GitHub <noreply@github.com>` (squash-merge web flow) |
| `Co-authored-by` trailers | **0** |
| `Signed-off-by` trailers | **0** |
| New files added | 13 — 5 `.py`, 5 `.ts`, 3 `.tsx`, all Tavotto-authored |
| New binary or asset files | **0** |
| Third-party markers introduced (`Copyright`, `SPDX-License-Identifier`, "derived from", "adapted from", "copied from", "vendored") | **0** |
| Dependency manifests changed | `pyproject.toml` only — and the diff is **entirely Ruff configuration** (`I` rule, `src` roots). No new runtime, frontend or Rust dependency. |
| Lockfiles changed | none (`pnpm-lock.yaml`, both `Cargo.lock`s, `runtime-lock.json`, `playground-runtime.json` untouched) |

The diffstat for this range is large (243 files, +24205/−11663) but is dominated
by `7d0f98b`, a repository-wide `ruff format` baseline described in its own
commit message as "纯机械提交，无一处人工改动" (a purely mechanical commit).
Reformatting existing first-party code creates no new authorship and introduces
no third-party material. The functional work in this range is the 13 new files
listed above plus edits to existing Tavotto code.

**No external human contribution entered the tree in this range.**

### External human contributors

**None found.** No commit, PR, co-author trailer or merge in the audited history
originates from a human other than the maintainer.

There are no merges from forks: every merge commit in the history is the
maintainer merging `origin/main` into their own branch.

### Machine-generated contributions — formal record

Classified **REVIEW**. Both commits were re-verified against the current
baseline and are reachable from `main`.

| Field | `b8124733a328bae3e49773e1fef790ef441ef9d4` | `edf5469d808b50cc2c9c437c4074f4bf8565e4a6` |
|---|---|---|
| Date | 2026-08-18 15:26:06 +0800 | 2026-08-18 15:26:24 +0800 |
| Author | `erwanjun <88193520+erwanjun@users.noreply.github.com>` | same |
| Committer | `GitHub <noreply@github.com>` | same |
| Trailer | `Co-authored-by: Copilot Autofix powered by AI <175728472+Copilot@users.noreply.github.com>` | same |
| Subject | "Potential fix for pull request finding" | same |
| Path | `src/magplot/engine/handoff.py` (now `src/tavotto/engine/handoff.py`) | same |
| Diffstat | 8 lines changed (4+/4−) | 4 lines changed (3+/1−) |
| Nature of change | Rewrote a four-line docstring paragraph explaining why `os.path` is used instead of `pathlib` | Added a three-line `should_write` guard around a config write |
| Still present in the current tree? | Docstring has since been edited further | **Yes** — `should_write` is live at `src/tavotto/engine/handoff.py:226` |

A scan of the full history reachable from the baseline finds **exactly these
two** commits carrying a Copilot or AI co-author trailer, and no others.

**What this record does and does not say.** It records the facts: which commits,
which paths, which lines, what metadata. It does not reach a legal conclusion.

> The repository history records Copilot Autofix involvement in these two
> commits. Whether any additional copyright or provenance action is required is
> outside the repository's ability to determine.

Two observations that bound the question without resolving it: no third-party
*human* is asserting authorship here — Copilot Autofix is a GitHub feature
operating on this repository's own code, and both changes were reviewed and
accepted by the rights holder. And the exposure is small and reimplementable —
one file, seven lines — so if counsel prefers them removed, that is cheap.

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

## Authorship of this legal documentation

`docs/legal/**` is **not uniformly Tavotto-owned**, and should not be described
that way.

| Material | Origin | Terms |
|---|---|---|
| The operative `# Agreement` sections of [CLA_INDIVIDUAL.md](CLA_INDIVIDUAL.md) and [CLA_CORPORATE.md](CLA_CORPORATE.md) | **Harmony Agreements 1.0** (Project Harmony, 2011), with the template's own options selected and blanks filled | The template carries "This work is licensed under a Creative Commons Attribution 3.0 Unported License"; the Harmony policies page separately grants a worldwide, non-exclusive, royalty-free licence to modify, reproduce and distribute the templates. **Attribution is given** at the foot of each document. |
| Everything else in those two files — provenance tables, configuration notes, "How to sign", Schedule A | Tavotto-authored | AGPL-3.0-only with the repository |
| All other files in `docs/legal/`, plus `TRADEMARKS.md`, `.github/cla-policy.json`, `scripts/ci/cla_gate.py`, `tests/test_legal_contribution_policy.py` | Tavotto-authored | AGPL-3.0-only with the repository |

Two consequences worth stating plainly:

- **Do not claim blanket Tavotto copyright over `docs/legal/**`.** Part of it is
  Harmony's text used under CC BY 3.0, and the attribution notices must survive
  edits to those files.
- **Project Harmony trademarks are not used.** Harmony's trademark licence
  covers only the *unmodified* template; because options were selected and
  blanks filled, that licence does not apply and is not relied on. The
  references to Harmony are factual statements of derivation, not claims of
  endorsement or certification.

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

Re-verified against the current baseline.

| Artefact | Project `LICENSE`? | Third-party notices? | Evidence |
|---|---|---|---|
| Wheel / sdist | **Yes** | via SBOM at release | `pyproject.toml` `license-files = ["LICENSE"]`; hatchling places it in `.dist-info/licenses/` |
| GitHub Release | **Yes** (source archives) | SPDX SBOM of the wheel | `anchore/sbom-action` in `release.yml`; no separate `LICENSE`/`NOTICE` asset is uploaded |
| Desktop app (PyInstaller + Tauri) | **No** | **No** | `packaging/tavotto.spec` contains zero occurrences of `license`/`notice`/`third-party`; `src-tauri/tauri.conf.json` has `bundle.licenseFile: null` and `bundle.resources` carrying only the sidecar |
| Codex plugin | **No** | **No** | `codex-plugin/` ships `README.md`, `AGENTS.md`, `assets/`, `mcp/`, `skills/` — no licence file |

**This is a gap in the current AGPL distribution**, independent of any future
commercial edition. The desktop artefact is a distributed binary of an
AGPL-3.0-only program that also bundles MIT-, BSD-, PSF- and MPL-licensed
components whose licences require their notices to accompany binary
distribution. Redistributed CPython is in the same position.

It is deliberately **not fixed here.** This change is scoped to contributor
governance and licensing documentation; packaging is a different root cause with
a different verification surface, and a notices file should be *generated* from
the dependency closure rather than hand-maintained.

Tracked as **[issue #182](https://github.com/Tavotto/Tavotto/issues/182)** —
"Distribution licensing: desktop/plugin artifacts must ship project licence and
generated third-party notices".

The CLA must **not** be shipped in any product artefact. It is contributor
governance, not a runtime licence.

## Open questions

| # | Question | Status |
|---|---|---|
| 1 | ~~Who is the rights holder, and under which law?~~ | **Resolved 2026-08-28: Jiaqi Wan**, a natural person, under Hong Kong SAR law. A company was not required. CLA is at `1.0` and signable. |
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
