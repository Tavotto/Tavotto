"""CG-03 triage: rule table, honest unknowns, and clustering that never merges G1 with G2."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_explore as ex  # noqa: E402
import compat_explore_transforms as xt  # noqa: E402
import compat_guardian as cg  # noqa: E402
import compat_guardian_taxonomy as tx  # noqa: E402
import compat_triage as tg  # noqa: E402
from test_compat_guardian import GuardianBase, native_result  # noqa: E402


def explore_facts(**over) -> dict:
    facts = {
        "kind": None,
        "outcome": "difference_found",
        "tavotto_error": None,
        "tavotto_error_message": "",
        "native_ok": True,
        "native_figure_count": 1,
        "native_figures_shown": None,
        "native_savefig_calls": None,
        "tavotto_figure_count": 0,
        "chain": [],
    }
    facts.update(over)
    return facts


class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = tg.load_rules()

    def attr(self, **facts):
        return tg.attribute(explore_facts(**facts), "explore", self.rules)

    def test_rule_table_is_valid_data(self):
        ids = [r["id"] for r in self.rules]
        self.assertEqual(len(ids), len(set(ids)))
        for rule in self.rules:
            self.assertIn(rule["family"], tx.CATEGORIES)
            self.assertTrue(rule["modules"] and rule["next_action"] and rule["why"])
        self.assertEqual(len(tx.CATEGORIES), 16)
        self.assertEqual(tx.CATEGORIES[-1], "unknown")

    def test_every_family_that_can_be_proven_has_at_least_one_rule_or_is_documented_unreachable(
        self,
    ):
        covered = {r["family"] for r in self.rules}
        unreachable_from_reports = {
            "argument_or_input",
            "dependency_version",
            "product_entry_reachability",
        }
        self.assertEqual(set(tx.CATEGORIES) - covered - unreachable_from_reports, set())

    def test_a_bad_rule_is_a_load_error_not_a_silent_non_match(self):
        document = json.loads(tg.RULES_PATH.read_text(encoding="utf-8"))
        for mutate, label in (
            (lambda d: d["rules"][0]["when"][0].update(field="no_such_fact"), "unknown fact"),
            (lambda d: d["rules"][0].update(family="made_up"), "unknown family"),
            (
                lambda d: d["rules"][0].update(modules=["src/tavotto/engine/not_a_module.py"]),
                "missing module",
            ),
            (lambda d: d["rules"][0]["when"][0].update(op="true"), "operator without value"),
            (lambda d: d["rules"].append(copy.deepcopy(d["rules"][0])), "duplicate id"),
        ):
            doc = copy.deepcopy(document)
            mutate(doc)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "rules.json"
                path.write_text(json.dumps(doc), encoding="utf-8")
                with self.assertRaises(cg.EvidenceError, msg=label):
                    tg.load_rules(path)

    def test_safe_blocked_savefig_then_readback_is_never_missing_data(self):
        for facts in (
            dict(
                tavotto_error="safe_file_operation_blocked",
                tavotto_error_message="FileNotFoundError: shot.png",
            ),
            dict(
                tavotto_error="script_error",
                tavotto_error_message="FileNotFoundError: shot.png",
                native_savefig_calls=2,
            ),
        ):
            got = self.attr(kind="zero_figures", **facts)
            self.assertEqual(got["family"], "safe_execution_divergence", facts)
            self.assertNotEqual(got["family"], "cwd_or_data_path")

    def test_filenotfound_without_evidence_is_unknown_and_with_native_clean_is_low_confidence(self):
        unproven = self.attr(
            kind="zero_figures",
            tavotto_error="script_error",
            tavotto_error_message="FileNotFoundError: data.csv",
        )
        self.assertEqual(unproven["family"], "unknown")
        data = self.attr(
            kind="zero_figures",
            tavotto_error="script_error",
            tavotto_error_message="FileNotFoundError: data.csv",
            native_savefig_calls=0,
        )
        self.assertEqual((data["family"], data["confidence"]), ("cwd_or_data_path", "low"))

    def test_g1_and_g2_shapes_are_attributed_with_evidence_requirements(self):
        g1 = self.attr(
            kind="zero_figures",
            tavotto_error="script_no_figure",
            native_figures_shown=1,
            native_savefig_calls=0,
        )
        self.assertEqual(
            (g1["family"], g1["stage"], g1["confidence"]), ("figure_capture", "capture", "high")
        )
        weak = self.attr(
            kind="zero_figures", tavotto_error="script_no_figure", chain=["show_close"]
        )
        self.assertEqual(
            weak["confidence"], "medium", "no show/savefig counters: lower confidence, said so"
        )
        g2 = self.attr(kind="same_path_content_mismatch")
        self.assertEqual((g2["family"], g2["confidence"]), ("figure_capture", "high"))
        self.assertTrue(any("figsession" in m for m in g2["likely_modules"]))

    def test_an_absent_fact_never_satisfies_a_positive_condition(self):
        got = self.attr(
            kind="zero_figures",
            tavotto_error="script_no_figure",
            native_figures_shown=None,
            native_savefig_calls=0,
        )
        self.assertNotEqual(got["rule_id"], "x.show_without_save")

    def test_unmatched_findings_are_unknown_with_a_next_action(self):
        got = self.attr(kind="something_new", outcome="difference_found")
        self.assertEqual(
            (got["family"], got["confidence"], got["rule_id"]), ("unknown", "low", None)
        )
        self.assertTrue(got["next_action"])

    def test_partial_title_and_generator_findings_map_to_their_families(self):
        self.assertEqual(self.attr(kind="fewer_figures")["family"], "partial_capture")
        self.assertEqual(self.attr(kind="title_missing")["family"], "manifest_semantics")
        self.assertEqual(self.attr(outcome="invalid_candidate")["family"], "invalid_fixture")
        self.assertEqual(self.attr(outcome="invalid_fixture")["family"], "invalid_fixture")

    def test_guardian_rows_use_the_same_table(self):
        row = cg.new_row("x", "contract_fixture")
        row["status"] = "failed"
        row["native"] = {"status": "passed", "result": native_result()}
        row["stages"]["capture"] = cg.stage("failed", "x")
        row["figures"] = {"native": 2, "tavotto_captured": 1, "tavotto_valid": 1}
        got = tg.attribute(tg.row_facts(row), "row", self.rules)
        self.assertEqual(got["family"], "partial_capture")
        row["native"]["status"] = "invalid_fixture"
        self.assertEqual(
            tg.attribute(tg.row_facts(row), "row", self.rules)["family"], "invalid_fixture"
        )
        crash = cg.new_row("y", "runner")
        crash["raw"] = {"runner_error": "x"}
        self.assertEqual(tg.attribute(tg.row_facts(crash), "row", self.rules)["family"], "unknown")


class CallSite(unittest.TestCase):
    def test_signature_is_alias_loop_and_parent_independent(self):
        a = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nplt.show()\nplt.close('all')\n"
        b = "import matplotlib.pyplot as cg_plt3\ncg_plt3.figure()\ncg_plt3.show()\ncg_plt3.close(1)\n"
        self.assertEqual(tg.call_site_signature(a), ["figure", "show", "close"])
        self.assertEqual(tg.call_site_signature(a), tg.call_site_signature(b))
        loop = "for i in range(3):\n    fig = plt.figure()\n    fig.savefig('x.png')\n"
        self.assertEqual(tg.call_site_signature(loop), ["loop:figure", "loop:savefig"])


class Clusters(unittest.TestCase):
    """27 findings shaped like the CG-02 run (11 show/close, 16 same path) -> exactly two clusters."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.run_dir = Path(cls.tmp.name) / "RUN"
        cls.generated = xt.generate(42)
        ex.write_candidates(cls.generated, cls.run_dir)
        chosen = [
            c
            for c in cls.generated["candidates"]
            if c["execution_plan"]["status"] == "executable"
            and (
                "show_close" in [s["name"] for s in c["chain"]]
                or "same_path_loop" in [s["name"] for s in c["chain"]]
            )
        ]
        g1 = [c for c in chosen if "show_close" in [s["name"] for s in c["chain"]]][:11]
        g2 = [c for c in chosen if "same_path_loop" in [s["name"] for s in c["chain"]]][:16]
        rows = []
        for i, c in enumerate(g1 + g2):
            first = c in g1
            diff = (
                {
                    "kind": "zero_figures",
                    "summary": "native 1 figure(s) / Tavotto 0 figures",
                    "category": "figure_capture",
                    "evidence": {
                        "native_figures": 1,
                        "tavotto_figures": 0,
                        "tavotto_error": "script_no_figure",
                    },
                }
                if first
                else {
                    "kind": "same_path_content_mismatch",
                    "summary": "native last / Tavotto kept first",
                    "category": "figure_capture",
                    "evidence": {"path": "o.png"},
                }
            )
            rows.append(
                {
                    "candidate_id": c["candidate_id"],
                    "parent": c["parent"]["case"],
                    "suite": "compat_engine",
                    "chain": [s["name"] for s in c["chain"]],
                    "axis_vector": c["axis_vector"],
                    "class": c["class"],
                    "seed": 42,
                    "ast_signature": c["ast_signature"],
                    "trust": c["trust"],
                    "outcome": "difference_found",
                    "differences": [diff],
                    "note": "",
                    "executions": {},
                    "tavotto_route": "probe",
                    "timings_s": None,
                    "evidence": {
                        "native": {
                            "exit_status": 0,
                            "figure_count": 1 if first else 3,
                            "figures_shown": 1 if first else 0,
                            "figures_saved": 0 if first else 3,
                            "savefig_calls": 0 if first else 3,
                            "artifacts": [],
                            "last_writer": {},
                            "titles": [],
                        },
                        # varied, deliberately different message text: clustering must not key on it
                        "tavotto": {
                            "route": "probe",
                            "error": (
                                {"code": "script_no_figure", "message": f"variant text {i}"}
                                if first
                                else None
                            ),
                            "stems": {},
                        },
                    }
                    if True
                    else None,
                }
            )
        cls.report = {
            "schema": "tavotto-guardian-explore/1",
            "run_id": "ab" * 16,
            "candidates": rows,
        }
        cls.report_path = cls.run_dir / "reports" / "explore-report.json"
        cls.report_path.parent.mkdir()
        cls.report_path.write_text(json.dumps(cls.report), encoding="utf-8")
        cls.g1_count, cls.g2_count = len(g1), len(g2)

    def test_the_findings_cluster_into_exactly_two_families_and_never_merge(self):
        result = tg.triage([self.report_path])
        self.assertEqual(result["summary"]["findings"], self.g1_count + self.g2_count)
        clusters = result["clusters"]
        self.assertEqual(len(clusters), 2, [c["key"] for c in clusters])
        sizes = {c["key"]["behavior_kind"]: c["size"] for c in clusters}
        self.assertEqual(
            sizes, {"zero_figures": self.g1_count, "same_path_content_mismatch": self.g2_count}
        )
        self.assertFalse(set(clusters[0]["members"]) & set(clusters[1]["members"]))
        self.assertEqual(result["summary"]["unknown"], 0)

    def test_parent_and_extra_transforms_do_not_split_a_cluster(self):
        result = tg.triage([self.report_path])
        parents = {a["ref"] for a in result["attributions"]}
        self.assertGreater(len(parents), 4)
        self.assertEqual(len(result["clusters"]), 2)

    def test_same_error_text_with_a_different_call_site_is_a_different_cluster(self):
        report = copy.deepcopy(self.report)
        victim = next(
            r for r in report["candidates"] if r["differences"][0]["kind"] == "zero_figures"
        )
        other = next(
            r
            for r in report["candidates"]
            if r["differences"][0]["kind"] == "same_path_content_mismatch"
        )
        # make the G2 candidate look like the G1 one in every report field; only its code differs
        other["differences"] = copy.deepcopy(victim["differences"])
        other["evidence"] = copy.deepcopy(victim["evidence"])
        path = self.run_dir / "reports" / "explore-copy.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        result = tg.triage([path])
        sites = {tuple(c["key"]["call_site"]) for c in result["clusters"]}
        self.assertEqual(len(result["clusters"]), 3, "call-site differs: its own cluster")
        self.assertIn(("loop:figure", "loop:savefig"), sites)
        self.assertIn(("figure", "show", "close"), sites)

    def test_tracebacks_do_not_enter_the_key(self):
        result = tg.triage([self.report_path])
        for cluster in result["clusters"]:
            self.assertNotIn("Traceback", json.dumps(cluster["key"]))
        report = copy.deepcopy(self.report)
        for r in report["candidates"]:
            if r["evidence"]["tavotto"]["error"]:
                r["evidence"]["tavotto"]["error"]["message"] = "completely different words"
        path = self.run_dir / "reports" / "explore-words.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        self.assertEqual(len(tg.triage([path])["clusters"]), 2)

    def test_both_correct_makes_no_findings_case_15(self):
        report = {
            "schema": "tavotto-guardian-explore/1",
            "run_id": "cd" * 16,
            "candidates": [
                {**r, "outcome": "no_difference", "differences": []}
                for r in self.report["candidates"][:5]
            ],
        }
        path = self.run_dir / "reports" / "explore-clean.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        result = tg.triage([path])
        self.assertEqual((result["attributions"], result["clusters"]), ([], []))

    def test_a_null_recorded_error_matches_only_findings_without_an_error(self):
        families = tg.load_families()
        g2 = next(f for f in families if f["id"].startswith("G2"))
        self.assertIsNone(g2["signature"]["tavotto_error"])
        shape = {
            "behavior_kind": g2["signature"]["behavior_kind"],
            "call_site": g2["signature"]["call_site"],
        }
        plain = {**shape, "stable_code": f"diff:{shape['behavior_kind']}"}
        with_error = {**shape, "stable_code": "script_error"}
        self.assertEqual(tg.known_family(plain, families), g2["id"])
        self.assertIsNone(tg.known_family(with_error, families), "a new error code is NEW")

    def test_known_family_records_link_the_clusters(self):
        result = tg.triage([self.report_path])
        linked = {c["key"]["behavior_kind"]: c["known_family"] for c in result["clusters"]}
        self.assertEqual(linked["zero_figures"], "G1-show-close-zero-figures")
        self.assertEqual(linked["same_path_content_mismatch"], "G2-same-path-multi-figure")


class RuntimeConfinement(unittest.TestCase):
    """静态检查之外的运行时兜底：真跑一次 triage CLI，用审计钩子记下所有写、改名、删除、建目录、
    子进程与网络事件——只许落在本次的输出目录里（AST 检查总能被新写法绕过，Codex #1139）。"""

    setUpClass = classmethod(Clusters.setUpClass.__func__)

    HOOK = textwrap.dedent(
        """
        import json, os, sys
        events = []
        WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        def hook(event, args):
            if event == "open":
                path, mode, flags = args
                if isinstance(path, (str, bytes, os.PathLike)) and (
                    (isinstance(mode, str) and any(c in mode for c in "wax+"))
                    or (isinstance(flags, int) and flags & WRITE)
                ):
                    events.append(["write", os.fsdecode(path)])
            elif event in ("os.rename", "os.remove", "os.rmdir", "os.mkdir", "os.unlink",
                           "os.symlink", "os.link", "os.chmod", "os.truncate", "shutil.rmtree"):
                events.append([event, os.fsdecode(args[0])])
            elif event.startswith(("subprocess.", "os.exec", "os.spawn", "os.posix_spawn",
                                   "os.system", "socket.connect", "urllib.")):
                events.append([event, ""])
        sys.addaudithook(hook)
        sys.path.insert(0, sys.argv[1])
        import compat_guardian as cg
        code = cg.main(sys.argv[2:])
        print("AUDIT " + json.dumps({"code": code, "events": events}))
        """
    )

    def test_the_real_triage_cli_only_touches_its_own_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp).resolve()
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    self.HOOK,
                    str(cg.HERE),
                    "triage",
                    str(self.report_path),
                    "--out",
                    str(out_dir / "triage.json"),
                    "--markdown-out",
                    str(out_dir / "triage.md"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=120,
            )
            line = [ln for ln in proc.stdout.splitlines() if ln.startswith("AUDIT ")]
            self.assertTrue(line, proc.stderr[-2000:])
            audit = json.loads(line[-1][len("AUDIT ") :])
            self.assertEqual(audit["code"], 0, proc.stderr[-2000:])
            self.assertTrue(audit["events"], "the hook saw the CLI's own output writes")
            outside = [
                e
                for e in audit["events"]
                if not e[1] or not Path(e[1]).resolve().is_relative_to(out_dir)
            ]
            self.assertEqual(outside, [], "triage touched something outside its output dir")


class Case14SemanticsDiffer(unittest.TestCase):
    def test_both_sides_draw_figures_with_different_content_is_detected(self):
        native = native_result(
            figure_count=1,
            semantics={"figure0": {"titles": ["native truth"]}},
            savefig_last_writer={},
        )
        tavotto = {"stems": {"fig": {"titles": ["something else"]}}, "error": None}
        verdict = ex.compare("axis_changing", native, tavotto, None)
        self.assertEqual(verdict["outcome"], "difference_found")
        self.assertEqual(verdict["differences"][0]["kind"], "title_missing")
        self.assertEqual(
            tg.attribute(
                tg.explore_facts(
                    {"outcome": "difference_found", "chain": []}, verdict["differences"][0]
                ),
                "explore",
                tg.load_rules(),
            )["family"],
            "manifest_semantics",
        )


class RunReportProof(GuardianBase):
    """Run reports enter triage only through the summary's structural validation."""

    def write(self):
        path = self.root / "guardian.json"
        path.write_text(json.dumps(self.report), encoding="utf-8")
        return path

    def test_a_consistent_run_report_is_accepted(self):
        self.assertEqual(tg.triage([self.write()])["sources"][0]["kind"], "run")

    def test_a_row_relabelled_failed_against_its_own_proof_is_rejected(self):
        self.row["status"] = "failed"
        self.row["failure_category"] = {
            "category": "figure_capture",
            "source_code": "edited",
            "basis": "x",
        }
        self.report["verdict"] = "failed"
        self.report["coverage"] = cg.coverage_table([self.row])
        with self.assertRaises(cg.EvidenceError):
            tg.triage([self.write()])

    def test_a_verdict_that_does_not_follow_from_its_cases_is_rejected(self):
        self.report["verdict"] = "failed"
        with self.assertRaises(cg.EvidenceError):
            tg.triage([self.write()])


class TriageCliContainment(unittest.TestCase):
    def test_markdown_out_inside_the_worktree_is_refused_before_any_write(self):
        target = cg.ROOT / "tests/compat_guardian/expectations.json"
        before = target.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                reports=[], out=str(Path(tmp) / "t.json"), markdown_out=str(target)
            )
            with self.assertRaisesRegex(cg.EvidenceError, "markdown-out"):
                tg.run(args)
            self.assertFalse((Path(tmp) / "t.json").exists(), "nothing written at all")
        self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
