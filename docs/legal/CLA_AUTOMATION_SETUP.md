# CLA automation — design, security model, and what is still manual

> This document is an engineering/licensing artefact, not legal advice.

## Current status

**CLA enforcement is scaffolded and wired into CI, but not in force.** The check
runs on every pull request today and correctly qualifies the rights holder and
the two exempt bots. It cannot yet *accept* a signature from anybody else,
because the agreements are still `1.0-draft` with an unset counterparty.

Do not describe this as "CLA legal onboarding is production-ready". It is not.
Two things are missing, and only one of them is engineering work.

| Piece | State |
|---|---|
| Agreement texts (Individual, Corporate) | Written, versioned, hashed |
| Versioning + hash-binding policy | In force — CI red on drift |
| Repository-side qualification check | **Live** — feeds `CI fast gate` |
| Security model of that check | Complete; no secrets, no PR code executed |
| Signature ledger | Present, empty, schema documented |
| Legal counterparty ("We"/"Us") | **MISSING — blocks everything below** |
| Signature collection provider | Not installed |

## Why it was built this way

The obvious approach — install a CLA bot and let it own the whole problem — did
not survive contact with the candidates.

| Option | Finding | Verdict |
|---|---|---|
| **CLA Assistant Lite** (`contributor-assistant/github-action`) | The repository is **archived and read-only as of 2026-03-23**. Its README: *"This repository is no longer actively maintained. I no longer have the bandwidth to maintain this project. The repository has been archived and is now read-only."* It also requires `pull_request_target` with write permissions. | **Rejected.** An unmaintained action holding write permissions on a privileged trigger is the worst combination available: no security fixes will ever ship for it. |
| **`iainmcgin/cla-github-action`** (fork of the above) | Explicitly documented as maintained for internal use only, not a general-purpose community successor, with no support or issue triage. | **Rejected.** |
| **EasyCLA** (Linux Foundation) | Designed around LF-hosted projects and LF membership. | **Not applicable.** |
| **CLA Assistant** (`cla-assistant/cla-assistant`, by SAP) | Actively maintained. Free hosted offering at `cla-assistant.io`; also self-hostable via Docker. Signatures stored in Azure Cosmos DB in Europe since 2021-08-27, exportable as CSV. Uses a GitHub App plus an OAuth app. | **Recommended as the signature provider**, when signature collection is needed. |
| **Writing our own signing flow** ("comment `I agree`, then grep the comments") | — | **Rejected outright.** Contract formation is not something to prototype. |

So the responsibilities were split, along the line where each side is actually
competent:

```
Signature collection  →  an external provider (or a countersigned document)
                         — contract formation, identity, consent UX

Signature record      →  docs/legal/cla-signatures.json
                         — a reviewable ledger in the repository

Qualification check   →  scripts/ci/cla_gate.py
                         — small, pure-stdlib, unit-tested, no network,
                           no secrets, feeds the existing CI fast gate
```

The ledger is **a record, not the signing mechanism**. Nothing is signed by
appearing in it; entries are written *because* a signature was already obtained
elsewhere. That distinction is what keeps this from being the DIY contract
infrastructure rejected above.

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
the decision depends on — the gate script, the policy, the ledger, and both
agreement texts — is fetched from the **default branch** via `gh api`, the same
pattern `ci.yml` already uses to obtain a trusted `aggregate_gate.py`.

The reasoning is the same as for the gates: the tree under review must not
supply the logic that judges it. Here it is sharper still, because a PR that
could supply its own ledger could add its author to it and pass its own check.

The gate script runs under `python3 -I` (isolated mode: no script-directory
`sys.path` entry, `PYTHONPATH` ignored), so a shadowing module cannot be
smuggled in either.

### 3. Least privilege, and no secrets

```yaml
permissions:
  contents: read        # read policy/ledger/agreements from the default branch
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

### 1. Decide the legal rights holder — **required, and not an engineering task**

Every `RIGHTS_HOLDER_CONFIGURATION_REQUIRED` marker traces to one unanswered
question: *who is the legal person on the other side of this agreement?*

The repository does not say. `README.md` states only "Tavotto™ is a trademark of
the Tavotto project"; `pyproject.toml` names `erwanjun` as author; there is no
company, foundation, or incorporated body anywhere in the tree. **A GitHub
organisation is not a legal person and cannot hold rights or sign agreements.**
Nothing was invented to fill this gap.

The options are the individual rights holder in their own name, or a legal
entity formed or nominated for the purpose. Both are ordinary; the choice has
tax, liability and jurisdiction consequences that belong with counsel.

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

Not needed while contributions come solely from the rights holder. When it is:

1. Sign in at <https://cla-assistant.io> with the GitHub account that owns the
   repository and authorise the app for `Tavotto/Tavotto`.
2. Point it at `docs/legal/CLA_INDIVIDUAL.md` as the agreement text.
3. Note that CLA Assistant stores signatures in Azure Cosmos DB in Europe.
   If that is unacceptable, self-host it (the project ships Docker instructions)
   or collect signatures as countersigned documents instead — the ledger and the
   gate work identically either way.
4. Export signatures and record them in `docs/legal/cla-signatures.json` with
   the version and hash signed, per
   [CLA_VERSIONING.md](CLA_VERSIONING.md#the-signature-ledger).

The gate reads only the ledger, so the provider is swappable and no CI change is
needed to adopt, replace or drop one.

### 3. Corporate signatures stay manual — deliberately

No CI check can establish that a GitHub username is authorised to bind a
company. That evidence lives outside GitHub entirely. Corporate agreements are
reviewed by a human, and a maintainer then records the covered accounts in the
ledger. See [CLA_CORPORATE.md](CLA_CORPORATE.md#how-to-sign).

## Verifying the check locally

```sh
# Rights holder → exempt, exit 0
python3 scripts/ci/cla_gate.py --event pull_request --pr-author erwanjun \
  --commits-json <(echo '[{"sha":"a","commit":{"author":{"name":"e","email":"1259959884@qq.com"},"message":"x"},"author":{"login":"erwanjun"}}]')

# Merge queue → not_applicable, exit 0 (must never be skipped)
python3 scripts/ci/cla_gate.py --event merge_group

# Unsigned external contributor → exit 1
python3 scripts/ci/cla_gate.py --event pull_request --pr-author outsider \
  --commits-json <(echo '[{"sha":"b","commit":{"author":{"name":"o","email":"o@example.com"},"message":"x"},"author":{"login":"outsider"}}]')

.venv/bin/python -m pytest tests/test_legal_contribution_policy.py
```
