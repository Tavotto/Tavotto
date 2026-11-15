# CLA versioning

> This document is an engineering/licensing artefact, not legal advice.

A signature is consent to **a specific text**. If the text can change underneath
a signature, the record of that signature stops meaning anything — and nothing in
the repository would go red when it happened. This document defines how the two
are kept bound together.

## The invariant

> A signature is bound to the exact bytes of the agreement that was signed, and
> those bytes can never change without a new version.

Concretely: `.github/cla-policy.json` records, for each agreement, the version
string and the SHA-256 of the document. `tests/test_legal_contribution_policy.py`
recomputes both from the files on disk and fails if either has drifted. Editing
a word of `CLA_INDIVIDUAL.md` without updating the policy turns CI red.

## Identity of an agreement

| Field | Where it lives | Example |
|---|---|---|
| Version | `CLA_VERSION:` line in the document, mirrored in `.github/cla-policy.json` | `1.0` |
| Hash | `.github/cla-policy.json` only — never inside the document, which would be circular | `sha256:…` |

The hash is SHA-256 over the raw bytes of the file:

```sh
python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" docs/legal/CLA_INDIVIDUAL.md
# or: shasum -a 256 docs/legal/CLA_INDIVIDUAL.md
```

To regenerate every recorded hash after an intentional change:

```sh
python3 scripts/ci/cla_gate.py --refresh-hashes
```

That command **only rewrites the hashes**. It does not bump the version — that
is a decision, and the section below is about who makes it.

## What requires a new version

A **material change** requires a new version string. A material change is any
edit that could alter what a signer is agreeing to:

- anything in the operative `# Agreement` section — definitions, Sections 1–6,
  the signature blocks;
- the identity of the counterparty ("We"/"Us");
- the governing law;
- the outbound-licence option;
- Schedule A's effect on scope in the Corporate CLA.

A **non-material change** may keep the version, but still needs its hash
refreshed and the change recorded below:

- typography, link fixes, Markdown formatting;
- the explanatory sections *outside* `# Agreement` — provenance tables,
  "How to sign", configuration notes.

When the distinction is unclear, treat it as material. The cost of an
unnecessary version bump is asking people to sign again; the cost of a missed one
is a signature that does not cover the text it is recorded against.

## Rules that must not be broken

1. **Existing signatures never migrate.** A ledger entry recorded against
   `1.0` stays against `1.0` forever. Nothing may rewrite old entries to point
   at a new version — not a script, not a bulk edit.
2. **A new version means new consent.** Contributors who signed an earlier
   version have not signed the new one. Whether their earlier signature still
   suffices for a given contribution is a legal judgement about the two texts,
   not something CI decides.
3. **Version strings are append-only.** `1.0` is never reused with different
   bytes. Superseded versions stay in the table below.
4. **`-draft` is not signable.** A version carrying the `-draft` suffix is one
   where `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` is unresolved and the text has
   not been formally activated for signatures. This is about missing details —
   counterparty, contact, governing law — not about any particular legal form
   being required. The gate enforces it structurally: it refuses to let a
   provider be marked as configured while any agreement is still a draft.
   *(No agreement is currently a draft; the rule stands for future revisions.)*

## Where signature records live

**Not in this repository.** The authoritative record of who has signed what is
held by the signature provider (see
[CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md)). The repository stores the
agreement text, its version and hash, the policy, and the explicit exemptions —
and nothing about individual signers.

An earlier draft of this infrastructure kept a hand-maintained
`docs/legal/cla-signatures.json`. That was removed, because it created **two
sources of truth for the same legal fact**: the provider's database and a file
in the repo. Nothing kept them in step, and once they disagreed there would be
no principled way to say which one governed. A ledger is only safe if it is
derived automatically and verifiably from the provider — and can detect its own
staleness. Absent that, one authority is better than two.

What the version and hash in `.github/cla-policy.json` are *for*, then, is to
pin **which text** the provider is collecting signatures against. The provider
answers "did this person sign?"; the repository answers "sign what, exactly?".

### Countersigned paper agreements signed before the service existed

Before the signing service was enabled, the only way to sign was the one the
agreement itself names: the contributor signed the agreement text, sent it
privately to <contact@tavotto.com>, and the rights holder countersigned. **The
executed copy (both signatures) is the authoritative record** for that
contributor, held privately by the rights holder. It carries the contributor's
postal address, so it never enters this repository.

These records stay valid after the service is enabled
([ADR 0183](../adr/0183-cla-paper-signatures-as-exemptions.md)); the contributor
does not sign again. `.github/cla-policy.json` lists each one under `exemptions`
with `kind: countersigned_paper`, as an **index** only: login, immutable GitHub
account id, agreement, version, the two signing dates and a one-line note on
where the record is kept — no name, address or email (the gate rejects any extra
field). The gate qualifies a contributor from it only when the version still
equals the current agreement version and every account id that GitHub itself
reports for that login in the pull request (the PR author's id, the `author.id`
of their commits) equals the recorded `github_id`. The service treats the same
account id as exempt, so a pull request or merge group that mixes this
contributor with online signers is judged on the online signers alone.

**One authority per person.** A contributor listed as `countersigned_paper` is
judged by the paper record only; everyone else is judged by the service only.
Once the service is enabled, new contributors sign online and no new
`countersigned_paper` entries are added. When the agreement version changes,
the entry stops qualifying; the contributor signs the new version online and
the maintainer then removes the entry. The former `offline_signatures` section
is retired and rejected if present.

Everyone else is still blocked **with an explanation** until they sign: an
unreachable service is not consent, and silence is not a signature.

## Rights transfer, if the holder ever changes

If the CLA is accepted by an individual rights holder and Tavotto's IP is later
moved to a company — on incorporation, or as part of financing — the rights
received under the CLA do not travel automatically. A future transfer would need
to cover, at minimum:

- copyright in Tavotto's own code;
- the trademark rights described in [`TRADEMARKS.md`](../../TRADEMARKS.md);
- the right to grant commercial licences;
- **the contractual rights received under signed CLAs** — these are contracts
  with each contributor, and Section 6.3 of both agreements requires an assignee
  to agree in writing to abide by the agreement's rights and obligations;
- any applicable patent rights.

This repository deliberately does **not** contain an IP assignment agreement or
attempt to draft one. This is corporate-formation and financing legal work, and
it requires legal review at the time. It is recorded here only so that the CLA's
existence is not forgotten during such a transaction.

## Version history

| Version | Status | Date | Notes |
|---|---|---|---|
| `1.0-draft` | Superseded by `1.0` | 2026-08-27 | Initial text. Harmony 1.0, CLA form, Option Five. Held at draft while `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` was unresolved. **No signature was ever collected against it**, so nothing had to migrate. |
| **`1.0`** | **Current — signable** | 2026-08-28 | `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` resolved: counterparty is **Jiaqi Wan**, a natural person; governing law is the **Hong Kong SAR**; correspondence to `support@tavotto.com`. Counterparty and governing law are material fields, so this is a new version rather than an edit to the draft. Operative text otherwise byte-identical to `1.0-draft`. **This is the first published version** — `1.0-draft` never left the branch it was written on, and no signature was ever collected against it. |

A provider may now be marked as configured (the structural bar on draft versions
no longer applies), but none is configured yet — see
[CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md).
