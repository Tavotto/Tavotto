"""CG-03 minimize: bounded, re-verified reduction; refuses reductions that lose or change the failure."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore_transforms as xt  # noqa: E402
import compat_guardian as cg  # noqa: E402
import compat_minimize as mn  # noqa: E402
import compat_triage as tg  # noqa: E402

RULES = tg.load_rules()


def record(
    outcome="difference_found",
    kinds=("zero_figures",),
    error="script_no_figure",
    native_exit=0,
    shown=1,
    saved=0,
):
    diffs = [
        {
            "kind": k,
            "summary": k,
            "category": "figure_capture",
            "evidence": {"tavotto_error": error},
        }
        for k in kinds
    ]
    return {
        "judgment": {
            "outcome": outcome,
            "differences": diffs if outcome == "difference_found" else [],
        },
        "native": {
            "exit_status": native_exit,
            "figure_count": 1,
            "figures_shown": shown,
            "savefig_calls": saved,
            "savefig_last_writer": {},
            "semantics": {},
            "artifacts": [],
        },
        "tavotto": {
            "route": "probe",
            "error": {"code": error, "message": ""} if error else None,
            "stems": {},
        },
    }


class Accept(unittest.TestCase):
    def sig(self, **kw):
        return mn.signature(record(**kw), ["show_close"], RULES)

    def test_same_failure_is_accepted(self):
        self.assertTrue(mn.accept(self.sig(), self.sig())[0])

    def test_case_10_error_vanishes_so_the_reduction_is_rejected(self):
        ok, reason = mn.accept(self.sig(), self.sig(outcome="no_difference", kinds=(), error=None))
        self.assertFalse(ok)
        self.assertIn("disappeared", reason)

    def test_a_new_error_masquerading_as_the_defect_is_rejected(self):
        for code in ("script_error", "syntax_error", "missing_dependency"):
            ok, reason = mn.accept(self.sig(), self.sig(error=code))
            self.assertFalse(ok, code)
            self.assertIn("different Tavotto error", reason)

    def test_a_reduction_that_breaks_native_is_rejected_not_blamed_on_tavotto(self):
        ok, reason = mn.accept(self.sig(), self.sig(native_exit=1))
        self.assertFalse(ok)
        self.assertIn("native", reason)

    def test_a_changed_native_expectation_or_difference_kind_is_rejected(self):
        self.assertFalse(mn.accept(self.sig(), self.sig(shown=0, saved=1))[0])
        overwrite = mn.signature(record(), ["show_close"], RULES)
        overwrite["native_expect"] = dict(overwrite["native_expect"], overwrite=True)
        self.assertFalse(mn.accept(self.sig(), overwrite)[0])
        self.assertFalse(
            mn.accept(self.sig(), self.sig(kinds=("zero_figures", "fewer_figures")))[0]
        )

    def test_a_changed_attributed_stage_or_family_is_rejected(self):
        original = self.sig()
        changed = dict(original, family="partial_capture")
        self.assertFalse(mn.accept(original, changed)[0])


class Machinery(unittest.TestCase):
    SRC = (
        "import json\nimport matplotlib.pyplot as plt\n\n\ndef main():\n"
        "    fig, ax = plt.subplots()\n    ax.plot([1, 2])\n    for i in range(2):\n        ax.set_title(str(i))\n"
        "    plt.show()\n    plt.close('all')\n"
    )

    def test_apply_reduction_only_removes_code(self):
        tree = ast.parse(self.SRC)
        addrs = xt.statement_addresses(tree)
        reduced = xt.apply_reduction(self.SRC, {"delete": addrs[:2]})
        before = {ast.dump(n) for n in ast.walk(tree) if isinstance(n, ast.stmt)}
        after = {ast.dump(n) for n in ast.walk(ast.parse(reduced)) if isinstance(n, ast.stmt)}
        self.assertLessEqual({d for d in after if "Pass" not in d}, before)
        compile(reduced, "r.py", "exec")

    def test_unwrap_replaces_a_block_by_its_body_and_rejects_non_blocks(self):
        loop = next(
            a
            for a in xt.statement_addresses(ast.parse(self.SRC))
            if isinstance(_at(self.SRC, a), ast.For)
        )
        out = xt.apply_reduction(self.SRC, {"unwrap": [loop]})
        self.assertNotIn("for i in", out)
        self.assertIn("ax.set_title(str(i))", out)
        call = next(
            a
            for a in xt.statement_addresses(ast.parse(self.SRC))
            if "plot" in ast.unparse(_at(self.SRC, a))
        )
        with self.assertRaises(cg.EvidenceError):
            xt.apply_reduction(self.SRC, {"unwrap": [call]})

    def test_unknown_address_is_rejected(self):
        with self.assertRaises(cg.EvidenceError):
            xt.apply_reduction(self.SRC, {"delete": ["/body99"]})

    def test_a_reduction_cannot_drop_the_script_or_an_absent_file(self):
        parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        with self.assertRaises(cg.EvidenceError):
            xt.build_reduced(
                parent, ("import_alias",), 1, {"delete": [], "drop_files": [parent.script]}
            )
        with self.assertRaises(cg.EvidenceError):
            xt.build_reduced(
                parent, ("import_alias",), 1, {"delete": [], "drop_files": ["nope.csv"]}
            )

    def test_entry_function_and_main_guard_are_never_removal_units(self):
        units = mn.units_of(self.SRC, "main")
        nodes = [_at(self.SRC, a) for a in units]
        self.assertFalse(any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in nodes))
        guarded = "import os\nif __name__ == '__main__':\n    print(1)\n"
        self.assertFalse(
            any(isinstance(_at(guarded, a), ast.If) for a in mn.units_of(guarded, "__main__"))
        )

    def test_ddmin_finds_a_minimal_keep_set_and_is_bounded(self):
        needed = {"c", "f"}
        calls = []

        def test(kept):
            calls.append(tuple(kept))
            return needed <= set(kept)

        got = mn.ddmin(list("abcdefgh"), test, mn.Budget(100, 100))
        self.assertEqual(set(got), needed)
        self.assertEqual(len(calls), len(set(calls)), "no config is evaluated twice")

    def test_budget_rejects_non_finite_or_non_positive_values(self):
        for bad in ((0, 10), (5, 0), (5, float("inf")), (True, 5)):
            with self.assertRaises(cg.EvidenceError):
                mn.Budget(*bad)


def _at(source, addr):
    tree = ast.parse(source)
    node = tree
    for part in addr.strip("/").split("/"):
        field = part.rstrip("0123456789")
        node = getattr(node, field)[int(part[len(field) :])]
    return node


def result_original(case) -> str:
    return xt.build(case.parent, ("show_close",), 7)[1][case.parent.script].decode()


class Search(unittest.TestCase):
    """The whole search against a scripted oracle (no processes): fast and deterministic."""

    @classmethod
    def setUpClass(cls):
        cls.parent = xt.load_parent("compat_engine", "shape_oo_savefig")
        cls.manifest, _ = xt.build(cls.parent, ("show_close",), 7)

    def oracle_for(self, behavior):
        calls = []

        def oracle(reduction, tag):
            _, files = xt.build_reduced(self.parent, ("show_close",), 7, reduction)
            calls.append(tag)
            return behavior(files[self.parent.script].decode())

        oracle.calls = calls
        return oracle

    def search(self, behavior, budget=None, repeat=2):
        oracle = self.oracle_for(behavior)
        result = mn.minimize_candidate(
            self.manifest, self.parent, oracle, RULES, budget or mn.Budget(80, 600), repeat
        )
        return result, oracle

    @staticmethod
    def failing(source):
        """The scripted product: zero figures iff the script still shows then closes."""
        if "show()" in source and "close(" in source:
            if "plt.subplots" not in source and "ax." in source:
                return record(error="script_error")  # NameError-ish: a different failure
            return record()
        return record(outcome="no_difference", kinds=(), error=None)

    def test_reduces_to_the_statements_that_matter_and_keeps_the_same_failure(self):
        result, oracle = self.search(self.failing)
        self.assertEqual(result["status"], "minimized", result.get("note"))
        text = result["files"][self.parent.script].decode()
        self.assertIn("show()", text)
        self.assertIn("close(", text)
        self.assertNotIn("set_xlabel", text)
        self.assertLess(result["after"]["statements"], result["before"]["statements"])
        self.assertEqual(result["reproducibility"], {"confirmed": 3, "total": 3})
        rejected = [t for t in result["trials"] if not t["accepted"]]
        self.assertTrue(rejected, "rejections are recorded, not hidden")
        self.assertTrue(any("disappeared" in t["reason"] for t in rejected))

    def test_case_10_a_reduction_that_loses_the_error_never_survives(self):
        result, _ = self.search(self.failing)
        text = result["files"][self.parent.script].decode()
        self.assertTrue(
            "show()" in text and "close(" in text, "the lines that carry the defect must remain"
        )
        self.assertTrue(any("disappeared" in t["reason"] for t in result["trials"]))

    def test_a_reduction_that_swaps_in_a_new_error_is_rejected(self):
        result, _ = self.search(self.failing)
        # removing plt.subplots while ax.* stays would "fail" with a different error: rejected
        self.assertTrue(any("different Tavotto error" in t["reason"] for t in result["trials"]))

    def test_budget_exhaustion_is_minimization_failed_and_keeps_the_original(self):
        result, oracle = self.search(self.failing, mn.Budget(3, 600))
        self.assertEqual(result["status"], "minimization_failed")
        self.assertIsNone(result["reduction"])
        original = xt.build(self.parent, ("show_close",), 7)[1]
        self.assertEqual(result["files"], original)
        self.assertLessEqual(len(oracle.calls), 3)
        self.assertIn("budget", result["note"])

    def test_time_budget_also_stops_the_search(self):
        budget = mn.Budget(100, 0.001)
        import time

        time.sleep(0.01)
        result, _ = self.search(self.failing, budget)
        self.assertEqual(result["status"], "minimization_failed")

    def test_case_15_a_candidate_that_does_not_fail_is_not_reproduced_and_nothing_is_invented(self):
        result, oracle = self.search(
            lambda source: record(outcome="no_difference", kinds=(), error=None)
        )
        self.assertEqual(result["status"], "not_reproduced")
        self.assertEqual(
            oracle.calls, ["base"], "no reduction is attempted without a reproduced failure"
        )
        self.assertIsNone(result["reduction"])
        for key in ("files", "after", "runs", "seconds", "trials"):
            self.assertIn(key, result, f"not_reproduced skipped the common tail: {key}")

    def test_a_not_reproduced_result_is_published_by_write_outcome_without_a_crash(self):
        result, _ = self.search(
            lambda source: record(outcome="no_difference", kinds=(), error=None)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = mn.write_outcome(
                result, self.manifest, self.parent, Path(tmp), RULES, {"max_runs": 80}
            )
            doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["status"], "not_reproduced")
        self.assertIsNone(doc["reduction"])
        self.assertEqual(doc["before_after"]["before"], doc["before_after"]["after"])
        self.assertEqual(doc["script"], self.parent.script)

    def test_minimize_refuses_candidates_the_report_never_observed_as_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "reports").mkdir(parents=True)
            manifests = [
                {"candidate_id": "a" * 20},
                {"candidate_id": "b" * 20},
                {"candidate_id": "c" * 20},
            ]
            (run_dir / "candidates.json").write_text(
                json.dumps({"candidates": manifests}), encoding="utf-8"
            )
            report = run_dir / "reports/explore-report.json"
            report.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {"candidate_id": "a" * 20, "outcome": "difference_found"},
                            {"candidate_id": "b" * 20, "outcome": "no_difference"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            manifest, _ = mn.find_candidate(report, "a" * 20)
            self.assertEqual(manifest["candidate_id"], "a" * 20)
            for cid in ("b" * 20, "c" * 20):  # observed-but-clean, and listed-but-never-run
                with self.assertRaises(cg.EvidenceError, msg=cid):
                    mn.find_candidate(report, cid)

    def test_a_failed_confirmation_falls_back_with_the_base_run_evidence_only(self):
        def oracle(reduction, tag):
            rec = record() if not tag.startswith("confirm") else record(error="script_error")
            return {**rec, "_tag": tag}

        result = mn.minimize_candidate(
            self.manifest, self.parent, oracle, RULES, mn.Budget(80, 600), 1
        )
        self.assertEqual(result["status"], "minimization_failed")
        self.assertEqual(result["final_record"]["_tag"], "base", "evidence is the base run's")
        self.assertIsNone(result["reproducibility"])
        self.assertIsNone(result["reduction"])
        self.assertEqual(result["files"][self.parent.script].decode(), result_original(self))

    def test_each_trial_is_bounded_by_the_remaining_wall_clock_budget(self):
        budget = mn.Budget(10, 100)
        self.assertLessEqual(mn.trial_timeout(240, budget), 100)
        short = mn.Budget(10, 1)
        short.started -= 0.9  # 0.1 s left
        self.assertLess(mn.trial_timeout(240, short), 1.0)
        self.assertGreater(mn.trial_timeout(240, short), 0)
        spent = mn.Budget(10, 1)
        spent.started -= 5
        self.assertEqual(mn.trial_timeout(240, spent), 0.5)
        self.assertEqual(mn.trial_timeout(3, mn.Budget(10, 1000)), 3)

    def test_the_documented_replay_command_shape_parses_and_runs(self):
        result, _ = self.search(self.failing)
        self.assertEqual(result["status"], "minimized")
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as scratch:
            repro = mn.write_outcome(
                result, self.manifest, self.parent, Path(tmp), RULES, {"max_runs": 80}
            )
            doc = json.loads(repro.read_text(encoding="utf-8"))
            self.assertIn("minimize --replay <dir>/repro.json", doc["commands"]["tavotto"])
            with mock.patch.object(mn.ex, "run_one", return_value=record()) as run_one:
                code = cg.main(["minimize", "--replay", str(repro), "--out", scratch])
            self.assertEqual(code, cg.EXIT_PASSED)
            self.assertEqual(run_one.call_args.args[-1], "replay")

    def test_dirty_execution_code_is_refused_before_minimizing_or_replaying(self):
        dirty = ["scripts/ci/compat_driver.py"]
        with (
            tempfile.TemporaryDirectory() as scratch,
            mock.patch.object(mn.ex, "dirty_execution_code", return_value=dirty),
            mock.patch.object(mn.ex, "run_one", side_effect=AssertionError("executed")),
        ):
            for mode in ({"replay": "a.json"}, {"report": "r.json", "candidate": "c"}):
                args = Namespace(out=scratch, timeout=5, replay=None, report=None, candidate=None)
                vars(args).update(mode)
                with self.assertRaisesRegex(cg.EvidenceError, "compat_driver"):
                    mn.run(args)

    def test_minimize_modes_are_exclusive_and_one_is_required(self):
        for argv in (
            ["minimize", "--out", "/x"],
            ["minimize", "--report", "r.json", "--out", "/x"],
            [
                "minimize",
                "--replay",
                "a.json",
                "--report",
                "r.json",
                "--candidate",
                "c",
                "--out",
                "/x",
            ],
        ):
            with self.assertRaises(SystemExit) as caught:
                cg.main(argv)
            self.assertEqual(caught.exception.code, 2, argv)

    def test_unstable_reproduction_is_not_reported_as_minimized(self):
        def oracle(reduction, tag):
            _, files = xt.build_reduced(self.parent, ("show_close",), 7, reduction)
            if tag.startswith("confirm"):
                return record(outcome="no_difference", kinds=(), error=None)
            return self.failing(files[self.parent.script].decode())

        result = mn.minimize_candidate(
            self.manifest, self.parent, oracle, RULES, mn.Budget(80, 600), 2
        )
        self.assertEqual(result["status"], "minimization_failed")
        self.assertIn("did not reproduce", result["note"])
        self.assertIsNone(result["reduction"])


if __name__ == "__main__":
    unittest.main()
