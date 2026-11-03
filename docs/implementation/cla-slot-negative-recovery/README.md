# Active-slot negative recovery

This default-disabled candidate extends the existing recovery invocation. It
adds no route, schema, permission, credential, resource, timer or real authority.
Base: #1169, `7324a2d0541d79b90f106aed74991b22b8af60ae`. The contributor's
personal signing requirements and the private negative-witness protocol stay
unchanged. All actual execution evidence uses disposable synthetic SQLite and
in-process GitHub-shaped responses; it is not live GitHub/D1 acceptance.

## The defect and the boundary

A healthy confirmed owner already schedules reconciliation at proof expiry.
That path observes the original run, withdraws an ineligible success and needs
a new exact failure GET witness before releasing capacity. Confirmation itself
does not release the slot. This candidate does not replace that rule with time.

The actual permanent dead end was a held manual or exhausted row: outbox claim
and publication selection excluded it forever. Keeping an outbox row due every
minute did not make it selectable. Even if its original remote run later became
an exact, independently observable failure, no observation could release it.

Only the durable active owner now crosses those retry exclusions. It retains
the original attempt, head, proof, run identity, revision, lease/generation CAS
and live send fence. Other manual/exhausted records are not reopened. The
negative-only predicate is mirrored in SQL eligibility and the runtime guard;
tests exercise both boundaries.

An exhausted/manual owner can only observe or drain. An exact failure is
resolved with the existing fresh, private, single-use GET witness. An exact
success is withdrawn under the existing publication flag, lease/revision guards
and App scope, then independently read as failure. It cannot evaluate positive
eligibility, become confirmed or issue a POST. Existing manual head and refresh
decisions are never reset by this channel.

Missing, ambiguous, malformed or failed observations retain the owner in manual
state, increment its bounded counter without lowering it, and persist a database
deadline of now+60 seconds. That branch strictly respects its deadline even if
expiry, generation or another row makes the outbox due sooner. Outbox retry
exhaustion is preserved while the owner remains held. Compatible flag-disabled
recovery still sees the permanent latch and can drain it.

Manual owners do not gain within-target withdrawal priority: the existing rowid
scan can process older due rows. A fixed horizon prevents new rows from extending
the current pass. This is not a global fairness or latency guarantee. A live
send fence, active lease, stale tuple or changed revision never yields capacity.

## What remains blocked

If no exact owned run ever appears, neither list absence nor 404, elapsed proof
lifetime, repeated polls or operator assertion proves that the uncertain send
did not execute. The slot remains held. This change removes the permanent
selection dead end when verifiable negative facts become available; it does not
automatically recover every abnormal outcome.

D1 due values are not events. The disabled deployment proposal has a two-minute
recovery cadence and a reserved fifteen-minute automatic-head cadence, with no
actual cron configured here. A two-minute tick can already consume a 120-second
proof's whole lifetime. Merely changing it to one minute does not guarantee
`schedule + dispatch + independent confirmation + final CI < remaining lifetime`
under the existing 60-second work / 90-second cleanup limits. No new idle-handoff
or timing planner is presented as that guarantee.

Maintainer options for a future event path are: accept a measured conditional
cadence with tighter remaining-lifetime admission; approve and validate an
existing GitHub check-run webhook subscription and its handler contract; or
separately approve Queue/Durable Object resources, bindings and costs. Each still
needs real timing, quota and recovery acceptance. This PR creates/enables none.

Native merge-group remains unimplemented. Full queue-prefix versus exact-member
policy is not decided; Merge queues App permission and installation/webhook
changes are not approved. Trusted-main CI wiring requires separately landing
the reviewed source first, then an independently reviewed wiring change and
real authority acceptance. Existing PR proof/CI success cannot substitute.

## Verification

The new root regressions actually fail on the exact base runtime (five actual
AssertionErrors, exit 1), then pass with this implementation. Independent tests
and unique-location counter-mutations separately exercise the recovery contract.
[Commands, raw UTF-8 evidence and source fingerprints](evidence/verification.json)
record actual exits and distinguish the original, fixed and mutation runs.

No live endpoint, real signature, successful publication across proof expiry,
dependency retry or proxy/mirror/environment workaround is exercised. Hosted
current-head type/build, high-risk full CI, real D1/GitHub behavior, legal
authority, deployment and final enablement remain separate prerequisites.
