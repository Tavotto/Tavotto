"""Trend summary: fixed-set regressions vs scope changes, environment comparability, honest metrics."""

from __future__ import annotations

import ast
import copy
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_guardian as cg  # noqa: E402
import compat_guardian_trend as tr  # noqa: E402
import compat_triage as tg  # noqa: E402
from test_compat_guardian import GuardianBase  # noqa: E402


class Trend(GuardianBase):
    def make_report(self, rows: list[dict], **top) -> dict:
        report = copy.deepcopy(self.report)
        report["cases"] = rows
        report["expected_cases"] = [r["id"] for r in rows]
        report["coverage"] = cg.coverage_table(rows)
        report["verdict"] = cg.verdict(rows)
        report.update(top)
        return report

    def case(self, name: str, status="passed", fixture="a", env=None):
        row = copy.deepcopy(self.row)
        row["id"] = name
        row["status"] = status
        row["fixture"] = {
            "files": {f"tests/x/{name}.py": fixture},
            "source_sha256": "0" * 64,
            "data_sha256": "0" * 64,
        }
        if env:
            row["environment_before"] = row["environment_after"] = env
        if status == "failed":
            row["stages"]["capture"] = cg.stage("failed", "x")
            row["failure_category"] = {
                "category": "figure_capture",
                "source_code": "stage:capture",
                "basis": "x",
            }
        return row

    def write(self, name: str, report: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def summary(self, rows, previous=None, corpus="1" * 64, env=None):
        rows = [self.case(*r) if isinstance(r, tuple) else r for r in rows]
        report = self.make_report(rows, corpus_sha256=corpus)
        path = self.write(f"r{len(list(self.root.iterdir()))}.json", report)
        return tr.build([path], [], previous, [])

    # ---- metrics
    def test_section_12_metrics_are_present_and_honestly_marked(self):
        out = self.summary([("a",), ("b",)])
        metrics = out["metrics"]
        self.assertEqual(
            set(metrics),
            {
                "first_figure_success",
                "execution_attempts",
                "semantic_fidelity",
                "editable_export_success",
                "family_coverage",
                "real_user_success_rate",
            },
        )
        self.assertEqual(metrics["family_coverage"]["status"], "not_computed")
        self.assertIn("no explore report", metrics["family_coverage"]["reason"])
        user = metrics["real_user_success_rate"]
        self.assertEqual(user["status"], "not_computed")
        self.assertIn("not be read as user success", user["reason"])
        ffs = metrics["first_figure_success"]["compat_engine"]
        self.assertEqual((ffs["status"], ffs["numerator"], ffs["denominator"]), ("computed", 2, 2))
        self.assertIn("not real users", ffs["population"])

    def test_adapter_that_does_not_drive_a_layer_is_not_computed_not_zero(self):
        rows = [self.case("a")]
        for name in cg.V2_UNCOVERED:
            rows[0]["stages"][name] = cg.stage("not_covered", "x")
        rows[0]["status"] = "partial_coverage"
        out = self.summary(rows)
        ee = out["metrics"]["editable_export_success"]["compat_engine"]
        self.assertEqual(ee["status"], "not_computed")
        self.assertIn("not_covered", ee["reason"])

    def test_failures_stay_in_the_denominator(self):
        out = self.summary([("a",), ("b", "failed")])
        ffs = out["metrics"]["first_figure_success"]["compat_engine"]
        self.assertEqual((ffs["numerator"], ffs["denominator"]), (1, 2))

    def test_unmeasured_executions_are_unmeasured_not_zero(self):
        rows = [self.case("a")]
        rows[0]["executions"]["tavotto"] = None
        out = self.summary(rows)
        self.assertEqual(
            out["metrics"]["execution_attempts"]["compat_engine"]["status"], "not_computed"
        )

    # ---- comparison
    def baseline(self):
        return self.summary([("a",), ("b",), ("c", "failed")])

    def test_no_previous_summary_means_trend_and_scope_are_not_computed(self):
        self.assertEqual(self.baseline()["comparison"]["status"], "no_previous")

    def test_same_cases_same_environment_regression_is_detected_on_the_fixed_set(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b", "failed"), ("c", "failed")], previous)
        cmp_ = current["comparison"]["fixed_set"]
        self.assertEqual(cmp_["verdict"], "regression")
        self.assertEqual(
            [(t["case"], t["kind"]) for t in cmp_["transitions"]],
            [("compat_engine/b", "regression")],
        )

    def test_removing_a_hard_case_is_a_scope_change_never_an_improvement(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b",)], previous)
        comparison = current["comparison"]
        self.assertEqual(comparison["scope_changes"]["removed"], ["compat_engine/c"])
        self.assertEqual(comparison["scope_changes"]["removed_non_passing"], ["compat_engine/c"])
        self.assertEqual(comparison["scope_changes"]["denominator"], {"previous": 3, "current": 2})
        self.assertFalse(comparison["scope_changes"]["aggregate_rates_compared"])
        self.assertEqual(comparison["fixed_set"]["transitions"], [])
        self.assertEqual(
            [x for x in comparison["fixed_set"]["transitions"] if x["kind"] == "improvement"], []
        )

    def test_a_fixed_case_getting_fixed_is_the_only_improvement(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b",), ("c", "passed")], previous)
        kinds = [t["kind"] for t in current["comparison"]["fixed_set"]["transitions"]]
        self.assertEqual(kinds, ["improvement"])

    def test_a_rewritten_declaration_is_scope_not_an_improvement(self):
        previous = self.baseline()  # c is an undeclared failure
        declared = self.case("c", "expected_limitation")
        declared["expectation"] = {
            "declared_outcome": "expected_limitation",
            "observed_outcome": "expected_limitation",
            "result": "control_met",
            "reason": "now declared a known limitation",
            "references": [{"kind": "issue", "ref": "#1"}],
            "diff": None,
        }
        current = self.summary([("a",), ("b",), declared], previous)
        comparison = current["comparison"]
        self.assertEqual(comparison["fixed_set"]["transitions"], [])
        self.assertEqual(comparison["fixed_set"]["size"], 2)
        self.assertEqual(comparison["scope_changes"]["declaration_changed"], ["compat_engine/c"])
        self.assertIn("declaration changed", tr.render_markdown(current))
        # The same declaration on both sides is compared as usual.
        again = self.summary([("a",), ("b",), declared], current)
        self.assertEqual(again["comparison"]["scope_changes"]["declaration_changed"], [])
        self.assertEqual(again["comparison"]["fixed_set"]["size"], 3)

    def test_different_corpus_version_is_not_compared_as_a_regression(self):
        previous = self.baseline()
        current = self.summary(
            [("a", "passed", "CHANGED"), ("b", "failed", "CHANGED")], previous, corpus="2" * 64
        )
        scope = current["comparison"]["scope_changes"]
        self.assertTrue(scope["corpus_version_changed"]["compat_engine"])
        self.assertEqual(sorted(scope["fixture_changed"]), ["compat_engine/a", "compat_engine/b"])
        fixed = current["comparison"]["fixed_set"]
        self.assertEqual(fixed["size"], 0)
        self.assertEqual(
            fixed["verdict"], "no_regression", "changed fixtures cannot regress; they are scope"
        )
        self.assertEqual(fixed["transitions"], [])

    def test_a_different_environment_gives_no_regression_verdict(self):
        previous = self.baseline()
        env = copy.deepcopy(self.row["environment_before"])
        env["facts"] = {**env["facts"], "python": "3.14.0 other", "platform": "Other-OS"}
        env["fingerprint"] = cg.canonical(env["facts"])
        current = self.summary(
            [("a", "passed", "a", env), ("b", "failed", "a", env), ("c", "failed", "a", env)],
            previous,
        )
        fixed = current["comparison"]["fixed_set"]
        self.assertEqual(fixed["verdict"], "incomparable")
        self.assertFalse(fixed["environment_comparable"])
        self.assertIn("platform", fixed["environment_differences"])
        self.assertTrue(fixed["transitions"], "the observations are still listed")

    def test_markdown_separates_fixed_set_from_scope_and_says_what_is_not_covered(self):
        previous = self.baseline()
        current = self.summary([("a",), ("b",)], previous)
        text = tr.render_markdown(current)
        self.assertIn("**Fixed set**", text)
        self.assertIn("**Scope changes**", text)
        self.assertIn("(not an improvement)", text)
        self.assertIn("### Not covered", text)
        self.assertIn("not user success rates", text)

    # ---- explore side: family coverage and alerts
    def explore_report(self, rows):
        by_family = {
            "show_close": {
                "axis": "output_call",
                "transforms": ["show_close"],
                "parents_applicable": [],
                "parent_count": 0,
                "candidates": 1,
            },
            "import_alias": {
                "axis": "import_alias",
                "transforms": ["import_alias"],
                "parents_applicable": [],
                "parent_count": 0,
                "candidates": 1,
            },
        }
        return {
            "schema": "tavotto-guardian-explore/1",
            "run_id": "ee" * 16,
            "seed": 42,
            "source": {"commit": "a" * 40},
            "candidates": rows,
            "outcome_counts": {},
            "known_gaps": {"G1": {"found": False}},
            "coverage": {
                "by_family": by_family,
                "parents": {
                    "corpus_by_manifest_category": {"core_artists": 3, "script_shapes": 5},
                    "used_by_manifest_category": {"script_shapes": 2},
                },
            },
        }

    def test_family_coverage_counts_only_families_with_an_executed_candidate(self):
        rows = [
            {"outcome": "no_difference", "chain": ["show_close"]},
            {"outcome": "static_only", "chain": ["import_alias"]},
        ]
        path = self.write("explore.json", self.explore_report(rows))
        out = tr.build([], [path], None, [{"in_corpus": False, "fix_pr": None}])
        cov = out["metrics"]["family_coverage"]
        self.assertEqual((cov["numerator"], cov["denominator"]), (1, 2))
        self.assertEqual(cov["failure_family_records"], {"recorded": 1, "in_corpus": 0, "fixed": 0})
        self.assertTrue(any("core_artists" in u for u in out["uncovered"]))
        self.assertEqual(out["metrics"]["first_figure_success"]["status"], "not_computed")


class Alerts(unittest.TestCase):
    def cluster(self, **over):
        base = {
            "cluster_id": "cl-x",
            "key": {
                "family": "figure_capture",
                "stage": "capture",
                "behavior_kind": "zero_figures",
                "call_site": ["figure"],
            },
            "known_family": None,
            "reproducible": True,
            "size": 2,
            "representative": "r",
            "next_action": "look",
        }
        base.update(over)
        return base

    def test_only_new_reproduced_product_families_alert(self):
        self.assertEqual(len(tg.alerts([self.cluster()])), 1)
        self.assertEqual(tg.alerts([self.cluster(known_family="G1-show-close-zero-figures")]), [])
        self.assertEqual(tg.alerts([self.cluster(reproducible=None)]), [], "never re-run: no alert")
        self.assertEqual(tg.alerts([self.cluster(reproducible=False)]), [])
        for family in tg.NOT_PRODUCT_FAMILIES:
            self.assertEqual(
                tg.alerts([self.cluster(key={**self.cluster()["key"], "family": family})]), []
            )

    def test_triage_is_structurally_confined_to_its_report_files_and_annotations(self):
        """Proves, by parsing compat_triage.py with `ast` (comments and strings cannot fool it):
        its own imports stay inside a fixed allowlist (no network, subprocess or dynamic-import
        module), it has no `open`/`eval`/`exec`/`__import__`/`os.system`-style calls, and the only
        file writes are `.write_text` on the `--out` temp file and the `--markdown-out` path.
        It does NOT prove what the imported Guardian modules do, nor what the runner permits;
        the workflow's read-only permissions are checked separately."""
        tree = ast.parse((Path(cg.HERE) / "compat_triage.py").read_text(encoding="utf-8"))
        allowed = {
            "__future__",
            "ast",
            "hashlib",
            "json",
            "os",
            "pathlib",
            "compat_guardian",
            "compat_guardian_taxonomy",
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertLessEqual(imported, allowed)
        banned_names = {"open", "eval", "exec", "compile", "__import__"}
        banned_os = {"system", "popen", "remove", "unlink", "rename", "makedirs", "putenv", "kill"}
        writes = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                self.assertNotIn(func.id, banned_names, ast.dump(node)[:80])
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    self.assertNotIn(func.attr, banned_os)
                    self.assertFalse(func.attr.startswith(("exec", "spawn")), func.attr)
                if func.attr in ("write_text", "write_bytes", "open", "touch", "unlink"):
                    writes.append(ast.unparse(func.value))
        self.assertEqual(
            sorted(writes), ["Path(args.markdown_out)", "tmp"], "unexpected file-write target"
        )
        # the only stdout channel for alerts is a workflow annotation, emitted by print()
        printed = [
            ast.unparse(n.args[0])
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "print" and n.args
        ]
        self.assertTrue(any("::warning title=" in p for p in printed))


class ExploreOptions(unittest.TestCase):
    def test_unknown_family_is_rejected_and_filter_restricts_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                seed=1,
                out=tmp,
                limit=None,
                parent="shape_oo_savefig",
                static_only=True,
                expected_sha=None,
                timeout=5,
                families="nope",
                candidates=None,
                confirm_differences=0,
            )
            with self.assertRaises(cg.EvidenceError):
                ex.explore(args)
            args.families = "import_alias"
            with mock.patch.object(ex, "run_one", side_effect=AssertionError("static only")):
                ex.explore(args)
            (run,) = Path(tmp).iterdir()
            report = json.loads((run / "reports/explore-report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["candidates"])
            self.assertTrue(all("import_alias" in c["chain"] for c in report["candidates"]))

    def test_confirmation_reruns_one_candidate_per_shape_and_records_reproduction(self):
        diff = {
            "kind": "zero_figures",
            "summary": "s",
            "category": "figure_capture",
            "evidence": {},
        }
        rows = [
            {
                "candidate_id": f"c{i}",
                "outcome": "difference_found",
                "chain": ["show_close"],
                "differences": [diff],
            }
            for i in range(3)
        ] + [
            {
                "candidate_id": "ok",
                "outcome": "no_difference",
                "chain": ["rcparams"],
                "differences": [],
            }
        ]
        again = {"judgment": {"outcome": "difference_found", "differences": [diff]}}
        args = Namespace(confirm_differences=2, seed=1, timeout=1)
        with mock.patch.object(ex, "run_one", return_value=again) as rerun:
            ex.confirm_differences(
                rows, {r["candidate_id"]: {} for r in rows}, args, Path("/nonexistent")
            )
        self.assertEqual(rerun.call_count, 1, "one rerun per distinct finding shape")
        self.assertEqual(rows[0]["confirmation"], {"reproduced": True, "runs": 1})
        self.assertIsNone(rows[1]["confirmation"])
        gone = {"judgment": {"outcome": "no_difference", "differences": []}}
        rows2 = [dict(rows[0], confirmation=None)]
        with mock.patch.object(ex, "run_one", return_value=gone):
            ex.confirm_differences(rows2, {"c0": {}}, args, Path("/nonexistent"))
        self.assertFalse(rows2[0]["confirmation"]["reproduced"])


if __name__ == "__main__":
    unittest.main()
