-- Local candidate extension; the frozen deployed migration is unchanged.
CREATE TABLE publication_attempts (
 id TEXT PRIMARY KEY, target_key TEXT NOT NULL REFERENCES targets(target_key),
 head_sha TEXT NOT NULL, revision INTEGER NOT NULL, expires INTEGER NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('issued','confirmed','unknown','reconcile','resolved','manual')),
 run_id INTEGER, created INTEGER NOT NULL, updated INTEGER NOT NULL,
 conclusion TEXT NOT NULL CHECK(conclusion IN ('success','failure')), proof_text TEXT NOT NULL,
 observation TEXT NOT NULL DEFAULT '',
 reconcile_due INTEGER DEFAULT 0, reconcile_attempts INTEGER NOT NULL DEFAULT 0 CHECK(reconcile_attempts BETWEEN 0 AND 3),
 observed_generation INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX publication_unresolved ON publication_attempts(target_key) WHERE phase IN ('issued','unknown');
CREATE TABLE reconciliation_progress (
 target_key TEXT PRIMARY KEY REFERENCES targets(target_key),
 cursor INTEGER NOT NULL DEFAULT 0 CHECK(cursor>=0), horizon INTEGER NOT NULL DEFAULT 0 CHECK(horizon>=0)
);
CREATE TABLE agreement_documents (
 digest TEXT PRIMARY KEY REFERENCES agreements(digest), kind TEXT NOT NULL, version TEXT NOT NULL,
 source_sha TEXT NOT NULL, content TEXT NOT NULL
);
CREATE TRIGGER documents_update BEFORE UPDATE ON agreement_documents BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER documents_delete BEFORE DELETE ON agreement_documents BEGIN SELECT RAISE(ABORT,'immutable'); END;
