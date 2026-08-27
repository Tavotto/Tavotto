# Licensing

> This document is an engineering/licensing inventory, not legal advice. Final
> commercial licensing and trademark decisions should be reviewed by qualified
> counsel.

Tavotto Community is released under **AGPL-3.0-only**. The full text is in
[`LICENSE`](../../LICENSE) at the root of the repository. That is the licence of
this repository, and this change does not alter it.

## What this means in practice

For almost everyone, nothing changes:

- **Using it, modifying it, deploying it inside your lab** — unrestricted. The
  AGPL does not govern private use.
- **The figures and PDFs you produce** — entirely yours. The licence does not
  reach the output.
- **Citing Tavotto in a paper** — nothing needs to be open-sourced.

The obligations apply to **distribution**. If you give a modified Tavotto to
other people, or run it as a service others reach over a network, the
corresponding source has to be made available to those users (AGPL sections 5
and 13).

---

# The three layers

The rest of this document exists because one question keeps getting answered
wrongly: *"we own the code, so we can license it however we like."* That is true
of exactly one of the three layers below, and confusing them is how projects
discover a licensing problem after shipping rather than before.

```
1. Tavotto-owned code        → AGPL to the community.
                               Relicensable by the rights holder.

2. Contributor-owned code    → Contributor keeps copyright.
                               Relicensable only within the CLA's grant.

3. Third-party dependencies  → Not Tavotto's to license, at all.
                               Each governed solely by its own terms.
```

## Layer 1 — Tavotto-owned code

Code whose copyright is held by the Tavotto rights holder.

Released to the community under **AGPL-3.0-only**. Being the copyright holder,
they are not bound by their own outbound licence and may additionally offer the
same code under other terms, including commercial or proprietary ones — provided
the rights chain is complete, which is what layer 2 is about.

Today this is essentially the whole repository: 744 of 745 commits come from a
single rights holder, with no external human contribution found. See
[IP_PROVENANCE.md](IP_PROVENANCE.md).

## Layer 2 — Contributor-owned contributions

When someone else contributes, **they own their contribution**. Tavotto does not
acquire it by merging it.

Without a further grant, a contribution accepted only under AGPL-3.0-only can
be redistributed by Tavotto only under AGPL-3.0-only. **Tavotto cannot
unilaterally relicense that contribution under proprietary terms** — the
decision belongs to whoever holds its copyright, not to the project.

That is a constraint, not a dead end. It can still be resolved later by
obtaining a retroactive CLA or a separate licence from the contributor, by
rewriting or replacing the contribution, or by excluding it from a separately
licensed edition. What changes is that future relicensing of that code becomes
**dependent on additional permission or on replacement**, rather than being
Tavotto's to decide.

The reason to have a CLA in place beforehand is that all of those remedies cost
more, and depend on a third party's cooperation, than simply asking at the time
of the contribution.

The [Contributor License Agreement](CLA_INDIVIDUAL.md) resolves this without
taking anyone's copyright:

- **The contributor keeps copyright** (CLA Section 2.1(a)) and retains every
  right they had before signing — including using and licensing their own code
  elsewhere.
- **Tavotto receives a perpetual, worldwide, non-exclusive, royalty-free,
  irrevocable, sublicensable licence** to reproduce, modify, display, perform
  and distribute the contribution as part of Tavotto (Section 2.1(b)), plus a
  matching patent licence (Section 2.2).
- **Tavotto may license it under other terms, including commercial or
  proprietary ones — and must also keep licensing it under the licence in force
  when it was submitted** (Section 2.3, Harmony Option Five). Concretely: a
  contribution submitted today can appear in a separately licensed edition, and
  Tavotto remains bound to keep offering that same contribution under
  AGPL-3.0-only in the community edition. The community edition cannot be
  quietly closed behind the contributor's back.

Only contributions carrying this grant can enter a future proprietary edition.
Anything else is community-only — see
[COMMERCIAL_EDITION_RIGHTS_POLICY.md](COMMERCIAL_EDITION_RIGHTS_POLICY.md).

**A DCO would not do this job.** A Developer Certificate of Origin certifies
*provenance* — that the signer has the right to submit the code under the
project's existing licence. It is not a copyright grant, conveys no
relicensing rights, and is not a substitute for a CLA. Tavotto has never
operated a DCO, and a modified DCO dressed up as one would be worse than
neither.

## Layer 3 — Third-party dependencies

**These do not become Tavotto's property because a contributor signed a CLA.**
The CLA governs contributions. It has no effect on Flask, matplotlib, React,
Tauri or PyMuPDF, each of which remains governed solely by its own licence.

The consequence is the one most easily missed:

> Even if every line of Tavotto's own code were freely dual-licensable, a
> proprietary build would still have to pass a complete third-party licence
> audit — because the dependencies were never Tavotto's to relicense.

The current audit is
[COMMERCIALIZATION_DEPENDENCY_AUDIT.md](COMMERCIALIZATION_DEPENDENCY_AUDIT.md).
Its headline finding:

> **PyMuPDF** is dual-licensed `Dual Licensed - GNU AFFERO GPL 3.0 or Artifex
> Commercial License`, and Tavotto takes it under the AGPL arm. That is entirely
> correct for an AGPL-3.0-only project and creates **no issue whatsoever for the
> community edition**. It is the one component that would block a proprietary
> edition on today's terms: continuing to distribute AGPL-obtained PyMuPDF in a
> proprietary product may create incompatible obligations. Before any
> proprietary distribution, Tavotto must confirm Artifex commercial licensing,
> replace the backend, or obtain other appropriate authorisation.

This is precisely why `src/tavotto/pdfbackend/pymupdf_backend.py` is the only
module in the repository permitted to `import pymupdf`. That boundary was
introduced for replaceability and is enforced as a repository invariant; it is
also a licensing control, and it should not be relaxed.

## Third-party components at a glance

| Component | License | Used for |
|---|---|---|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | AGPL-3.0 / Artifex commercial (dual) | PDF reading, rasterising, vector composition |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause | Local HTTP server |
| [matplotlib](https://matplotlib.org/) | matplotlib licence (PSF-based) | Rendering worker |
| [NumPy](https://numpy.org/) / [SciPy](https://scipy.org/) / [pandas](https://pandas.pydata.org/) / [seaborn](https://seaborn.pydata.org/) | BSD-3-Clause | Bundled scientific stack |
| [Pillow](https://python-pillow.org/) | MIT-CMU | Image handling |
| CPython | PSF License | Bundled interpreter (desktop) |
| [React](https://react.dev/) / [Vite](https://vite.dev/) / [Tailwind](https://tailwindcss.com/) / [Radix UI](https://www.radix-ui.com/) | MIT | Web interface |
| [Tauri](https://tauri.app/) | MIT / Apache-2.0 | Desktop shell |
| [Pyodide](https://pyodide.org/) | MPL-2.0 | Browser playground (loaded from CDN, not redistributed) |

Complete lists: `web/pnpm-lock.yaml`, `packaging/runtime-lock.json`,
`workerd/Cargo.lock`, `src-tauri/Cargo.lock`. Releases also publish an SPDX SBOM
of the wheel.

## If the rights holder ever changes

The CLA may be accepted by an individual rights holder — that is a fully
supported configuration, and forming a company is not a precondition. But if
Tavotto's IP is later moved to a company, on incorporation or as part of
financing, the rights do not travel automatically. Such a transfer would need to
cover copyright in Tavotto's own code, the trademark rights in
[`TRADEMARKS.md`](../../TRADEMARKS.md), the right to grant commercial licences,
**the contractual rights received under signed CLAs**, and any applicable patent
rights.

Section 6.3 of both agreements is directly relevant: an assignee must agree in
writing to abide by the agreement's rights and obligations. This repository does
not contain, and does not attempt to draft, an IP assignment agreement — that is
corporate-formation and financing legal work requiring review at the time. It is
noted here only so the CLA is not overlooked during such a transaction. See
[CLA_VERSIONING.md](CLA_VERSIONING.md#rights-transfer-if-the-holder-ever-changes).

## What is *not* promised

- No commercial or proprietary edition of Tavotto exists.
- Nothing here commits the rights holder to creating one.
- The CLA does not promise contributors payment, nor that any contribution will
  be merged (Section 2.5).

The CLA preserves an **option**. Whether it is ever exercised is a separate
decision.
