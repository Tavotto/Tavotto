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
