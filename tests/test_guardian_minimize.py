"""Mechanism tests with explicit synthetic oracles; NOT AC-06 product evidence."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import guardian_minimize as gm  # noqa: E402


class MinimizeContract(unittest.TestCase):
    def proof(self):
        return {"status":"failed", "native_correct":True, "comparable":True,
                "native_executions":1, "tavotto_executions":1, "environment":"synthetic",
                "signature":{"stage":"semantic", "codes":["wrong-value"]},
                "evidence_sha256":"evidence", "source_sha256":"source"}

    def test_missing_package_and_syntax_cannot_preserve_defect(self):
        original = self.proof()
        for status in ("environment_unavailable", "invalid_fixture"):
            bad = {**original, "status":status, "native_correct":False}
            self.assertFalse(gm.accepts(original, bad))

    def test_same_traceback_different_stage_not_same_failure(self):
        original = self.proof()
        changed = copy.deepcopy(original)
        changed["signature"]["stage"] = "execute"
        self.assertFalse(gm.accepts(original, changed))

    def test_environment_change_and_zero_execution_rejected(self):
        for change in ({"environment":"other"}, {"native_executions":0}, {"tavotto_executions":0}, {"evidence_sha256":None}):
            self.assertFalse(gm.accepts(self.proof(), {**self.proof(), **change}))

    def test_every_accepted_reduction_calls_both_leg_oracle(self):
        files = {"script.py":b'x=1\ny=2\n', "data/input.csv":b'x,y\n1,2\n'}
        candidates = [{**files, "script.py":code} for _,code in gm.deletion_candidates(files["script.py"])]
        approved = {gm.cg.canonical({k:gm.cg.digest(v) for k,v in c.items()}) for c in candidates}
        calls = []
        def oracle(candidate, remaining):
            calls.append(candidate)
            return self.proof()
        result = gm.minimize(files, "script.py", oracle, approved, attempts=1, seconds=1)
        self.assertEqual(result["status"], "minimized")
        self.assertEqual(len(calls), 2)  # original + real-oracle contract for one candidate
        self.assertEqual(result["attempts"], 1)
        self.assertTrue(result["file_relationships_preserved"])
        self.assertIn("data/input.csv", calls[-1])

    def test_unreviewed_candidate_never_reaches_oracle(self):
        calls = []
        def oracle(candidate, remaining):
            calls.append(candidate)
            return self.proof()
        result = gm.minimize({"script.py":b'x=1\ny=2\n'}, "script.py", oracle, set(), attempts=1, seconds=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "not_reduced")
        self.assertTrue(result["pending_review"])

    def test_traceback_alone_cannot_cluster_roots(self):
        rows = [{**self.proof(), "traceback":"same"}, {**self.proof(), "traceback":"same"}]
        result = gm.cluster(rows)
        self.assertEqual(result["confirmed"], {})
        self.assertEqual(len(result["hypotheses"]), 2)


if __name__ == "__main__":
    unittest.main()
