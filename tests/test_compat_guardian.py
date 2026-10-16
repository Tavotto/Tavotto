"""Guardian evidence-contract tests, runnable without pytest or product dependencies.

These synthetic protocol records test the validator, never claim product success.
Actual native/product execution belongs in the CLI artifacts, not these records.
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


class GuardianContract(unittest.TestCase):
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
        (self.ev / "raw.json").write_text('{"contract_fixture":true}')
        self.source = {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "dirty": False,
            "content_sha256": "c" * 64,
        }
        environment = {"facts": {"contract_fixture": True}}
        environment["fingerprint"] = cg.canonical(environment["facts"])
        self.row = {
            "id": self.cid,
            "status": "passed",
            "layer": "contract_fixture",
            "profile": "safe",
            "desktop_shell_verified": False,
            "stages": {
                s: cg.stage("passed", "synthetic validator test only") for s in cg.CC.STAGES
            },
            "executions": {"native": 1, "tavotto": 1, "basis": "contract_fixture"},
            "native": {"status": "passed", "result": {"contract_fixture": True}},
            "comparable": True,
            "environment_before": environment,
            "environment_after": copy.deepcopy(environment),
            "product_classification": None,
            "raw": {
                "launch_environments": {
                    "native": [environment],
                    "tavotto": [copy.deepcopy(environment)],
                }
            },
            "unverified": ["all product behavior"],
            "fixture": copy.deepcopy(self.fixture),
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
            "unverified": ["all product behavior"],
            "duration_s": 1.0,
        }

    def valid(self):
        cg.validate_report(self.report, self.ev, self.source)

    def rejected(self):
        with self.assertRaises(cg.EvidenceError):
            self.valid()

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

    def test_both_graphs_with_semantic_difference_fail(self):
        self.row["status"] = "failed"
        self.row["stages"]["semantic"]["status"] = "failed"
        self.report["verdict"] = "failed"
        self.valid()

    def test_both_correct_do_not_invent_defect(self):
        self.assertEqual(cg.case_status(self.row), "passed")

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
        process = json.loads((self.ev / "process.json").read_text())
        self.assertTrue(process["timed_out"])
        self.assertIn("started", (self.ev / "stdout.txt").read_text())
        if os.name != "nt":
            self.assertTrue(process["cleanup"]["group_gone"])

    def test_partial_adapter_retains_counter_evidence(self):
        raw = '{"contract_fixture":"partial native execution"}\n'

        def interrupted(command, *, work, evidence, env, timeout):
            (work / "native-execs.jsonl").write_text(raw)
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
        report = json.loads(report_path.read_text())
        retained = report_path.parent.parent / "evidence" / self.cid / "native-execs.jsonl"
        self.assertEqual(retained.read_text(), raw)
        refs = {item["path"]: item for item in report["evidence"]}
        self.assertEqual(
            refs[f"{self.cid}/native-execs.jsonl"]["sha256"], cg.digest(raw.encode())
        )

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
        (self.ev / "raw.json").write_text("tampered")
        self.rejected()

    def test_duplicate_case_rejected(self):
        self.report["cases"].append(copy.deepcopy(self.row))
        self.rejected()

    def test_symlink_evidence_outside_root_rejected(self):
        target = self.root / "private.txt"
        target.write_text("outside evidence")
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
        script.write_text("print('trusted test fixture')\n")
        counter = self.root / "exec.jsonl"
        env = cg.isolated_env(self.root, counter, [cg.digest(script.read_bytes())])
        out = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        records = [json.loads(line) for line in counter.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_sha256"], cg.digest(script.read_bytes()))

    def test_atomic_publication_preserves_complete_previous_report(self):
        dest = cg.write_report("r.json", {"old": True}, root=self.root)
        with mock.patch("_common.os.replace", side_effect=OSError("simulated failed publish")):
            with self.assertRaises(OSError):
                cg.write_report("r.json", {"new": True}, root=self.root)
        self.assertEqual(json.loads(dest.read_text()), {"old": True})

    def test_public_schema_independent_reader(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema independent reader unavailable")
        schema = json.loads(cg.SCHEMA_PATH.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(self.report, schema)
        del self.row["fixture"]["source_sha256"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(self.report, schema)


if __name__ == "__main__":
    unittest.main()
