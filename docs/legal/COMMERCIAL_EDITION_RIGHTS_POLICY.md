# Commercial edition rights policy

> This document is an engineering/licensing artefact, not legal advice. Final
> commercial licensing decisions should be reviewed by qualified counsel.

## Scope

This document defines the rights boundary that any future separately licensed
edition of Tavotto would have to respect. It is written **before** such an
edition exists, which is the only time it can be written honestly.

**No proprietary edition exists, and nothing in this repository creates one.**
There is no `tavotto-pro/`, no closed-source branch, no licence-key system, no
feature gating, no subscription code, and no cloud authentication. This is
option value, not a product.

## Readiness verdict

> **If a proprietary Tavotto Pro were created tomorrow, is the current tree
> ready at the copyright and third-party licence layers?**

## `READY_WITH_BLOCKERS`

The copyright layer is in unusually good shape. The third-party layer is not
ready, and one item on it is a genuine blocker.

### Why not `NOT_READY`

- **The rights baseline is clean, and stayed clean.** 418 of the 419 commits
  reachable from baseline `ff732ea` come from a single rights holder; one is a
  Dependabot version bump; there is no external human contributor, no
  third-party source copied into the tree, no vendored code and no bundled
  fonts. The 19 commits added since the previous audit introduced no external
  contribution, no new dependency and no third-party material.
- **No relicensing-blocked contribution has been merged.** There is nothing to
  claw back, rewrite or exclude — the usual reason projects cannot dual-license
  simply does not apply here.
- **The dependency position is better than typical.** Of ~900 distributed
  third-party components across Python, JavaScript and Rust (re-measured: 22 +
  526 Rust crates, 363 frontend packages, plus the pinned Python closure),
  exactly one is a blocker. No GPL, LGPL, SSPL, BUSL, Commons Clause or
  source-available dependency exists anywhere in the closure. The MPL-2.0 items
  are a notice obligation, not a copyleft barrier — see the
  [dependency audit](COMMERCIALIZATION_DEPENDENCY_AUDIT.md#the-mpl-20-crates-facts-separated-from-conclusions).
- **The one blocker has a designed exit.** `pdfbackend/` is already a contract
  boundary with a single permitted `import pymupdf`, enforced as a repository
  invariant.

### Why not `READY`

| # | Blocker | Class | Owner |
|---|---|---|---|
| 1 | **PyMuPDF.** Dual-licensed AGPL-3.0 / Artifex commercial; taken under the AGPL arm. Continuing to distribute it under AGPL terms inside a proprietary product may create incompatible obligations. | Third-party licence | Requires an Artifex commercial licence, a replacement backend, or other appropriate authorisation. **Legal review required before proprietary distribution.** |
| 2 | **The rights holder is not yet configured.** The project has not identified the legal person or entity that owns, or is authorised to receive, the relevant Tavotto rights, nor recorded its contact details and governing law. **This does not require forming a company** — a natural person can be the counterparty and grant or receive a commercial licence. What is missing is the decision and its details. | Governance/legal | `RIGHTS_HOLDER_CONFIGURATION_REQUIRED`. See [CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md). |
| 3 | **The CLA is not yet activated for signatures.** Agreements are `1.0-draft` because `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` is unresolved and the text has not been formally activated; no signature provider is configured. Once the CI job lands (a follow-up PR, for bootstrap-ordering reasons) enforcement qualifies only the rights holder and two named bots, and blocks everyone else with an explanatory message. | Governance | Follows from #2. |
| 4 | **Distributed artefacts carry no notices.** The desktop app and the Codex plugin ship neither `LICENSE` nor third-party notices, and 5 unmodified MPL-2.0 Rust crates are statically linked into the desktop binary (MPL-2.0 §3.2 requires telling recipients how to obtain their source). This is an obligation of the **current AGPL distribution**, not only a future commercial one. | Packaging | **Tracked separately as [#182](https://github.com/Tavotto/Tavotto/issues/182)** — not fixed in the legal-governance change. See [IP_PROVENANCE.md](IP_PROVENANCE.md#notices-in-distributed-artefacts). |
| 5 | **Two AI-suggested edits unreviewed.** Copilot Autofix co-authored seven lines across two commits in one file. | Provenance | **NEEDS LEGAL REVIEW**; bounded and trivially reimplementable. |
| 6 | **Rights-holder encumbrance unverified.** Whether the rights holder's own work is subject to an employment or institutional agreement cannot be established from the repository. | Provenance | Only the rights holder can answer. |

Blockers 2 and 3 are governance: they cost a decision and the details that go
with it, not a corporate structure. Blocker 1 is the only one with real money or
engineering attached. Blocker 4 should be fixed regardless of whether any
commercial edition ever happens — it is an obligation of what is shipping today.

## Principles for a future proprietary edition

These are binding constraints on how such an edition may be assembled, not
aspirations.

### 1. Only include code Tavotto has the rights to relicense

A proprietary edition may include:

- **Tavotto-owned code** — copyright held by the rights holder; and
- **Contributions carrying sufficient commercial relicensing rights** — that is,
  contributions covered by a signed CLA at a non-draft version, recorded in
  the ledger with the version and hash signed.

Nothing else. In particular, a contribution merged under AGPL-3.0-only without a
CLA grant is **not** eligible, however small, however long ago, and however
convenient.

### 2. Every third-party dependency must independently pass a commercial audit

The CLA has no effect on third-party components. A dependency that is fine in
the AGPL edition is not thereby fine in a proprietary one — PyMuPDF is exactly
that case. Each must be re-examined against the terms of the proprietary
distribution, not inherited from the community build.

### 3. The AGPL community history stays AGPL

Everything released under AGPL-3.0-only remains available under AGPL-3.0-only.
A commercial edition is an *addition*, never a withdrawal. Published releases
are not relicensed, taken down, or retroactively restricted.

This is also a CLA obligation, not merely a promise: Section 2.3 binds Tavotto
to keep licensing each contribution under the licence in force on its submission
date.

### 4. A proprietary branch must never silently absorb a contribution lacking the required rights

The failure mode is a merge that nobody examines: an AGPL-only contribution
flows from community into proprietary because the branches are routinely synced.
The defence is that eligibility is a **recorded property of each contribution**,
established at merge time by the CLA gate — not a judgement someone makes months
later while resolving a conflict.

If a proprietary edition is ever built, this principle needs an enforcement
mechanism, and that mechanism should be built *before* the first sync, not
after.

### 5. `community-only` must be a supported outcome

Some contribution will eventually arrive that Tavotto wants but cannot
relicense: a contributor declines the CLA, or code arrives under a
copyleft-compatible-but-not-relicensable licence.

The system must allow marking it **`community-only`** — merged into the AGPL
edition and excluded from any separately licensed one unless and until the
necessary rights are obtained. It must never be forced into Pro, and it must
never be rejected merely because the label is inconvenient.

Under current policy every external PR requires a CLA, so this should be rare.
"Rare" is not "impossible", and a policy with no path for it will be quietly
violated the first time it happens.

### 6. A change of rights holder is itself a rights event

If the CLA is accepted by an individual and Tavotto's IP later moves to a
company, the rights received under signed CLAs must be transferred explicitly
along with copyright, trademarks, commercial licensing rights and any patent
rights — Section 6.3 of both agreements requires an assignee to agree in writing
to abide by them. A proprietary edition assembled *after* such a transfer but
*without* it having been done properly would rest on rights the new entity does
not hold. This repository does not draft that assignment; it requires legal
review at the time. See
[CLA_VERSIONING.md](CLA_VERSIONING.md#rights-transfer-if-the-holder-ever-changes).

### 7. No trademark rights travel with the code

An AGPL copyright licence is not a trademark licence. A proprietary edition and
its naming are governed by [`TRADEMARKS.md`](../../TRADEMARKS.md) independently.

## What would move this to `READY`

1. Resolve `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` — identify the legal person
   or entity (an individual is sufficient), record its contact details and the
   governing law; finalise the CLA at `1.0` and configure a provider.
2. Resolve PyMuPDF — Artifex commercial licence, or an alternative backend
   behind the existing `pdfbackend/` contract.
3. Ship licence and third-party notices in every distributed artefact,
   including MPL-2.0 source availability for the linked Rust crates.
4. Obtain legal review of the Copilot Autofix edits and of any rights-holder
   encumbrance.
5. Re-run [COMMERCIALIZATION_DEPENDENCY_AUDIT.md](COMMERCIALIZATION_DEPENDENCY_AUDIT.md)
   against the dependency set the proprietary edition actually ships.

Items 1 and 3 are unblocked today. Item 2 is the one with a real cost attached.
