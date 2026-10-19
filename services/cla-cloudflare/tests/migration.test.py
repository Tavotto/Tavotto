import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "migration", Path(__file__).resolve().parents[1] / "migrations/migrate-local-snapshot.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Migration(unittest.TestCase):
    def test_exact_history_and_rowid(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "old.sqlite", Path(d) / "new.sqlite"
            db = sqlite3.connect(old)
            db.executescript(m.ddl("v1"))
            db.execute("INSERT INTO cla_native_v1_authority VALUES(1,1)")
            db.execute(
                "INSERT INTO cla_native_v1_agreements VALUES('individual','1.0',?)", ("a" * 64,)
            )
            db.execute(
                "INSERT INTO cla_native_v1_packets VALUES('old',2,'individual','1.0',?,1,2)",
                ("a" * 64,),
            )
            db.execute(
                "INSERT INTO cla_native_v1_signatures VALUES('old','contributor',2,'synthetic-marker','synthetic-hash',NULL,1)"
            )
            db.execute("INSERT INTO cla_native_v1_targets VALUES('pr:707:1','synthetic')")
            db.execute(
                "INSERT INTO cla_native_v1_publication_attempts(rowid,id,target_key,head_sha,revision,expires,phase,created,updated,conclusion,proof_text) VALUES(77,'pub','pr:707:1',?,0,120,'confirmed',0,0,'success','SYNTHETIC')",
                ("a" * 40,),
            )
            db.execute("INSERT INTO cla_native_v1_reconciliation_progress VALUES('pr:707:1',76,77)")
            db.commit()
            db.close()
            original = old.read_bytes()
            report = m.migrate(old, new)
            self.assertTrue(report["v1_preserved"])
            self.assertTrue(report["v2_exact_source_row_copy"])
            self.assertEqual(
                report["target_column_defaults"], {"publication_attempts.send_until": 0}
            )
            self.assertEqual(old.read_bytes(), original)
            db = sqlite3.connect(new)
            self.assertEqual(
                db.execute("SELECT send_until FROM cla_native_v2_publication_attempts").fetchone(),
                (0,),
            )
            for table in [
                "delegated_grants",
                "delegated_controls",
                "delegated_receipts",
                "head_requests",
                "head_deliveries",
            ]:
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM sqlite_schema WHERE type='table' AND name=?",
                        ("cla_native_v2_" + table,),
                    ).fetchone(),
                    (1,),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM sqlite_schema WHERE type='table' AND name=?", (table,)
                    ).fetchone(),
                    (0,),
                )
            self.assertEqual(
                db.execute("SELECT rowid FROM cla_native_v2_publication_attempts").fetchone()[0], 77
            )
            self.assertEqual(
                db.execute(
                    "SELECT cursor,horizon FROM cla_native_v2_reconciliation_progress"
                ).fetchone(),
                (76, 77),
            )
            for v in ["v1", "v2"]:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE cla_native_{v}_packets SET valid_until=999 WHERE id='old'")
                db.rollback()
            self.assertEqual(
                db.execute("SELECT count(*) FROM cla_native_v2_signatures").fetchone()[0], 1
            )
            db.close()

    def test_rejects_corrupted_source_column(self):
        connect = sqlite3.connect

        class CorruptCopy(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                result = super().execute(sql, parameters)
                if sql.startswith("INSERT INTO cla_native_v2_authority "):
                    super().execute("UPDATE cla_native_v2_authority SET holder=999")
                return result

        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "old.sqlite", Path(d) / "new.sqlite"
            db = connect(old)
            db.executescript(m.ddl("v1"))
            db.execute("INSERT INTO cla_native_v1_authority VALUES(1,1)")
            db.commit()
            db.close()
            original = old.read_bytes()

            def corrupted_connect(*args, **kwargs):
                return connect(*args, **kwargs, factory=CorruptCopy)

            with patch.object(m.sqlite3, "connect", side_effect=corrupted_connect):
                with self.assertRaisesRegex(ValueError, "row preservation mismatch"):
                    m.migrate(old, new)
            self.assertEqual(old.read_bytes(), original)

    def test_unknown_schema_refused_without_output(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "old.sqlite", Path(d) / "new.sqlite"
            db = sqlite3.connect(old)
            db.executescript(m.ddl("v1"))
            db.execute("ALTER TABLE cla_native_v1_packets ADD COLUMN mystery TEXT")
            db.commit()
            db.close()
            with self.assertRaisesRegex(ValueError, "unknown v1 schema"):
                m.migrate(old, new)
            self.assertFalse(new.exists())

    def test_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / "old.sqlite", Path(d) / "new.sqlite"
            old.touch()
            new.touch()
            with self.assertRaises(ValueError):
                m.migrate(old, new)


if __name__ == "__main__":
    unittest.main()
