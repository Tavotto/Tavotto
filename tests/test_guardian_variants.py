"""Static generation tests; candidate scripts are deliberately never executed."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import guardian_variants as variants  # noqa: E402


class Variants(unittest.TestCase):
    def test_all_families_are_deterministic(self):
        for template in variants.TEMPLATES:
            with self.subTest(template=template):
                a, af = variants.candidate(template, 17)
                b, bf = variants.candidate(template, 17)
                self.assertEqual((a, af), (b, bf))
                self.assertEqual(a["execution_status"], "not_run")
                self.assertEqual(a["review"]["status"], "unreviewed")
                self.assertEqual(a["review"]["content_sha256"], variants.cg.canonical(a["files"]))

    def test_seed_changes_reviewable_content(self):
        a, _ = variants.candidate("entry_argparse", 0)
        b, _ = variants.candidate("entry_argparse", 1)
        self.assertNotEqual(a["files"], b["files"])
        self.assertNotEqual(a["oracle"]["line_y"], b["oracle"]["line_y"])

    def test_static_compile_never_executes(self):
        result = variants.static_check({"script.py": b'raise RuntimeError("would execute")\n'})
        self.assertEqual(result["runtime"], "not_run")

    def test_helper_and_data_graph_preserved(self):
        row, files = variants.candidate("helper_csv", 12)
        self.assertIn("data.csv", files)
        self.assertIn("_cg_data_0000000c.py", files)
        self.assertEqual(row["parent"]["case"], "shape_relative_csv")

    def test_bitmap_does_not_claim_artist_editability(self):
        row, _ = variants.candidate("bitmap_only", 2)
        self.assertIn("no Artist", row["oracle"]["edit_scope"])

    def test_provider_observation_does_not_authorize_install(self):
        from tavotto.engine import depresolve
        row, _ = variants.candidate("multi_provider", 1)
        self.assertFalse(row["oracle"]["installation_authorized"])
        self.assertEqual(row["oracle"]["provider_candidates"]["cv2"], list(depresolve.distribution_alternatives("cv2")))
        self.assertIn("not exercised", row["oracle"]["unverified"])

    def test_invalid_fixture_and_path_rejected(self):
        for files in ({}, {"../bad.py": b'pass'}, {"script.py": b'def broken('}):
            with self.subTest(files=files), self.assertRaises((variants.cg.EvidenceError, SyntaxError)):
                variants.static_check(files)

    def test_write_results_only_to_external_unique_directory(self):
        with tempfile.TemporaryDirectory(prefix="cg-variants-test-") as tmp:
            rows = variants.generate(42, Path(tmp))
            self.assertEqual(len(rows), len(variants.TEMPLATES))
            self.assertTrue(all((Path(tmp) / r["candidate_id"] / "candidate.json").is_file() for r in rows))
            self.assertTrue(all(variants.validate_candidate(Path(tmp) / r["candidate_id"]) == r for r in rows))
            with self.assertRaises(FileExistsError):
                variants.generate(42, Path(tmp))

    def test_oracle_change_invalidates_review_identity(self):
        with tempfile.TemporaryDirectory(prefix="cg-variants-identity-") as tmp:
            row = variants.generate(1, Path(tmp))[0]
            root = Path(tmp) / row["candidate_id"]
            path = root / "candidate.json"
            row["oracle"] = {"invented_truth": True}
            path.write_text(json.dumps(row))
            with self.assertRaises(variants.cg.EvidenceError):
                variants.validate_candidate(root)

    def test_seed_range_checked(self):
        for seed in (-1, 2**32, True):
            with self.assertRaises(variants.cg.EvidenceError):
                variants.candidate("entry_show", seed)


if __name__ == "__main__":
    unittest.main()
