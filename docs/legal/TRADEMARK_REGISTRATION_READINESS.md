# Trademark registration readiness

> This document is an inventory of evidence that exists in this repository. It
> is **not** legal advice, not a clearance search, and not an opinion on
> registrability. Any filing decision requires trademark counsel.

## Status

**Tavotto is not registered anywhere.** There is no application, no serial
number, no registration number, and no filing in progress. The applicant
question is settled (Jiaqi Wan); pursuing a filing is a separate decision, and
the current one is **not to file yet**. Nothing in this
repository should be read as claiming otherwise, and the **®** symbol is not
used and must not be.

This document only assembles what evidence already exists, so that a
conversation with counsel can start from facts instead of reconstruction.

## Current mark usage

| | |
|---|---|
| Word mark | **Tavotto** |
| Symbol used | **™** (unregistered) |
| Styling | Fixed spelling and capitalisation, enforced in code |
| Single source of truth | `web/src/lib/brand.ts` (`PRODUCT_NAME`) and `engine/brand.py` — the interface and export formats never hand-write the product name |

Derived identifiers using the mark:

| Identifier | Value |
|---|---|
| PyPI distribution | `tavotto` |
| GitHub | `github.com/Tavotto/Tavotto` |
| Domain | `tavotto.com` (site and `/try` playground); `telemetry.tavotto.com` |
| File extension | `.tavotto` |
| Clipboard/format identifiers | `tavotto-package`, `tavotto-proof`, `tavotto/objects@1` |
| Desktop binaries | `Tavotto`, `tavotto-workerd`, `tavotto-desktop` |

## First-use evidence in this repository

The mark has a **short and precisely datable** history, which is unusual and
helpful. The project was previously named **Magplot**, renamed in a single
clean-break commit.

| Event | Date | Evidence |
|---|---|---|
| Project begins as "Magplot" | 2026-08-16 | `c1cec93` "Magplot 0.1.0" |
| Releases v0.1.0 – v0.7.0 published as Magplot | 2026-08-16 → 2026-08-18 | GitHub release list |
| **Rename to Tavotto** | **2026-08-20** | `103af50` "改名 Magplot → Tavotto：全仓库一次换干净，不留兼容层" |
| **First public distribution under the mark** | **2026-08-20T06:31:22Z** | PyPI `tavotto` 0.8.0 — first upload, licence `AGPL-3.0-only` |
| First GitHub release under the mark | 2026-08-20T06:22:18Z | `v0.8.0` |
| Continuous releases since | → 2026-08-27 | 5 PyPI versions (0.8.0 → 0.12.0); GitHub `v0.8.0` → `v0.12.0` |

The rename was deliberately a **clean break** with no compatibility layer: the
old `magplot-package` / `.magplot` / `magplot/objects@1` identifiers are not
accepted at all. For trademark purposes this means use of "Tavotto" begins
cleanly on 2026-08-20 with no ambiguous transition period.

**Note the age.** First use is approximately one week old as of this document.
Some jurisdictions and some filing bases care a great deal about use in
commerce, its date, and its continuity.

## Logo and design assets

All created for the project; no stock or third-party artwork was found (see
[IP_PROVENANCE.md](IP_PROVENANCE.md)).

| Asset | Path |
|---|---|
| Primary mark | `assets/brand/tavotto-mark.svg` |
| Variants | `tavotto-mark-compact.svg`, `-mini.svg`, `-mono.svg`, `-paper.svg`, `-reverse.svg` |
| Application icons | `assets/icon/icon.svg`, `icon-1024.png`, `icon-512.png`, `icon-256.png`, `icon.icns`, `icon.ico` |
| Installer artwork | `assets/brand/dmg-background.png` |
| Plugin mark | `codex-plugin/assets/tavotto.svg` |
| Source artwork | `logo.ai`, `logo.pdf` (repository root, untracked in some checkouts) |

A brand system document exists at the repository root
(`Magplot Brand System.html`) — note it still carries the **pre-rename name in
its filename**. Worth tidying, and worth knowing about before it is handed to
anyone as brand evidence.

## Goods and services — raw material only

Not Nice classifications. Counsel assigns those. This is what the product
factually is, drawn from the repository's own description:

- Downloadable computer software for editing, laying out and exporting
  scientific figures produced by matplotlib, without losing the generating code
  (`pyproject.toml`: *"Make AI-generated scientific figures editable without
  losing the code behind them."*).
- Distributed as a Python package, a desktop application (Windows, macOS), a
  browser-based playground, and a plugin for a coding agent.
- Target audience: researchers and scientific publication workflows
  (classifiers: *Intended Audience :: Science/Research*,
  *Topic :: Scientific/Engineering :: Visualization*).

## What is not established

| Question | Status |
|---|---|
| Who would be the applicant? | **Resolved: Jiaqi Wan**, a natural person. An applicant must be a legal person, which an individual is; no company was required. |
| Which jurisdictions? | **Requires trademark counsel / filing decision.** Not inferable from the repository. |
| Which Nice classes? | **Requires trademark counsel / filing decision.** Class 9 and/or 42 are the obvious neighbourhood for downloadable software and SaaS, but scope is a legal judgement. |
| Is "Tavotto" available and registrable? | **Not searched.** No clearance search has been performed — no knock-out search, no full search, no common-law or domain conflict review. Nothing here says the mark is available. |
| Is there conflicting prior use? | **Unknown.** Not investigated. |
| Does the "Magplot" history matter? | **Unknown.** The prior name was used publicly for four days across seven releases. Whether that affects anything is a question for counsel. |

## If a filing is pursued

Rough order, with the repository-side work marked:

1. ~~**Establish the applicant**~~ — **done: Jiaqi Wan.**
2. **Clearance search** by counsel, in the target jurisdictions. *(Not repository work.)*
3. **Decide jurisdictions and classes.** *(Not repository work.)*
4. **Assemble specimens** — the repository already holds most of this: the
   PyPI listing, GitHub releases, the interface screenshots in
   `assets/readme/` and `docs/ux/img/`, the logo files, and the website.
5. **File.** *(Not repository work.)*
6. **Only after registration issues**, and only in the jurisdictions where it
   issued, update `TRADEMARKS.md` and the READMEs from ™ to ®. Until then the
   test suite deliberately fails on any use of ® — see
   `tests/test_legal_contribution_policy.py`.

Steps 1–3 and 5 cannot be done from inside the repository, and no attempt was
made to guess at them.
