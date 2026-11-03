# Complete-prefix metadata transport — offline candidate

The native CLA capabilities remain default off. This change does not deploy a
service, enable a provider, configure credentials, change queue policy or wire a
new required check. Real GitHub App permissions, Cloudflare CPU/D1 and queue
wakeup timing remain separate acceptance gates.

## Reproduction and cause

`Pn` means the complete ordered queue prefix through candidate position `n`,
matched by its current group head and base; it is not a contributor count or a
per-wakeup batch. Every member's authors and coauthors still contribute to the
whole-group qualification. The existing repository queue allows four candidates
to build and merges one at a time, so P3/P4 must have reachable success paths.

With one queue page, one compare page per PR and no extra identity lookups, the
old native group dispatch needed `20 + 6n` external calls. The 20 fixed calls
include publication authentication/owned-run pre-read, both complete snapshots'
installation/repository/main reads, three immutable policy/legal-text reads,
four complete queue reads and the publication. Each PR adds metadata before,
its complete compare page, and metadata after in each of the two snapshots.

| Prefix | Old complete requirement | Old actual outcome | Candidate dispatch |
| --- | ---: | --- | ---: |
| P1 | 26 | completes | 26 |
| P2 | 32 | completes | 28 |
| P3 | 38 | call 36 rejected at external=35 | 30 |
| P4 | 44 | call 36 rejected at external=35 | 32 |

The old P3 rejection was the closing queue read; P4 rejected a PR metadata read.
38/44 are the calculated full requirements, not claims that those requests were
sent. No journal/publication was issued after the budget rejection. The four
P3/P4 personal/delegated positive regressions failed before the production fix.

## Change and safety boundaries

Only when the existing invocation-local prefix read session is enabled and the
prefix has multiple members, each snapshot batches dynamic PR metadata into a
read-only GraphQL request before and after **all** member compare pages. Each
member still has four fresh metadata reads across two complete snapshots; all
compare pagination, four queue reads, main reads, identity and revision/expiry
fences remain. The resulting dispatch is `24 + 2n` for multi-member prefixes.
This is transport batching, not dynamic evidence caching across snapshots or
wakeups, and adds no persistent checkpoint.

Missing/extra members, partial GraphQL errors, changed repository/base/head,
author ID/login/type, commit count and expired original read tokens fail closed.
A GraphQL denial has no REST fallback or token remint. The normal PR path and
the disabled/absent prefix read-session path keep their existing REST behavior.
No queue/proof hash format, permission, migration or budget is changed.

The unchanged work limits are 20 SQL statements and 35 external calls (40 total
of each including settlement). Whole-group SQL admission still permits at most
three personal or two delegated non-exempt contributors. Those are total group
limits, not resumable contributor batches. More compare/queue pages, coauthors
or previous budget charges can still exhaust external capacity. This change
does not establish arbitrary-scale or real four-candidate platform acceptance.

## Verification

From `services/cla-cloudflare` with Node 24 and the locked development tools:

```sh
npm run check
```

The entry runs type checking, all Node tests, four local SQLite migration tests,
the production-import build and the full isolated mutation suite. Exact local
results and individual mutation exit codes are in [validation.json](validation.json).
Hosted CI is reported separately on the PR; local passes are not hosted results.

`group-prefix-budget.test.mjs` exercises complete P3/P4 publication, independent
reconciliation and both CI authority reads in personal/delegated modes, P4 with
two independently signed contributors in both modes, P4 with three personal
contributors (`sql=24`, `external=32`), and lost-response/expiry recovery without
replay. `prefix-metadata-boundaries.test.mjs` covers the changed transport's
freshness and rejection boundaries. The five new mutation cases require actual
assertion failures, including removal of the closing batch and the read-session
gate. Existing prefix, group and normal PR lifecycle regressions remain active.

All identities, keys, signing operations and provider responses in these tests
are synthetic and local. They perform no real HTTP publication or signing.
Reverting this candidate requires no data migration or platform rollback.
