# Final offline repo-v2 upgrade candidate

This is a synthetic SQLite rehearsal of an in-place upgrade of the existing
`cla_native_v2_` namespace to #1168. It does not read or write production,
contact D1, publish, select an agreement, sign, register authority, create a
resource or enable a flag. No further capability is added by this slice.
Stack parent: #1168, `dc2431f725798bd78c40a3b07e0a8054690fe82c`, branch
`feat/cla-confirmation-slot-20261018`.

## Exactly which v2

The source is the **repo-defined native v2** at
`77e2cab77597318859f874d9319c47f15da0e1dd`: its actual Store prefix is
`cla_native_v2_`; its base/native DDL is frozen byte-for-byte under
`services/cla-cloudflare/migrations/v2-main-77e2cab7/`.
Direct GitHub reads at main `0991bb9501489395a38542c94867fcfeffbdf196` matched
both frozen files exactly. This is not the separate v1 snapshot migration, and
it is not a claim that a live D1 database has that schema. No live schema was
queried. A production inventory and mapping remain an acceptance prerequisite.

The source has 19 tables, four explicit indices and 16 triggers. #1168 has
26 tables, four indices and 39 triggers. `base.sql` is unchanged. The only
existing-table change is appending `publication_attempts.send_until INTEGER
NOT NULL DEFAULT 0 CHECK(send_until>=0)`. The remaining delta is seven empty
new tables and 23 triggers: **31 DDL statements, no table rebuild or drop**.
The module pins the exact four source/target DDL file hashes; later source or
target edits require another review rather than automatic accommodation.

All user schema objects are compared, including out-of-namespace objects.
Unknown views/triggers/indices/tables, mixed v1/v2, unsupported intermediate
versions, partial upgrades, attached databases and temporary schema objects
are refused. SQLite internal names use a literal `sqlite_` prefix check;
`sqliteX...` cannot hide as an internal name. Foreign keys must already be on.
Equivalent-looking hand-edited schema is not silently accepted.

SQLite rewrites the publication CREATE text when adding a column, so that
text differs from a fresh #1168 CREATE. The expected upgraded model is built
by executing the exact frozen source plus the one reviewed ALTER and target
new-object definitions. The actual resulting schema must match that full
model exactly. Table columns, foreign keys and index metadata are additionally
compared with the fresh target model. The tool does not erase or ignore SQL
text to make mismatches disappear.

## Disabled interface and transaction

Run the read-only-to-caller CLI:

    node scripts/v2-upgrade-offline.mjs --plan

It constructs only an empty in-memory synthetic source and prints a disabled
plan. It accepts no file path, credentials, D1 binding or execution option.
The exported `applySyntheticV2Upgrade` is also disabled before touching its
caller database unless both `execute:true` and `syntheticFixture:true` are
explicitly supplied by a local test. That label is a caller assertion, not
proof of data provenance or authorization for production. No production
execution adapter or migration command is supplied.

Preparation includes exact schema and complete per-table data fingerprints.
Application starts `BEGIN IMMEDIATE`, then rebuilds the plan and rechecks
source schema, integrity, foreign keys, data and quiescence under that lock.
A changed/tampered plan fails before DDL. A caller's existing transaction is
rejected untouched. Only the 31 reviewed additive statements execute, then
full preservation/default/empty-table/schema/integrity checks run before COMMIT.
Every pre-commit failure rolls back all schema and data changes.

Each old table is read in full: every declared column plus signed 64-bit rowid.
Integers use `setReadBigInts(true)` and typed encoding, rather than lossy JSON
numbers. BLOB and TEXT additionally include SQL `hex()` bytes so invalid UTF-8
cannot collapse into the same decoded replacement character. NULL, Unicode,
control characters, ciphertext and large integer values remain distinguishable.
Reports expose counts and SHA-256 only, not evidence plaintext or row values.

Original authority, agreements, documents, signatures, holds, audits, sessions,
OAuth state, revision, publication phase/proof/run/expiry, manual/exhausted
refresh state, outbox generation and reconciliation cursor/horizon are never
rewritten or re-seeded. `send_until` is zero for every historical attempt.
The seven new capability tables, including grant/control/receipt, head request,
activation and slot latch, stay empty. No old proof is qualified or extended.

Source `issued` or `unknown` rows of either conclusion, **any** nonempty lease
or nonzero lease deadline (even expired), and any `_guards` row refuse upgrade.
They are not cleared or converted to make the database look quiet. The SQLite
lock protects local atomicity; it cannot certify that production Workers,
queues, personal signing or an old service have been stopped. External writer
quiescence and a consistent backup require separate real-platform acceptance.

An uncertain committed result can be recognized without replaying DDL only
when the target schema, all original data, historical zero defaults, empty new
tables and quiescence still match the prepared plan. An exact target database
can otherwise receive a read-only `already_target` result from its own new plan;
that does not attribute historical preservation to an earlier migration.
`activation_performed:false` reports this tool's actions; it does not certify
that an existing target database or externally configured runtime is inactive.
Partial or changed state never triggers an automatic repair.

The rollback guarantee covers this transaction **before commit**. After new
signatures, holds or controls have been accepted, restoring an old backup or
starting an old binary is not a safe automatic downgrade. No downgrade tool is
provided. Schema-only preservation makes no version selection, so this slice
does not depend on the unresolved agreement-version rollback policy.

## Verification and convergence boundary

At original reviewed head `a19e88f0`, the Node suite passed 507 tests; the existing v1 snapshot migration
passed four tests unchanged; the mutation harness detected 146 cases (22 new
unique-location counter-mutations, each actual AssertionError / exit 1).
Independent contract tests pass 46 cases with 18 reverse mutants; the separate
review executes 24 groups, including 31 individual DDL fault rollbacks and the
BEGIN red-to-green regression. CLI tests add three cases/three mutants; two
new disposable synthetic file/WAL tests and one commit-durability mutant cover
close/reopen. Independent original logs are preserved byte-for-byte as JSON
strings with their original SHA256 in raw-log bundles, so assertion whitespace
is not altered.

[Evidence and exact command exits](evidence/verification.json) distinguish
independent tests, reverse mutants, raw output, source fingerprints and blocked
local type/build checks. All execution uses disposable synthetic databases.
There is no publication, Worker entry, new credential, network probe against
production, or successful-publication transition across expiry. The earlier
npm E403 is preserved, with no download/proxy/mirror/environment workaround.

This completes the specified offline upgrade candidate. Live D1 schema
inventory, system-table mapping, SQLite-engine differences, D1 transactional DDL
and trigger support, realistic dataset size/charging/latency, backups and the
single-authority cutover remain unverified. The Node SQLite transaction is not
a claim that these 31 statements are an approved D1 migration batch.
Original head `a19e88f00e959d29dc0ca391774b884a59ce2acb` passed hosted run
39135502966, including actual checkout, dependency installation, typecheck,
507 Node tests, four migrations, build and 146 mutations. Its first complete
code/security review completed; security had no findings and code reported the
fresh-schema recognition P2 described below. The corrected head requires its
own hosted checks before the one allowed targeted follow-up.

**#1168 is still not an available fully automatic loop.** A confirmed run keeps
the sole global slot. Only a fresh owned failure GET witness releases it;
missing/unknown outcomes that become manual may block every later positive
publication indefinitely. Expiry does not release capacity. Safety remains
fail closed; the availability/recovery closure and its real acceptance are
unfinished, and this migration deliberately does not loosen those rules.

Actual wakeup/deadline execution, native merge-group and trusted-main CI wiring
remain unfinished under their existing permission, product-decision and merge
dependencies. No new resources or runtime ability are added to address them.
Real holder identity/key custody, exact legal terms and limited signed authority,
version selection policy, deployment and final flag activation remain separately
approved decisions. Offline tests establish no legal effect.

## Shared TS2700 correction and stack propagation

The manual run of original #1169 head
`23e0f50c0e7fb318d4d42c8421fcf821071ba3b8` (39126028608/job117671233509)
installed dependencies successfully, then failed at the shared Store raw-row
type with TS2700. Its Node/migration/build/mutation chain did not execute.
The parent #1168 failed identically. The fix omits the business boolean field
before declaring the database numeric CASE result, without any or suppression.
It is inherited bottom-up from corrected parent
`a5cf3549818f14c97c9c05f7aff6f14853a27f91`.

The pinned schema target reference `dc2431f7` remains the exact DDL version:
this parent correction changes only an erased TypeScript type, not nativeSQL,
the target DDL, the migration module or its source/target profile pins. Original
independent reports retain their historical full-file hashes. Current inherited
Store hash and actual new local results are separately recorded in
[the propagation evidence](evidence/ts2700/verification.json): 507 Node, four
migration, 146 mutation and 50 targeted refresh/budget checks pass. The parent
archives both original raw CI logs and six independent erasure/row-boundary
probes. No old-head CI success is borrowed. The corrected parent and inherited
type fix were compiled by the hosted jobs, including the original `a19e88f0` run
above. Local tools are still absent and no dependency retry was made.

## P2: uncertain-commit recognition requires the actual ALTER result

The first code review found that an old prepared source plan could be presented
to an independently created fresh target with matching old rows. That target's
CREATE text cannot result from the reviewed ALTER sequence, yet the old branch
reported `already_applied` and `data_preserved:true`. Empty and matching-row
synthetic fixtures reproduce the error with actual AssertionError / exit 1.

Recognition now checks the current schema hash against the trusted model's exact
ALTER-generated schema before projecting original data. It does not trust a hash
supplied in the plan. A mismatch rolls back without writes or a residual
transaction. A fresh target can still use its own `already_target` plan, with
zero writes and no claim that an old source migration preserved its data. A real
ALTER result still supports read-only lost-COMMIT recognition and WAL reopen.

Current local checks pass 509 Node tests, four migrations and 147 mutation cases.
The added unique guard-deletion mutant actually fails the new regression with
AssertionError / exit 1. Independent tests separately reproduce the original
failure, pass all three fixed cases and reject the same reverse mutant.
[Current P2 commands, raw logs and fingerprints](evidence/p2-fresh-schema/verification.json)
separate these results from the original head's hosted run. Current-fix hosted
typecheck/build and the single targeted follow-up remain pending. No production
database, D1 adapter, real signature, publication or dependency download is used.
