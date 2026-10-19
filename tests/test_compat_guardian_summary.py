"""`summarize` and `promote`: aggregation never invents a rate; promotion is proposal-only."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_guardian as cg
import compat_guardian_summary as sm
from test_compat_guardian import GuardianBase


class SummaryAndPromote(GuardianBase):
    def write(self, name: str, report: dict | None = None) -> Path:
        path = self.root / name
        path.write_text(json.dumps(report or self.report), encoding="utf-8")
        return path

    @staticmethod
    def rebind_env(row: dict, facts: dict) -> None:
        """Keep a row internally consistent after changing its environment facts."""
        env = {"facts": facts, "fingerprint": cg.canonical(facts)}
        row["environment_before"] = copy.deepcopy(env)
        row["environment_after"] = copy.deepcopy(env)
        for records in row["raw"]["launch_environments"].values():
            for record in records:
                record.update(copy.deepcopy(env))

    def variant(self, **top) -> dict:
        report = copy.deepcopy(self.report)
        report.update(top)
        return report

    def test_same_corpus_and_env_aggregate_with_an_explicit_denominator(self):
        out = sm.summarize(
            [sm.load_report(self.write("a.json")), sm.load_report(self.write("b.json"))]
        )
        (group,) = out["groups"]
        self.assertEqual((group["n"], group["passed"]), (2, "2/2"))
        self.assertFalse(out["incomparable"])

    def test_different_corpus_is_marked_incomparable_and_never_combined(self):
        other = self.variant(corpus_sha256="1" * 64)
        out = sm.summarize(
            [sm.load_report(self.write("a.json")), sm.load_report(self.write("b.json", other))]
        )
        self.assertEqual(len(out["groups"]), 2)
        self.assertTrue(out["incomparable"])
        self.assertIn("corpus version differs between groups", out["incomparable_reasons"])
        self.assertIn("INCOMPARABLE", sm.render_markdown(out))

    def test_different_environment_is_incomparable(self):
        other = self.variant()
        self.rebind_env(other["cases"][0], {"other": True})
        out = sm.summarize(
            [sm.load_report(self.write("a.json")), sm.load_report(self.write("b.json", other))]
        )
        self.assertTrue(out["incomparable"])
        self.assertIn("environment differs between groups", out["incomparable_reasons"])

    def test_partial_coverage_and_failures_stay_in_the_denominator(self):
        partial = self.variant()
        row = partial["cases"][0]
        for name in cg.V2_UNCOVERED:
            row["stages"][name] = cg.stage("not_covered", "x")
        row["status"] = "partial_coverage"
        partial["verdict"] = "partial_coverage"
        partial["coverage"] = cg.coverage_table(partial["cases"])
        out = sm.summarize(
            [sm.load_report(self.write("a.json")), sm.load_report(self.write("p.json", partial))]
        )
        (group,) = out["groups"]
        self.assertEqual(group["passed"], "1/2", "partial_coverage must not count as passed")
        self.assertEqual(group["status_counts"], {"partial_coverage": 1, "passed": 1})
        self.assertEqual(group["not_covered_stage_counts"]["export"], 1)
        self.assertIn("layers not covered", sm.render_markdown(out))

    def test_inconsistent_report_is_rejected_not_averaged_in(self):
        lying = self.variant(verdict="unknown")
        with self.assertRaises(cg.EvidenceError):
            sm.load_report(self.write("lie.json", lying))

    def test_a_row_its_own_facts_cannot_support_is_rejected_not_counted(self):
        lying = self.variant()
        row = lying["cases"][0]
        self.assertEqual(row["status"], "passed")
        row["executions"]["tavotto"] = 0  # a pass with no Tavotto execution proves nothing
        with self.assertRaises(cg.EvidenceError) as caught:
            sm.load_report(self.write("zero.json", lying))
        self.assertIn("proof invariants", str(caught.exception))
        forged = self.variant()
        forged["cases"][0]["status"] = "control_met"
        forged["cases"][0]["failure_category"] = {
            "category": None,
            "source_code": None,
            "basis": "x",
        }
        forged["verdict"] = cg.verdict(forged["cases"])
        forged["coverage"] = cg.coverage_table(forged["cases"])
        with self.assertRaises(cg.EvidenceError):
            sm.load_report(self.write("forged.json", forged))

    def test_a_self_declared_mismatch_never_waives_proof_for_a_green_row(self):
        lying = self.variant()
        row = lying["cases"][0]
        row["executions"]["tavotto"] = 0
        row["expectation"] = {
            "declared_outcome": "expected_limitation",
            "observed_outcome": "passed",
            "result": "mismatch",
            "reason": "x" * 30,
            "references": [{"kind": "issue", "ref": "#1"}],
            "diff": "declared expected_limitation but observed passed",
        }
        with self.assertRaises(cg.EvidenceError) as caught:
            sm.load_report(self.write("mismatch-lie.json", lying))
        self.assertIn("proof invariants", str(caught.exception))

    def test_unsupported_schema_field_is_rejected(self):
        extra = self.variant(surprise=True)
        with self.assertRaises(cg.EvidenceError):
            sm.load_report(self.write("extra.json", extra))

    def test_markdown_carries_no_package_inventory_and_no_raw_text(self):
        report = self.variant()
        facts = copy.deepcopy(report["cases"][0]["environment_before"]["facts"])
        facts["distributions"] = ["secret-pkg==1.0"]
        self.rebind_env(report["cases"][0], facts)
        text = sm.render_markdown(sm.summarize([sm.load_report(self.write("a.json", report))]))
        self.assertNotIn("secret-pkg", text)
        self.assertNotIn("Traceback", text)

    def test_cli_summarize_writes_requested_outputs(self):
        path = self.write("a.json")
        out_json, out_md = self.root / "s.json", self.root / "s.md"
        code = cg.main(
            ["summarize", str(path), "--json-out", str(out_json), "--markdown-out", str(out_md)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out_json.read_text(encoding="utf-8"))["schema"], sm.SUMMARY_SCHEMA
        )
        self.assertIn("passed 1/1", out_md.read_text(encoding="utf-8"))

    # ------------------------------------------------------------ promote
    def test_promote_emits_a_proposal_and_changes_nothing_else(self):
        protected = [
            cg.ROOT / "tests/compat/manifest.json",
            cg.ROOT / "tests/compat/baseline.json",
        ]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
        out = self.root / "candidate.json"
        record = sm.promote(sm.load_report(self.write("a.json")), self.cid, out)
        self.assertTrue(record["proposal_only"] and not record["applied"])
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), record)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected})

    def test_promote_refuses_to_write_into_the_corpus_or_baseline(self):
        report = sm.load_report(self.write("a.json"))
        for target in (
            cg.ROOT / "tests/compat/baseline.json",
            cg.ROOT / "tests/compat/manifest.json",
            cg.ROOT / "tests/compat/new-case.json",
            cg.ROOT / "tests/fixtures/compat_v2/s99/script.py",
            cg.ROOT / "tests/compat_guardian/expectations.json",
        ):
            with self.assertRaises(cg.EvidenceError, msg=str(target)):
                sm.promote(report, self.cid, target)
            self.assertFalse(target.name == "new-case.json" and target.exists())

    def test_promote_marks_a_dirty_or_undecided_row_ineligible(self):
        dirty = self.variant()
        dirty["source"]["dirty"] = True
        record = sm.promote(
            sm.load_report(self.write("d.json", dirty)), self.cid, self.root / "c1.json"
        )
        self.assertFalse(record["eligible_for_review"])
        self.assertIn("report was produced on a dirty tree", record["ineligible_reasons"])
        unknown = self.variant()
        unknown["cases"][0]["status"] = "unknown"
        unknown["cases"][0]["failure_category"] = {
            "category": "unknown",
            "source_code": None,
            "basis": "x",
        }
        unknown["verdict"] = "unknown"
        unknown["coverage"] = cg.coverage_table(unknown["cases"])
        record = sm.promote(
            sm.load_report(self.write("u.json", unknown)), self.cid, self.root / "c2.json"
        )
        self.assertFalse(record["eligible_for_review"])

    def test_promote_unknown_case_is_an_error(self):
        with self.assertRaises(cg.EvidenceError):
            sm.promote(sm.load_report(self.write("a.json")), "not-in-report", self.root / "c.json")
