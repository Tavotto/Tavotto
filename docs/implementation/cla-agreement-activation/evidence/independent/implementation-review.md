# Independent reverse review: actual agreement activation mechanism

Reviewed worktree /workspace/Tavotto-cla-agreement-activation on base 6d8809f03150b762cfafeacdaf89af6c9c9bbc05 (#1161), including then-uncommitted activation-record.ts, agreement-activation.ts, policy/config/schema/store/worker/bootstrap and disabled manifest changes. This report is local independent review evidence, not a hosted CI/reviewer or deployment conclusion.

No correctness/security defect requiring a block was reproduced in this revision. The implementation closes the principal design gaps identified earlier: legacy code is fenced by a real third immutable agreement, new default-off code checks absence of anchors, current mode pins the latest hash/config, runtime reads recompute canonical body/hash, packet/intent/sign guards check current selection at the transaction, and prior personal/delegated records are retained without copied consent.

Executed baseline: node --test /tmp/cla-activation-independent-review.test.mjs > /tmp/cla-activation-independent-review.tap
Actual exit code: 0. Tests: 17 passed, 0 failed. The complete suite is independent of the author's new formal tests and does not call HTTP, worker.fetch, publishing or downloads. It uses marked synthetic fixture documents, people, ephemeral test keys/consent/grants and SQLite only. global fetch throws; outbound calls remained 0. No dynamic successful publication across expiry was executed.

Subjects verified:
- First apply consumes exactly 13 SQL statements across its entire metered call (one current-anchor read plus twelve batch statements), below unchanged 20-work ceiling. Runtime ready config succeeds; the ORIGINAL old readiness SQL returns false.
- A source-only first anchor (no third real agreement) rejects and preserves the complete relevant row snapshot. No sentinel is used.
- JSON alone cannot enable the capability. New flagoff legacy config rejects a DB containing anchors.
- Original corporate document source remains unchanged when the anchor records a newer source observation. Stored canonical body/columns agree; tested field corruption rejects.
- Tampered prepared revision/predecessor/hash/body and a NEW individual document's bytes reject without writes.
- A forced failure at the final anchor INSERT rolls back inserted agreements/documents, state CAS and relevant outbox/head/intent/evidence state.
- Exact repeated proposal is already_active with an unchanged snapshot. Applying a newer anchor makes an old proposal/config reject.
- Concurrent identical proposals give one applied plus one already_active result. Concurrent different proposals give exactly one successful activation.
- Current store cannot create/commit an old agreement tuple for a new subject, nor create a signing intent for an old packet. Existing historical hold remains available. The resulting later evidence revision remains ready.
- Existing delegated receipt/cipher/evidence rows survive byte-for-byte; audit under the new configuration reports superseded; historical revocation works using the old registered key after current authorization ID/key rotation.
- Fresh current packet receives zero copied signatures and zero copied receipts. Completed old personal consent remains auditable but cannot qualify a new current agreement; one fresh contributor consent still does not qualify it.
- Activation injected between pre-read and packet/intent transaction rejects the stale final write. An old sign transaction with a pre-activation revision is rejected without copied/committed consent.
- Actual stored canonical body corruption (in private synthetic DB only, with immutable trigger intentionally removed for falsification) makes both assertReady and revision reject. Wrong configured hash cannot be masked by otherwise identical current tuples.

Executed isolated falsification: python3 /tmp/cla-activation-independent-mutation.py
Harness actual exit code: 0. Five code mutations in /tmp clones each produced a targeted AssertionError and node exit 1. Each transform checked an exact unique source anchor first, and report.json records original source SHA256, mutated SHA256, replacement count=1, test pattern and actual exit. No workspace source was mutated.

1. Relax first registry count >2 to >=2: source-only initial anchor rejection test fails.
2. Remove prepare's raw document SHA check: tampered FRESH individual 2.0 content is accepted and the rejection test fails.
3. Bypass verifySelection: actual DB canonical body corruption stops rejecting and its runtime test fails.
4. Bypass createAuthenticatedPacket's supplied tuple match: old tuple can be committed for a new subject before post-read rejection; snapshot-preservation assertion fails.
5. Replace currentTuple's actual packet predicate with non-null bind checks: old packet signing intent is accepted and the rejection test fails.

An initial version of mutation #2 survived when its test mutated the already-stored corporate document. That was a test-subject blind spot: existing-document equality independently rejected the corrupted old corporate bytes. The target was corrected to the newly inserted individual agreement with its version marker still intact. Final mutation result is exit 1 at the intended SHA boundary. Do not use a reused immutable corporate document alone to claim the fresh-byte SHA gate was falsified.

Concrete source references:
- core/policy.ts:4-6 defines policy material and bounded numeric versions; :9/:20 preserves strict default version validation.
- native/config.ts:19-21 gates widened versions on explicit capability/pin; worker.ts:74-77 overwrites JSON capability with exact env flag.
- native/activation-record.ts:10-29 binds canonical body, its hash, mirrored columns and optional current config. Store.assertReady/revision include the existing SELECT's bounded record projection and call verification (:58-67), adding no SQL read.
- native/agreement-activation.ts:9-35 validates/prepares two real documents and proposal; :53-65 reconstructs and checks mutable input plus latest/no-op; :68-82 performs the one guarded transaction; :83-85 observes exact concurrent success without replaying writes.
- native/store.ts:34-46 distinguishes no-anchor legacy from latest-anchor current mode, permits history, and allows later revisions. :367-413 adds final selection/tuple guards to packet, intent and personal signature operations.
- native/schema.sql:34-49 is the immutable ordered activation journal. Existing outbox and head revision triggers are retained.
- scripts/generate-bootstrap.mjs:9 rejects activated config; :16 rejects anchors within bootstrap's transaction.

Boundary notes, not blocking findings:
- The anchor binds numeric repository_id and holder_id. It does not independently certify holderName, repository name strings or provider configuration; policyMaterial deliberately contains agreements/exemptions, preserving the existing proof digest semantics.
- document_source_pins are explicit caller-supplied source commit pins for this activation observation. They differ from the immutable original archive source, and a syntactically valid 40-hex pin plus content SHA does not independently authenticate Git provenance. Do not label it as independent Git authentication.
- New protocol/legal text, accepted older versions, rights-holder changes, actual legal approval, real signatures/credentials, rollout and deployment remain outside these tests and this engineering authorization.
- Existing excluded HTTP/publication/expiry acceptance scenarios remain excluded, not silently counted as passed. No hosted status exists for this unpushed worktree.

Source SHA256 observed at final review:
activation-record.ts 0f47b6adc0e70104b112e83cf1766ed457488ad297db4362a4cf96c84032d279
agreement-activation.ts 7c48adcd5c2efed70c230fca9b499b4536bd1e9872566db772c3cf8b12373ce9
store.ts 2619aa8ab95f3ebb3d6dc5fcb3fd12916501f2fb8b8499807f921e1624de389f
config.ts 7d4d4daaaf8099cf0aef7e2a4ce2bc27148ae139227c2522110418e4c09fb50a
schema.sql 1d5bd07f4f23db7819351e6846abc635971d956998cbd04d838b8181457bb1b2

Deliverables: /tmp/cla-activation-independent-review.test.mjs, its .tap result, /tmp/cla-activation-independent-mutation.py, /tmp/cla-activation-independent-mutants/report.json and each isolated mutant/result.tap. No remaining independent review blocker.
