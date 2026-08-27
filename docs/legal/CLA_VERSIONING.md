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
| Version | `CLA_VERSION:` line in the document, mirrored in `.github/cla-policy.json` | `1.0-draft` |
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
4. **`-draft` is not signable.** A version carrying the `-draft` suffix has
   unresolved `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` blanks. The gate refuses to
   treat any signature recorded against a draft version as valid.

## The signature ledger

`docs/legal/cla-signatures.json` is the repository's record of who has signed
what. It is **a record, not the signing mechanism** — the consent itself is
collected by a signature provider or by a countersigned document reviewed by a
human (see [CLA_AUTOMATION_SETUP.md](CLA_AUTOMATION_SETUP.md)). Nothing is
"signed" by virtue of appearing in this file; entries are written *because* a
signature was already obtained.

Each entry records:

| Field | Meaning |
|---|---|
| `github_login` | The account whose contributions the signature covers |
| `agreement` | `individual` or `corporate` |
| `agreement_version` | The version signed — must exist in the table below |
| `agreement_sha256` | The hash of the exact text signed |
| `signed_at` | ISO-8601 UTC timestamp of the signature |
| `recorded_by` | The maintainer who wrote the entry |
| `evidence` | Where the signature itself can be found (provider record id, document reference) |
| `entity` | Corporate only — the Legal Entity bound |

The gate reads this file **from the default branch**, never from the pull
request under review. A PR cannot add its own author to the ledger and pass its
own check.

## Version history

| Version | Status | Date | Notes |
|---|---|---|---|
| `1.0-draft` | **Not signable** | 2026-08-27 | Initial text. Harmony 1.0, CLA form, Option Five. Blocked on `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` (counterparty identity, governing law). No signatures may be recorded against it. |

When the rights holder is configured, the intended first signable version is
`1.0` — a new row here, a new hash in the policy, and the `-draft` suffix
removed from both documents.
