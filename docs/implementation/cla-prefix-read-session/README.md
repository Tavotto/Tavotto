# CLA prefix assessment: disabled invocation-local read reuse

This slice depends on #1172's complete-prefix assessment. The separate
`CLA_MERGE_PREFIX_READ_SESSION_ENABLED` switch defaults to false and requires
`CLA_MERGE_PREFIX_ASSESSMENT_ENABLED`. Only the internal assessment uses it;
there is no new route, cron, webhook, publication, journal, proof or CI consumer.
JSON configuration cannot turn either environment switch on. No deployment
input enables them. The original assessment path remains when this switch is off.

One private callback can reuse a fully validated read installation token and the
policy plus both agreement contents at one exact trusted commit. It still checks
App JWT context and performs the installation GET at each original authentication
boundary, including each fresh identity lookup. Token permissions remain exactly
contents/read, pull_requests/read, checks/read and metadata/read; merge_queues is
neither requested nor accepted. No actual queue schema or permission acceptance
is claimed by the synthetic fixture.

Both assessment rounds still read repository metadata, current main, the entire
queue before and after enumeration, each selected PR before/compare/after, and
all required identities. The second round must observe the same trusted source
SHA even if file bytes happen to match. Source reuse begins only after all three
files pass their existing checks, and returned policy objects are separate clones.
The existing contributor evidence and authorization verifiers, final atomic
readiness/revision/database-time SQL and authorization deadline remain mandatory.
No identity, qualification or final eligibility is cached.

The lease pins the complete canonical configuration (including provider and
exemptions), signing material, target and shared budget. It checks actual token
expiry and nondecreasing wall and monotonic clocks before and after reads and
before returning the assessment. A single conservative monotonic deadline is
anchored at session start, including time spent obtaining the token; a stalled
wall clock cannot extend a short token. It never renews. Failure, overlapping
operations, extra snapshots or a retained reader after callback exit close the
lease. Catching an error in the callback does not reopen it. The token and source
cache are cleared at exit, without fallback or replay.

With single-page queue and compare reads, measured capacity is documented in the
verification evidence. The accounting formula is `16 + 6P + 2L`, where P is the
number of selected PRs and L is the total fresh user lookups across both rounds.
The actual adapter plus SQLite fixture measured 28 external / 6 SQL for two
members and one delegated contributor, and 34 / 6 for three members. Four members
stop at 35 external calls. Two members with a coauthor resolved once per round
cost 32 / 7 and remain ineligible because that coauthor has not signed; three
members with that coauthor stop at 35. Two personally completed
contributors cost 28 / 7. These are distinct synthetic evidence scenarios.
Pagination and prior work consume the same budget. The 35 work / 40 total external
and 20 work / 40 total SQL ceilings are unchanged; an oversized assessment rejects
in full and produces no report. Structural member limits are not throughput promises.

The parent assessment protocol remains `cla-merge-prefix-assessment-v1`,
`publication: not_attempted`. This is a technical observation, not a contract or
live merge authorization. Contributor personal signing, holder personal signing
and independent recovery retain their existing paths. This slice creates no real
identity/key registration, grant, control, signature, credential or resource.

Before actual queue integration, separately establish the live GraphQL schema and
least required installation/token permission, then implement the group durable
request/send/unknown journal and exact negative recovery capability. Dedicated
group proof/authority/CI double reads, trusted-main rollout, actual wakeup/deadline
and platform acceptance, and a separately approved signed limited holder authority
remain outstanding. Existing PR-only target/witness guards remain closed to groups.
No merge, deployment or activation is included. See ADR 0137 and the parent
`../cla-merge-prefix/README.md` for the authority and platform boundaries.
