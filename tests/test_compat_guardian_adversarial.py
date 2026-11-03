"""Adversarial acceptance cases (maintainer brief, section 15) owned by CG-01.

Numbers refer to the brief's list. Cases 1-8, 10, 14 belong to later stages or to other
suites; 5 and 13 are covered here where Guardian can state them truthfully and
otherwise point at the existing test that owns the behavior. Each test here would go
red if the guard it names were removed (mutation results are in docs/ci/compat-guardian.md).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_guardian as cg
import compat_guardian_taxonomy as tx
from test_compat_guardian import GuardianBase, native_result

DRIVER = Path(cg.HERE) / "compat_driver.py"


def run_native_ref(project: Path, script_text: str, entry: str = "__main__") -> dict:
    """Drive the real `native_ref` mode on a throwaway project (needs matplotlib)."""
    project.mkdir(parents=True, exist_ok=True)
    script = project / "script.py"
    script.write_text(textwrap.dedent(script_text), encoding="utf-8")
    request = project.parent / f"{project.name}-request.json"
    request.write_text(
        json.dumps({"project": str(project), "script": str(script), "entry": entry}),
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
    assert done.stdout.strip(), done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


class Case09NativeItselfFails(GuardianBase):
    """#9: a failing native reference is a fixture problem, never a Tavotto defect."""

    def test_a_raising_script_is_invalid_fixture_in_the_real_driver(self):
        result = run_native_ref(self.root / "p9", "raise ZeroDivisionError('boom')\n")
        self.assertIs(result["ok"], True, "the driver observed it; the script failed")
        self.assertEqual(result["exit_status"], 1)
        self.assertEqual(cg._native_status(result), "invalid_fixture")

    def test_sys_exit_nonzero_is_also_a_native_failure(self):
        result = run_native_ref(self.root / "p9b", "import sys\nsys.exit(3)\n")
        self.assertEqual(result["exit_status"], 3)
        self.assertEqual(cg._native_status(result), "invalid_fixture")

    def test_missing_module_is_environment_not_fixture(self):
        result = run_native_ref(self.root / "p9c", "import no_such_module_for_guardian\n")
        self.assertEqual(cg._native_status(result), "environment_unavailable")

    def test_row_status_follows_native_and_carries_no_product_attribution(self):
        row = cg.new_row("x", "contract_fixture")
        row["native"] = {
            "status": "invalid_fixture",
            "result": native_result(exit_status=1, error={"type": "X", "traceback": "t"}),
        }
        row["stages"]["execute"] = cg.stage("failed", "would look like a Tavotto failure")
        row["comparable"] = True
        row["executions"].update(native=1, tavotto=1)
        cg.finalize_row(row)
        self.assertEqual(row["status"], "invalid_fixture")
        self.assertEqual(row["failure_category"]["category"], "invalid_fixture")

    def test_a_forged_failed_verdict_over_a_failed_native_is_rejected(self):
        self.row["native"]["result"] = native_result(exit_status=1)
        self.row["status"] = "failed"
        self.row["stages"]["execute"]["status"] = "failed"
        self.row["failure_category"] = {"category": "unknown", "source_code": None, "basis": "x"}
        self.report["verdict"] = "failed"
        self.rejected()

    def test_unreadable_native_image_is_invalid_fixture(self):
        bad = native_result(
            artifacts=[{"path": "out.png", "bytes": 3, "sha256": "e" * 64, "readable": False}]
        )
        self.assertEqual(cg._native_status(bad), "invalid_fixture")


class Case11NothingRan(GuardianBase):
    """#11: if nothing actually ran, the report cannot be a success."""

    def test_zero_tavotto_executions_cannot_pass(self):
        self.row["executions"]["tavotto"] = 0
        self.assertEqual(cg.case_status(self.row), "unknown")
        self.rejected()

    def test_zero_native_executions_cannot_pass(self):
        self.row["executions"]["native"] = 0
        self.assertEqual(cg.case_status(self.row), "unknown")
        self.rejected()

    def test_counter_that_was_never_opened_is_unknown_not_zero(self):
        self.assertIsNone(cg._count(self.root / "never-created.jsonl"))

    def test_adapter_that_produces_nothing_gives_unknown_and_a_nonzero_exit(self):
        def silent(command, *, work, evidence, env, timeout):
            # "succeeds" and leaves process evidence, but never writes result.json
            (evidence / "process.json").write_text("{}", encoding="utf-8")

        args = Namespace(
            suite="compat_engine",
            case=self.cid,
            out=str(self.root / "runs"),
            expected_sha=cg.git("rev-parse", "HEAD"),
            timeout=1,
        )
        with mock.patch.object(cg, "launch_adapter", side_effect=silent):
            code = cg.run(args)
        self.assertNotEqual(code, cg.EXIT_PASSED)
        report = json.loads(
            next((self.root / "runs").glob("*/reports/guardian.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(report["verdict"], "unknown")
        self.assertEqual(report["cases"][0]["status"], "unknown")
        self.assertEqual(report["cases"][0]["failure_category"]["category"], "unknown")

    def test_a_crashing_runner_is_unknown_and_never_attributed_to_the_product(self):
        row = cg.new_row("x", "contract_fixture")
        row["native"] = {"status": "passed", "result": native_result()}
        row["comparable"] = True
        row["executions"].update(native=1, tavotto=1)
        row["stages"] = {s: cg.stage("passed", "x") for s in cg.CC.STAGES}
        row["raw"] = {"runner_error": "Traceback ... IndexError"}
        cg.finalize_row(row)
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["failure_category"]["source_code"], "runner_crash")
        self.assertNotEqual(row["failure_category"]["category"], "invalid_fixture")

    def test_a_timed_out_adapter_is_a_timeout_category(self):
        row = cg.new_row("x", "runner")
        row["raw"] = {"adapter_timeout": "adapter timeout"}
        cg.finalize_row(row)
        self.assertEqual(row["failure_category"]["category"], "timeout_or_process_crash")
        self.assertNotEqual(row["status"], "passed")

    def test_a_report_of_only_unrun_cases_does_not_aggregate_to_passed(self):
        self.row["status"] = "not_run"
        self.assertEqual(cg.verdict([self.row]), "unknown")
        self.assertEqual(cg.exit_code_for("unknown"), cg.EXIT_UNKNOWN)
        self.assertEqual(cg.verdict([]), "unknown")


class Case12StaleEvidence(GuardianBase):
    """#12: evidence for an older SHA/tree can never prove a newer commit."""

    def test_run_refuses_an_expected_sha_that_is_not_head(self):
        args = Namespace(
            suite="compat_engine",
            case=self.cid,
            out=str(self.root / "runs"),
            expected_sha="0" * 40,
            timeout=1,
        )
        with self.assertRaises(cg.EvidenceError):
            cg.run(args)
        self.assertFalse((self.root / "runs").exists(), "nothing may be produced for a stale pin")

    def test_old_report_does_not_validate_against_a_newer_checkout(self):
        newer = {**self.source, "commit": "9" * 40, "tree": "8" * 40}
        with self.assertRaises(cg.EvidenceError):
            cg.validate_report(self.report, self.ev, newer)

    def test_same_commit_with_different_tree_content_is_rejected(self):
        changed = {**self.source, "content_sha256": "f" * 64}
        with self.assertRaises(cg.EvidenceError):
            cg.validate_report(self.report, self.ev, changed)

    def test_source_change_during_a_run_publishes_no_report(self):
        states = iter([self.source, {**self.source, "content_sha256": "f" * 64}])
        args = Namespace(
            suite="compat_engine",
            case=self.cid,
            out=str(self.root / "runs"),
            expected_sha=self.source["commit"],
            timeout=1,
        )
        with (
            mock.patch.object(cg, "source_state", side_effect=lambda: next(states)),
            mock.patch.object(cg, "launch_adapter", side_effect=cg.EvidenceError("skip")),
        ):
            with self.assertRaises(cg.EvidenceError):
                cg.run(args)
        self.assertEqual(list((self.root / "runs").glob("*/reports/guardian.json")), [])

    def test_evidence_files_from_another_run_are_rejected_by_hash(self):
        (self.ev / "raw.json").write_text(
            '{"contract_fixture":"from another run"}', encoding="utf-8"
        )
        self.rejected()


class Case15BothCorrect(GuardianBase):
    """#15: when native and Tavotto are both right, no defect may be invented."""

    def test_clean_row_has_no_failure_category(self):
        row = cg.finalize_row(self.row)
        self.assertEqual(row["status"], "passed")
        self.assertIsNone(row["failure_category"]["category"])
        self.assertEqual(tx.attribute(row)["category"], None)

    def test_a_legacy_product_bug_label_alone_does_not_create_a_failure(self):
        self.row["product_classification"] = "product_bug"
        cg.finalize_row(self.row)
        self.assertNotEqual(self.row["status"], "failed")

    def test_real_driver_agrees_with_itself_on_a_benign_script(self):
        script = """
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            ax.plot([1, 2, 3])
            ax.set_title("benign")
            fig.savefig("benign.png")
        """
        first = run_native_ref(self.root / "p15a", script)
        again = run_native_ref(self.root / "p15b", script)
        self.assertEqual(cg._native_status(first), "passed")
        self.assertEqual(first["figure_count"], again["figure_count"])
        self.assertEqual(first["semantics"], again["semantics"])
        self.assertEqual(first["side_effects"]["created"], ["benign.png"])


class Case05MultiFigure(GuardianBase):
    """#5: a multi-figure script must not be silently reduced to one figure."""

    SCRIPT = """
        import matplotlib.pyplot as plt
        for index in range(2):
            fig, ax = plt.subplots()
            ax.set_title(f"figure {index}")
            fig.savefig(f"fig{index}.png")
    """

    def test_native_reference_counts_every_figure(self):
        result = run_native_ref(self.root / "p5", self.SCRIPT)
        self.assertEqual(result["figure_count"], 2)
        self.assertEqual(sorted(a["path"] for a in result["artifacts"]), ["fig0.png", "fig1.png"])

    def test_capturing_fewer_figures_than_native_is_attributed_to_partial_capture(self):
        row = cg.new_row("x", "contract_fixture")
        row["native"] = {"status": "passed", "result": native_result(figure_count=2)}
        row["comparable"] = True
        row["executions"].update(native=1, tavotto=1)
        row["stages"] = {s: cg.stage("passed", "x") for s in cg.CC.STAGES}
        row["stages"]["capture"] = cg.stage("failed", "got 1 of 2")
        row["figures"] = {"native": 2, "tavotto_captured": 1, "tavotto_valid": 1}
        cg.finalize_row(row)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["failure_category"]["category"], "partial_capture")

    def test_same_path_reused_by_different_figures_is_visible(self):
        result = run_native_ref(
            self.root / "p5b",
            """
                import matplotlib.pyplot as plt
                a = plt.figure(); a.savefig("shot.png")
                b = plt.figure(); b.savefig("shot.png")
            """,
        )
        self.assertEqual(result["same_path_distinct_figures"], ["shot.png"])
        self.assertEqual(result["figure_count"], 2)

    def test_the_corpus_pins_the_multi_figure_expectation(self):
        case = cg.registered("compat_engine")["shape_multi_figure_a"]
        self.assertGreaterEqual(case["expected_figures"], 2)


class Case13RestrictedEntry(GuardianBase):
    """#13: a restricted (browser-mode) entry must not auto-upgrade to Native.

    The behavior itself is owned by tests/test_execution_fallback_e2e.py. Guardian adds
    one thing on top, declaratively (tests/compat_guardian/expectations.json): the V2
    controls that pin Safe-only behavior are `control_met` only while the observed outcome
    equals the declared one; any change, including a result that looks better (and may be
    a silent Native upgrade), is a mismatch and therefore `failed`.
    """

    OWNER = "tests/test_execution_fallback_e2e.py::test_a_browser_mode_service_never_falls_back_to_native"

    def entry(self, case="s06_savefig_pil_readback"):
        return cg.EX.find(cg.EX.load_entries(), "compat_v2", case)

    def v2_row(self, status_stage="failed", baseline_match=True):
        row = cg.new_row("s06_savefig_pil_readback", "authenticated_server_safe")
        row["native"] = {"status": "passed", "result": native_result()}
        row["comparable"] = True
        row["executions"].update(native=1, tavotto=1)
        row["figures"] = {"native": 1, "tavotto_captured": 0, "tavotto_valid": 0}
        row["stages"]["discover"] = cg.stage("passed", "x")
        row["stages"]["execute"] = cg.stage(status_stage, "x")
        row["raw"] = {"baseline_match": baseline_match}
        return row

    def test_owner_test_still_exists(self):
        path, name = self.OWNER.split("::")
        self.assertIn(f"def {name}", (Path(cg.ROOT) / path).read_text(encoding="utf-8"))

    def test_declarations_exist_for_the_three_controls_and_cite_the_owner(self):
        for case in ("s04b_savefig_tmp_replace", "s05_dynamic_rename", "s06_savefig_pil_readback"):
            entry = self.entry(case)
            self.assertEqual(entry["outcome"], "expected_limitation")
            self.assertIn(self.OWNER, [r["ref"] for r in entry["references"]])

    def test_observed_baseline_match_meets_the_declared_control(self):
        result = cg.EX.evaluate(self.v2_row(), self.entry())
        self.assertEqual(result["status"], "control_met")
        self.assertEqual(result["expectation"]["result"], "control_met")
        self.assertIsNone(result["category"]["category"])

    def test_a_better_looking_result_is_a_mismatch_not_a_quiet_pass(self):
        row = self.v2_row(status_stage="passed", baseline_match=False)
        for name in cg.V2_UNCOVERED:
            row["stages"][name] = cg.stage("not_covered", "x")
        for name in ("capture", "open", "semantic"):
            row["stages"][name] = cg.stage("passed", "x")
        row["figures"]["tavotto_captured"] = 1
        result = cg.EX.evaluate(row, self.entry())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["expectation"]["observed_outcome"], "partial_coverage")
        self.assertIn(
            "declared expected_limitation but observed partial_coverage",
            result["expectation"]["diff"],
        )

    def test_a_worse_result_off_baseline_is_a_mismatch(self):
        result = cg.EX.evaluate(self.v2_row(baseline_match=False), self.entry())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["category"]["source_code"], "expectation_mismatch")

    def test_without_a_declaration_a_baseline_gap_stays_failed(self):
        row = self.v2_row()
        self.assertIsNone(cg.EX.find(cg.EX.load_entries(), "compat_v2", "s25_partial_capture"))
        self.assertEqual(cg.case_status(row), "failed")
