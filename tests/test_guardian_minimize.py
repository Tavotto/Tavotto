"""Synthetic retained-packet tests; no native/Tavotto candidate executes here."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import guardian_minimize as gm  # noqa: E402


class MinimizeContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cg-min-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = {"script.py": b"x=1\ny=2\n", "data/input.csv": b"x,y\n1,2\n"}
        self.source = {"commit": "a" * 40, "tree": "b" * 40, "dirty": False, "content_sha256": "c" * 64}
        self.plan = "d" * 64
        self.truth = {"figures": {"plot": ["truth"]}}
        self.env = {"facts": {"contract_fixture": True}}
        self.env["fingerprint"] = gm.cg.canonical(self.env["facts"])
        self.sequence = 0

    def packet(self, files=None, mutate=None):
        files = self.files if files is None else files
        self.sequence += 1
        dest = self.root / str(self.sequence)
        dest.mkdir()
        for rel, data in files.items():
            path = dest / "reviewed-inputs" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        raw = {
            "schema": 2, "run_id": f"{self.sequence:032x}", "source_before": self.source,
            "source_after": copy.deepcopy(self.source), "plan_sha256": self.plan, "files": gm.graph(files),
            "environment_before": self.env, "environment_after": copy.deepcopy(self.env),
            "native": {"ok": True, "stems": ["plot"], "semantics": {"plot": {"titles": ["truth"]}}},
            "tavotto": {"ran": True, "exec_count": 1, "figures_captured": 1, "figures_valid": 1,
                        "captured_stems": ["plot"], "failed_figures": [], "render_errors": [],
                        "titles": ["wrong"], "final_phase": "completed", "problem_codes": [],
                        "error_type": None, "facts": {"exec_counts_at_checks": [0, 0]}}}
        if mutate:
            mutate(raw)
        gm.cg.write_report("probe-raw.json", raw, root=dest)
        event = {"pid": 1, "source_sha256": gm.cg.digest(files["script.py"]), "environment": self.env}
        for role in ("native", "tavotto"):
            (dest / f"{role}-execs.jsonl").write_text(json.dumps(event) + "\n")
        (dest / "fixture-execs.jsonl").write_text('{"contract_fixture":true}\n')
        (dest / "stdout.txt").write_text("synthetic packet; no product execution\n")
        (dest / "process.json").write_text(json.dumps({"exit_code": 0, "timed_out": False,
                                                     "cleanup": {"group_gone": True, "returncode": 0}}))
        return gm.Packet(dest, gm.cg.evidence_refs(dest))

    def verify(self, packet, files=None, truth=None):
        return gm.verify_packet(packet, self.files if files is None else files, source=self.source,
                                plan_sha256=self.plan, truth=self.truth if truth is None else truth)

    def minimize(self, oracle, approved, **kw):
        return gm.minimize(self.files, "script.py", oracle, approved, attempts=kw.get("attempts", 1),
                           seconds=kw.get("seconds", 2), source=self.source,
                           plan_sha256=self.plan, truth=self.truth)

    def test_dictionary_cannot_self_authorize_proof_or_cluster(self):
        fake = {"status": "failed", "native_correct": True, "comparable": True,
                "source_sha256": "wrong", "environment": None, "signature": {},
                "evidence_sha256": "not-an-artifact", "confirmed_cause": {"id": "same", "evidence_sha256": "fake"}}
        self.assertFalse(gm.accepts(fake, fake, "wrong"))
        with self.assertRaises(gm.cg.EvidenceError):
            self.verify(fake)
        self.assertEqual(gm.cluster([fake, copy.deepcopy(fake)])["confirmed"], {})

    def test_valid_retained_failure_is_verified(self):
        proof = self.verify(self.packet())
        self.assertTrue(gm.accepts(proof, proof, gm.cg.canonical(gm.graph(self.files))))
        self.assertEqual(proof.signature["stage"], "semantic")

    def test_wrong_candidate_graph_rejected(self):
        with self.assertRaises(gm.cg.EvidenceError):
            self.verify(self.packet(), {**self.files, "script.py": b"x=9\n"})
        with self.assertRaises(gm.cg.EvidenceError):
            self.verify(self.packet(), {**self.files, "data/input.csv": b"x,y\n6,601\n"})
        with self.assertRaises(gm.cg.EvidenceError):
            self.verify(self.packet(mutate=lambda raw: raw["files"].update({"data/input.csv": "e" * 64})))
        proof = self.verify(self.packet())
        self.assertFalse(gm.accepts(proof, proof, "e" * 64))

    def test_tampered_evidence_rejected(self):
        packet = self.packet()
        (packet.root / "stdout.txt").write_text("changed retained evidence\n")
        with self.assertRaises(gm.cg.EvidenceError):
            self.verify(packet)

    def test_retained_input_graph_must_match_expected_subject(self):
        packet = self.packet()
        (packet.root / "reviewed-inputs/data/input.csv").write_text("decoy\n601\n")
        with self.assertRaises(gm.cg.EvidenceError):
            self.verify(gm.Packet(packet.root, gm.cg.evidence_refs(packet.root)))

    def test_complete_raw_packet_cannot_hide_timeout_or_cleanup_uncertainty(self):
        for field, value in (("timed_out", True), ("exit_code", 1), ("exit_code", False),
                             ("cleanup", {"group_gone": False, "returncode": 0}),
                             ("cleanup", {"group_gone": None, "returncode": 0})):
            packet = self.packet()
            path = packet.root / "process.json"
            process = json.loads(path.read_text())
            process[field] = value
            path.write_text(json.dumps(process))
            with self.assertRaises(gm.cg.EvidenceError):
                self.verify(gm.Packet(packet.root, gm.cg.evidence_refs(packet.root)))

    def test_wrong_counter_subject_rejected(self):
        packet = self.packet()
        path = packet.root / "native-execs.jsonl"
        event = json.loads(path.read_text())
        event["source_sha256"] = "e" * 64
        path.write_text(json.dumps(event) + "\n")
        with self.assertRaises(gm.cg.EvidenceError):
            self.verify(gm.Packet(packet.root, gm.cg.evidence_refs(packet.root)))

    def test_implementation_or_plan_change_rejected(self):
        for key, value in (("source_after", {}), ("plan_sha256", "e" * 64), ("run_id", "")):
            with self.assertRaises(gm.cg.EvidenceError):
                self.verify(self.packet(mutate=lambda raw: raw.update({key: value})))

    def test_native_missing_package_and_error_are_not_original_defect(self):
        for traceback, status in (("ModuleNotFoundError: missing", "environment_unavailable"),
                                  ("SyntaxError: invalid", "invalid_fixture")):
            def change(raw):
                raw["native"] = {"ok": False, "code": "native_script_error", "traceback": traceback}
            bad = self.verify(self.packet(mutate=change))
            self.assertEqual(bad.status, status)
            self.assertFalse(gm.accepts(bad, bad, gm.cg.canonical(gm.graph(self.files))))

    def test_native_wrong_scientific_truth_is_invalid_fixture(self):
        proof = self.verify(self.packet(mutate=lambda r: r["native"]["semantics"]["plot"].update(titles=["decoy"])))
        self.assertEqual(proof.status, "invalid_fixture")

    def test_environment_mismatch_and_unobserved_count_rejected(self):
        packet = self.packet()
        path = packet.root / "tavotto-execs.jsonl"
        event = json.loads(path.read_text())
        env = {"facts": {"other": True}}
        env["fingerprint"] = gm.cg.canonical(env["facts"])
        event["environment"] = env
        path.write_text(json.dumps(event) + "\n")
        proof = self.verify(gm.Packet(packet.root, gm.cg.evidence_refs(packet.root)))
        self.assertEqual(proof.status, "unknown")
        packet = self.packet()
        (packet.root / "fixture-execs.jsonl").write_text("")
        self.assertEqual(self.verify(gm.Packet(packet.root, gm.cg.evidence_refs(packet.root))).status, "unknown")

    def test_same_stage_different_wrong_value_cannot_reduce(self):
        original = self.verify(self.packet())
        different = self.verify(self.packet(mutate=lambda r: r["tavotto"].update(titles=["other wrong value"])))
        self.assertFalse(gm.accepts(original, different, gm.cg.canonical(gm.graph(self.files))))

    def test_verified_packets_from_different_implementations_cannot_mix(self):
        original = self.verify(self.packet())
        self.source = {**self.source, "commit": "e" * 40}
        other = self.verify(self.packet())
        self.assertFalse(gm.accepts(original, other, gm.cg.canonical(gm.graph(self.files))))

    def test_untitled_figure_loss_preserves_capture_failure(self):
        def change(raw):
            raw["native"] = {"ok": True, "stems": ["good", "bad"],
                             "semantics": {"good": {"titles": ["good"]}, "bad": {"titles": [""]}}}
            raw["tavotto"].update(captured_stems=["good"], titles=["good"])
        proof = self.verify(self.packet(mutate=change), truth={"figures": {"good": ["good"], "bad": [""]}})
        self.assertEqual(proof.status, "failed")
        self.assertEqual(proof.signature["stage"], "capture")
        self.assertEqual(proof.signature["criterion_delta"]["expected_count"], 2)

    def test_both_correct_does_not_invent_defect(self):
        proof = self.verify(self.packet(mutate=lambda r: r["tavotto"].update(titles=["truth"])))
        self.assertEqual(proof.status, "not_reproduced")

    def test_action_required_before_capture_keeps_execution_failure_stage(self):
        def change(raw):
            raw["tavotto"].update(final_phase="action_required", figures_captured=0,
                                 figures_valid=0, captured_stems=[], titles=[], problem_codes=["safe_file_operation_blocked"])
        proof = self.verify(self.packet(mutate=change))
        self.assertEqual(proof.status, "failed")
        self.assertEqual(proof.signature["stage"], "execute")

    def test_global_titles_cannot_prove_multifigure_association(self):
        def change(raw):
            raw["native"] = {"ok": True, "stems": ["one", "two"],
                             "semantics": {"one": {"titles": ["A"]}, "two": {"titles": ["B"]}}}
            raw["tavotto"].update(figures_captured=2, figures_valid=2, captured_stems=["one", "two"], titles=["B", "A"])
        proof = self.verify(self.packet(mutate=change), truth={"figures": {"one": ["A"], "two": ["B"]}})
        self.assertEqual(proof.status, "unknown")
        diagnosis = gm.diagnose(gm.asdict(proof))
        self.assertEqual(diagnosis["category"], "unknown")
        self.assertIsNone(diagnosis["product_defect"])
        clustered = gm.cluster([gm.asdict(proof)])
        self.assertEqual(clustered["hypotheses"][0]["diagnosis"]["execution_status"], "unknown")

    def test_finite_budgets_rejected_before_oracle(self):
        calls = []
        for seconds in (float("inf"), float("nan"), 0, -1):
            with self.assertRaises(gm.cg.EvidenceError):
                self.minimize(lambda *args: calls.append(args), set(), seconds=seconds)
        self.assertEqual(calls, [])

    def test_unreviewed_original_and_reductions_never_execute(self):
        calls = []
        def oracle(candidate, remaining):
            calls.append(candidate)
            return self.packet(candidate)
        result = self.minimize(oracle, set())
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(calls, [])
        result = self.minimize(oracle, {gm.cg.canonical(gm.graph(self.files))})
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "not_reduced")
        self.assertTrue(result["pending_review"])

    def test_every_acceptance_rechecks_candidate_packet_and_preserves_files(self):
        candidates = [{**self.files, "script.py": code} for _, code in gm.deletion_candidates(self.files["script.py"])]
        approved = {gm.cg.canonical(gm.graph(c)) for c in [self.files, *candidates]}
        calls = []
        def oracle(candidate, remaining):
            calls.append(candidate)
            return self.packet(candidate)
        result = self.minimize(oracle, approved)
        self.assertEqual(result["status"], "minimized")
        self.assertEqual(result["oracle_calls"], 2)
        self.assertEqual(result["candidate_attempts"], 1)
        self.assertTrue(result["preserved_relative_names"])
        self.assertTrue(result["unchanged_non_entry_bytes"])
        self.assertEqual(calls[-1]["data/input.csv"], self.files["data/input.csv"])

    def test_interrupted_trial_retains_history_without_false_acceptance(self):
        approved = {gm.cg.canonical(gm.graph(c)) for c in
                    [self.files, *[{**self.files, "script.py": code} for _, code in gm.deletion_candidates(self.files["script.py"])]]}
        calls = []
        def oracle(candidate, remaining):
            calls.append(candidate)
            if len(calls) > 1:
                raise gm.cg.EvidenceError("simulated timeout; partial files retained")
            return self.packet(candidate)
        result = self.minimize(oracle, approved)
        self.assertEqual(result["status"], "not_reduced")
        self.assertEqual(len(result["history"]), 1)
        self.assertFalse(result["history"][0]["accepted"])


if __name__ == "__main__":
    unittest.main()
