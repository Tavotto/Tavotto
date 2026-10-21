"""The declaration file: schema, mandatory references, and the control_met / mismatch rule."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_guardian as cg  # noqa: E402
import compat_guardian_expectations as ex  # noqa: E402
from test_compat_guardian import GuardianBase, native_result  # noqa: E402


class Declarations(GuardianBase):
    def document(self) -> dict:
        return json.loads(ex.PATH.read_text(encoding="utf-8"))

    def write(self, document: dict) -> Path:
        path = self.root / "expectations.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def rejects(self, document: dict) -> None:
        with self.assertRaises(cg.EvidenceError):
            ex.load_entries(self.write(document))

    def test_the_committed_file_loads_and_every_reference_resolves(self):
        entries = ex.load_entries()
        self.assertGreaterEqual(len(entries), 9)
        self.assertEqual({e["suite"] for e in entries}, {"compat_v2"})

    def test_schema_rejects_unknown_fields_and_outcomes(self):
        extra = self.document()
        extra["expectations"][0]["surprise"] = True
        self.rejects(extra)
        bad = self.document()
        bad["expectations"][0]["outcome"] = "passed"
        self.rejects(bad)

    def test_a_declaration_without_a_reference_is_not_allowed(self):
        document = self.document()
        document["expectations"][0]["references"] = []
        self.rejects(document)
        del document["expectations"][0]["references"]
        self.rejects(document)

    def test_a_declaration_without_a_real_reason_is_not_allowed(self):
        document = self.document()
        document["expectations"][0]["reason"] = "ok"
        self.rejects(document)

    def test_references_must_resolve(self):
        document = self.document()
        document["expectations"][0]["references"] = [{"kind": "test", "ref": "tests/no_such.py::t"}]
        self.rejects(document)
        document["expectations"][0]["references"] = [
            {
                "kind": "test",
                "ref": "tests/test_compat_guardian.py::test_that_does_not_exist_anywhere",
            }
        ]
        self.rejects(document)
        document["expectations"][0]["references"] = [{"kind": "issue", "ref": "1234"}]
        self.rejects(document)
        document["expectations"][0]["references"] = [{"kind": "issue", "ref": "#1234"}]
        ex.load_entries(self.write(document))

    def test_unregistered_or_duplicate_cases_are_rejected(self):
        document = self.document()
        document["expectations"].append(copy.deepcopy(document["expectations"][0]))
        self.rejects(document)
        document = self.document()
        document["expectations"][0]["case"] = "not_a_case"
        self.rejects(document)

    def v2_row(self, **raw):
        row = cg.new_row("s16a_zero_division", "authenticated_server_safe")
        row["native"] = {"status": "invalid_fixture", "result": native_result(exit_status=1)}
        row["raw"] = raw
        return row

    def entry(self):
        return ex.find(ex.load_entries(), "compat_v2", "s16a_zero_division")

    def test_observed_equal_to_declared_is_control_met_and_not_a_pass_or_failure(self):
        result = ex.evaluate(self.v2_row(), self.entry())
        self.assertEqual(result["status"], "control_met")
        self.assertIsNone(result["category"]["category"])
        self.assertEqual(cg.verdict([{"status": "control_met"}]), "control_met")
        self.assertNotEqual(cg.exit_code_for("control_met"), cg.EXIT_PASSED)
        self.assertNotEqual(cg.exit_code_for("control_met"), cg.EXIT_FAILED)

    def test_a_precondition_unmet_row_is_environment_unavailable_not_unknown(self):
        row = self.v2_row(precondition_unmet="numpy missing")
        row["native"] = {"status": "passed", "result": native_result()}
        row["status"] = "environment_unavailable"
        self.assertEqual(ex.observed_outcome(row), "environment_unavailable")
        declared = {
            "outcome": "environment_unavailable",
            "reason": "x" * 30,
            "references": [],
        }
        met = ex.evaluate(row, declared)
        self.assertEqual(met["status"], "control_met")
        self.assertNotEqual(met["status"], "passed")
        # Undeclared, it stays a visible mismatch: environment_unavailable is never a pass.
        other = ex.evaluate(row, {**declared, "outcome": "partial_coverage"})
        self.assertEqual(other["status"], "failed")
        self.assertEqual(other["expectation"]["observed_outcome"], "environment_unavailable")

    def test_a_crashed_or_silent_native_driver_is_unknown_and_never_control_met(self):
        for result in (
            {"ok": False, "code": "driver_no_output", "message": "exit 1"},
            {"ok": False, "code": "driver_timeout", "message": "timeout"},
        ):
            self.assertEqual(cg._native_status(result), "unknown", result)
            row = self.v2_row()
            row["native"] = {"status": cg._native_status(result), "result": result}
            declared = {"outcome": "environment_unavailable", "reason": "x" * 30, "references": []}
            met = ex.evaluate(row, declared)
            self.assertNotEqual(met["status"], "control_met", result)
        missing = {"ok": False, "traceback": "ModuleNotFoundError: No module named 'cv2'"}
        self.assertEqual(cg._native_status(missing), "environment_unavailable")

    def test_mismatch_is_failed_with_the_difference_attached(self):
        row = self.v2_row()
        row["native"] = {"status": "passed", "result": native_result()}
        row["comparable"] = True
        row["executions"].update(native=1, tavotto=1)
        row["stages"] = {s: cg.stage("passed", "x") for s in cg.CC.STAGES}
        result = ex.evaluate(row, self.entry())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["expectation"]["result"], "mismatch")
        self.assertEqual(
            result["expectation"]["diff"], "declared invalid_fixture but observed passed"
        )
        self.assertEqual(result["category"]["source_code"], "expectation_mismatch")

    def test_validate_rejects_a_forged_control_met_and_a_forged_expectation_block(self):
        self.report["suite"] = "compat_v2"
        row = self.v2_row()
        row["id"] = "s16a_zero_division"
        row["fixture"] = copy.deepcopy(self.row_fixture("s16a_zero_division"))
        resolved = ex.evaluate(row, self.entry())
        row.update(
            status=resolved["status"],
            expectation=resolved["expectation"],
            failure_category=resolved["category"],
        )
        self.report.update(
            suite="compat_v2",
            expected_cases=["s16a_zero_division"],
            cases=[row],
            verdict="control_met",
            coverage=cg.coverage_table([row]),
            corpus_sha256=cg.canonical({"s16a_zero_division": row["fixture"]["files"]}),
        )
        cg.validate_report(self.report, self.ev, self.source)
        row["expectation"]["declared_outcome"] = "expected_limitation"
        self.rejected()
        row["expectation"] = resolved["expectation"]
        row["status"] = "passed"
        self.rejected()

    def row_fixture(self, case):
        files = cg.fixture_files("compat_v2", cg.registered("compat_v2")[case])
        return {
            "files": files,
            "source_sha256": cg.canonical({k: v for k, v in files.items() if k.endswith(".py")}),
            "data_sha256": cg.canonical({k: v for k, v in files.items() if not k.endswith(".py")}),
        }

    def test_control_met_without_any_declaration_is_rejected(self):
        self.row["status"] = "control_met"
        self.report["verdict"] = "control_met"
        self.rejected()

    def test_report_binds_the_declaration_file_digest(self):
        self.report["expectations_sha256"] = "0" * 64
        self.rejected()

    def test_declaration_file_is_checked_against_head_like_fixtures(self):
        self.assertIn(ex.RELATIVE_PATH, (ex.RELATIVE_PATH, ex.SCHEMA_RELATIVE_PATH))
        import inspect

        self.assertIn("EX.RELATIVE_PATH", inspect.getsource(cg.registered))


def test_a_reference_must_name_an_exact_definition_not_a_substring(tmp_path, monkeypatch):
    """改名成更长的名字后，旧的 `::test_x` 引用不得仍然成立（Codex #1118 r4305942184）。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text(
        "class Group:\n    def test_inner(self):\n        pass\n\n"
        "def test_scenario_baseline_new():\n    pass  # test_scenario_baseline in a comment\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cg, "ROOT", tmp_path)
    ok = [
        {"kind": "test", "ref": "tests/t.py::test_scenario_baseline_new"},
        {"kind": "test", "ref": "tests/t.py::Group::test_inner"},
        {"kind": "test", "ref": "tests/t.py::Group"},
        {"kind": "test", "ref": "tests/t.py::test_scenario_baseline_new[param]"},
    ]
    for ref in ok:
        ex._check_reference(ref)
    for bad in (
        "tests/t.py::test_scenario_baseline",
        "tests/t.py::Group::test_missing",
        "tests/t.py::test_inner",
        "tests/t.py",
        "tests/t.py::",
    ):
        with pytest.raises(cg.EvidenceError):
            ex._check_reference({"kind": "test", "ref": bad})
