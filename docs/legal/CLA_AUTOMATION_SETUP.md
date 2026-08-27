# CLA automation — design, security model, and what is still manual

> This document is an engineering/licensing artefact, not legal advice.

## Current status

**CLA enforcement is scaffolded and wired into CI, but not in force.** The check
runs on every pull request today and correctly qualifies the rights holder and
the two exempt bots. It cannot yet *accept* a signature from anybody else,
because the agreements are still `1.0-draft` with an unset counterparty.

Do not describe this as "CLA legal onboarding is production-ready". It is not.
Policy infrastructure being ready and a legal signature service being activated
are two different things, and only the first is done.

| Piece | State |
|---|---|
| Agreement texts (Individual, Corporate) | Written, versioned, hashed |
| Versioning + hash-binding policy | In force — CI red on drift |
| Repository-side qualification check | **Live** — feeds `CI fast gate` |
| Security model of that check | Complete; no secrets, no PR code executed |
| Signature source of truth | **Defined: the provider.** The repository stores no signer data. |
| Rights holder (`RIGHTS_HOLDER_CONFIGURATION_REQUIRED`) | **Unresolved — blocks activation** |
| Signature provider | Not configured (`provider.configured: false`) |

## Why it was built this way

The obvious approach — install a CLA bot and let it own the whole problem — did
not survive contact with the candidates. Facts below were checked via the GitHub
API on 2026-08-27; they are observations, not endorsements, and they go stale.

| Option | Observed facts | Verdict |
|---|---|---|
| **CLA Assistant Lite** (`contributor-assistant/github-action`) | `archived: true` (last push 2026-03-23). README: *"This repository is no longer actively maintained. I no longer have the bandwidth to maintain this project. The repository has been archived and is now read-only."* Requires `pull_request_target` with write permissions. | **Rejected.** An archived action holding write permissions on a privileged trigger will never receive a security fix. |
| **`iainmcgin/cla-github-action`** (fork of the above) | Documented as maintained for internal use only, explicitly not a general-purpose community successor, no support or issue triage. | **Rejected.** |
| **EasyCLA** (Linux Foundation) | Designed around LF-hosted projects and LF membership. | **Not applicable.** |
| **CLA Assistant** (`cla-assistant/cla-assistant`, by SAP) | Repository `archived: false`, `disabled: false`. **Last commit 2023-10-16; last release `v2.13.1` on 2023-08-15; last push 2024-06-06; 242 open issues.** Hosted service `https://cla-assistant.io` returned **HTTP 200** on 2026-08-27. Self-hosting via Docker is documented. Signatures stored in Azure Cosmos DB in Europe (per its README, since 2021-08-27); CSV export available. | **The most plausible provider, if one is adopted** — but see the note below. |
| **Writing our own signing flow** ("comment `I agree`, then grep the comments") | — | **Rejected outright.** Contract formation is not something to prototype. |

**On CLA Assistant's status, precisely.** The hosted service is *currently
available and operational*. The repository is *not archived*. Those are the two
things that can be verified. What cannot be claimed from this evidence is that
the project is actively maintained or a safe long-term bet: there have been no
commits for roughly three years and no release since 2023. An earlier draft of
this document described it as "actively maintained" — that was not supported by
the evidence and has been corrected.

The practical consequence is that **choosing a provider is a decision to be made
when one is actually needed**, against the facts of that day, not a choice
locked in now. The architecture below is deliberately provider-agnostic for
exactly this reason.

## Architecture: one authority for signatures

Responsibilities are split along the line where each side is competent:

```
Signature collection + record  →  the provider  (AUTHORITATIVE)
                                  contract formation, identity, consent UX,
                                  and the database of who signed what

Agreement identity             →  this repository
                                  CLA text, version, SHA-256, policy,
                                  explicit exemptions, provider configuration

Qualification decision         →  scripts/ci/cla_gate.py
                                  enumerate every human in the PR, apply
                                  exemptions, and ask the provider's own
                                  check for the signature verdict
```

**The repository stores no signature data.** An earlier draft kept a
hand-maintained `docs/legal/cla-signatures.json` listing signers. That was
removed before this ever ran, because it created **two sources of truth for the
same legal fact** — the provider's database and a file in the repo — with
nothing keeping them in step and no principled way to resolve a disagreement. A
repository ledger is only defensible if it is derived automatically and
verifiably from the provider and can detect its own staleness. Absent that, one
authority beats two.

The gate therefore reads the provider's own check-run conclusion for the pull
request head, and treats it as the answer to "did these people sign?". The
repository answers only "sign *what*, exactly?" — which text, which version,
which hash.

## Security model

The check is the `cla-check` job in `.github/workflows/ci.yml`. Six properties,
each asserted by `tests/test_legal_contribution_policy.py` so that removing one
turns CI red rather than silently widening the attack surface.

### 1. It does not use `pull_request_target`

Most CLA automation needs `pull_request_target` because it wants to post
comments and set statuses on fork PRs, which requires a write token. That
trigger runs with the *base* repository's permissions and secrets while a fork
controls the branch contents — every protection then rests on the workflow never
executing PR-supplied code.

This check does not need write access, so it uses plain `pull_request` and
sidesteps the entire class. On a fork PR the token is read-only and no secrets
are exposed. The signing UX belongs to the provider, which runs as its own app.

### 2. It never checks out or executes PR code

The `cla-check` job contains **no `actions/checkout` step at all**. Everything
the decision depends on — the gate script, the policy (including the exemption
list) and both agreement texts — is fetched from the **default branch** via
`gh api`, the same pattern `ci.yml` already uses to obtain a trusted
`aggregate_gate.py`.

The reasoning is the same as for the gates: the tree under review must not
supply the logic that judges it. Here it is sharper still, because a PR that
could supply its own policy could add its own author to the exemption list and
pass its own check.

The gate script runs under `python3 -I` (isolated mode: no script-directory
`sys.path` entry, `PYTHONPATH` ignored), so a shadowing module cannot be
smuggled in either.

### 3. Least privilege, and no secrets

```yaml
permissions:
  contents: read        # read policy + agreement texts from the default branch
  pull-requests: read   # read the PR's commit list
```

No `issues: write`, no `pull-requests: write`, no `contents: write`. The job
**requires no secrets whatsoever** — nothing to configure, nothing to rotate,
nothing to leak. If a provider is added later and needs a token, record its
purpose, scope, setup and rotation consequence here; never commit a value.

### 4. Third-party actions are pinned to immutable SHAs

The job currently uses **no third-party actions at all** — only `gh`, which is
preinstalled on GitHub runners. If one is ever added it must be pinned to a full
commit SHA (`uses: owner/repo@<40-hex> # vN`), never `@main`/`@v1`/`@v2`. A tag
is a moving pointer and can be repointed at new code. The test suite asserts
this for the CLA workflow.

### 5. It qualifies every human in the PR, not just the opener

Checking only `pull_request.user.login` misses the common cases: a PR carrying
someone else's commits, or `Co-authored-by` trailers. The check collects the PR
author, every commit author, and every co-author trailer, then requires each to
be signed or explicitly exempt.

Where a GitHub account cannot be resolved — a co-author whose email is not a
`@users.noreply.github.com` address — the check **fails rather than guessing or
ignoring**. A human then resolves it.

### 6. Exemptions are explicit, and there is no "bots are fine" rule

Exemptions live in `.github/cla-policy.json` and are matched by exact login.
Each entry must carry a `kind` (`rights_holder` or `bot`) and a written
`reason`; the gate refuses to start if any is missing. There is deliberately no
"if the login ends in `[bot]`, trust it" branch — `dependabot[bot]` passes
because it is named in the file, not because of its suffix. An exemption nobody
can justify is one nobody will dare delete later.

Currently exempt: `erwanjun` (rights holder), `dependabot[bot]`,
`github-actions[bot]`.

## How it fits the existing CI

Tavotto's ruleset depends on exactly three required contexts — `CI fast gate`,
`CI integration gate`, `CodeQL gate` — with the decision converged in
`scripts/ci/aggregate_gate.py`. **No fourth required context was added.**
`cla-check` is an ordinary job inside the fast gate's `needs` closure and its
`--required` set, so its failure surfaces through `CI fast gate`.

One consequence is easy to get wrong and is worth stating plainly:

> `aggregate_gate.py --mode fast` treats **`skipped` as failure**. So
> `cla-check` must *run and succeed* on `merge_group`, not be skipped there.

If the job were written `if: github.event_name == 'pull_request'`, it would be
skipped on every queue candidate and `CI fast gate` would be permanently red —
nothing could merge. Instead the job runs on both events and the script returns
`not_applicable` (exit 0) on `merge_group`, because qualification already
happened on the pull request and a queue candidate has no PR context to inspect.

CLA qualification therefore happens **before** entry to the merge queue, and is
not re-litigated inside it. `tests/test_legal_contribution_policy.py` pins this
shape directly.

## Manual steps remaining

In order. Step 1 blocks everything else.

### 1. Resolve `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` — **required, and not an engineering task**

**Definition.** Before the CLA is activated for real signatures, the project
must identify the legal person or entity that currently owns, or is authorised
to receive, the relevant Tavotto rights — and record its name, contact address
and choice of governing law in the agreement.

**This does not mean a company has to be formed.** A natural person can hold
copyright, grant and receive licences, and be a party to a contract. An
individual rights holder is a fully supported configuration here, and given that
the audit finds a single individual behind essentially the whole history, it is
the most obvious one.

What is missing is not a corporation. It is a **decision plus the details that
go with it**: which legal person is "Us", how they are reached, and under which
law the agreement is read. The repository records none of these — `README.md`
says only "Tavotto™ is a trademark of the Tavotto project", and `pyproject.toml`
names `erwanjun` as author, neither of which identifies a contracting party.
Note that a GitHub organisation is *not* itself a legal person, so naming the
org would not resolve this either. Nothing was invented to fill the gap.

The realistic options are the current individual rights holder in their own
name, or an entity formed or nominated for the purpose. Both are ordinary; the
choice has tax, liability and jurisdiction consequences that belong with
counsel, and the repository must not make it for the owner.

Once decided, fill in:

- the counterparty in `docs/legal/CLA_INDIVIDUAL.md` and
  `docs/legal/CLA_CORPORATE.md` (the "Us" signature block, and the opening line);
- the governing law in Section 6.1 of both;
- a contact address for corporate signatures;
- then drop the `-draft` suffix, bump to `1.0` in both documents and in
  `.github/cla-policy.json`, add a row to the version history table in
  `docs/legal/CLA_VERSIONING.md`, and run:

  ```sh
  python3 scripts/ci/cla_gate.py --refresh-hashes
  .venv/bin/python -m pytest tests/test_legal_contribution_policy.py
  ```

Until this is done the gate correctly refuses to honour *any* signature: a
draft version is unsignable by design.

### 2. Install a signature provider — only when an external contribution is actually expected

Not needed while contributions come solely from the rights holder. Step 1 must
be done first — a provider cannot be marked configured while any agreement is
still `-draft`, and the gate enforces that structurally.

When the time comes:

1. **Re-check the provider's status on that day.** The facts in the table above
   will be stale. Confirm the service is operational and decide whether its
   maintenance position is acceptable; self-hosting is an option.
2. Install the provider's GitHub App on `Tavotto/Tavotto` and point it at
   `docs/legal/CLA_INDIVIDUAL.md` as the agreement text.
3. Check where it stores signatures and whether that is acceptable. CLA
   Assistant's README states Azure Cosmos DB in Europe. If not acceptable,
   self-host, or collect countersigned documents and use a manual check instead.
4. **Wire it into the gate — four edits, all in this repository:**
   - `.github/cla-policy.json`: set `provider.configured: true`, `provider.name`,
     and `provider.check_name` to the **exact** check-run name the provider
     publishes;
   - `.github/workflows/ci.yml`, `cla-check` job: add `checks: read` to
     `permissions`, add a step fetching
     `gh api repos/$REPO/commits/<pr head sha>/check-runs`, and pass it as
     `--provider-checks-json`;
   - `tests/test_legal_contribution_policy.py`: update the permissions assertion
     to the new expected set (it pins the exact permission map on purpose);
   - run `python3 scripts/ci/cla_gate.py --refresh-hashes` if the agreement text
     changed at the same time.

The gate reads only the provider's check-run conclusion, so providers are
swappable: replacing one is a config change plus a check name, not a redesign.

### 3. Corporate signatures stay manual — deliberately

No CI check can establish that a GitHub username is authorised to bind a
company. That evidence lives outside GitHub entirely. Corporate agreements are
reviewed by a human, and the covered accounts are then registered with the
provider so its check reflects them. See
[CLA_CORPORATE.md](CLA_CORPORATE.md#how-to-sign).

## Verifying the check locally

```sh
# Rights holder → exempt, exit 0
python3 scripts/ci/cla_gate.py --event pull_request --pr-author erwanjun \
  --commits-json <(echo '[{"sha":"a","commit":{"author":{"name":"e","email":"1259959884@qq.com"},"message":"x"},"author":{"login":"erwanjun"}}]')

# Merge queue → not_applicable, exit 0 (must never be skipped)
python3 scripts/ci/cla_gate.py --event merge_group

# Non-exempt contributor with no provider configured → exit 1, with an
# explanation naming the reason and where to go next (never a silent pass)
python3 scripts/ci/cla_gate.py --event pull_request --pr-author outsider \
  --commits-json <(echo '[{"sha":"b","commit":{"author":{"name":"o","email":"o@example.com"},"message":"x"},"author":{"login":"outsider"}}]')

.venv/bin/python -m pytest tests/test_legal_contribution_policy.py
```
