# CLA merge queue prefix: disabled membership and qualification candidate

The approved membership rule is every PR from queue position 1 through the unique
current candidate. An unsigned contributor in an earlier PR blocks the current
candidate. This engineering policy approval does not authorize signatures,
permissions, credentials, resources, deployment or production activation.

This slice implements an internal, read-only assessment on the native stack.
`GitHub.mergePrefixSnapshot()` and `LiveService.assessMergePrefix()` have no HTTP,
webhook or cron entry. They return `cla-merge-prefix-assessment-v1`, with
`publication: not_attempted`. The result is not a CheckProposal, a
`tavotto-cla-v1` proof, a confirmed run or reusable CI evidence. No assessment is
persisted and no target, journal, head request or publication is created.

## Membership and fresh facts

The GraphQL query and queue-prefix rule come from
[`services/cla/github.py`](../../../services/cla/github.py), rather than a new
branch-name or commit-message inference rule. The native reader retains the
existing bounded response transport and invocation budget. It requires:

- Exact repository ID, main branch, candidate queue head **and** queue base.
- Complete queue pagination with a stable total, advancing unique cursors, unique
  entry IDs and PRs, consecutive positions, and an explicit final page. The
  reader admits at most 1,000 queue entries over ten pages; the selected prefix
  admits at most 100 PRs. An oversized or incomplete queue is rejected, never
  truncated to the current PR.
- Complete PR snapshots for every selected position, including each PR's own
  base/head comparison, exact commit count, unique commit SHAs and presence of its
  head. Open state, repository, base branch, author identity and head are checked
  again after enumeration. The existing PR path shares this enumeration helper.
- The existing numeric-ID union of every PR author, commit author and coauthor.
  Unknown authors, malformed trailers, conflicting identities, stale exemptions
  and incomplete commits fail closed. The entire prefix shares the existing
  contributor/lookup caps and qualification rules; members are not qualified in
  separate cached batches.

The candidate queue head `G`, queue base `B`, trusted main source `T`, and each
PR's `Hi/Bi` have separate meanings. In particular, `B` can be an earlier queue
candidate and need not equal `T` or any PR's base. Policy and both agreement
documents are read at pinned `T`; candidate selection uses exact `G/B`.

Each snapshot reads the full queue before and after all PRs. The assessment then
obtains a second complete snapshot and contributor binding after qualification.
It compares the normal binding, ordered prefix entry/position digest, complete
queue digest and exact trusted source SHA. A queue/source/identity/head change
rejects the assessment, including changes that preserve contributor count or
policy bytes. PR-number sorting in the normal binding does not replace the
ordered prefix digest.

Every non-exempt person uses the existing personal/delegated evidence verifier.
The final readiness-guarded SQL snapshot reads evidence revision and database time
together. A hold, revocation or changed revision before that snapshot rejects;
authorization expiry caps `valid_until`. This is a point-in-time internal
assessment, not a guarantee about future queue state or legal effect.

## Default off and unchanged authority

`mergePrefixAssessmentEnabled` defaults false. The Worker factory overrides any
JSON value with the exact environment comparison
`CLA_MERGE_PREFIX_ASSESSMENT_ENABLED === 'true'`. No checked-in deployment enables
it, and no route or scheduled job calls it. The assessment requires one shared
Store/GitHub invocation budget. The original ceilings remain 20 work / 40 total
SQL statements and 35 work / 40 total external requests; excess work rejects the
whole assessment. Queue length, pagination, commits and identity lookups affect
capacity, so these are not promises that every prefix below the shape limits can
finish within one invocation.

The native installation-token permission request is unchanged: contents/read,
pull_requests/read, checks/read (or the existing write path) and metadata/read.
It does not request or accept `merge_queues`. The synthetic transport tests do
not establish that a real installation can read the queue. Future permission
approval, minimal token downscope changes, GraphQL schema verification and real
installation acceptance remain separate work. No App, OAuth or credential was
created or changed here.

The existing group rejections in proof parsing, CI locator/CLI, authority,
publication, automatic PR admission and recovery remain. A later group merge gate
must first integrate durable group send/unknown/recovery handling and exact
negative witnesses; the PR-only head-request table and `pr:` witness constraint
are not bypassed. Trusted-main workflow wiring, actual recovery wakeup/deadline,
platform acceptance, real limited holder authorization and activation remain
separate dependencies. No merge, deployment, real signature or live enablement
is performed by this slice. The #1163 formal-coverage gap and exhausted #1169
review budget are retained.

## Verification

The committed [evidence manifest](evidence/verification.json) records actual command exits, source hashes,
independent counterexamples and mutation logs. Tests use synthetic documents,
identities and signatures, local SQLite and an in-process GitHub-shaped
transport. Installation-token/GraphQL POSTs in that transport are synthetic
read/authentication seams; no Check Run is published and no real GitHub HTTP or
Cloudflare/D1 acceptance is claimed. The previously denied dependency download
and successful-publication-across-proof-expiry tests are not retried or renamed.

Latest main was read through the authorized GitHub connector as
`0991bb9501489395a38542c94867fcfeffbdf196`; the implementation is stacked on #1171
`f73a4b17b89f58e4595f3581afd2fe8ecaf6a90b`. A normal command-line Git fetch of
public main returned CONNECT tunnel 403; it was not retried or bypassed. No
parent/donor hosted checks are treated as evidence for the new head.
