"""Failure-family records (tests/compat_guardian/families) and their minimized reproductions."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_guardian as cg  # noqa: E402

FAMILIES = cg.ROOT / "tests/compat_guardian/families"
SCHEMA = cg.ROOT / "tests/compat_guardian/families.schema.json"
DRIVER = Path(cg.HERE) / "compat_driver.py"


def records() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FAMILIES.glob("*.json"))]


class Records(unittest.TestCase):
    def test_there_are_records_for_the_two_audited_gaps(self):
        self.assertEqual(
            [r["id"] for r in records()],
            ["G1-show-close-zero-figures", "G2-same-path-multi-figure"],
        )

    def test_records_follow_the_schema_and_say_what_is_not_done_yet(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for record in records():
            cg.schema_check(record, schema)
            self.assertFalse(record["in_corpus"], "not in the corpus until a maintainer adds it")
            self.assertFalse(record["promoted"])
            self.assertIsNone(record["fix_pr"])
            self.assertTrue(record["environments"]["unverified"])
            self.assertIn(record["category"], __import__("compat_guardian_taxonomy").CATEGORIES)

    def test_repro_scripts_exist_and_match_their_recorded_hash(self):
        for record in records():
            path = cg.ROOT / record["repro"]["script"]
            self.assertTrue(path.is_file(), path)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), record["repro"]["sha256"]
            )
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            self.assertLess(
                record["repro"]["after"]["statements"], record["repro"]["before"]["statements"]
            )
            self.assertEqual(
                record["repro"]["reproducibility"]["confirmed"],
                record["repro"]["reproducibility"]["total"],
            )

    def test_every_listed_regression_test_exists(self):
        for record in records():
            for ref in record["key_regression_tests"]:
                path, _, name = ref.partition("::")
                text = (cg.ROOT / path).read_text(encoding="utf-8")
                self.assertIn(name.split("::")[-1], text, ref)


def _project(tmp: str, record: dict) -> Path:
    project = Path(tmp) / "project"
    project.mkdir()
    (project / "repro.py").write_bytes((cg.ROOT / record["repro"]["script"]).read_bytes())
    return project


def _native(project: Path, entry: str) -> dict:
    request = project.parent / "req.json"
    request.write_text(
        json.dumps({"project": str(project), "script": str(project / "repro.py"), "entry": entry}),
        encoding="utf-8",
    )
    done = subprocess.run(
        [sys.executable, str(DRIVER), "--mode", "native_ref", "--request", str(request)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def _tavotto(project: Path) -> dict:
    code = "import json, sys; from pathlib import Path; import compat_explore as ex; print(json.dumps(ex.tavotto_observe(Path(sys.argv[1]), 'repro.py')))"
    work = project.parent / "iso"
    work.mkdir()
    done = subprocess.run(
        [sys.executable, "-c", code, str(project)],
        env=cg.isolated_env(work),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        stdin=subprocess.DEVNULL,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


class RealRepro(unittest.TestCase):
    """The minimal scripts still show the gaps on the current product code.

    When a gap is fixed these go red on purpose: update the family record (fix_pr) and turn
    the repro into a normal regression case at that point.
    """

    def run_family(self, family_id):
        record = next(r for r in records() if r["id"] == family_id)
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(tmp, record)
            native = _native(project, record["repro"]["entry"])
            tavotto = _tavotto(project)
        return ex.compare("axis_changing", native, tavotto, None), native, tavotto

    def test_g1_minimal_repro_still_shows_the_gap(self):
        verdict, native, tavotto = self.run_family("G1-show-close-zero-figures")
        self.assertEqual((native["figures_shown"], native["savefig_calls"]), (1, 0))
        self.assertEqual(tavotto["error"]["code"], "script_no_figure")
        self.assertEqual([d["kind"] for d in verdict["differences"]], ["zero_figures"])

    def test_g2_minimal_repro_still_shows_the_gap(self):
        verdict, native, tavotto = self.run_family("G2-same-path-multi-figure")
        self.assertEqual(native["same_path_distinct_figures"], ["shape_oo.pdf"])
        self.assertEqual(
            [d["kind"] for d in verdict["differences"]], ["same_path_content_mismatch"]
        )
        self.assertIn("Tavotto kept ['cg_pass_0']", verdict["differences"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
