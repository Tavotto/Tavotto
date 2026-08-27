# docs/legal/

> These documents are engineering/licensing artefacts, not legal advice. Final
> commercial licensing and trademark decisions should be reviewed by qualified
> counsel.

## Four different questions

They get confused with each other constantly. They are not the same question and
they do not have the same answer.

```
LICENSE              →  what USERS may do with Tavotto
                        AGPL-3.0-only. Unchanged.

CLA                  →  what CONTRIBUTORS grant to Tavotto
                        You keep your copyright. Tavotto gets a broad licence.

TRADEMARKS           →  who may use the NAME and LOGO
                        Separate from copyright. The AGPL does not cover it.

Dependency audit     →  what THIRD-PARTY code permits
                        Not affected by the CLA. Not Tavotto's to relicense.
```

The mistake worth naming: **"we own the code, so we can license it however we
like."** That holds for layer 1 only. A contribution someone else wrote is
layer 2 (the CLA governs it), and Flask, React and PyMuPDF are layer 3 (nothing
Tavotto signs changes their terms).

## The documents

| File | Answers |
|---|---|
| [LICENSING.md](LICENSING.md) | How the three layers fit together. **Start here.** |
| [CLA_INDIVIDUAL.md](CLA_INDIVIDUAL.md) | The individual contributor agreement. Harmony 1.0, CLA form, Option Five. |
| [CLA_CORPORATE.md](CLA_CORPORATE.md) | The entity agreement, for employer-owned work. |
| [CLA_VERSIONING.md](CLA_VERSIONING.md) | How a signature stays bound to the exact text signed. |
| [CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md) | How the CI check works, its security model, and the manual steps left. |
| [IP_PROVENANCE.md](IP_PROVENANCE.md) | Who actually wrote what is in this repository. The audit. |
| [COMMERCIALIZATION_DEPENDENCY_AUDIT.md](COMMERCIALIZATION_DEPENDENCY_AUDIT.md) | Every distributed third-party component, classified GREEN / REVIEW / BLOCKER. |
| [COMMERCIAL_EDITION_RIGHTS_POLICY.md](COMMERCIAL_EDITION_RIGHTS_POLICY.md) | Rules a future separately licensed edition must obey, and the readiness verdict. |
| [TRADEMARK_REGISTRATION_READINESS.md](TRADEMARK_REGISTRATION_READINESS.md) | Evidence that exists if a filing is ever pursued. |

Also: [`LICENSE`](../../LICENSE), [`TRADEMARKS.md`](../../TRADEMARKS.md) and
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) at the repository root.

## Current state, in one table

| | |
|---|---|
| Community licence | **AGPL-3.0-only**, unchanged, and staying that way |
| Rights baseline | 744/745 commits from one rights holder; **no external human contribution**; no third-party source copied in |
| CLA model | Contributor **keeps copyright**; Tavotto gets a sublicensable licence + the right to offer separately licensed editions |
| CLA status | **`1.0-draft` — not signable.** Blocked on the legal rights holder being configured |
| CI enforcement | **Live.** `cla-check` feeds the existing `CI fast gate`; no fourth required context added |
| Trademark | **Tavotto™**, unregistered. ® is not used and CI fails if it appears |
| Proprietary-edition readiness | **`READY_WITH_BLOCKERS`** — one real dependency blocker (**PyMuPDF**) plus governance items |

## The two things a maintainer should not get wrong

**A DCO is not a CLA.** A Developer Certificate of Origin certifies that the
signer had the right to submit the code under the project's existing licence.
It grants no copyright, conveys no relicensing rights, and cannot support a
separately licensed edition. Tavotto has never operated a DCO. The single
`Signed-off-by` trailer in the history is Dependabot's own convention, not
evidence of one.

**The CLA does not launder dependencies.** However complete Tavotto's rights
over its own code become, PyMuPDF is still dual-licensed AGPL/commercial and
React is still MIT. A proprietary build must pass a fresh third-party audit
regardless.
