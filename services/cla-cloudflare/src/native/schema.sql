-- Local candidate extension; the frozen deployed migration is unchanged.
CREATE TABLE publication_attempts (
 id TEXT PRIMARY KEY, target_key TEXT NOT NULL REFERENCES targets(target_key),
 head_sha TEXT NOT NULL, revision INTEGER NOT NULL, expires INTEGER NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('issued','confirmed','unknown','reconcile','resolved','manual')),
 run_id INTEGER, created INTEGER NOT NULL, updated INTEGER NOT NULL,
 conclusion TEXT NOT NULL CHECK(conclusion IN ('success','failure')), proof_text TEXT NOT NULL,
 observation TEXT NOT NULL DEFAULT '',
 reconcile_due INTEGER DEFAULT 0, reconcile_attempts INTEGER NOT NULL DEFAULT 0 CHECK(reconcile_attempts BETWEEN 0 AND 3),
 observed_generation INTEGER NOT NULL DEFAULT 0,
 send_until INTEGER NOT NULL DEFAULT 0 CHECK(send_until>=0)
);
CREATE UNIQUE INDEX publication_unresolved ON publication_attempts(target_key) WHERE phase IN ('issued','unknown');
-- One permanent protocol latch and one nullable global owner. No existing row
-- gains a new default. The marker exists only inside the original issued batch.
CREATE TABLE slot_policy (
 id INTEGER PRIMARY KEY CHECK(id=1),protocol TEXT NOT NULL CHECK(protocol='cla-confirmation-slot-v1'),
 first_attempt TEXT NOT NULL REFERENCES publication_attempts(id),
 active_attempt TEXT REFERENCES publication_attempts(id),created INTEGER NOT NULL
);
CREATE TRIGGER slot_policy_insert BEFORE INSERT ON slot_policy BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM slot_policy) OR coalesce((NEW.active_attempt=NEW.first_attempt
  AND EXISTS(SELECT 1 FROM publication_attempts p JOIN _guards g ON g.id='confirmation-slot:'||p.id AND g.ok=1
   WHERE p.id=NEW.active_attempt AND p.phase='issued' AND p.conclusion='success' AND p.send_until>CAST(strftime('%s','now') AS INTEGER))),0)!=1
 THEN RAISE(ABORT,'slot_policy_enrollment_required') END;
END;
CREATE TRIGGER slot_policy_delete BEFORE DELETE ON slot_policy BEGIN SELECT RAISE(ABORT,'slot_policy_immutable'); END;
CREATE TRIGGER slot_policy_update BEFORE UPDATE ON slot_policy BEGIN
 SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.protocol IS NOT OLD.protocol OR NEW.first_attempt IS NOT OLD.first_attempt
  OR NEW.created IS NOT OLD.created THEN RAISE(ABORT,'slot_policy_immutable') END;
 SELECT CASE WHEN coalesce((
  (OLD.active_attempt IS NULL AND NEW.active_attempt IS NOT NULL AND EXISTS(
   SELECT 1 FROM publication_attempts p JOIN _guards g ON g.id='confirmation-slot:'||p.id AND g.ok=1
    WHERE p.id=NEW.active_attempt AND p.phase='issued' AND p.conclusion='success' AND p.send_until>CAST(strftime('%s','now') AS INTEGER)))
  OR (OLD.active_attempt IS NOT NULL AND NEW.active_attempt IS NULL AND EXISTS(
   SELECT 1 FROM publication_attempts p JOIN outbox o ON o.target_key=p.target_key WHERE p.id=OLD.active_attempt AND p.phase='resolved'
    AND CASE WHEN json_valid(p.observation) THEN json_extract(p.observation,'$.protocol') END='cla-negative-release-v1'
    AND json_extract(p.observation,'$.attempt')=p.id AND json_extract(p.observation,'$.run_id')=p.run_id
    AND json_extract(p.observation,'$.recovery_revision')=(SELECT revision FROM state WHERE id=1)
    AND json_extract(p.observation,'$.observed_at')<=CAST(strftime('%s','now') AS INTEGER)
    AND json_extract(p.observation,'$.valid_until')>CAST(strftime('%s','now') AS INTEGER)
    AND o.lease=json_extract(p.observation,'$.lease') AND o.generation=json_extract(p.observation,'$.generation')
    AND o.lease_until>CAST(strftime('%s','now') AS INTEGER)))
 ),0)!=1 THEN RAISE(ABORT,'slot_transition_rejected') END;
END;
CREATE TRIGGER publication_slot_insert BEFORE INSERT ON publication_attempts BEGIN
 -- REPLACE may skip DELETE triggers. Fence it before any implicit delete.
 SELECT CASE WHEN EXISTS(SELECT 1 FROM publication_attempts p WHERE p.id=NEW.id AND (
   p.id=(SELECT active_attempt FROM slot_policy WHERE id=1) OR
   CASE WHEN json_valid(p.observation) THEN json_extract(p.observation,'$.protocol') END='cla-negative-release-v1'
   OR EXISTS(SELECT 1 FROM _guards WHERE id='confirmation-slot:'||NEW.id)))
 OR EXISTS(SELECT 1 FROM publication_attempts p WHERE p.id=(SELECT active_attempt FROM slot_policy WHERE id=1)
  AND p.target_key=NEW.target_key AND (p.head_sha=NEW.head_sha OR p.phase IN ('issued','unknown')))
 OR CASE WHEN json_valid(NEW.observation) THEN json_extract(NEW.observation,'$.protocol') END='cla-negative-release-v1'
 THEN RAISE(ABORT,'slot_history_protected') END;
 SELECT CASE WHEN NEW.conclusion='success' AND NOT EXISTS(SELECT 1 FROM _guards WHERE id='confirmation-slot:'||NEW.id AND ok=1)
  AND EXISTS(SELECT 1 FROM slot_policy) THEN RAISE(ABORT,'slot_required') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM _guards WHERE id='confirmation-slot:'||NEW.id) AND coalesce((
  NEW.conclusion='success' AND NEW.phase='issued' AND NEW.send_until>CAST(strftime('%s','now') AS INTEGER)
  AND NOT EXISTS(SELECT 1 FROM slot_policy WHERE active_attempt IS NOT NULL)
  AND NOT EXISTS(SELECT 1 FROM publication_attempts WHERE conclusion='success' AND phase!='resolved')
 ),0)!=1 THEN RAISE(ABORT,'slot_capacity_unavailable') END;
END;
CREATE TRIGGER publication_slot_enroll AFTER INSERT ON publication_attempts
 WHEN EXISTS(SELECT 1 FROM _guards WHERE id='confirmation-slot:'||NEW.id AND ok=1) BEGIN
 INSERT INTO slot_policy SELECT 1,'cla-confirmation-slot-v1',NEW.id,NEW.id,CAST(strftime('%s','now') AS INTEGER)
 WHERE NOT EXISTS(SELECT 1 FROM slot_policy);
 UPDATE slot_policy SET active_attempt=NEW.id WHERE id=1 AND active_attempt IS NULL;
 SELECT CASE WHEN (SELECT active_attempt FROM slot_policy WHERE id=1) IS NOT NEW.id THEN RAISE(ABORT,'slot_enrollment_failed') END;
 DELETE FROM _guards WHERE id='confirmation-slot:'||NEW.id;
END;
CREATE TRIGGER publication_slot_delete BEFORE DELETE ON publication_attempts WHEN
 OLD.id=(SELECT active_attempt FROM slot_policy WHERE id=1) OR
 CASE WHEN json_valid(OLD.observation) THEN json_extract(OLD.observation,'$.protocol') END='cla-negative-release-v1'
 BEGIN SELECT RAISE(ABORT,'slot_history_protected'); END;
CREATE TRIGGER outbox_slot_delete BEFORE DELETE ON outbox WHEN EXISTS(
 SELECT 1 FROM publication_attempts p WHERE p.target_key=OLD.target_key AND p.id=(SELECT active_attempt FROM slot_policy WHERE id=1))
 BEGIN SELECT RAISE(ABORT,'slot_recovery_required'); END;
CREATE TRIGGER publication_slot_update BEFORE UPDATE ON publication_attempts BEGIN
 SELECT CASE WHEN (OLD.id=(SELECT active_attempt FROM slot_policy WHERE id=1) OR
  CASE WHEN json_valid(OLD.observation) THEN json_extract(OLD.observation,'$.protocol') END='cla-negative-release-v1') AND
  (NEW.id IS NOT OLD.id OR NEW.target_key IS NOT OLD.target_key OR NEW.head_sha IS NOT OLD.head_sha OR NEW.revision IS NOT OLD.revision
   OR NEW.expires IS NOT OLD.expires OR NEW.conclusion IS NOT OLD.conclusion OR NEW.proof_text IS NOT OLD.proof_text OR NEW.created IS NOT OLD.created
   OR (OLD.run_id IS NOT NULL AND NEW.run_id IS NOT OLD.run_id)) THEN RAISE(ABORT,'slot_identity_immutable') END;
 SELECT CASE WHEN CASE WHEN json_valid(OLD.observation) THEN json_extract(OLD.observation,'$.protocol') END='cla-negative-release-v1'
  AND NEW.observation IS NOT OLD.observation THEN RAISE(ABORT,'slot_release_immutable') END;
 SELECT CASE WHEN OLD.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND NEW.send_until IS NOT OLD.send_until
  AND NOT(OLD.phase='issued' AND NEW.phase IN ('unknown','reconcile') AND NEW.send_until=0)
 THEN RAISE(ABORT,'slot_send_fence_protected') END;
 SELECT CASE WHEN OLD.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND NEW.phase='resolved' AND coalesce((
  OLD.send_until<=CAST(strftime('%s','now') AS INTEGER) AND CASE WHEN json_valid(NEW.observation) THEN
   json_extract(NEW.observation,'$.protocol')='cla-negative-release-v1'
   AND json_extract(NEW.observation,'$.attempt')=OLD.id AND json_extract(NEW.observation,'$.target_key')=OLD.target_key
   AND json_extract(NEW.observation,'$.head')=OLD.head_sha AND json_extract(NEW.observation,'$.original_revision')=OLD.revision
   AND json_extract(NEW.observation,'$.proof_text')=OLD.proof_text AND json_extract(NEW.observation,'$.expires')=OLD.expires
   AND json_extract(NEW.observation,'$.old_run_id') IS OLD.run_id AND json_extract(NEW.observation,'$.observed_phase')=OLD.phase
   AND json_extract(NEW.observation,'$.run_id')=NEW.run_id AND NEW.run_id>0
   AND json_extract(NEW.observation,'$.source') IN ('observed_get','withdrawal_get')
   AND json_extract(NEW.observation,'$.observed_at')<=CAST(strftime('%s','now') AS INTEGER)
   AND json_extract(NEW.observation,'$.valid_until')>CAST(strftime('%s','now') AS INTEGER)
   AND json_extract(NEW.observation,'$.recovery_revision')=(SELECT revision FROM state WHERE id=1)
   AND EXISTS(SELECT 1 FROM outbox o WHERE o.target_key=OLD.target_key AND o.lease=json_extract(NEW.observation,'$.lease')
    AND o.generation=json_extract(NEW.observation,'$.generation') AND o.lease_until>CAST(strftime('%s','now') AS INTEGER)) END
 ),0)!=1 THEN RAISE(ABORT,'fresh_negative_release_required') END;
END;
CREATE TRIGGER publication_slot_release AFTER UPDATE ON publication_attempts
 WHEN OLD.id=(SELECT active_attempt FROM slot_policy WHERE id=1) AND NEW.phase='resolved' BEGIN
 UPDATE slot_policy SET active_attempt=NULL WHERE id=1 AND active_attempt=NEW.id;
END;
CREATE TABLE reconciliation_progress (
 target_key TEXT PRIMARY KEY REFERENCES targets(target_key),
 cursor INTEGER NOT NULL DEFAULT 0 CHECK(cursor>=0), horizon INTEGER NOT NULL DEFAULT 0 CHECK(horizon>=0)
);
-- One refresh decision per target/source/revision. Exhaustion remains manual
-- across later revisions; only explicit operator review may unblock it.
CREATE TABLE refresh_requests (
 target_key TEXT PRIMARY KEY REFERENCES targets(target_key), source_attempt TEXT NOT NULL,
 revision INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','done','manual')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3), due INTEGER NOT NULL,
 reason TEXT NOT NULL
);
CREATE TABLE agreement_documents (
 digest TEXT PRIMARY KEY REFERENCES agreements(digest), kind TEXT NOT NULL, version TEXT NOT NULL,
 source_sha TEXT NOT NULL, content TEXT NOT NULL
);
CREATE TRIGGER documents_update BEFORE UPDATE ON agreement_documents BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER documents_delete BEFORE DELETE ON agreement_documents BEGIN SELECT RAISE(ABORT,'immutable'); END;

-- Explicit current selection; historical agreements/documents remain immutable.
CREATE TABLE agreement_activations (
 generation INTEGER PRIMARY KEY CHECK(generation>0),hash TEXT NOT NULL UNIQUE,previous_hash TEXT NOT NULL,
 activation_revision INTEGER NOT NULL CHECK(activation_revision>0),repository_id INTEGER NOT NULL CHECK(repository_id>0),
 holder_id INTEGER NOT NULL CHECK(holder_id>0),reference_sha TEXT NOT NULL,policy_json TEXT NOT NULL,
 individual_version TEXT NOT NULL,individual_digest TEXT NOT NULL REFERENCES agreement_documents(digest),
 corporate_version TEXT NOT NULL,corporate_digest TEXT NOT NULL REFERENCES agreement_documents(digest),
 observation_json TEXT NOT NULL,body TEXT NOT NULL,created INTEGER NOT NULL CHECK(created>=0)
);
CREATE TRIGGER activation_order BEFORE INSERT ON agreement_activations BEGIN
 SELECT CASE WHEN NEW.generation!=coalesce((SELECT max(generation) FROM agreement_activations),0)+1
 OR NEW.previous_hash!=coalesce((SELECT hash FROM agreement_activations ORDER BY generation DESC LIMIT 1),printf('%064d',0))
 OR NEW.activation_revision!=(SELECT revision FROM state WHERE id=1)
 THEN RAISE(ABORT,'activation_order') END;
END;
CREATE TRIGGER activations_update BEFORE UPDATE ON agreement_activations BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER activations_delete BEFORE DELETE ON agreement_activations BEGIN SELECT RAISE(ABORT,'immutable'); END;

-- Bounded OAuth admission and expiry pruning use this index.
CREATE INDEX oauth_expiry ON oauth_states(expires,state_hash);

CREATE TRIGGER authority_update BEFORE UPDATE ON authority BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER authority_delete BEFORE DELETE ON authority BEGIN SELECT RAISE(ABORT,'immutable'); END;

-- Separate from personal signatures: delegated evidence never occupies a holder signing slot.
CREATE TABLE delegated_grants (id TEXT PRIMARY KEY,body TEXT NOT NULL,signature TEXT NOT NULL,hash TEXT NOT NULL UNIQUE,public_key TEXT NOT NULL);
CREATE TABLE delegated_controls (
 grant_id TEXT NOT NULL REFERENCES delegated_grants(id),sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 100),
 body TEXT NOT NULL,signature TEXT NOT NULL,hash TEXT NOT NULL UNIQUE,
 action TEXT NOT NULL CHECK(action IN ('enable','suspend','revoke')),issued_at INTEGER NOT NULL,
 PRIMARY KEY(grant_id,sequence)
);
CREATE TRIGGER delegated_control_order BEFORE INSERT ON delegated_controls BEGIN
 SELECT CASE WHEN NEW.sequence!=coalesce((SELECT max(sequence) FROM delegated_controls WHERE grant_id=NEW.grant_id),0)+1
 OR EXISTS(SELECT 1 FROM delegated_controls WHERE grant_id=NEW.grant_id AND action='revoke')
 THEN RAISE(ABORT,'control_order') END;
END;
CREATE TABLE delegated_receipts (
 packet TEXT NOT NULL REFERENCES packets(id),grant_id TEXT NOT NULL REFERENCES delegated_grants(id),control_hash TEXT NOT NULL REFERENCES delegated_controls(hash),
 contributor_hash TEXT NOT NULL,cipher TEXT NOT NULL,hash TEXT NOT NULL,created INTEGER NOT NULL,expires INTEGER NOT NULL,
 PRIMARY KEY(packet,grant_id,control_hash)
);
CREATE TRIGGER delegated_grants_update BEFORE UPDATE ON delegated_grants BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER delegated_grants_delete BEFORE DELETE ON delegated_grants BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER delegated_controls_update BEFORE UPDATE ON delegated_controls BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER delegated_controls_delete BEFORE DELETE ON delegated_controls BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER delegated_receipts_update BEFORE UPDATE ON delegated_receipts BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER delegated_receipts_delete BEFORE DELETE ON delegated_receipts BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TABLE head_requests (
 request_key TEXT PRIMARY KEY,pr INTEGER NOT NULL CHECK(pr>0),head TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','done','dispatched','manual')),attempt TEXT NOT NULL,
 lease_until INTEGER NOT NULL,created INTEGER NOT NULL,result TEXT NOT NULL
);
CREATE TABLE head_deliveries (id TEXT PRIMARY KEY,body_hash TEXT NOT NULL,request_key TEXT NOT NULL REFERENCES head_requests(request_key),created INTEGER NOT NULL);
CREATE TRIGGER head_deliveries_update BEFORE UPDATE ON head_deliveries BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER head_deliveries_delete BEFORE DELETE ON head_deliveries BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER head_request_identity BEFORE UPDATE ON head_requests WHEN NEW.request_key!=OLD.request_key OR NEW.pr!=OLD.pr OR NEW.head!=OLD.head OR NEW.created!=OLD.created
 BEGIN SELECT RAISE(ABORT,'immutable_identity'); END;
-- A new signature/hold/control is a new evaluation. Clear prior observations atomically.
-- Manual infrastructure failures remain manual; there is no automatic release of those states.
CREATE TRIGGER head_revision AFTER UPDATE OF revision ON state BEGIN
 UPDATE head_requests SET status='pending',attempt='',lease_until=0,result='' WHERE status IN ('done','running');
END;
