"""Guardian evidence-contract tests, runnable without pytest or product dependencies.

These synthetic protocol records test the validator, never claim product success.
Actual native/product execution belongs in the CLI artifacts, not these records.
Adversarial acceptance cases live in test_compat_guardian_adversarial.py, which reuses
`GuardianBase` from here.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import compat_guardian as cg  # noqa: E402


def native_result(**changes) -> dict:
    """A well-formed `native_ref` driver response for a script that behaved."""
    result = {
        "ok": True,
        "exit_status": 0,
        "error": None,
        "script_entries": 1,
        "figure_count": 1,
        "figures_saved": 1,
        "figures_open_at_exit": 0,
        "savefig_calls": 1,
        "same_path_distinct_figures": [],
        "artifacts": [{"path": "out.png", "bytes": 10, "sha256": "d" * 64, "readable": True}],
        "side_effects": {"created": ["out.png"], "modified": [], "deleted": []},
        "semantics": {"figure0": {"titles": ["correct"]}},
        "log": "",
    }
    result.update(changes)
    return result


class GuardianBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cid = "shape_oo_savefig"
        cls.files = cg.fixture_files("compat_engine", cg.registered("compat_engine")[cls.cid])
        cls.fixture = {
            "files": cls.files,
            "source_sha256": cg.canonical(
                {k: v for k, v in cls.files.items() if k.endswith(".py")}
            ),
            "data_sha256": cg.canonical(
                {k: v for k, v in cls.files.items() if not k.endswith(".py")}
            ),
        }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cg-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ev = self.root / "evidence"
        self.ev.mkdir()
        (self.ev / "raw.json").write_text('{"contract_fixture":true}', encoding="utf-8")
        self.source = {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "dirty": False,
            "content_sha256": "c" * 64,
        }
        environment = {"facts": {"contract_fixture": True}}
        environment["fingerprint"] = cg.canonical(environment["facts"])
        self.row = cg.new_row(self.cid, "contract_fixture", environment)
        self.row.update(
            status="passed",
            stages={s: cg.stage("passed", "synthetic validator test only") for s in cg.CC.STAGES},
            executions={
                "native": 1,
                "tavotto": 1,
                "basis": "contract_fixture",
                "attempt_history": None,
                "attempt_history_agrees": None,
            },
            native={"status": "passed", "result": native_result()},
            comparable=True,
            figures={"native": 1, "tavotto_captured": 1, "tavotto_valid": 1},
            environment_after=copy.deepcopy(environment),
            raw={
                "launch_environments": {
                    "native": [environment],
                    "tavotto": [copy.deepcopy(environment)],
                }
            },
            unverified=["all product behavior"],
            fixture=copy.deepcopy(self.fixture),
        )
        self.row["failure_category"] = {
            "category": None,
            "source_code": None,
            "basis": "no failure to attribute (passed)",
        }
        self.report = {
            "schema_version": cg.SCHEMA_VERSION,
            "run_id": "1" * 32,
            "source": self.source,
            "expected_sha": "a" * 40,
            "suite": "compat_engine",
            "expected_cases": [self.cid],
            "corpus_sha256": cg.canonical({self.cid: self.files}),
            "cases": [self.row],
            "evidence": cg.evidence_refs(self.ev),
            "verdict": "passed",
            "coverage": cg.coverage_table([self.row]),
            "expectations_sha256": cg.EX.file_digest(),
            "unverified": ["all product behavior"],
            "duration_s": 1.0,
        }

    def valid(self):
        cg.validate_report(self.report, self.ev, self.source)

    def rejected(self):
        with self.assertRaises(cg.EvidenceError):
            self.valid()


class GuardianContract(GuardianBase):
    def test_complete_contract_is_accepted(self):
        self.valid()

    def test_empty_execution_never_passes(self):
        self.row["executions"]["tavotto"] = 0
        self.rejected()

    def test_unknown_count_never_passes(self):
        self.row["executions"]["tavotto"] = None
        self.rejected()

    def test_http_count_cannot_supply_script_count(self):
        del self.row["executions"]["tavotto"]
        self.row["executions"]["http_requests"] = 9
        self.rejected()

    def test_missing_stage_never_passes(self):
        del self.row["stages"]["semantic"]
        self.rejected()

    def test_all_not_run_never_passes(self):
        self.row["stages"] = {s: cg.stage("not_run", "no execution") for s in cg.CC.STAGES}
        self.rejected()

    def test_unknown_status_never_passes(self):
        self.row["stages"]["open"]["status"] = "ok"
        self.rejected()

    def test_missing_schema_never_passes(self):
        del self.report["schema_version"]
        self.rejected()

    def test_missing_final_verdict_never_passes(self):
        del self.report["verdict"]
        self.rejected()

    def test_uniform_old_sha_does_not_prove_current_head(self):
        self.report["source"] = {**self.source, "commit": "0" * 40}
        self.report["expected_sha"] = "0" * 40
        self.rejected()

    def test_dirty_content_cannot_borrow_main_sha(self):
        self.report["source"] = {**self.source, "dirty": True, "content_sha256": "0" * 64}
        self.rejected()

    def test_unbound_source_digest_rejected(self):
        del self.report["source"]["content_sha256"]
        self.rejected()

    def test_changed_environment_rejected(self):
        self.row["environment_after"]["facts"] = {"different_python": True}
        self.rejected()

    def test_false_comparability_flag_rejected(self):
        self.row["comparable"] = False
        self.rejected()

    def test_forged_environment_fingerprint_rejected(self):
        self.row["environment_before"]["fingerprint"] = "0" * 64
        self.row["environment_after"] = copy.deepcopy(self.row["environment_before"])
        self.rejected()

    def test_native_failure_is_not_product_failure(self):
        self.row["status"] = "failed"
        self.row["native"]["status"] = "invalid_fixture"
        self.row["stages"]["execute"]["status"] = "failed"
        self.report["verdict"] = "failed"
        self.rejected()

    def test_native_environment_failure_is_classified_first(self):
        self.assertEqual(
            cg._native_status(
                {
                    "ok": False,
                    "code": "native_script_error",
                    "traceback": "ModuleNotFoundError: missing",
                }
            ),
            "environment_unavailable",
        )
        self.assertEqual(
            cg._native_status(
                {
                    "ok": False,
                    "code": "native_script_error",
                    "traceback": "ZeroDivisionError: division by zero",
                }
            ),
            "invalid_fixture",
        )

    def test_exit_zero_protocol_failure_not_success(self):
        self.assertEqual(cg._native_status({"ok": False}), "unknown")
        self.assertEqual(cg._native_status({"ok": True, "stems": []}), "unknown")
        self.assertEqual(cg._native_status(native_result(exit_status=None)), "unknown")

    def test_both_graphs_with_semantic_difference_fail(self):
        self.row["status"] = "failed"
        self.row["stages"]["semantic"]["status"] = "failed"
        self.row["failure_category"] = {
            "category": "manifest_semantics",
            "source_code": "stage:semantic",
            "basis": "first stage that failed while the native reference passed",
        }
        self.report["verdict"] = "failed"
        self.report["coverage"] = cg.coverage_table([self.row])
        self.valid()

    def test_both_correct_do_not_invent_defect(self):
        self.assertEqual(cg.case_status(self.row), "passed")

    def v2_observation(self, **changes):
        return {
            "ran": True,
            "exec_count": 1,
            "final_phase": "completed",
            "outcome_kind": "succeeded",
            "facts": {"facts": {"execution_finished": True}},
            "figures_captured": 1,
            "figures_valid": 1,
            "titles": ["correct"],
            **changes,
        }

    def test_v2_invocation_is_not_successful_execution(self):
        observed = self.v2_observation(
            final_phase="action_required",
            outcome_kind="failed",
            facts={"facts": {}},
            figures_captured=0,
            figures_valid=0,
            titles=[],
        )
        stages = cg.v2_stages(observed, {}, ["known Safe readback difference"])
        self.assertEqual(stages["execute"]["status"], "failed")
        for name in ("capture", "open", "semantic"):
            self.assertEqual(stages[name]["status"], "not_run")

    def test_v2_incomplete_execution_cannot_pass(self):
        stages = cg.v2_stages(self.v2_observation(outcome_kind=None), {}, [])
        self.assertEqual(stages["execute"]["status"], "unknown")

    def test_v2_both_graphs_wrong_science_fails(self):
        native = {"semantics": {"figure": {"titles": ["independent truth"]}}}
        stages = cg.v2_stages(self.v2_observation(), native, [])
        self.assertEqual(stages["open"]["status"], "passed")
        self.assertEqual(stages["semantic"]["status"], "failed")

    def test_v2_both_correct_preserves_unverified_funnel(self):
        native = {"semantics": {"figure": {"titles": ["correct"]}}}
        self.row["stages"] = cg.v2_stages(self.v2_observation(), native, [])
        for name in ("discover", "execute", "capture", "open", "semantic"):
            self.assertEqual(self.row["stages"][name]["status"], "passed")
        # Exercised layers pass; edit/replay/export/fidelity are not_covered. The row is
        # a defined non-pass: partial_coverage, never passed and never permanent unknown.
        self.assertEqual(cg.case_status(self.row), "partial_coverage")
        for name in cg.V2_UNCOVERED:
            self.assertEqual(self.row["stages"][name]["status"], "not_covered")

    def partial_observation(self):
        return self.v2_observation(final_phase="partial", titles=["good"])

    def partial_native(self):
        return {"figure_count": 2, "semantics": {"a": {"titles": ["good"]}, "b": {"titles": [""]}}}

    def test_v2_designed_partial_met_is_an_executed_declared_limitation(self):
        self.row["stages"] = cg.v2_stages(
            self.partial_observation(), self.partial_native(), [], designed_partial=True
        )
        self.assertEqual(self.row["stages"]["execute"]["status"], "passed")
        self.assertEqual(self.row["stages"]["capture"]["status"], "expected_limitation")
        self.row["figures"] = {"native": 2, "tavotto_captured": 1, "tavotto_valid": 1}
        self.assertEqual(cg.case_status(self.row), "expected_limitation")

    def test_v2_fewer_figures_than_native_never_passes_capture(self):
        for kwargs, missing in (({}, []), ({"designed_partial": True}, ["oracle gap"])):
            stages = cg.v2_stages(
                self.partial_observation(), self.partial_native(), missing, **kwargs
            )
            self.assertEqual(stages["capture"]["status"], "failed", kwargs)

    def test_v2_more_figures_than_native_never_passes_capture(self):
        native = {"figure_count": 1, "semantics": {}}
        observed = self.v2_observation(figures_captured=2, figures_valid=2)
        self.assertEqual(cg.v2_stages(observed, native, [])["capture"]["status"], "failed")

    def test_v2_open_needs_every_captured_figure_to_render(self):
        native = {"figure_count": 2, "semantics": {}}
        observed = self.v2_observation(figures_captured=2, figures_valid=1)
        self.assertEqual(cg.v2_stages(observed, native, [])["open"]["status"], "failed")
        observed = self.v2_observation(figures_captured=2, figures_valid=2)
        self.assertEqual(cg.v2_stages(observed, native, [])["open"]["status"], "passed")

    def test_v2_zero_invocations_do_not_reach_downstream_stages(self):
        stages = cg.v2_stages(self.v2_observation(exec_count=0), {}, [])
        for name in ("execute", "capture", "open", "semantic"):
            self.assertEqual(stages[name]["status"], "not_run")

    def test_actual_product_route_failure_not_hidden_by_stage_green(self):
        self.row["raw"]["routes"] = {"safe_probe": {"status": "fail"}}
        self.rejected()

    def test_legacy_bug_label_without_failure_evidence_is_unknown(self):
        self.row["product_classification"] = "product_bug"
        self.assertEqual(cg.case_status(self.row), "unknown")

    def test_timeout_retains_output_and_reaps_process_group(self):
        work = self.root / "timeout-work"
        work.mkdir()
        with self.assertRaises(cg.EvidenceError):
            cg.launch_adapter(
                [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(10)"],
                work=work,
                evidence=self.ev,
                env=cg.isolated_env(work),
                timeout=0.2,
            )
        process = json.loads((self.ev / "process.json").read_text(encoding="utf-8"))
        self.assertTrue(process["timed_out"])
        self.assertIn("started", (self.ev / "stdout.txt").read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertTrue(process["cleanup"]["group_gone"])

    def test_partial_adapter_retains_counter_evidence(self):
        raw = '{"contract_fixture":"partial native execution"}\n'

        def interrupted(command, *, work, evidence, env, timeout):
            (work / "native-execs.jsonl").write_bytes(raw.encode())
            raise cg.EvidenceError("simulated adapter interruption")

        args = Namespace(
            suite="compat_engine",
            case=self.cid,
            out=str(self.root / "runs"),
            expected_sha=cg.git("rev-parse", "HEAD"),
            timeout=1,
        )
        with mock.patch.object(cg, "launch_adapter", side_effect=interrupted):
            self.assertEqual(cg.run(args), 3)
        report_path = next((self.root / "runs").glob("*/reports/guardian.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        retained = report_path.parent.parent / "evidence" / self.cid / "native-execs.jsonl"
        self.assertEqual(retained.read_bytes(), raw.encode())
        refs = {item["path"]: item for item in report["evidence"]}
        self.assertEqual(refs[f"{self.cid}/native-execs.jsonl"]["sha256"], cg.digest(raw.encode()))

    def test_missing_data_digest_rejected(self):
        del self.row["fixture"]["data_sha256"]
        self.rejected()

    def test_valid_length_forged_digest_rejected(self):
        self.row["fixture"]["data_sha256"] = "0" * 64
        self.rejected()

    def test_forged_corpus_digest_rejected(self):
        self.report["corpus_sha256"] = "0" * 64
        self.rejected()

    def test_unregistered_case_rejected(self):
        self.row["id"] = "unregistered"
        self.report["expected_cases"] = ["unregistered"]
        self.rejected()

    def test_materialization_target_outside_project_rejected(self):
        case = {**cg.registered("compat_engine")[self.cid], "script_dest": "../../outside.py"}
        with self.assertRaises(cg.EvidenceError):
            cg.fixture_files("compat_engine", case)

    def test_child_environment_mismatch_rejected(self):
        child = {"facts": {"different": True}}
        child["fingerprint"] = cg.canonical(child["facts"])
        self.row["raw"]["launch_environments"]["tavotto"] = [child]
        self.rejected()

    def test_evidence_tampering_rejected(self):
        (self.ev / "raw.json").write_text("tampered", encoding="utf-8")
        self.rejected()

    def test_duplicate_case_rejected(self):
        self.report["cases"].append(copy.deepcopy(self.row))
        self.rejected()

    def test_symlink_evidence_outside_root_rejected(self):
        target = self.root / "private.txt"
        target.write_text("outside evidence", encoding="utf-8")
        (self.ev / "raw.json").unlink()
        try:
            (self.ev / "raw.json").symlink_to(target)
        except OSError:
            self.skipTest("platform cannot create symlink")
        self.rejected()

    def test_child_environment_is_local_and_strips_credentials(self):
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "never-export",
                "TAVOTTO_DATA_DIR": "/real/user",
                "HOME": "/real/home",
            },
        ):
            env = cg.isolated_env(self.root / "launch")
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertTrue(Path(env["HOME"]).is_relative_to(self.root))
        self.assertTrue(Path(env["TAVOTTO_DATA_DIR"]).is_relative_to(self.root))
        self.assertEqual(env["TAVOTTO_NO_DIAG_UPLOAD"], "1")

    def test_exec_counter_measures_real_script_entry(self):
        script = self.root / "script.py"
        script.write_text("print('trusted test fixture')\n", encoding="utf-8")
        counter = self.root / "exec.jsonl"
        env = cg.isolated_env(self.root, counter, [cg.digest(script.read_bytes())])
        out = subprocess.run(
            [sys.executable, str(script)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        records = [json.loads(line) for line in counter.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_sha256"], cg.digest(script.read_bytes()))

    def test_atomic_publication_preserves_complete_previous_report(self):
        dest = cg.write_report("r.json", {"old": True}, root=self.root)
        with mock.patch("_common.os.replace", side_effect=OSError("simulated failed publish")):
            with self.assertRaises(OSError):
                cg.write_report("r.json", {"new": True}, root=self.root)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")), {"old": True})

    def test_partial_coverage_row_is_accepted_but_is_not_a_pass(self):
        for name in cg.V2_UNCOVERED:
            self.row["stages"][name] = cg.stage("not_covered", "layer not exercised")
        self.row["status"] = "partial_coverage"
        self.report["verdict"] = "partial_coverage"
        self.report["coverage"] = cg.coverage_table([self.row])
        self.valid()
        self.assertEqual(cg.exit_code_for("partial_coverage"), cg.EXIT_PARTIAL)
        self.assertNotEqual(cg.exit_code_for("partial_coverage"), cg.EXIT_PASSED)
        self.assertEqual(cg.verdict([self.row]), "partial_coverage")

    def test_aggregate_verdict_for_declared_limitations_and_partial_rows(self):
        def row(status):
            return {"status": status}

        self.assertEqual(
            cg.verdict([row("passed"), row("expected_limitation")]), "expected_limitation"
        )
        self.assertEqual(
            cg.verdict([row("partial_coverage"), row("expected_limitation")]), "partial_coverage"
        )
        self.assertEqual(cg.verdict([row("partial_coverage"), row("failed")]), "failed")
        self.assertEqual(cg.verdict([row("partial_coverage"), row("unknown")]), "unknown")
        self.assertEqual(cg.exit_code_for("expected_limitation"), cg.EXIT_PARTIAL)
        self.assertEqual(cg.exit_code_for("passed"), cg.EXIT_PASSED)

    def test_partial_coverage_without_an_uncovered_layer_rejected(self):
        self.row["status"] = "partial_coverage"
        self.report["verdict"] = "partial_coverage"
        self.rejected()

    def test_not_covered_layer_never_counts_as_green(self):
        self.row["stages"]["export"] = cg.stage("not_covered", "layer not exercised")
        self.assertEqual(cg.case_status(self.row), "partial_coverage")
        self.row["status"] = "passed"
        self.rejected()

    def test_failure_category_must_be_set_on_non_green_and_absent_on_green(self):
        self.row["failure_category"]["category"] = "figure_capture"
        self.rejected()

    def test_failure_category_outside_the_taxonomy_rejected(self):
        self.row["status"] = "unknown"
        self.row["failure_category"]["category"] = "made_up_family"
        self.report["verdict"] = "unknown"
        self.rejected()

    def test_every_non_green_row_gets_a_category_and_unknown_is_allowed(self):
        row = cg.new_row("x", "contract_fixture")
        self.assertEqual(cg.finalize_row(row)["failure_category"]["category"], "unknown")

    def test_taxonomy_mapping_only_points_into_the_closed_set(self):
        import compat_guardian_taxonomy as tx

        self.assertEqual(len(set(tx.CATEGORIES)), len(tx.CATEGORIES))
        for value in (*tx.CODE_TO_CATEGORY.values(), *tx.STAGE_TO_CATEGORY.values()):
            self.assertIn(value, tx.CATEGORIES)
        self.assertEqual(tx.category_of_code("never_seen_before"), "unknown")
        self.assertEqual(set(tx.STAGE_TO_CATEGORY), set(cg.CC.STAGES))

    def test_every_status_a_report_can_hold_is_in_the_schema_enums(self):
        schema = json.loads(cg.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["$defs"]["row_status"]["enum"], list(cg.ROW_STATUSES))
        self.assertEqual(schema["$defs"]["stage_status"]["enum"], list(cg.STAGE_STATUSES))
        self.assertEqual(
            schema["$defs"]["case"]["properties"]["failure_category"]["properties"]["category"][
                "enum"
            ],
            [None, *__import__("compat_guardian_taxonomy").CATEGORIES],
        )

    def test_every_case_row_declares_timings_authorization_and_category(self):
        row = cg.new_row("x", "contract_fixture")
        self.assertEqual(set(row["timings_s"]["stages"]), set(cg.CC.STAGES))
        self.assertIn("required", row["needs_authorization"])
        self.assertIn("category", row["failure_category"])
        self.assertIsNone(row["timings_s"]["native"], "unmeasured must be null, never 0")

    def test_fonts_dir_override_is_exclusive_and_forwarded_to_the_adapter(self):
        override = self.root / "fonts"
        override.mkdir()
        with mock.patch.dict(os.environ, {"TAVOTTO_FONTS_DIR": str(override)}):
            self.assertEqual(cg.fonts_dir(), override)
            missing = cg.preflight()
            self.assertTrue(any(item.startswith("font:") for item in missing))
            env = cg.isolated_env(self.root / "launch-fonts")
            self.assertEqual(env["TAVOTTO_FONTS_DIR"], str(override))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TAVOTTO_FONTS_DIR", None)
            self.assertEqual(cg.fonts_dir(), cg.ROOT / "src/tavotto/resources/fonts")

    def test_environment_in_reports_has_version_summary_and_digest_not_the_full_inventory(self):
        facts = cg.environment()["facts"]
        self.assertNotIn("distributions", facts)
        self.assertRegex(facts["distributions_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(facts["distribution_count"], 0)
        self.assertLess(len(json.dumps(facts)), 4000)

    def test_stub_subcommands_exit_nonzero_and_produce_nothing(self):
        for name in ("explore", "triage", "minimize"):
            with mock.patch("sys.stderr") as err:
                self.assertEqual(cg.main([name]), cg.EXIT_USAGE)
            self.assertIn(
                "not implemented in CG-01", "".join(c.args[0] for c in err.write.call_args_list)
            )

    def test_public_schema_independent_reader(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema independent reader unavailable")
        schema = json.loads(cg.SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(self.report, schema)
        del self.row["fixture"]["source_sha256"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(self.report, schema)


if __name__ == "__main__":
    unittest.main()


class MatchesHeadLineEndings(unittest.TestCase):
    """Windows checkout 按 `core.autocrlf` 把 LF 写成 CRLF：同一份评审过的内容不能因换行被判「与 HEAD 不同」，
    真改了内容仍要判不同（windows-latest backend-platforms 上 `execution metadata differs` 的根因）。"""

    def test_crlf_checkout_matches_but_a_real_edit_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

            def git(*args):
                subprocess.run(["git", *args], cwd=root, check=True, env=env, capture_output=True)

            git("init", "-q")
            git("config", "core.autocrlf", "true")
            (root / "m.json").write_bytes(b'{"a": 1}\n')
            git("add", "m.json")
            git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x")
            with mock.patch.dict(os.environ, env):
                (root / "m.json").write_bytes(b'{"a": 1}\r\n')
                self.assertTrue(cg.matches_head(root, "m.json"))
                (root / "m.json").write_bytes(b'{"a": 2}\r\n')
                self.assertFalse(cg.matches_head(root, "m.json"))


class EvidenceRefsArePosix(unittest.TestCase):
    """证据引用的相对路径一律 posix：Windows 上 `str(relative_to)` 带 `\\`，会被 `contained()` 判成不安全
    （windows-latest backend-platforms 的 `unsafe relative path: shape_oo_savefig\\process.json`）。"""

    def test_nested_evidence_paths_use_forward_slashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "case" / "sub").mkdir(parents=True)
            (root / "case" / "sub" / "process.json").write_text("{}", encoding="utf-8")
            (ref,) = cg.evidence_refs(root)
            self.assertEqual(ref["path"], "case/sub/process.json")


class StagedSourceHashes(unittest.TestCase):
    """观测器认的是实际拷进沙盒的字节，不是 HEAD blob 的摘要（Windows CRLF checkout，Codex r4310318506）。"""

    def test_hashes_follow_the_working_tree_bytes_not_the_head_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "c").mkdir()
            (root / "c" / "plot.py").write_bytes(b"print(1)\r\n")
            (root / "c" / "data.csv").write_bytes(b"1\r\n")
            head_identity = {"c/plot.py": cg.digest(b"print(1)\n"), "c/data.csv": "x"}
            got = cg.staged_source_hashes(root, head_identity, "plot.py")
            self.assertEqual(got, [cg.digest(b"print(1)\r\n")])
            self.assertNotIn(head_identity["c/plot.py"], got)
