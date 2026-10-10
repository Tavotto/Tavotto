"""Private SQLite authority: atomic consent, append-only encrypted evidence, durable jobs."""

import contextlib
import hashlib
import os
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet

from .config import canonical

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS agreements (
 kind TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL, body BLOB NOT NULL,
 PRIMARY KEY(kind, version), UNIQUE(digest));
CREATE TABLE IF NOT EXISTS packets (
 id TEXT PRIMARY KEY, subject INTEGER NOT NULL, digest TEXT NOT NULL REFERENCES agreements(digest),
 created INTEGER NOT NULL, UNIQUE(subject,digest));
CREATE TABLE IF NOT EXISTS signatures (
 packet TEXT NOT NULL REFERENCES packets(id), role TEXT NOT NULL CHECK(role IN ('contributor','holder')),
 actor INTEGER NOT NULL, evidence BLOB NOT NULL, evidence_digest TEXT NOT NULL,
 created INTEGER NOT NULL, PRIMARY KEY(packet,role));
CREATE TABLE IF NOT EXISTS holds (
 packet TEXT PRIMARY KEY REFERENCES packets(id), reason TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, packet TEXT NOT NULL, actor INTEGER NOT NULL,
 event TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 token TEXT PRIMARY KEY, actor INTEGER NOT NULL, login TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS oauth (
 state TEXT PRIMARY KEY, browser TEXT NOT NULL, verifier TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS intents (
 nonce TEXT PRIMARY KEY, session TEXT NOT NULL, packet TEXT NOT NULL, role TEXT NOT NULL,
 digest TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
 id TEXT PRIMARY KEY, digest TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, pr INTEGER NOT NULL UNIQUE, due INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS prs (
 number INTEGER PRIMARY KEY, sha TEXT NOT NULL, checked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS groups (
 head TEXT PRIMARY KEY, base TEXT NOT NULL, base_ref TEXT NOT NULL, received INTEGER NOT NULL,
 due INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS final_checks (sha TEXT PRIMARY KEY, run_id INTEGER NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS checks (
 sha TEXT PRIMARY KEY, run_id INTEGER NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO state VALUES ('revision', 0);
"""
for _table in ("agreements", "packets", "signatures", "audit"):
    for _op in ("UPDATE", "DELETE"):
        SCHEMA += f"CREATE TRIGGER IF NOT EXISTS {_table}_{_op} BEFORE {_op} ON {_table} BEGIN SELECT RAISE(ABORT, 'immutable evidence'); END;\n"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, config):
        self.config = config
        self.cipher = Fernet(config.evidence_key)
        config.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if config.data_dir.stat().st_mode & 0o077:
            raise ValueError("private directory must be mode 0700")
        self.path = config.data_dir / "cla.sqlite3"
        # umask also protects WAL/SHM created by every connection/process.
        os.umask(0o077)
        with self.connection() as db:
            db.executescript(SCHEMA)
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def register(self, policy):
        with self.transaction() as db:
            for kind, ag in policy["agreements"].items():
                raw = (self.config.root / ag["path"]).read_bytes()
                old = db.execute(
                    "SELECT digest,body FROM agreements WHERE kind=? AND version=?",
                    (kind, ag["version"]),
                ).fetchone()
                if old and (old["digest"] != ag["sha256"] or old["body"] != raw):
                    raise ValueError("existing version cannot be rebound")
                db.execute(
                    "INSERT OR IGNORE INTO agreements VALUES (?,?,?,?)",
                    (kind, ag["version"], ag["sha256"], raw),
                )

    def oauth_start(self):
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.transaction() as db:
            db.execute("DELETE FROM oauth WHERE expires<?", (int(time.time()),))
            db.execute(
                "INSERT INTO oauth VALUES (?,?,?,?)",
                (digest(state), digest(browser), verifier, int(time.time()) + 300),
            )
        return state, browser, verifier

    def oauth_consume(self, state, browser):
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM oauth WHERE state=? AND browser=? AND expires>=?",
                (digest(state), digest(browser), int(time.time())),
            ).fetchone()
            if not row:
                raise PermissionError("expired or invalid login")
            db.execute("DELETE FROM oauth WHERE state=?", (digest(state),))
            return row["verifier"]

    def session(self, token):
        with self.connection() as db:
            return db.execute(
                "SELECT * FROM sessions WHERE token=? AND expires>=?",
                (digest(token), int(time.time())),
            ).fetchone()

    def login(self, actor, login):
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (int(time.time()),))
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?)",
                (digest(token), actor, login, int(time.time()) + 900),
            )
        return token

    def packet(self, subject, agreement_digest):
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO packets VALUES (?,?,?,?)",
                (secrets.token_urlsafe(24), subject, agreement_digest, int(time.time())),
            )
            return dict(
                db.execute(
                    "SELECT * FROM packets WHERE subject=? AND digest=?",
                    (subject, agreement_digest),
                ).fetchone()
            )

    def get_packet(self, packet):
        with self.connection() as db:
            row = db.execute(
                "SELECT p.*,a.version,a.body,a.kind FROM packets p JOIN agreements a USING(digest) WHERE p.id=?",
                (packet,),
            ).fetchone()
            if not row:
                raise KeyError("packet not found")
            result = dict(row)
            result["signatures"] = [
                dict(r)
                for r in db.execute(
                    "SELECT role,actor,created,evidence,evidence_digest FROM signatures WHERE packet=?",
                    (packet,),
                )
            ]
            result["held"] = bool(
                db.execute("SELECT 1 FROM holds WHERE packet=?", (packet,)).fetchone()
            )
            return result

    def intent(self, session, packet, role, agreement_digest):
        nonce = secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute("DELETE FROM intents WHERE expires<?", (int(time.time()),))
            db.execute(
                "INSERT INTO intents VALUES (?,?,?,?,?,?)",
                (
                    digest(nonce),
                    session["token"],
                    packet,
                    role,
                    agreement_digest,
                    int(time.time()) + 300,
                ),
            )
        return nonce

    @staticmethod
    def enqueue(db, pr):
        db.execute(
            "INSERT INTO jobs(pr,due) VALUES (?,?) ON CONFLICT(pr) DO UPDATE SET due=excluded.due",
            (pr, int(time.time())),
        )

    @staticmethod
    def changed(db):
        db.execute("UPDATE state SET value=value+1 WHERE key='revision'")
        db.execute("UPDATE groups SET due=? WHERE active=1", (int(time.time()),))
        for pr in db.execute("SELECT number FROM prs").fetchall():
            Store.enqueue(db, pr[0])

    def sign(self, session, packet, nonce, evidence):
        now = int(time.time())
        with self.transaction() as db:
            intent = db.execute(
                "SELECT * FROM intents WHERE nonce=? AND session=? AND packet=? AND expires>=?",
                (digest(nonce), session["token"], packet, now),
            ).fetchone()
            if not intent:
                raise PermissionError("signing intent expired or already used")
            p = db.execute("SELECT * FROM packets WHERE id=?", (packet,)).fetchone()
            role = intent["role"]
            actor = session["actor"]
            if p["digest"] != intent["digest"] or actor != (
                p["subject"] if role == "contributor" else self.config.holder_id
            ):
                raise PermissionError("identity or agreement changed")
            if db.execute("SELECT 1 FROM holds WHERE packet=?", (packet,)).fetchone():
                raise PermissionError("packet requires manual resolution")
            if (
                role == "holder"
                and not db.execute(
                    "SELECT 1 FROM signatures WHERE packet=? AND role='contributor'", (packet,)
                ).fetchone()
            ):
                raise PermissionError("contributor must sign first")
            if db.execute(
                "SELECT 1 FROM signatures WHERE packet=? AND role=?", (packet, role)
            ).fetchone():
                raise PermissionError("already signed")
            evidence.update(
                {
                    "packet": packet,
                    "github_id": actor,
                    "github_login_at_signing": session["login"],
                    "role": role,
                    "agreement_sha256": p["digest"],
                    "signed_at_unix": now,
                }
            )
            raw = canonical(evidence).encode()
            db.execute(
                "INSERT INTO signatures VALUES (?,?,?,?,?,?)",
                (
                    packet,
                    role,
                    actor,
                    self.cipher.encrypt(raw),
                    hashlib.sha256(raw).hexdigest(),
                    now,
                ),
            )
            db.execute("DELETE FROM intents WHERE nonce=?", (digest(nonce),))
            db.execute(
                "INSERT INTO audit(packet,actor,event,created) VALUES (?,?,?,?)",
                (packet, actor, "signed:" + role, now),
            )
            self.changed(db)

    def hold(self, session, packet, nonce):
        with self.transaction() as db:
            intent = db.execute(
                "SELECT 1 FROM intents WHERE nonce=? AND session=? AND packet=? AND role='hold' AND expires>=?",
                (digest(nonce), session["token"], packet, int(time.time())),
            ).fetchone()
            p = db.execute("SELECT subject FROM packets WHERE id=?", (packet,)).fetchone()
            if not intent or not p or session["actor"] not in (p["subject"], self.config.holder_id):
                raise PermissionError("invalid hold request")
            db.execute(
                "INSERT OR IGNORE INTO holds VALUES (?,?,?)",
                (packet, "party_requested_review", int(time.time())),
            )
            db.execute("DELETE FROM intents WHERE nonce=?", (digest(nonce),))
            db.execute(
                "INSERT INTO audit(packet,actor,event,created) VALUES (?,?,?,?)",
                (packet, session["actor"], "hold", int(time.time())),
            )
            self.changed(db)

    def qualified(self, subject, agreement_digest):
        with self.connection() as db:
            packet = db.execute(
                "SELECT id FROM packets WHERE subject=? AND digest=? AND NOT EXISTS (SELECT 1 FROM holds WHERE holds.packet=packets.id)",
                (subject, agreement_digest),
            ).fetchone()
            if not packet:
                return False
            rows = db.execute("SELECT * FROM signatures WHERE packet=?", (packet[0],)).fetchall()
            if {(r["role"], r["actor"]) for r in rows} != {
                ("contributor", subject),
                ("holder", self.config.holder_id),
            }:
                return False
            # Missing/corrupt/undecryptable evidence cannot qualify, even if SQL flags exist.
            for row in rows:
                raw = self.cipher.decrypt(row["evidence"])
                if hashlib.sha256(raw).hexdigest() != row["evidence_digest"]:
                    return False
            return True

    def webhook(self, delivery, raw_digest, pr):
        with self.transaction() as db:
            old = db.execute("SELECT digest FROM deliveries WHERE id=?", (delivery,)).fetchone()
            if old:
                if old[0] != raw_digest:
                    raise PermissionError("delivery identifier reused with different body")
                return False
            db.execute(
                "INSERT INTO deliveries VALUES (?,?,?)", (delivery, raw_digest, int(time.time()))
            )
            self.enqueue(db, pr)
            return True

    def group_webhook(self, delivery, raw_digest, head, base, base_ref):
        with self.transaction() as db:
            old = db.execute("SELECT digest FROM deliveries WHERE id=?", (delivery,)).fetchone()
            if old:
                if old[0] != raw_digest:
                    raise PermissionError("delivery identifier reused")
                return False
            existing = db.execute(
                "SELECT base,base_ref FROM groups WHERE head=?", (head,)
            ).fetchone()
            if existing and tuple(existing) != (base, base_ref):
                raise PermissionError("candidate SHA cannot be rebound")
            now = int(time.time())
            db.execute("INSERT INTO deliveries VALUES (?,?,?)", (delivery, raw_digest, now))
            db.execute(
                "INSERT INTO groups(head,base,base_ref,received,due) VALUES (?,?,?,?,?) ON CONFLICT(head) DO UPDATE SET due=excluded.due,received=excluded.received,attempts=0,active=1",
                (head, base, base_ref, now, now),
            )
            return True
