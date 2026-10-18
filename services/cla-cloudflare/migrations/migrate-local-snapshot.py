"""OFFLINE ONLY: migrate a canonical, disposable local snapshot into v2.
No live D1 access and no deployment. Refuse unknown schemas or pre-existing v2.
The original snapshot file is copied first and never opened for writing.
Actual activation requires a separately approved consistent cutover and evidence verification.
"""

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBJECTS = "authority_update authority_delete oauth_expiry packets_subject_digest authority agreements state sessions oauth_states packets intents signatures eligibility_blocks audit targets outbox outbox_due deliveries published_checks _guards revision_outbox delivery_outbox agreements_update agreements_delete packets_update packets_delete signatures_update signatures_delete blocks_update blocks_delete audit_update audit_delete publication_attempts publication_unresolved reconciliation_progress refresh_requests agreement_documents documents_update documents_delete".split()


def namespace(s, version):
    return re.sub(
        r"\b[A-Za-z_][A-Za-z_0-9]*\b",
        lambda m: "cla_native_" + version + "_" + m[0] if m[0] in OBJECTS else m[0],
        s,
    )


def ddl(version):
    files = (
        [ROOT / "migrations/v1-base.sql", ROOT / "migrations/v1-native-schema.sql"]
        if version == "v1"
        else [ROOT / "src/base.sql", ROOT / "src/native/schema.sql"]
    )
    return "\n".join(namespace(f.read_text(), version) for f in files)


def schema(db, version):
    return db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name LIKE ? AND sql IS NOT NULL ORDER BY type,name",
        ("cla_native_" + version + "_%",),
    ).fetchall()


def rows(db, table):
    # Snapshot comparison includes every column; blobs are encoded without disclosure.
    def encode(v):
        return {"blob": v.hex()} if isinstance(v, bytes) else v

    return sorted(
        json.dumps([encode(v) for v in row], separators=(",", ":"), ensure_ascii=False)
        for row in db.execute("SELECT rowid,* FROM " + table)
    )


def digest(values):
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def migrate(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or output.exists():
        raise ValueError("output must be a new separate local file")
    if not source.is_file():
        raise ValueError("input snapshot missing")
    # An explicit SQLite backup produces a coherent file, not an unsafe copy of a WAL main file.
    old = sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True)
    expected = sqlite3.connect(":memory:")
    expected.executescript(ddl("v1"))
    if schema(old, "v1") != schema(expected, "v1"):
        raise ValueError("unknown v1 schema: manual mapping required")
    if schema(old, "v2"):
        raise ValueError("v2 already exists")
    if (
        old.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or old.execute("PRAGMA foreign_key_check").fetchall()
    ):
        raise ValueError("source integrity failed")
    # Unknown triggers may mutate v1 when v2 is inserted; refuse all external triggers.
    allowed = {r[1] for r in schema(expected, "v1") if r[0] == "trigger"}
    if (
        set(r[0] for r in old.execute("SELECT name FROM sqlite_schema WHERE type='trigger'"))
        - allowed
    ):
        raise ValueError("unknown triggers require review")
    tables = [r[1] for r in schema(expected, "v1") if r[0] == "table"]
    before = {t: rows(old, t) for t in tables}
    new = sqlite3.connect(output)
    old.backup(new)
    old.close()
    new.execute("PRAGMA foreign_keys=ON")
    model = sqlite3.connect(":memory:")
    model.executescript(ddl("v2"))
    definitions = schema(model, "v2")
    try:
        new.execute("BEGIN IMMEDIATE")
        new.execute("PRAGMA defer_foreign_keys=ON")
        for typ, name, table, sql in definitions:
            if typ == "table":
                new.execute(sql)
        for t in tables:
            target = t.replace("cla_native_v1_", "cla_native_v2_", 1)
            columns = [row[1] for row in new.execute("PRAGMA table_info(" + t + ")")]
            names = ",".join(["rowid"] + columns)
            new.execute("INSERT INTO " + target + " (" + names + ") SELECT " + names + " FROM " + t)
        for typ, name, table, sql in definitions:
            if typ != "table":
                new.execute(sql)
        for t in tables:
            if (
                rows(new, t) != before[t]
                or rows(new, t.replace("cla_native_v1_", "cla_native_v2_", 1)) != before[t]
            ):
                raise ValueError("row preservation mismatch")
        if new.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("foreign key check failed")
        # No eligibility is asserted by migration. LiveService still decrypts/checks evidence.
        new.commit()
        return {
            "offline_only": True,
            "source_opened_read_only": True,
            "v1_preserved": True,
            "v2_exact_row_copy": True,
            "legal_or_cryptographic_validation": False,
            "deployment_authorized": False,
            "tables": {t: {"rows": len(before[t]), "sha256": digest(before[t])} for t in tables},
        }
    except Exception:
        new.rollback()
        raise
    finally:
        new.close()
        expected.close()
        model.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output")
    a = parser.parse_args()
    print(json.dumps(migrate(a.source, a.output), indent=2))
