# Commercialisation dependency audit

> This document is an engineering/licensing inventory, not legal advice. Final
> commercial licensing decisions should be reviewed by qualified counsel.
> Nothing here is a determination that any particular licence does or does not
> permit a particular use.

## What this audit answers

Not "is Tavotto's own code relicensable" — that is
[IP_PROVENANCE.md](IP_PROVENANCE.md) and the CLA. This one answers a separate
question that the CLA does **not** touch:

> If a proprietary Tavotto Pro were built tomorrow, which third-party components
> could it distribute unchanged, and which could it not?

A contributor agreement grants rights over *contributions*. It has no effect
whatsoever on a dependency's licence. Every component below is governed solely
by its own terms.

## Audited baseline

Commit **`ff732eaa8b58df9eeccf32ec5e0cbf5efb928851`** (`main`), re-measured
2026-08-27. The previous run of this audit was at `aaa065f`.

**Delta since `aaa065f`: none.** The 19 intervening commits changed exactly one
dependency manifest — `pyproject.toml` — and that diff is entirely Ruff
configuration (adding the `I` rule and the `src` roots). No new runtime,
frontend or Rust dependency was introduced; `web/pnpm-lock.yaml`, both
`Cargo.lock` files, `packaging/runtime-lock.json` and
`packaging/playground-runtime.json` are untouched. Every count below was
nonetheless **re-measured rather than carried over**.

Sources, all read from the manifests and the artefacts' own metadata rather than
from memory:

| Layer | Source of truth | Scope |
|---|---|---|
| Python runtime deps | `pyproject.toml` `dependencies` | Installed by pip alongside the wheel |
| Python worker deps | `pyproject.toml` `[worker]` extra | Optional; bundled in desktop |
| Bundled desktop runtime | `packaging/runtime-lock.json` (full transitive closure, pinned) | Shipped inside the desktop app |
| Browser playground | `packaging/playground-runtime.json` | Loaded from CDN at runtime |
| Frontend | `web/package.json` + installed `node_modules` (363 packages scanned) | Bundled into the shipped UI |
| Rust supervisor | `cargo metadata` on `workerd/Cargo.toml` (22 crates) | Shipped binary |
| Desktop shell | `cargo metadata` on `src-tauri/Cargo.toml` (526 crates) | Shipped binary |

Licence values come from each distribution's own metadata (PyPI JSON API,
installed `.dist-info`, `cargo metadata`, `node_modules/*/package.json`).

## Classification

| Class | Meaning |
|---|---|
| **GREEN** | Permissive; commercial and proprietary distribution straightforward, subject to attribution/notice obligations. |
| **REVIEW** | Weak or file-level copyleft, dual licences, or terms whose obligations depend on *how* the component is used. Needs a specific determination, not a blanket answer. |
| **BLOCKER** | Cannot enter a proprietary build under its current terms without a separate commercial licence, replacement, or isolation. |
| **UNKNOWN** | Could not be determined. |

## The one that matters

| Dependency | Version | License | Used where | Distributed? | Community impact | Proprietary Pro impact | Required action |
|---|---|---|---|---|---|---|---|
| **PyMuPDF** | 1.28.2 | **`Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License`** — verbatim from the installed distribution's own metadata | `src/tavotto/pdfbackend/pymupdf_backend.py` — the **only** module in the repository permitted to import it. PDF reading, rasterising, vector composition. | **Yes** — a hard runtime dependency in `pyproject.toml`, so pip installs it with the wheel, and PyInstaller bundles it into the desktop app | **None.** AGPL-3.0-only against AGPL-3.0-only is the licence working as intended. | **BLOCKER** as things stand. | See below. |

### What can and cannot be said about it

**What is established.** PyMuPDF is dual-licensed. The open-source arm is
AGPL-3.0; the alternative is a commercial licence from Artifex Software, who
maintain both PyMuPDF and the underlying MuPDF. Tavotto currently takes it
under the AGPL arm — that is the only arm available without a commercial
agreement, and it is entirely appropriate for an AGPL-3.0-only project.

**What is not established, and is not decided here.** Whether any *particular*
proprietary architecture would or would not create incompatible obligations.
That depends on facts that do not exist yet: how Pro is built, how it links,
what it distributes, and to whom.

**The statement this audit does make:**

> If a proprietary Tavotto Pro continues to distribute, or to combine in a
> distributed product, a copy of PyMuPDF obtained under AGPL terms, that
> combination may produce incompatible licensing obligations. Before any formal
> proprietary distribution, Tavotto must confirm Artifex commercial licensing,
> replace the backend, or obtain other appropriate authorisation.

**What is already in place, and is worth keeping.** The repository maintains a
hard architectural boundary: `pdfbackend/pymupdf_backend.py` is the sole module
allowed to `import pymupdf`, enforced as a repository invariant (root
`AGENTS.md`), documented in `CONTRIBUTING.md` as grounds for sending a PR back,
and listed on the pull request template checklist.

That boundary was established for replaceability and explicitly notes that it
"matters for licensing". This audit is the point at which that reasoning becomes
concrete: **the boundary is what makes "replace the backend" a real option
rather than a rewrite.** It should be treated as a licensing control, not just a
design preference — and it should not be relaxed.

The boundary is a *contract*, not a solution. It does not reduce PyMuPDF's
obligations for the current AGPL product, and it does not make Pro possible on
its own. It means only that the substitution point exists and is small.

**Required action before proprietary distribution:** obtain an Artifex
commercial licence, or implement an alternative backend behind the existing
contract, or obtain other appropriate authorisation. **Requires legal review
before proprietary distribution.**

## Python — runtime and bundled

Every package in the desktop app's pinned closure (`packaging/runtime-lock.json`),
plus the wheel's runtime dependencies.

| Dependency | Version | License | Used where | Distributed? | Community impact | Proprietary Pro impact | Required action |
|---|---|---|---|---|---|---|---|
| Flask | 3.1.3 | BSD-3-Clause | Local HTTP server (parent process) | Yes — pip dep | None | GREEN | Notice |
| PyMuPDF | 1.28.2 | AGPL-3.0 / Artifex commercial | PDF backend | Yes — pip dep + bundled | None | **BLOCKER** | See above |
| matplotlib | 3.11.1 | matplotlib licence (PSF-based; classifier: Python Software Foundation License) | Render worker | Bundled in desktop; optional `[worker]` extra otherwise | None | GREEN | Notice |
| numpy | 2.5.2 | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | Worker | Bundled | None | GREEN | Notice |
| scipy | 1.18.0 | BSD-3-Clause | Worker | Bundled | None | GREEN | Notice |
| pandas | 3.0.5 | BSD-3-Clause | Worker | Bundled | None | GREEN | Notice |
| seaborn | 0.13.2 | BSD-3-Clause | Worker | Bundled | None | GREEN | Notice |
| pillow | 12.3.0 | MIT-CMU | Worker | Bundled | None | GREEN | Notice |
| fonttools | 4.63.0 | MIT | matplotlib dep | Bundled | None | GREEN | Notice |
| contourpy | 1.3.3 | BSD-3-Clause | matplotlib dep | Bundled | None | GREEN | Notice |
| cycler | 0.12.1 | BSD-3-Clause | matplotlib dep | Bundled | None | GREEN | Notice |
| kiwisolver | 1.5.0 | BSD-3-Clause | matplotlib dep | Bundled | None | GREEN | Notice |
| pyparsing | 3.3.2 | MIT | matplotlib dep | Bundled | None | GREEN | Notice |
| python-dateutil | 2.9.0.post0 | Apache-2.0 AND BSD-3-Clause (dual) | pandas dep | Bundled | None | GREEN | Notice |
| packaging | 26.3 | `Apache-2.0 OR BSD-2-Clause` | transitive | Bundled | None | GREEN | Notice |
| six | 1.17.0 | MIT | transitive | Bundled | None | GREEN | Notice |
| **CPython** | 3.13.15 (Windows embeddable) / python-build-standalone (macOS) | PSF License Agreement | The bundled interpreter itself | **Yes** — shipped inside the desktop app | None | GREEN — the PSF licence permits proprietary redistribution with notice | **Notice required.** A redistributed CPython must carry its licence. Currently missing — see the notices gap. |

## Frontend

363 installed packages scanned. Distribution: 318 MIT, 11 Apache-2.0, 10 ISC,
6 MPL-2.0, 5 BlueOak-1.0.0, 3 `MIT OR Apache-2.0`, 2 MIT-0, 2 BSD-3-Clause,
2 BSD-2-Clause, 1 `Apache-2.0 AND MIT`, 1 `Apache-2.0 OR MIT`, 1 0BSD,
1 CC0-1.0. **No GPL, AGPL, LGPL, SSPL, BUSL, Commons Clause or source-available
licence, and no package with an unknown or missing licence field.**

| Dependency | Version | License | Used where | Distributed? | Community impact | Proprietary Pro impact | Required action |
|---|---|---|---|---|---|---|---|
| React, Radix UI, Tauri JS API, i18next, zustand, immer, lucide-react, clsx, tailwind-merge, react-markdown, remark-gfm, Tailwind, Vite (and the MIT/ISC/BSD bulk) | see `web/pnpm-lock.yaml` | MIT / ISC / BSD / Apache-2.0 | Shipped UI | Yes — bundled into `src/tavotto/web/`, `canvas.html`, `playground.html` | None | GREEN | Notice (MIT attribution travels with the bundle) |
| generative-loaders | 0.1.1 | MIT | Loading UI | Yes | None | GREEN | Notice |
| lightningcss (+ platform binaries) | 1.32.0 / 1.33.0 | **MPL-2.0** | CSS transform, pulled in by Tailwind v4 / Vite | **No** — build-time only; not part of the shipped bundle | None | **REVIEW**, but low: MPL-2.0 is file-level copyleft reaching only modified MPL files. Unmodified and not distributed. | Confirm it stays build-time only |
| axe-core, @axe-core/playwright | 4.13.0 | **MPL-2.0** | Accessibility assertions in Playwright E2E | **No** — devDependency, test-only | None | **REVIEW**, but low: test tooling, not distributed | Confirm it stays test-only |
| TypeScript | ~6.0.2 | Apache-2.0 | Build-time type checking | No | None | GREEN | — |
| Playwright | ^1.62.1 | Apache-2.0 | E2E tests | No | None | GREEN | — |

## Rust

| Dependency | Version | License | Used where | Distributed? | Community impact | Proprietary Pro impact | Required action |
|---|---|---|---|---|---|---|---|
| `tavotto-workerd` closure (22 crates: serde_json, sha2, and transitives) | see `workerd/Cargo.lock` | 16× `MIT OR Apache-2.0`, 2× MIT, 1× `Unlicense OR MIT`, 1× `MIT/Apache-2.0`, 1× `(MIT OR Apache-2.0) AND Unicode-3.0`; the 22nd is `tavotto-workerd` itself (AGPL-3.0-only, Tavotto's own code) | Rust supervisor | Yes — shipped binary | None | GREEN — entire third-party closure permissive | Notice |
| `tavotto-desktop` closure (526 crates: Tauri 2 + transitives) | see `src-tauri/Cargo.lock` | 243× `MIT OR Apache-2.0`, 116× MIT, 56× `Apache-2.0 OR MIT`, 19× `MIT/Apache-2.0`, 18× `Zlib OR Apache-2.0 OR MIT`, 18× Unicode-3.0, 9× `Unlicense OR MIT`, plus ISC / BSD-3-Clause / LLVM-exception variants | Desktop shell | Yes — shipped binary | None | GREEN | Notice |
| `cssparser`, `cssparser-macros`, `dtoa-short`, `option-ext`, `selectors` | 0.36.0 / 0.6.1 / 0.3.5 / 0.2.0 / 0.36.1 | **MPL-2.0** (5 crates, within the desktop shell closure) | Transitive under Tauri | **Yes** — statically linked into the desktop binary | None | **REVIEW** — see the note below. | Notice + source-location disclosure. **Requires legal review before proprietary distribution.** |
| `webpki-root-certs` | 1.0.9 | CDLA-Permissive-2.0 | Transitive | Yes | None | GREEN — permissive data licence | Notice |
| ICU crates (`icu_*`, `zerovec`, `yoke`, `tinystr`, `litemap`, `writeable`, `potential_utf`, `zerotrie`, `zerofrom`) | 2.3.x / 0.x | Unicode-3.0 | Transitive | Yes | None | GREEN — Unicode License v3 is permissive | Notice |

### The MPL-2.0 crates, facts separated from conclusions

An earlier draft of this audit summarised these as "statically linked, no
notice, current obligation gap", which blurred an observation into a legal
conclusion. Separated:

**Verified facts**

| Fact | Evidence |
|---|---|
| 5 MPL-2.0 crates in the desktop shell closure | `cargo metadata` on `src-tauri/Cargo.toml` |
| All resolved from crates.io, **unmodified** | every one has `source = "registry+https://github.com/rust-lang/crates.io-index"`; there is no `[patch]` section, no `path`/`git` override, no `vendor/` directory and no `.cargo/config` override in the repository |
| Upstream source is publicly available | `servo/rust-cssparser`, `servo/stylo`, `upsuper/dtoa-short`, `soc/option-ext` |
| Statically linked into the shipped desktop binary | Rust default linkage |
| The desktop artefact ships no notices | `packaging/tavotto.spec` and `src-tauri/tauri.conf.json` — see [IP_PROVENANCE.md](IP_PROVENANCE.md#notices-in-distributed-artefacts) |

**What the licence text provides for.** MPL-2.0 is *file-level* copyleft: its
obligations attach to the MPL-licensed files themselves, and §3.3 expressly
contemplates distributing them as part of a "Larger Work" under other terms —
including proprietary ones — provided the MPL files remain under the MPL. For
distribution in Executable Form, §3.2 requires that recipients be **informed how
to obtain the Source Code Form**.

**What this means here, stated carefully.** Because the crates are unmodified
and their source is publicly available, the shortfall appears to be a **missing
notice and source-location disclosure**, not an inability to distribute. Static
linking of unmodified MPL-2.0 code is **not**, on this evidence, a
closed-source blocker, and this audit does not classify it as one.

That is an engineering reading of the licence text, not a legal opinion, and it
does not excuse the missing notice — which is an obligation of the **current
AGPL distribution** and should be fixed regardless of whether any commercial
edition is ever built.

## Browser playground

`packaging/playground-runtime.json` pins Pyodide 314.0.5 (Python 3.14.2) plus
matplotlib, numpy, pandas, scipy and pillow, loaded from
`https://cdn.jsdelivr.net/pyodide/v314.0.5/full/` at runtime.

| Dependency | Version | License | Used where | Distributed? | Community impact | Proprietary Pro impact | Required action |
|---|---|---|---|---|---|---|---|
| Pyodide | 314.0.5 | MPL-2.0 | `/try` browser playground | **Not redistributed** — fetched from a public CDN by the user's browser | None | **REVIEW** — obligations differ substantially between "loaded from a third-party CDN" and "bundled and shipped". Currently the former. | If Pro ever self-hosts or bundles Pyodide, re-audit before shipping |
| Scientific stack inside Pyodide | see the lock | BSD / MIT / PSF as above | Playground | Not redistributed | None | GREEN | — |

## Development-only

Not distributed, listed so their copyleft does not surprise a future reader:
`certifi` (MPL-2.0), `docutils` (classifiers list Public Domain, BSD **and
GPL**) — both arrive via `twine` in the `[dev]` extra and never ship. `ruff`,
`pytest`, `build`, `oxlint`, `vitest`, `jsdom` are MIT.

## Summary

Re-measured at baseline `ff732ea`:

| Class | Count | Items |
|---|---|---|
| **BLOCKER** | **1** | **PyMuPDF** |
| **REVIEW** | **4** | 5 MPL-2.0 Rust crates (shipped, unmodified — notice/source disclosure, not a copyleft blocker); Pyodide (CDN-loaded, not redistributed); lightningcss (build-time only); axe-core (test-only) |
| **GREEN** | everything else | The entire Python scientific stack, CPython, all 363 frontend packages except the MPL items, and both Rust closures (22 + 526 crates) |
| **UNKNOWN** | **0** | Every scanned component reported a licence |

Closure sizes re-measured: `workerd` **22** crates (0 MPL), `src-tauri` **526**
crates (5 MPL), frontend **363** packages (318 MIT / 11 Apache-2.0 / 10 ISC /
6 MPL / 5 BlueOak / …). Identical to the previous baseline, as expected from a
range that touched no lockfile.

**The headline: exactly one true blocker, and it is PyMuPDF.** Everything else
is permissive, build-time-only, or file-level copyleft that MPL-2.0 explicitly
contemplates being combined with proprietary code.

Two obligations apply **today**, to the AGPL product, independent of any
commercial plan:

1. The MIT/BSD/PSF/Apache components require their notices to travel with binary
   distributions, and the desktop app currently ships none. See
   [IP_PROVENANCE.md](IP_PROVENANCE.md#notices-in-distributed-artefacts).
2. The 5 MPL-2.0 Rust crates are statically linked into the shipped desktop
   binary; MPL-2.0 §3.2 requires recipients to be told how to obtain their
   Source Code Form, which the same notices work should cover.

## Re-running this audit

It is a snapshot of pinned versions and goes stale on every dependency bump.

```sh
# Python — the bundled closure's own metadata
python3 -c "import json,urllib.request;\
 print(json.load(urllib.request.urlopen('https://pypi.org/pypi/pymupdf/json'))['info']['license'])"

# Rust — full transitive closure, both crates
cargo metadata --format-version 1 --manifest-path src-tauri/Cargo.toml \
  | python3 -c "import json,sys,collections; d=json.load(sys.stdin); \
    print(collections.Counter(p.get('license') or 'UNKNOWN' for p in d['packages']))"

# Frontend — installed closure
node -e "0" && find web/node_modules -name package.json -maxdepth 3 | head
```

The check that matters most is cheap: **has anything new appeared with a GPL,
AGPL, LGPL, SSPL, BUSL, Commons Clause or unknown licence?** Today the answer
is no, PyMuPDF excepted — and PyMuPDF is there deliberately.
