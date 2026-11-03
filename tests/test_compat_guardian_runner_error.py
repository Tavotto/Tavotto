"""Runner failures remain publishable unknown observations, not product classifications."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import compat_guardian as guardian  # noqa: E402


class GuardianRunnerErrorTests(unittest.TestCase):
    def execute_result(self, classification, detail=None):
        """Exercise the adapter projection with protocol stubs; execute no scripts."""
        with tempfile.TemporaryDirectory(prefix="guardian-runner-result-") as temp:
            work = Path(temp)
            project = work / "project"
            project.mkdir()
            environment = {"fingerprint": "a" * 64}
            for role in ("native", "tavotto"):
                (work / f"{role}-execs.jsonl").write_text(
                    json.dumps({"environment": environment}) + "\n", encoding="utf-8"
                )
            case = {"id": "registered", "expected": {}}
            result = {
                "classification": classification,
                "stages": {stage: True for stage in guardian.CC.STAGES},
                "detail": {"capture": {"got_figures": 1}, **(detail or {})},
            }
            matrix = types.ModuleType("compat_matrix")
            matrix.materialize = mock.Mock(return_value=(project, "plot.py", "__main__"))
            matrix.run_driver = mock.Mock(
                return_value={"ok": True, "exit_status": 0, "figure_count": 1}
            )
            matrix.run_group = mock.Mock(return_value={"registered": copy.deepcopy(result)})
            matrix.stage_export = mock.Mock()
            with (
                mock.patch.dict(sys.modules, {"compat_matrix": matrix}),
                mock.patch.dict(os.environ, {}),
                mock.patch.object(guardian, "registered", return_value={"registered": case}),
                mock.patch.object(guardian, "environment", return_value=environment),
                mock.patch.object(guardian, "preflight", return_value=[]),
            ):
                row = guardian._execute(
                    {
                        "suite": "compat_engine",
                        "case": "registered",
                        "work": str(work),
                        "evidence": str(work / "evidence"),
                    }
                )
            self.assertEqual(row["native"]["status"], "passed")
            self.assertTrue(row["comparable"])
            self.assertTrue(all(stage["status"] == "passed" for stage in row["stages"].values()))
            return row

    def test_runner_error_keeps_traceback_and_unknown_with_schema_valid_classification(self):
        traceback = "Traceback (most recent call last):\n" + "  observation frame\n" * 100
        traceback += "KeyError: 'svg'\n"

        row = self.execute_result(guardian.CC.RUNNER_ERROR, {"runner_error": traceback})

        self.assertIsNone(row["product_classification"])
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["raw"]["classification"], guardian.CC.RUNNER_ERROR)
        self.assertEqual(row["raw"]["detail"]["runner_error"], traceback)
        self.assertEqual(row["raw"]["runner_error"], traceback[-1500:])
        self.assertEqual(row["failure_category"]["category"], "unknown")
        self.assertEqual(row["failure_category"]["source_code"], "runner_crash")
        schema = json.loads(guardian.SCHEMA_PATH.read_text(encoding="utf-8"))
        allowed = schema["$defs"]["case"]["properties"]["product_classification"]["enum"]
        self.assertNotIn(guardian.CC.RUNNER_ERROR, allowed)
        self.assertIn(row["product_classification"], allowed)

    def test_real_product_classifications_are_preserved(self):
        for classification in guardian.CC.CLASSIFICATIONS:
            with self.subTest(classification=classification):
                row = self.execute_result(classification)
                self.assertEqual(row["product_classification"], classification)
                self.assertNotIn("runner_error", row["raw"])

    def test_unknown_classification_remains_invalid_for_the_schema(self):
        unknown = "unregistered_classification"

        row = self.execute_result(unknown)

        self.assertEqual(row["product_classification"], unknown)
        schema = json.loads(guardian.SCHEMA_PATH.read_text(encoding="utf-8"))
        allowed = schema["$defs"]["case"]["properties"]["product_classification"]["enum"]
        self.assertNotIn(row["product_classification"], allowed)


if __name__ == "__main__":
    unittest.main()
