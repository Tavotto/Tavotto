PRAGMA foreign_keys = ON;
CREATE TABLE authority (id INTEGER PRIMARY KEY CHECK(id=1), holder INTEGER NOT NULL CHECK(holder>0));
CREATE TABLE agreements (
 kind TEXT NOT NULL CHECK(kind IN ('individual','corporate')), version TEXT NOT NULL,
 digest TEXT NOT NULL UNIQUE, PRIMARY KEY(kind,version)
);
CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision>=0));
INSERT INTO state VALUES (1,0);
CREATE TABLE sessions (
 token_hash TEXT PRIMARY KEY, actor INTEGER NOT NULL CHECK(actor>0), login TEXT NOT NULL,
 csrf_hash TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE oauth_states (
 state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, verifier_cipher TEXT NOT NULL,
 expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0,1))
);
CREATE TABLE packets (
 id TEXT PRIMARY KEY, subject INTEGER NOT NULL CHECK(subject>0), kind TEXT NOT NULL,
 version TEXT NOT NULL, digest TEXT NOT NULL REFERENCES agreements(digest),
 created INTEGER NOT NULL, valid_until INTEGER NOT NULL CHECK(valid_until>created),
 UNIQUE(subject,digest), FOREIGN KEY(kind,version) REFERENCES agreements(kind,version)
);
CREATE TABLE intents (
 token_hash TEXT PRIMARY KEY, session_hash TEXT NOT NULL REFERENCES sessions(token_hash),
 packet TEXT NOT NULL REFERENCES packets(id), role TEXT NOT NULL CHECK(role IN ('contributor','holder','hold')),
 digest TEXT NOT NULL REFERENCES agreements(digest), expires INTEGER NOT NULL,
 used INTEGER NOT NULL DEFAULT 0 CHECK(used IN (0,1))
);
CREATE TABLE signatures (
 packet TEXT NOT NULL REFERENCES packets(id), role TEXT NOT NULL CHECK(role IN ('contributor','holder')),
 actor INTEGER NOT NULL CHECK(actor>0), evidence_cipher TEXT NOT NULL, evidence_hash TEXT NOT NULL,
 file_key TEXT, created INTEGER NOT NULL, PRIMARY KEY(packet,role)
);
CREATE TABLE eligibility_blocks (
 packet TEXT PRIMARY KEY REFERENCES packets(id), actor INTEGER NOT NULL CHECK(actor>0),
 created INTEGER NOT NULL, reason TEXT NOT NULL CHECK(reason='party_requested_review')
);
CREATE TABLE audit (
 id INTEGER PRIMARY KEY, packet TEXT NOT NULL, actor INTEGER NOT NULL,
 event TEXT NOT NULL CHECK(event IN ('signed:contributor','signed:holder','hold')), created INTEGER NOT NULL
);
CREATE TABLE targets (target_key TEXT PRIMARY KEY, target_json TEXT NOT NULL);
CREATE TABLE outbox (
 target_key TEXT PRIMARY KEY REFERENCES targets(target_key), wanted_revision INTEGER NOT NULL,
 generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>0),
 due INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 lease TEXT NOT NULL DEFAULT '', lease_until INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX outbox_due ON outbox(due,lease_until,attempts);
CREATE TABLE deliveries (
 id TEXT PRIMARY KEY, body_sha TEXT NOT NULL, target_key TEXT NOT NULL REFERENCES targets(target_key), created INTEGER NOT NULL
);
CREATE TABLE published_checks (
 target_key TEXT PRIMARY KEY REFERENCES targets(target_key), run_id INTEGER NOT NULL,
 head_sha TEXT NOT NULL, conclusion TEXT NOT NULL CHECK(conclusion IN ('success','failure')),
 revision INTEGER NOT NULL, created INTEGER NOT NULL
);
-- CHECK deliberately turns a zero-row CAS into a SQL failure inside the SAME batch.
CREATE TABLE _guards (id TEXT PRIMARY KEY, ok INTEGER NOT NULL CHECK(ok=1));
CREATE TRIGGER revision_outbox AFTER UPDATE OF revision ON state BEGIN
 INSERT INTO outbox(target_key,wanted_revision,due)
 SELECT target_key,NEW.revision,0 FROM targets WHERE 1
 ON CONFLICT(target_key) DO UPDATE SET wanted_revision=NEW.revision,generation=outbox.generation+1,due=0,attempts=0;
END;
CREATE TRIGGER delivery_outbox AFTER INSERT ON deliveries BEGIN
 INSERT INTO outbox(target_key,wanted_revision,due) VALUES(NEW.target_key,(SELECT revision FROM state WHERE id=1),0)
 ON CONFLICT(target_key) DO UPDATE SET wanted_revision=excluded.wanted_revision,generation=outbox.generation+1,due=0,attempts=0;
END;
CREATE TRIGGER agreements_update BEFORE UPDATE ON agreements BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER agreements_delete BEFORE DELETE ON agreements BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER packets_update BEFORE UPDATE ON packets BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER packets_delete BEFORE DELETE ON packets BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER signatures_update BEFORE UPDATE ON signatures BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER signatures_delete BEFORE DELETE ON signatures BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER blocks_update BEFORE UPDATE ON eligibility_blocks BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER blocks_delete BEFORE DELETE ON eligibility_blocks BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER audit_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER audit_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'immutable'); END;
